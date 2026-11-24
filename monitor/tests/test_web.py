from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.collector import upsert_hosts
from app.main import create_app
from app.models import Host, Snapshot
from app.timeutil import utcnow
from app.web.urls import host_url

from .conftest import DEVICE, make_auth_settings

HOST_ID = DEVICE["_id"]


def seed(db, with_snapshot: bool = True):
    upsert_hosts(db, [dict(DEVICE)], utcnow())
    if not with_snapshot:
        return
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


def test_dashboard_host_page_and_api(settings, db):
    seed(db)
    app = create_app(settings)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Бухгалтерия-01" in page.text
        assert "в сети" in page.text

        hosts = client.get("/api/hosts").json()["hosts"]
        assert len(hosts) == 1
        assert hosts[0]["name"] == "Бухгалтерия-01"
        assert hosts[0]["online"] is True

        detail = client.get(f"/api/hosts/{HOST_ID}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["snapshot"]["disks"][0]["mount"] == "C:"
        assert payload["snapshot"]["network"]["addresses"] == ["192.168.0.50"]

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
