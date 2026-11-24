"""HTTP-маршруты: дашборд, карточка хоста, JSON API, healthz."""

from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import desc, func, select, text

from ..collector import (
    latest_snapshot,
    recent_snapshots,
    run_inventory,
    snapshot_host,
    snapshot_script,
)
from ..models import BrowserSession, Host, JobRun, ProcessSnapshot, Snapshot
from ..timeutil import iso_utc, stale_cutoff, utcnow
from .charts import sparkline
from .urls import host_url

router = APIRouter()


def _ctx(request: Request):
    return request.app.state.ctx


def _templates(request: Request):
    return request.app.state.templates


def _page(request: Request, **extra):
    ctx = _ctx(request)
    base = {
        "request": request,
        "settings": ctx.settings,
        "now": utcnow(),
        "page": "",
    }
    base.update(extra)
    return base


def _summarise(hosts: list[Host], now, interval: int) -> dict:
    cutoff = stale_cutoff(now, interval)
    online = [h for h in hosts if h.is_online]
    stale = [
        h for h in online if h.last_snapshot_at is None or h.last_snapshot_at < cutoff
    ]
    errors = [h for h in hosts if h.last_snapshot_status == "error"]
    cpu = [h.cpu_percent for h in online if h.cpu_percent is not None]
    mem = [h.mem_percent for h in online if h.mem_percent is not None]
    return {
        "total": len(hosts),
        "online": len(online),
        "offline": len(hosts) - len(online),
        "stale": len(stale),
        "errors": len(errors),
        "browsers": sum((h.browser_processes or 0) for h in online),
        "cpu_avg": round(sum(cpu) / len(cpu), 1) if cpu else None,
        "mem_avg": round(sum(mem) / len(mem), 1) if mem else None,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    now = utcnow()
    cutoff = stale_cutoff(now, ctx.settings.snapshot_interval)
    with ctx.db.session() as session:
        hosts = list(
            session.scalars(
                select(Host).order_by(desc(Host.is_online), Host.name.asc())
            ).all()
        )
        last_inventory = session.scalar(select(func.max(Host.last_inventory_at)))
        last_snapshot = session.scalar(select(func.max(Snapshot.taken_at)))
        recent_runs = list(
            session.scalars(
                select(JobRun).order_by(desc(JobRun.started_at)).limit(5)
            ).all()
        )
    summary = _summarise(hosts, now, ctx.settings.snapshot_interval)
    return _templates(request).TemplateResponse(
        request=request,
        name="dashboard.html",
        context=_page(
            request,
            page="dashboard",
            hosts=hosts,
            summary=summary,
            stale_cutoff=cutoff,
            stale_minutes=max(ctx.settings.snapshot_interval * 3, 900) // 60,
            last_inventory=last_inventory,
            last_snapshot=last_snapshot,
            recent_runs=recent_runs,
            queued=request.query_params.get("queued"),
            error=request.query_params.get("err"),
        ),
    )


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    with ctx.db.session() as session:
        runs = list(
            session.scalars(
                select(JobRun).order_by(desc(JobRun.started_at)).limit(200)
            ).all()
        )
    return _templates(request).TemplateResponse(
        request=request,
        name="runs.html",
        context=_page(request, page="runs", runs=runs),
    )


@router.get("/host/{host_id:path}", response_class=HTMLResponse)
def host_page(request: Request, host_id: str) -> HTMLResponse:
    ctx = _ctx(request)
    now = utcnow()
    with ctx.db.session() as session:
        host = session.get(Host, host_id)
        if host is None:
            raise HTTPException(status_code=404, detail="хост не найден")
        snapshots = recent_snapshots(session, host_id, 60)
        latest = latest_snapshot(session, host_id)
        processes: list[ProcessSnapshot] = []
        browsers: list[BrowserSession] = []
        if latest is not None:
            processes = list(
                session.scalars(
                    select(ProcessSnapshot)
                    .where(ProcessSnapshot.snapshot_id == latest.id)
                    .order_by(desc(ProcessSnapshot.ws_mb))
                ).all()
            )
            browsers = list(
                session.scalars(
                    select(BrowserSession)
                    .where(BrowserSession.snapshot_id == latest.id)
                    .order_by(desc(BrowserSession.processes))
                ).all()
            )

    cpu_chart = sparkline([s.cpu_percent for s in snapshots])
    mem_chart = sparkline([s.mem_percent for s in snapshots])
    return _templates(request).TemplateResponse(
        request=request,
        name="host.html",
        context=_page(
            request,
            page="host",
            host=host,
            snapshots=list(reversed(snapshots)),
            latest=latest,
            processes=processes,
            browsers=browsers,
            cpu_chart=cpu_chart,
            mem_chart=mem_chart,
            stale_cutoff=stale_cutoff(now, ctx.settings.snapshot_interval),
            host_url=host_url(host_id),
            queued=request.query_params.get("queued"),
            error=request.query_params.get("err"),
        ),
    )


@router.post("/refresh/inventory")
def refresh_inventory(request: Request) -> RedirectResponse:
    ctx = _ctx(request)
    if not ctx.allow_manual("__inventory__", min_seconds=10):
        return RedirectResponse(url="/?err=rate", status_code=303)
    threading.Thread(
        target=run_inventory, args=(ctx,), name="inventory-manual", daemon=True
    ).start()
    return RedirectResponse(url="/?queued=inventory", status_code=303)


@router.post("/refresh/{host_id:path}")
def refresh_host(request: Request, host_id: str) -> RedirectResponse:
    ctx = _ctx(request)
    with ctx.db.session() as session:
        if session.get(Host, host_id) is None:
            raise HTTPException(status_code=404, detail="хост не найден")
    base = host_url(host_id)
    if not ctx.allow_manual(host_id, min_seconds=20):
        return RedirectResponse(url=f"{base}?err=rate", status_code=303)
    threading.Thread(
        target=snapshot_host,
        args=(ctx, host_id),
        kwargs={"force": True, "blocking": False},
        name=f"snapshot-{host_id[-8:]}",
        daemon=True,
    ).start()
    return RedirectResponse(url=f"{base}?queued=1", status_code=303)


# --------------------------------------------------------------------- JSON API


def _host_dict(host: Host) -> dict:
    return {
        "id": host.id,
        "name": host.name,
        "hostname": host.hostname,
        "group": host.group_name,
        "ip": host.ip,
        "os": host.os_desc,
        "os_caption": host.os_caption,
        "agent_version": host.agent_version,
        "online": bool(host.is_online),
        "last_seen_online": iso_utc(host.last_seen_online),
        "last_inventory_at": iso_utc(host.last_inventory_at),
        "last_snapshot_at": iso_utc(host.last_snapshot_at),
        "last_snapshot_status": host.last_snapshot_status,
        "last_snapshot_error": host.last_snapshot_error,
        "metrics": {
            "cpu_percent": host.cpu_percent,
            "cpu_model": host.cpu_model,
            "cpu_cores": host.cpu_cores,
            "cpu_logical": host.cpu_logical,
            "mem_percent": host.mem_percent,
            "mem_total_mb": host.mem_total_mb,
            "mem_used_mb": host.mem_used_mb,
            "disk_total_gb": host.disk_total_gb,
            "disk_free_gb": host.disk_free_gb,
            "disk_max_percent": host.disk_max_percent,
            "uptime_s": host.uptime_s,
            "process_count": host.process_count,
            "browser_processes": host.browser_processes,
            "browser_kinds": host.browser_kinds,
            "console_user": host.console_user,
        },
    }


@router.get("/api/summary")
def api_summary(request: Request) -> JSONResponse:
    ctx = _ctx(request)
    now = utcnow()
    with ctx.db.session() as session:
        hosts = list(session.scalars(select(Host)).all())
    return JSONResponse(_summarise(hosts, now, ctx.settings.snapshot_interval))


@router.get("/api/hosts")
def api_hosts(request: Request) -> JSONResponse:
    ctx = _ctx(request)
    with ctx.db.session() as session:
        hosts = list(
            session.scalars(
                select(Host).order_by(desc(Host.is_online), Host.name.asc())
            ).all()
        )
    return JSONResponse({"hosts": [_host_dict(h) for h in hosts]})


@router.get("/api/hosts/{host_id:path}")
def api_host(request: Request, host_id: str) -> JSONResponse:
    ctx = _ctx(request)
    with ctx.db.session() as session:
        host = session.get(Host, host_id)
        if host is None:
            raise HTTPException(status_code=404, detail="хост не найден")
        latest = latest_snapshot(session, host_id)
        payload = _host_dict(host)
        snapshot = None
        if latest is not None:
            processes = list(
                session.scalars(
                    select(ProcessSnapshot)
                    .where(ProcessSnapshot.snapshot_id == latest.id)
                    .order_by(desc(ProcessSnapshot.ws_mb))
                ).all()
            )
            browsers = list(
                session.scalars(
                    select(BrowserSession)
                    .where(BrowserSession.snapshot_id == latest.id)
                    .order_by(desc(BrowserSession.processes))
                ).all()
            )
            snapshot = {
                "id": latest.id,
                "taken_at": iso_utc(latest.taken_at),
                "reported_at": iso_utc(latest.reported_at),
                "console_user": latest.console_user,
                "users": latest.users,
                "disks": latest.disks,
                "network": latest.network,
                "browsers": [
                    {
                        "name": b.name,
                        "processes": b.processes,
                        "memory_mb": b.memory_mb,
                        "users": b.users,
                    }
                    for b in browsers
                ],
                "processes": [
                    {
                        "name": p.name,
                        "count": p.count,
                        "ws_mb": p.ws_mb,
                        "cpu_s": p.cpu_s,
                    }
                    for p in processes
                ],
            }
        payload["snapshot"] = snapshot
    return JSONResponse(payload)


@router.get("/api/snapshots/{host_id:path}")
def api_snapshots(request: Request, host_id: str) -> JSONResponse:
    ctx = _ctx(request)
    try:
        limit = min(500, max(1, int(request.query_params.get("limit", "100"))))
    except ValueError:
        limit = 100
    with ctx.db.session() as session:
        if session.get(Host, host_id) is None:
            raise HTTPException(status_code=404, detail="хост не найден")
        rows = list(
            session.scalars(
                select(Snapshot)
                .where(Snapshot.host_id == host_id)
                .order_by(desc(Snapshot.taken_at))
                .limit(limit)
            ).all()
        )
    return JSONResponse(
        {
            "host_id": host_id,
            "snapshots": [
                {
                    "id": s.id,
                    "taken_at": iso_utc(s.taken_at),
                    "reported_at": iso_utc(s.reported_at),
                    "cpu_percent": s.cpu_percent,
                    "mem_percent": s.mem_percent,
                    "mem_used_mb": s.mem_used_mb,
                    "uptime_s": s.uptime_s,
                    "process_count": s.process_count,
                    "browser_processes": s.browser_processes,
                }
                for s in rows
            ],
        }
    )


@router.get("/agent/snapshot.ps1")
def agent_snapshot_script() -> PlainTextResponse:
    """Скрипт для агентов MeshCentral: секретов нет, Basic здесь не нужен.

    Агент подтягивает его по LAN и запускает через run_command. Так мы обходим
    meshctrl upload, который с хоста ненадёжно передаёт бинарные кадры.
    """
    return PlainTextResponse(
        snapshot_script(),
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/healthz")
@router.get("/health", include_in_schema=False)
def healthz(request: Request) -> JSONResponse:
    ctx = _ctx(request)
    db_ok = True
    try:
        with ctx.db.session() as session:
            session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    scheduler = getattr(request.app.state, "scheduler", None)
    body = {
        "status": "ok" if db_ok else "error",
        "db": db_ok,
        "scheduler": bool(scheduler and scheduler.running),
        "meshcentral": ctx.client.health(),
    }
    return JSONResponse(body, status_code=200 if db_ok else 503)
