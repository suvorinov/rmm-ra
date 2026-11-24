from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.collector import (
    CollectorContext,
    SnapshotError,
    agent_fetch_command,
    normalise_snapshot,
    parse_snapshot_output,
    remote_script_path,
    run_cleanup,
    run_inventory,
    run_snapshots,
    snapshot_host,
    snapshot_script_sha,
    upsert_hosts,
)
from app.mc import MeshCentralError
from app.models import BrowserSession, Host, JobRun, ProcessSnapshot, Snapshot
from app.timeutil import parse_iso, utcnow

from .conftest import DEVICE, snapshot_output, snapshot_payload

HOST_ID = DEVICE["_id"]


def test_parse_snapshot_with_noise():
    output = "PowerShell header\r\n" + snapshot_output() + "\r\ntrailer\r\n"
    data = parse_snapshot_output(output)
    assert data["hostname"] == "WS-001"
    assert data["format"] == 1


def test_parse_snapshot_error_marker():
    output = '###SNAPSHOT-BEGIN###{"format":1,"error":"Access denied"}###SNAPSHOT-END###'
    with pytest.raises(SnapshotError, match="Access denied"):
        parse_snapshot_output(output)


def test_parse_snapshot_without_marker():
    with pytest.raises(SnapshotError, match="маркер"):
        parse_snapshot_output("Invalid nodeid")


def test_normalise_clamps_and_aggregates():
    data = snapshot_payload(
        cpu={"name": "x", "cores": 2, "logical": 4, "percent": 140, "queue": -1},
        memory={"total_mb": 100, "used_mb": 150, "percent": 150},
        disks=[
            {
                "mount": "C:",
                "label": "",
                "total_gb": 10,
                "free_gb": -5,
                "used_pct": 120,
            },
            {"mount": "D:", "total_gb": 20, "free_gb": 5, "used_pct": 75},
        ],
        users=["ZR\\ivanov"],
        console_user="ZR\\petrov",
    )
    norm = normalise_snapshot(data, utcnow())
    assert norm["cpu_percent"] == 100.0
    assert norm["mem_percent"] == 100.0
    assert norm["disk_max_percent"] == 100.0
    assert norm["disk_total_gb"] == 30.0
    assert norm["disk_free_gb"] == 5.0
    assert norm["users"] == ["ZR\\ivanov", "ZR\\petrov"]
    assert norm["browser_processes"] == 17
    assert norm["reported_at"] == parse_iso("2026-11-24T09:00:00Z")


def test_upsert_hosts_online_offline_transition(db, client):
    now = utcnow()
    assert upsert_hosts(db, [dict(DEVICE)], now) == 1
    with db.session() as session:
        host = session.get(Host, HOST_ID)
        assert host.name == "Бухгалтерия-01"
        assert host.hostname == "ws-001"
        assert host.is_online is True
        assert host.last_seen_online == now
        assert host.agent_version == "1.1.34"

    offline = dict(DEVICE)
    offline["conn"] = 0
    later = now + timedelta(minutes=1)
    upsert_hosts(db, [offline], later)
    with db.session() as session:
        host = session.get(Host, HOST_ID)
        assert host.is_online is False
        assert host.last_offline_at == later


def test_upsert_hosts_deduplicates_reinstalled_agents(db):
    now = utcnow()
    stale = dict(DEVICE)
    stale["_id"] = HOST_ID[:-4] + "aaaa"
    stale["conn"] = 0
    fresh = dict(DEVICE)
    fresh["conn"] = 1

    assert upsert_hosts(db, [stale, fresh], now) == 1
    with db.session() as session:
        hosts = list(session.scalars(select(Host)).all())
        assert len(hosts) == 1
        assert hosts[0].id == HOST_ID
        assert hosts[0].is_online is True

    # Оба офлайн — остаёмся на уже выбранном node id (стабильность ссылок).
    fresh["conn"] = 0
    upsert_hosts(db, [stale, fresh], now + timedelta(minutes=1))
    with db.session() as session:
        hosts = list(session.scalars(select(Host)).all())
        assert len(hosts) == 1
        assert hosts[0].id == HOST_ID
        assert hosts[0].is_online is False


