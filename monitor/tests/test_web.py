from __future__ import annotations

import json
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.collector import upsert_hosts
from app.main import create_app
from app.models import (
    BrowserSession,
    Host,
    ProcessSnapshot,
    RustdeskEvent,
    RustdeskHost,
    Snapshot,
)
from app.timeutil import utcnow
from app.web.urls import host_url, rustdesk_connect_url

from .conftest import DEVICE, make_auth_settings

HOST_ID = DEVICE["_id"]


def seed(db, with_snapshot: bool = True):
    upsert_hosts(db, [dict(DEVICE)], utcnow())
    if not with_snapshot:
        return
    with db.session() as session:
        host = session.get(Host, HOST_ID)
        host.last_snapshot_attempt_at = utcnow()
        host.cpu_percent = 23.4
        host.mem_percent = 50.0
        host.disk_max_percent = 74.7
        host.browser_kinds = 2
        host.browser_processes = 17
        host.process_count = 143
        host.uptime_s = 93784
        session.commit()
    with db.session() as session:
        snapshot = Snapshot(
            host_id=HOST_ID,
            taken_at=utcnow(),
            cpu_percent=23.4,
            mem_percent=50.0,
            mem_total_mb=16384,
            mem_used_mb=8192,
            uptime_s=93784,
            process_count=143,
            browser_processes=17,
            console_user="ZR\\ivanov",
            users_json=json.dumps(["ZR\\ivanov"]),
            disks_json=json.dumps(
                [
                    {
                        "mount": "C:",
                        "label": "System",
                        "total_gb": 476.9,
                        "free_gb": 120.5,
                        "used_pct": 74.7,
                    }
                ]
            ),
            network_json=json.dumps({"addresses": ["192.168.0.50"]}),
        )
        session.add(snapshot)
        session.commit()
        session.add_all(
            [
                BrowserSession(
                    snapshot_id=snapshot.id,
                    host_id=HOST_ID,
                    name="Firefox",
                    processes=17,
                    memory_mb=768.5,
                    users_json=json.dumps(["ZR\\ivanov"]),
                ),
                ProcessSnapshot(
                    snapshot_id=snapshot.id,
                    host_id=HOST_ID,
                    name="1Cv8",
                    count=2,
                    ws_mb=512.0,
                    cpu_s=42.5,
                ),
            ]
        )
        session.commit()


def seed_rustdesk(db, *, ip: str = "192.168.0.50", rid: str = "123456789"):
    with db.session() as session:
        session.add(
            RustdeskHost(
                ip=ip,
                rustdesk_id=rid,
                version="1.5.3",
                has_password=True,
                source_mtime=utcnow(),
                imported_at=utcnow(),
            )
        )


