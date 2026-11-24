"""Локальный запуск RMM Monitor без Docker.

Загружает monitor/monitor.env (или dev.env / пример), подставляет локальные пути
для SQLite и meshctrl и стартует uvicorn на 0.0.0.0, чтобы быть доступным
из локальной сети.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
MONITOR_DIR = ROOT / "monitor"
DEV_DATA = ROOT / "monitor-data-dev"
NODE_HOSTS_PRELOAD = MONITOR_DIR / "scripts" / "dev-node-hosts.js"

# Эти пути в docker-варианте абсолютные (/data, /opt/meshctrl), локально они
# не подходят. Значения, заданные снаружи (Makefile/CLI), имеют приоритет.
LOCAL_PATH_VARS = ("MON_DB_PATH", "MON_HOME", "MON_MESHCTRL")

# В контейнере MeshCentral доступен по имени сервиса, с хоста — нет.
DOCKER_MC_HOSTS = {"meshcentral"}
DEFAULT_DEV_MC_HOST = "rmm.zr.local"
DEFAULT_DEV_MC_IP = "127.0.0.1"


def parse_env_file(path: Path) -> dict[str, str]:
    """Мини-парсер KEY=VALUE без раскрытия $ и без внешних зависимостей."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def pick_env_file(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise SystemExit(f"Нет файла окружения: {path}")
        return path
    for name in ("monitor.env", "dev.env", "monitor.env.example"):
        candidate = MONITOR_DIR / name
        if candidate.is_file():
            return candidate
    return None


def prepare_local_paths() -> dict[str, str]:
    (DEV_DATA / "home").mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in (DEV_DATA, DEV_DATA / "home"):
        try:
            os.chmod(path, 0o700)
        except OSError:  # pragma: no cover - зависит от ФС
            pass
    return {
        "MON_DB_PATH": str(DEV_DATA / "monitor.db"),
        "MON_HOME": str(DEV_DATA / "home"),
        "MON_MESHCTRL": str(MONITOR_DIR / "vendor" / "meshctrl.js"),
    }


def local_meshcentral_url(
    raw: str, dev_host: str = DEFAULT_DEV_MC_HOST
) -> tuple[str, str | None]:
    """Заменяет docker-внутренний адрес MeshCentral на доступный с хоста.

    Возвращает ``(url, dns_name)``; ``dns_name`` не ``None``, если для Node
    понадобится подмена DNS (см. dev-node-hosts.js). Путь не добавляем:
    meshctrl сам допишет ``/control.ashx`` к базовому URL.
    """
    parts = urlsplit(raw)
    if (parts.hostname or "").lower() not in DOCKER_MC_HOSTS:
        return raw, None
    return urlunsplit(("wss", dev_host, "", parts.query, "")), dev_host


def safe_url(url: str) -> str:
    """Чтобы не светить логин-ключ в консоли."""
    return re.sub(r"(key=)[^&\s]+", r"\1***", url)


def enable_node_hosts(dns_name: str) -> None:
    """Включаем preload, подменяющий DNS только для дочерних node-процессов."""
    ip = os.environ.get("MON_DEV_MESH_IP", DEFAULT_DEV_MC_IP) or DEFAULT_DEV_MC_IP
    os.environ["MON_DEV_DNS_OVERRIDES"] = json.dumps({dns_name: ip})
    preload = f"--require {NODE_HOSTS_PRELOAD}"
    current = os.environ.get("NODE_OPTIONS", "")
    if preload not in current:
        os.environ["NODE_OPTIONS"] = f"{current} {preload}".strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="RMM Monitor: локальный запуск без Docker")
    parser.add_argument("--host", default="0.0.0.0", help="адрес слушателя (по умолчанию 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="порт (по умолчанию 8000)")
    parser.add_argument(
        "--lan-ip",
        default="192.168.0.9",
        help="IP для подсказки в консоли (по умолчанию 192.168.0.9)",
    )
    parser.add_argument("--env-file", help="путь к файлу окружения")
    parser.add_argument("--reload", action="store_true", help="автоперезагрузка при правках кода")
    parser.add_argument(
        "--no-scheduler",
        action="store_true",
        help="не опрашивать MeshCentral (чистый UI-режим для разработки)",
    )
    args = parser.parse_args()

    # Сначала запоминаем, что задал пользователь явно, — это важнее файла.
    explicit = {key: os.environ[key] for key in LOCAL_PATH_VARS if key in os.environ}

    env_file = pick_env_file(args.env_file)
    if env_file is None:
        raise SystemExit("Нет monitor/monitor.env, monitor/dev.env или monitor.env.example")
    for key, value in parse_env_file(env_file).items():
        os.environ.setdefault(key, value)
    if env_file.name == "monitor.env.example":
        print("ВНИМАНИЕ: используются заглушки из monitor.env.example — замени их в monitor.env")

    for key, default in prepare_local_paths().items():
        os.environ[key] = explicit.get(key, default)

    if args.no_scheduler:
        os.environ["MON_SCHEDULER_ENABLED"] = "false"

    dev_mesh_host = (
        os.environ.get("MON_DEV_MESH_HOST", DEFAULT_DEV_MC_HOST) or DEFAULT_DEV_MC_HOST
    )
    mc_url, dns_name = local_meshcentral_url(
        os.environ.get("MON_MC_URL", ""), dev_host=dev_mesh_host
    )
    if dns_name:
        os.environ["MON_MC_URL"] = mc_url
        enable_node_hosts(dns_name)

    sys.path.insert(0, str(MONITOR_DIR))
    import uvicorn  # pylint: disable=import-outside-toplevel

    print(
        f"\nRMM Monitor (dev): http://{args.host}:{args.port}\n"
        f"Из локальной сети:     http://{args.lan_ip}:{args.port}\n"
        f"Окружение:             {env_file}\n"
        f"База:                  {os.environ['MON_DB_PATH']}\n"
        f"MeshCentral:           {safe_url(os.environ.get('MON_MC_URL', ''))}\n"
        f"Планировщик:           {os.environ.get('MON_SCHEDULER_ENABLED', 'true')}\n",
        flush=True,
    )
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=os.environ.get("MON_LOG_LEVEL", "INFO").lower(),
    )


if __name__ == "__main__":
    main()