def test_upsert_hosts_marks_missing_devices_offline(db):
    now = utcnow()
    upsert_hosts(db, [dict(DEVICE)], now)
    other = dict(DEVICE)
    other["_id"] = HOST_ID[:-4] + "beef"
    other["name"] = "ws-002"
    upsert_hosts(db, [other], now + timedelta(minutes=1))
    with db.session() as session:
        host = session.get(Host, HOST_ID)
        assert host.is_online is False


def test_run_inventory_records_success(ctx):
    run_inventory(ctx)
    with ctx.db.session() as session:
        run = session.query(JobRun).order_by(JobRun.id.desc()).first()
        assert run.kind == "inventory"
        assert run.status == "ok"
        assert run.ok_count == 1
        assert session.get(Host, HOST_ID) is not None


def test_run_inventory_records_failure(ctx, client):
    def boom():
        raise MeshCentralError("Invalid login.")

    client.list_devices = boom  # type: ignore[assignment]
    run_inventory(ctx)
    with ctx.db.session() as session:
        run = session.query(JobRun).order_by(JobRun.id.desc()).first()
        assert run.status == "error"
        assert "Invalid login" in (run.message or "")


def test_snapshot_host_uploads_once_and_prunes_children(ctx, client):
    upsert_hosts(ctx.db, [dict(DEVICE)], utcnow())
    first = snapshot_host(ctx, HOST_ID, force=True)
    assert first.status == "ok", first.error
    assert len(client.uploads) == 1

    with ctx.db.session() as session:
        host = session.get(Host, HOST_ID)
        assert host.last_snapshot_status == "ok"
        assert host.cpu_percent == 23.4
        assert host.mem_percent == 50.0
        assert host.browser_kinds == 2
        assert host.browser_processes == 17
        assert host.disk_max_percent == 74.7
        assert session.query(Snapshot).count() == 1
        assert session.query(ProcessSnapshot).count() == 2
        assert session.query(BrowserSession).count() == 2

    second = snapshot_host(ctx, HOST_ID, force=True)
    assert second.status == "ok", second.error
    assert len(client.uploads) == 1, "повторно скрипт заливаться не должен"
    with ctx.db.session() as session:
        assert session.query(Snapshot).count() == 2
        assert session.query(ProcessSnapshot).count() == 2
        assert session.query(BrowserSession).count() == 2


def test_snapshot_uses_uploaded_script(ctx, client):
    upsert_hosts(ctx.db, [dict(DEVICE)], utcnow())
    snapshot_host(ctx, HOST_ID, force=True)
    assert client.runs
    _, command = client.runs[-1]
    assert "-File \"C:\\Windows\\Temp\\rmm-monitor-snapshot-" in command
    assert "-Top 25" in command
    with ctx.db.session() as session:
        host = session.get(Host, HOST_ID)
        assert host.snapshot_script_sha == snapshot_script_sha()
    assert client.uploads[0][1] == f"rmm-monitor-snapshot-{snapshot_script_sha()[:12]}.ps1"
    assert remote_script_path(snapshot_script_sha()).startswith("C:\\Windows\\Temp\\")


def test_agent_fetch_command_is_short_and_safe():
    command = agent_fetch_command("http://192.168.0.9:8000/", 25, snapshot_script_sha())
    assert len(command) < 600
    assert "http://192.168.0.9:8000/agent/snapshot.ps1" in command
    assert snapshot_script_sha() in command
    assert command.endswith('25"')


def test_snapshot_uses_http_when_configured(ctx, client, settings):
    http_settings = replace(settings, agent_script_url="http://mon.local:8000")
    http_ctx = CollectorContext(settings=http_settings, db=ctx.db, client=client)
    upsert_hosts(ctx.db, [dict(DEVICE)], utcnow())
    result = snapshot_host(http_ctx, HOST_ID, force=True)
    assert result.status == "ok", result.error
    assert client.uploads == [], "загрузка через meshctrl не нужна"
    assert "http://mon.local:8000/agent/snapshot.ps1" in client.runs[-1][1]


