from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.collector import CollectorContext  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402

DEVICE = {
    "_id": "node//testhost0000000000000000000000000000000000000000000000000000",
    "name": "ws-001",
    "rname": "Бухгалтерия-01",
    "groupname": "Офис",
    "meshid": "mesh//office",
    "ip": "192.168.0.50",
    "osdesc": "Windows 10.0.19045",
    "agent": {"ver": "1.1.34"},
    "conn": 1,
}


def snapshot_payload(**overrides) -> dict:
    data = {
        "format": 1,
        "collected_at": "2026-11-24T09:00:00Z",
        "hostname": "WS-001",
        "os": {
            "caption": "Microsoft Windows 10 Pro",
            "version": "10.0.19045",
            "build": "19045",
            "arch": "64-bit",
            "uptime_s": 93784,
        },
        "cpu": {"name": "Intel Core i5", "cores": 4, "logical": 8, "percent": 23.4, "queue": 1},
        "memory": {"total_mb": 16384, "used_mb": 8192, "free_mb": 8192, "percent": 50.0},
        "disks": [
            {"mount": "C:", "label": "System", "total_gb": 476.9, "free_gb": 120.5, "used_pct": 74.7}
        ],
        "network": {"addresses": ["192.168.0.50"]},
        "users": ["ZR\\ivanov"],
        "console_user": "ZR\\ivanov",
        "browsers": [
            {"name": "Chrome", "processes": 12, "memory_mb": 942.5, "users": ["ZR\\ivanov"]},
            {"name": "Yandex Browser", "processes": 5, "memory_mb": 301.0, "users": ["ZR\\ivanov"]},
        ],
        "processes": {
            "total": 143,
            "top": [
                {"name": "chrome", "count": 12, "ws_mb": 942.5, "cpu_s": 44.2},
                {"name": "explorer", "count": 1, "ws_mb": 120.0, "cpu_s": 2.0},
            ],
        },
        "browser_processes": 17,
    }
    data.update(overrides)
    return data


def snapshot_output(**overrides) -> str:
    return (
        "###SNAPSHOT-BEGIN###"
        + json.dumps(snapshot_payload(**overrides), ensure_ascii=False)
        + "###SNAPSHOT-END###"
    )


class FakeClient:
    def __init__(self) -> None:
        self.devices = [dict(DEVICE)]
        self.outputs: dict[str, str | Exception] = {}
        self.uploads: list[tuple[str, str]] = []
        self.runs: list[tuple[str, str]] = []

    def list_devices(self):
        return self.devices

    def run_command(self, node_id, command, timeout=None):
        self.runs.append((node_id, command))
        value = self.outputs.get(node_id, snapshot_output())
        if isinstance(value, Exception):
            raise value
        return value

    def run_powershell(self, node_id, script, timeout=None):
        return self.run_command(node_id, script, timeout=timeout)

    def upload_file(self, node_id, local_path, remote_dir, timeout=None):
        name = Path(local_path).name
        self.uploads.append((node_id, name))
        return remote_dir.rstrip("\\/") + "\\" + name

    def health(self):
        return {"configured": True, "meshctrl": True, "node": True, "url": "ws://test"}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "monitor.db",
        tz="Europe/Moscow",
        title="Test Monitor",
        mc_url="ws://meshcentral:443",
        mc_user="svc-monitor",
        mc_pass="s3cret-pass",
        meshctrl_path=tmp_path / "meshctrl.js",
        home_dir=tmp_path / "home",
        scheduler_enabled=False,
        inventory_interval=60,
        snapshot_interval=300,
        snapshot_concurrency=2,
        snapshot_timeout=30,
        auth_user="",
        auth_pass="",
        allow_anonymous=True,
    )


@pytest.fixture
def db(settings: Settings):
    database = Database(settings.db_path)
    database.init()
    yield database
    database.dispose()


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def ctx(settings: Settings, db: Database, client: FakeClient) -> CollectorContext:
    return CollectorContext(settings=settings, db=db, client=client)


def make_auth_settings(settings: Settings) -> Settings:
    return replace(settings, auth_user="admin", auth_pass="hunter2", allow_anonymous=False)
