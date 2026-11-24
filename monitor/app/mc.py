"""Обёртка над meshctrl.js: единственный транспорт к MeshCentral.

Пароль/токен передаются только через переменные окружения процесса,
никогда не попадают в argv и не пишутся в лог.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings, redact

log = logging.getLogger(__name__)


class MeshCentralError(RuntimeError):
    """Ошибка вызова MeshCentral/meshctrl."""


def powershell_encoded_command(script: str) -> str:
    """Команда для Windows PowerShell через -EncodedCommand (UTF-16LE)."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return (
        "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-EncodedCommand {encoded}"
    )


def parse_json_list(output: str) -> list[dict]:
    """meshctrl может напечатать предупреждение перед JSON — вырезаем массив."""
    start = output.find("[")
    end = output.rfind("]")
    if start < 0 or end <= start:
        raise MeshCentralError(f"meshctrl вернул не JSON: {_preview(output)}")
    chunk = output[start : end + 1]
    try:
        data = json.loads(chunk)
    except ValueError as exc:
        raise MeshCentralError(f"не разобрал JSON от meshctrl: {_preview(output)}") from exc
    if not isinstance(data, list):
        raise MeshCentralError("meshctrl вернул JSON, но это не список устройств")
    return [item for item in data if isinstance(item, dict)]


def _preview(text: str, limit: int = 300) -> str:
    flat = " ".join(text.strip().split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


@dataclass
class MeshCentralClient:
    settings: Settings
    _sem: threading.BoundedSemaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._sem = threading.BoundedSemaphore(max(1, self.settings.snapshot_concurrency))
        try:
            self.settings.home_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.settings.home_dir, 0o700)
            (self.settings.home_dir / ".config").mkdir(parents=True, exist_ok=True)
        except OSError:  # pragma: no cover - зависит от ФС
            log.warning("не смог подготовить домашний каталог для meshctrl")

    # ------------------------------------------------------------------ utils

    def _redact(self, text: str) -> str:
        secrets = (self.settings.mc_pass, self.settings.mc_token)
        return redact(text, secrets)

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["MESHCTRL_URL"] = self.settings.mc_url
        env["MESHCTRL_LOGINUSER"] = self.settings.mc_user
        env["HOME"] = str(self.settings.home_dir)
        env["XDG_CONFIG_HOME"] = str(self.settings.home_dir / ".config")
        if self.settings.mc_pass:
            env["MESHCTRL_LOGINPASS"] = self.settings.mc_pass
        else:
            env.pop("MESHCTRL_LOGINPASS", None)
        if self.settings.mc_token:
            env["MESHCTRL_TOKEN"] = self.settings.mc_token
        else:
            env.pop("MESHCTRL_TOKEN", None)
        return env

    def _run(self, args: list[str], timeout: int) -> str:
        meshctrl = self.settings.meshctrl_path
        if not meshctrl.is_file():
            raise MeshCentralError(f"не найден meshctrl.js: {meshctrl}")
        node = shutil.which(self.settings.node_bin) or self.settings.node_bin
        command = [node, str(meshctrl), *args]
        # В лог — только действие и id, без --run и прочих длинных аргументов.
        log.debug("meshctrl %s", " ".join(args[:3]))
        # node после вывода большого JSON закрывает stdout раньше, чем родитель
        # успевает вычитать pipe: capture_output обрезает ответ ровно до 64 KiB.
        # Пишем в обычные файлы — так обрезки нет ни на хосте, ни в контейнере.
        try:
            with tempfile.TemporaryFile(
                mode="w+", encoding="utf-8", errors="replace"
            ) as stdout_file, tempfile.TemporaryFile(
                mode="w+", encoding="utf-8", errors="replace"
            ) as stderr_file:
                proc = subprocess.run(
                    command,
                    env=self._env(),
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=timeout,
                )
                stdout_file.seek(0)
                stderr_file.seek(0)
                out = stdout_file.read()
                err = stderr_file.read()
        except FileNotFoundError as exc:
            raise MeshCentralError(f"не найден node/meshctrl: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise MeshCentralError(f"meshctrl не ответил за {timeout} с") from exc

        if proc.returncode != 0:
            detail = err.strip() or out.strip() or f"exit code {proc.returncode}"
            raise MeshCentralError(self._redact(_preview(detail, 500)))
        if not out.strip():
            detail = err.strip() or "пустой ответ"
            raise MeshCentralError(self._redact(f"meshctrl: {_preview(detail)}"))
        return out

    # ----------------------------------------------------------------- public

    def list_devices(self) -> list[dict]:
        with self._sem:
            output = self._run(["listdevices", "--json"], timeout=self.settings.mc_timeout)
        return parse_json_list(output)

    def run_command(self, node_id: str, command: str, timeout: int | None = None) -> str:
        """Выполняет короткую команду в cmd.exe хоста и забирает вывод.

        Агент MeshCentral сам открывает cmd.exe и кормит его командой через stdin,
        поэтому никакой дополнительной обёртки над PowerShell здесь нет.
        """
        with self._sem:
            return self._run(
                ["runcommand", "--id", node_id, "--run", command, "--reply"],
                timeout=timeout or self.settings.snapshot_timeout,
            )

    def run_powershell(self, node_id: str, script: str, timeout: int | None = None) -> str:
        """Короткий inline-скрипт PowerShell (только для служебных команд).

        Большой текст так передавать нельзя: лимит cmd.exe на строку ~8191 символ.
        Для снимков используем заранее загруженный файл и run_command().
        """
        return self.run_command(node_id, powershell_encoded_command(script), timeout)

    def upload_file(
        self,
        node_id: str,
        local_path: Path,
        remote_dir: str,
        timeout: int | None = None,
        remote_name: str | None = None,
    ) -> str:
        """Кладёт файл на хост. Возвращает полный удалённый путь.

        ``remote_name`` нужен, когда локальный файл уникален на каждую загрузку,
        а на хосте имя должно быть стабильным (кэш по SHA).
        """
        local_path = Path(local_path)
        with self._sem:
            output = self._run(
                [
                    "upload",
                    "--id",
                    node_id,
                    "--file",
                    str(local_path),
                    "--target",
                    remote_dir,
                ],
                timeout=timeout or max(self.settings.mc_timeout, 120),
            )
        if "Upload done" not in output:
            raise MeshCentralError(self._redact(f"загрузка не подтверждена: {_preview(output)}"))
        return remote_dir.rstrip("\\/") + "\\" + (remote_name or local_path.name)

    # ----------------------------------------------------------------- health

    def health(self) -> dict[str, object]:
        return {
            "configured": self.settings.mc_configured,
            "meshctrl": self.settings.meshctrl_path.is_file(),
            "node": bool(shutil.which(self.settings.node_bin)),
            "url": redact(self.settings.mc_url),
        }
