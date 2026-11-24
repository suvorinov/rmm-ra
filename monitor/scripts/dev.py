"""Локальный запуск RMM Monitor без Docker.

Загружает monitor/monitor.env (или dev.env / пример), подставляет локальные пути
для SQLite и meshctrl и стартует uvicorn на 0.0.0.0, чтобы быть доступным
из локальной сети.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MONITOR_DIR = ROOT / "monitor"
DEV_DATA = ROOT / "monitor-data-dev"

# Эти пути в docker-варианте абсолютные (/data, /opt/meshctrl), локально они
# не подходят. Значения, заданные снаружи (Makefile/CLI), имеют приоритет.
LOCAL_PATH_VARS = ("MON_DB_PATH", "MON_HOME", "MON_MESHCTRL")


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

    sys.path.insert(0, str(MONITOR_DIR))
    import uvicorn  # pylint: disable=import-outside-toplevel

    print(
        f"\nRMM Monitor (dev): http://{args.host}:{args.port}\n"
        f"Из локальной сети:     http://{args.lan_ip}:{args.port}\n"
        f"Окружение:             {env_file}\n"
        f"База:                  {os.environ['MON_DB_PATH']}\n"
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
