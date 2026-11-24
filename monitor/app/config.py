"""Настройки сервиса. Всё читается из переменных окружения (monitor.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}: ожидалось целое число, получено {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name}: значение должно быть >= {minimum}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name}: ожидалось true/false, получено {raw!r}")


@dataclass(frozen=True)
class Settings:
    # Хранилище
    db_path: Path = Path("/data/monitor.db")
    tz: str = "Europe/Moscow"
    title: str = "RMM Monitor"

    # MeshCentral
    mc_url: str = "ws://meshcentral:443"
    mc_user: str = ""
    mc_pass: str = ""
    mc_token: str = ""
    mc_timeout: int = 60
    meshctrl_path: Path = Path("/opt/meshctrl/meshctrl.js")
    node_bin: str = "node"
    home_dir: Path = Path("/tmp/monitor-home")

    # Расписание
    scheduler_enabled: bool = True
    inventory_interval: int = 60
    snapshot_interval: int = 300
    snapshot_concurrency: int = 4
    snapshot_timeout: int = 90
    snapshot_top_processes: int = 25
    snapshot_max_hosts: int = 0
    retention_days: int = 30
    runs_retention_days: int = 30

    # Доступ к веб-интерфейсу
    auth_user: str = ""
    auth_pass: str = ""
    allow_anonymous: bool = False

    log_level: str = "INFO"

    # Значения, которые нельзя писать в логи и сообщения об ошибках.
    secret_values: tuple[str, ...] = field(default=(), compare=False)

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            db_path=Path(_env("MON_DB_PATH", "/data/monitor.db")),
            tz=_env("MON_TZ", "Europe/Moscow") or "Europe/Moscow",
            title=_env("MON_TITLE", "RMM Monitor") or "RMM Monitor",
            mc_url=_env("MON_MC_URL", "ws://meshcentral:443") or "ws://meshcentral:443",
            mc_user=_env("MON_MC_USER"),
            mc_pass=_env("MON_MC_PASS"),
            mc_token=_env("MON_MC_TOKEN"),
            mc_timeout=_env_int("MON_MC_TIMEOUT", 60, minimum=5),
            meshctrl_path=Path(_env("MON_MESHCTRL", "/opt/meshctrl/meshctrl.js")),
            node_bin=_env("MON_NODE_BIN", "node") or "node",
            home_dir=Path(_env("MON_HOME", "/tmp/monitor-home")),
            scheduler_enabled=_env_bool("MON_SCHEDULER_ENABLED", True),
            inventory_interval=_env_int("MON_INVENTORY_INTERVAL", 60, minimum=15),
            snapshot_interval=_env_int("MON_SNAPSHOT_INTERVAL", 300, minimum=30),
            snapshot_concurrency=_env_int("MON_SNAPSHOT_CONCURRENCY", 4, minimum=1),
            snapshot_timeout=_env_int("MON_SNAPSHOT_TIMEOUT", 90, minimum=10),
            snapshot_top_processes=_env_int("MON_SNAPSHOT_TOP_PROCESSES", 25, minimum=1),
            snapshot_max_hosts=_env_int("MON_SNAPSHOT_MAX_HOSTS", 0, minimum=0),
            retention_days=_env_int("MON_RETENTION_DAYS", 30, minimum=1),
            runs_retention_days=_env_int("MON_RETENTION_RUNS_DAYS", 30, minimum=1),
            auth_user=_env("MON_AUTH_USER"),
            auth_pass=_env("MON_AUTH_PASS"),
            allow_anonymous=_env_bool("MON_ALLOW_ANONYMOUS", False),
            log_level=_env("MON_LOG_LEVEL", "INFO").upper() or "INFO",
        )
        settings._validate()
        return settings

    def _validate(self) -> None:
        try:
            ZoneInfo(self.tz)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"MON_TZ: неизвестная часовая зона {self.tz!r}") from exc
        if bool(self.auth_user) != bool(self.auth_pass):
            raise ValueError(
                "MON_AUTH_USER и MON_AUTH_PASS нужно задать вместе (или оба оставить пустыми "
                "вместе с MON_ALLOW_ANONYMOUS=true)"
            )
        if not self.auth_user and not self.allow_anonymous:
            raise ValueError(
                "Не задан доступ к интерфейсу: укажи MON_AUTH_USER/MON_AUTH_PASS "
                "или явно разреши MON_ALLOW_ANONYMOUS=true (только для локальной отладки)"
            )
        if not (self.mc_user and (self.mc_pass or "key=" in self.mc_url)):
            raise ValueError(
                "Не заданы учётные данные MeshCentral: нужен MON_MC_USER и MON_MC_PASS "
                "(либо логин-ключ в MON_MC_URL вида ws://meshcentral:443?key=...)"
            )

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_user and self.auth_pass)

    @property
    def mc_configured(self) -> bool:
        return bool(self.mc_user and (self.mc_pass or "key=" in self.mc_url))

    def safe_summary(self) -> dict[str, object]:
        """Данные для лога: без паролей, токенов и ключей."""
        return {
            "db_path": str(self.db_path),
            "tz": self.tz,
            "mc_url": redact(self.mc_url),
            "mc_user": self.mc_user,
            "inventory_interval": self.inventory_interval,
            "snapshot_interval": self.snapshot_interval,
            "snapshot_concurrency": self.snapshot_concurrency,
            "retention_days": self.retention_days,
            "auth": "basic" if self.auth_enabled else "anonymous",
            "scheduler": self.scheduler_enabled,
        }


def redact(text: str, extra: tuple[str, ...] = ()) -> str:
    """Убирает секреты из строк, которые собираются в лог/БД."""
    result = text
    for secret in extra:
        if secret and len(secret) >= 3:
            result = result.replace(secret, "***")
    # Логин-ключ в URL MeshCentral
    result = _redact_key_param(result)
    return result


def _redact_key_param(text: str) -> str:
    import re

    return re.sub(r"(key=)[^&\s'\"]+", r"\1***", text)