def test_dashboard_host_page_and_api(settings, db):
    seed(db)
    app = create_app(settings)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Бухгалтерия-01" in page.text
        assert "в сети" in page.text
        assert f'data-host-id="{HOST_ID}"' in page.text

        # кликабельные заголовки и числовые значения для сортировки
        for column in ("cpu", "mem", "disk", "browsers", "procs", "uptime"):
            assert f'data-sort="{column}"' in page.text
            assert f'data-sort-{column}=' in page.text
        for value in ('data-sort-cpu="23.4"', 'data-sort-mem="50.0"',
                      'data-sort-disk="74.7"', 'data-sort-browsers="17"',
                      'data-sort-procs="143"', 'data-sort-uptime="93784"'):
            assert value in page.text

        hosts = client.get("/api/hosts").json()["hosts"]
        assert len(hosts) == 1
        assert hosts[0]["name"] == "Бухгалтерия-01"
        assert hosts[0]["online"] is True

        detail = client.get(f"/api/hosts/{HOST_ID}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["snapshot"]["disks"][0]["mount"] == "C:"
        assert payload["snapshot"]["network"]["addresses"] == ["192.168.0.50"]
        assert payload["snapshot"]["browsers"][0]["name"] == "Firefox"
        assert payload["snapshot"]["processes"][0]["name"] == "1Cv8"
        assert payload["last_snapshot_attempt_at"] is not None

        # browser fetch() кодирует ID через encodeURIComponent — проверяем и этот путь
        encoded = client.get("/api/hosts/" + quote(HOST_ID, safe=""))
        assert encoded.status_code == 200
        assert encoded.json()["id"] == HOST_ID

        host_page = client.get(host_url(HOST_ID))
        assert host_page.status_code == 200
        assert "Нагрузка" in host_page.text
        assert "Chrome" not in host_page.text  # деталей из снимка ещё нет

        series = client.get(f"/api/snapshots/{HOST_ID}").json()["snapshots"]
        assert len(series) == 1
        assert series[0]["cpu_percent"] == 23.4

        health = client.get("/healthz")
        assert health.status_code == 200
        body = health.json()
        assert body["db"] is True
        assert body["scheduler"] is False
        assert "default-src" in health.headers["content-security-policy"]
        assert health.headers["x-content-type-options"] == "nosniff"


def test_unknown_host_renders_html_404(settings, db):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/host/node//does-not-exist")
        assert response.status_code == 404
        assert "не найден" in response.text

        api = client.get("/api/hosts/node//does-not-exist")
        assert api.status_code == 404
        assert api.json()["detail"] == "хост не найден"


def test_basic_auth(settings, db):
    seed(db, with_snapshot=False)
    app = create_app(make_auth_settings(settings))
    with TestClient(app) as client:
        assert client.get("/").status_code == 401
        assert client.get("/healthz").status_code == 200
        assert client.get("/", auth=("admin", "wrong")).status_code == 401
        ok = client.get("/", auth=("admin", "hunter2"))
        assert ok.status_code == 200
        assert ok.headers["x-frame-options"] == "DENY"


def test_cross_origin_post_is_rejected(settings, db):
    app = create_app(make_auth_settings(settings))
    with TestClient(app) as client:
        response = client.post(
            "/refresh/inventory",
            auth=("admin", "hunter2"),
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403


def test_agent_script_is_public(settings, db):
    app = create_app(make_auth_settings(settings))
    with TestClient(app) as client:
        response = client.get("/agent/snapshot.ps1")
        assert response.status_code == 200
        assert "###SNAPSHOT-BEGIN###" in response.text
        assert response.headers["cache-control"] == "no-cache"


def test_health_alias_is_public(settings, db):
    app = create_app(make_auth_settings(settings))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").json() == client.get("/healthz").json()


def test_explicit_allowed_origin_is_accepted(settings, db):
    import dataclasses

    allowed = dataclasses.replace(
        make_auth_settings(settings), allowed_origins=("http://dev.local:8000",)
    )
    app = create_app(allowed)
    with TestClient(app) as client:
        response = client.post(
            "/refresh/inventory",
            auth=("admin", "hunter2"),
            headers={"Origin": "http://dev.local:8000"},
            follow_redirects=False,
        )
        assert response.status_code == 303


def test_dashboard_empty_state(settings, db):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Пока пусто" in response.text
        assert client.get("/api/summary").json()["total"] == 0


def test_rustdesk_button_api_and_audit(settings, db):
    seed(db, with_snapshot=False)
    seed_rustdesk(db)
    app = create_app(make_auth_settings(settings))
    with TestClient(app) as client:
        auth = ("admin", "hunter2")
        page = client.get("/", auth=auth)
        assert page.status_code == 200
        assert rustdesk_connect_url(HOST_ID) in page.text
        assert "123456789" in page.text

        detail = client.get(f"/api/hosts/{HOST_ID}", auth=auth)
        assert detail.status_code == 200
        rd = detail.json()["rustdesk"]
        assert rd["id"] == "123456789"
        assert rd["url"] == "rustdesk://connection/new/123456789"
        assert rd["connect_url"] == rustdesk_connect_url(HOST_ID)

        connect = client.get(
            f"/api/rustdesk/connect/{HOST_ID}", auth=auth, follow_redirects=False
        )
        assert connect.status_code == 302
        assert connect.headers["location"] == "rustdesk://connection/new/123456789"

        as_json = client.get(
            f"/api/rustdesk/connect/{HOST_ID}",
            params={"format": "json"},
            auth=auth,
        )
        assert as_json.status_code == 200
        assert as_json.json()["rustdesk_id"] == "123456789"

        runs = client.get("/runs", auth=auth)
        assert runs.status_code == 200
        assert "admin" in runs.text
        assert "123456789" in runs.text

    with db.session() as session:
        events = list(session.scalars(select(RustdeskEvent)))
        assert len(events) == 2
        assert {event.status for event in events} == {"ok"}
        assert {event.user for event in events} == {"admin"}
        assert all(event.ip == "192.168.0.50" for event in events)
        assert all(event.rustdesk_id == "123456789" for event in events)


def test_rustdesk_connect_without_book_entry_is_404(settings, db):
    seed(db, with_snapshot=False)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get(
            f"/api/rustdesk/connect/{HOST_ID}", follow_redirects=False
        )
        assert response.status_code == 404
        assert response.json()["detail"].startswith("для хоста нет RustDesk ID")