def test_upload_local_file_is_unique_and_removed(ctx, client, monkeypatch):
    upsert_hosts(ctx.db, [dict(DEVICE)], utcnow())
    seen: list[Path] = []
    original = client.upload_file

    def spy(node_id, local_path, remote_dir, timeout=None, remote_name=None):
        seen.append(Path(local_path))
        return original(node_id, local_path, remote_dir, timeout=timeout, remote_name=remote_name)

    monkeypatch.setattr(client, "upload_file", spy)
    assert snapshot_host(ctx, HOST_ID, force=True).status == "ok"
    assert len(seen) == 1
    assert seen[0].name != f"rmm-monitor-snapshot-{snapshot_script_sha()[:12]}.ps1"
    assert not seen[0].exists(), "временный файл должен удаляться после загрузки"
    assert client.uploads[0][1] == f"rmm-monitor-snapshot-{snapshot_script_sha()[:12]}.ps1"


def test_snapshot_error_is_recorded(ctx, client):
    upsert_hosts(ctx.db, [dict(DEVICE)], utcnow())
    client.outputs[HOST_ID] = (
        '###SNAPSHOT-BEGIN###{"format":1,"error":"Access is denied"}###SNAPSHOT-END###'
    )
    result = snapshot_host(ctx, HOST_ID, force=True)
    assert result.status == "error"
    with ctx.db.session() as session:
        host = session.get(Host, HOST_ID)
        assert host.last_snapshot_status == "error"
        assert "Access is denied" in (host.last_snapshot_error or "")
        assert session.query(Snapshot).count() == 0


def test_snapshot_retries_after_temp_cleanup(ctx, client):
    upsert_hosts(ctx.db, [dict(DEVICE)], utcnow())
    snapshot_host(ctx, HOST_ID, force=True)
    calls = {"n": 0}
    original = client.run_command

    def flaky(node_id, command, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise MeshCentralError("Cannot find path C:\\Windows\\Temp\\script.ps1")
        return original(node_id, command, timeout=timeout)

    client.run_command = flaky  # type: ignore[assignment]
    result = snapshot_host(ctx, HOST_ID, force=True)
    assert result.status == "ok", result.error
    assert len(client.uploads) == 2


def test_run_snapshots_partial(ctx, client):
    upsert_hosts(ctx.db, [dict(DEVICE)], utcnow())
    second = dict(DEVICE)
    second["_id"] = HOST_ID[:-4] + "cafe"
    second["name"] = "ws-002"
    upsert_hosts(ctx.db, [dict(DEVICE), second], utcnow())
    client.outputs[second["_id"]] = "###SNAPSHOT-BEGIN###{\"format\":1,\"error\":\"timeout\"}###SNAPSHOT-END###"

    run_snapshots(ctx)
    with ctx.db.session() as session:
        run = session.query(JobRun).filter(JobRun.kind == "snapshot").order_by(JobRun.id.desc()).first()
        assert run.status == "partial"
        assert run.ok_count == 1
        assert run.fail_count == 1
        assert "timeout" in (run.message or "")


def test_cleanup_removes_old_rows(ctx):
    upsert_hosts(ctx.db, [dict(DEVICE)], utcnow())
    old = utcnow() - timedelta(days=ctx.settings.retention_days + 1)
    with ctx.db.session() as session:
        session.add(
            Snapshot(
                host_id=HOST_ID,
                taken_at=old,
                users_json="[]",
                disks_json="[]",
                network_json="{}",
            )
        )
        session.add(
            JobRun(
                kind="snapshot",
                status="ok",
                started_at=old,
                finished_at=old,
                ok_count=1,
                fail_count=0,
            )
        )
        session.commit()
    run_cleanup(ctx)
    with ctx.db.session() as session:
        assert session.query(Snapshot).count() == 0
        assert session.query(JobRun).filter(JobRun.kind == "snapshot").count() == 0
        cleanup = session.query(JobRun).filter(JobRun.kind == "cleanup").first()
        assert cleanup is not None and cleanup.status == "ok"
