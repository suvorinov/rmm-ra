"""Сбор инвентаря и снимков состояния хостов через MeshCentral."""

from __future__ import annotations

import json
import logging
import hashlib
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import delete, desc, select

from .config import Settings, redact
from .db import Database
from .mc import MeshCentralClient, MeshCentralError
from .models import BrowserSession, Host, JobRun, ProcessSnapshot, Snapshot
from .timeutil import clamp, iso_utc, parse_iso, utcnow

log = logging.getLogger(__name__)

SNAPSHOT_BEGIN = "###SNAPSHOT-BEGIN###"
SNAPSHOT_END = "###SNAPSHOT-END###"
SNAPSHOT_FORMAT = 1
REMOTE_SCRIPT_DIR = r"C:\Windows\Temp"


class SnapshotError(RuntimeError):
    """Снимок не получен или не разобран."""


@dataclass
class SnapshotResult:
    host_id: str
    status: str  # ok | error | skipped | busy
    error: str | None = None
    snapshot_id: int | None = None


@dataclass
class CollectorContext:
    settings: Settings
    db: Database
    client: MeshCentralClient
    log: logging.Logger = log
    _host_locks: dict[str, threading.Lock] = field(default_factory=dict)
    _locks_guard: threading.Lock = field(default_factory=threading.Lock)
    _manual_at: dict[str, float] = field(default_factory=dict)
    _manual_guard: threading.Lock = field(default_factory=threading.Lock)

    def host_lock(self, host_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._host_locks.setdefault(host_id, threading.Lock())

    def allow_manual(self, host_id: str, min_seconds: float = 20.0) -> bool:
        now = time.monotonic()
        with self._manual_guard:
            last = self._manual_at.get(host_id)
            if last is not None and now - last < min_seconds:
                return False
            self._manual_at[host_id] = now
            return True


# --------------------------------------------------------------------- parser


@lru_cache(maxsize=1)
def _snapshot_source() -> tuple[str, str]:
    path = Path(__file__).parent / "ps" / "snapshot.ps1"
    text = path.read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot_script() -> str:
    """Текст PowerShell-скрипта (агент тянет его по HTTP или он загружается)."""
    return _snapshot_source()[0]


def snapshot_script_sha() -> str:
    return _snapshot_source()[1]


def remote_script_path(sha: str) -> str:
    return f"{REMOTE_SCRIPT_DIR}\\rmm-monitor-snapshot-{sha[:12]}.ps1"


def parse_snapshot_output(stdout: str) -> dict[str, Any]:
    start = stdout.find(SNAPSHOT_BEGIN)
    if start < 0:
        raise SnapshotError(f"в ответе нет маркера снимка: {_preview(stdout)}")
    end = stdout.find(SNAPSHOT_END, start + len(SNAPSHOT_BEGIN))
    if end < 0:
        raise SnapshotError(f"снимок оборван: {_preview(stdout)}")
    payload = stdout[start + len(SNAPSHOT_BEGIN) : end].strip()
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise SnapshotError(f"не разобрал JSON снимка: {_preview(payload)}") from exc
    if not isinstance(data, dict):
        raise SnapshotError("снимок имеет неожиданный формат")
    if data.get("error"):
        raise SnapshotError(str(data["error"]))
    if data.get("format") != SNAPSHOT_FORMAT:
        raise SnapshotError(f"неизвестная версия формата снимка: {data.get('format')!r}")
    return data


def _preview(text: str, limit: int = 240) -> str:
    flat = " ".join(text.strip().split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _text(value: Any, limit: int = 255) -> str:
    if value is None:
        return ""
    return str(value)[:limit]


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, 128)
        if text and text not in result:
            result.append(text)
    return result


def _dict_list(value: Any) -> list[dict]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


def normalise_snapshot(data: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Приводит JSON с хоста к плоской структуре для БД."""
    os_info = data.get("os") if isinstance(data.get("os"), dict) else {}
    cpu = data.get("cpu") if isinstance(data.get("cpu"), dict) else {}
    memory = data.get("memory") if isinstance(data.get("memory"), dict) else {}
    processes = data.get("processes") if isinstance(data.get("processes"), dict) else {}
    network = data.get("network") if isinstance(data.get("network"), dict) else {}

    disks: list[dict[str, Any]] = []
    for disk in _dict_list(data.get("disks")):
        total = max(0.0, _num(disk.get("total_gb")))
        free = max(0.0, _num(disk.get("free_gb")))
        disks.append(
            {
                "mount": _text(disk.get("mount"), 16),
                "label": _text(disk.get("label"), 128),
                "total_gb": round(total, 1),
                "free_gb": round(free, 1),
                "used_pct": round(clamp(_num(disk.get("used_pct"))), 1),
            }
        )

    browsers: list[dict[str, Any]] = []
    for item in _dict_list(data.get("browsers")):
        name = _text(item.get("name"), 128)
        if not name:
            continue
        browsers.append(
            {
                "name": name,
                "processes": max(0, _int(item.get("processes"))),
                "memory_mb": round(max(0.0, _num(item.get("memory_mb"))), 1),
                "users": _str_list(item.get("users")),
            }
        )
    browsers.sort(key=lambda b: b["processes"], reverse=True)

    top: list[dict[str, Any]] = []
    for item in _dict_list(processes.get("top")):
        name = _text(item.get("name"), 128)
        if not name:
            continue
        top.append(
            {
                "name": name,
                "count": max(0, _int(item.get("count"))),
                "ws_mb": round(max(0.0, _num(item.get("ws_mb"))), 1),
                "cpu_s": round(max(0.0, _num(item.get("cpu_s"))), 1),
            }
        )

    users = _str_list(data.get("users"))
    console_user = _text(data.get("console_user"))
    if console_user and console_user not in users:
        users.append(console_user)

    disk_total = round(sum(d["total_gb"] for d in disks), 1)
    disk_free = round(sum(d["free_gb"] for d in disks), 1)
    disk_max = round(max((d["used_pct"] for d in disks), default=0.0), 1)
    browser_processes = sum(b["processes"] for b in browsers)

    return {
        "reported_at": parse_iso(data.get("collected_at")),
        "hostname": _text(data.get("hostname")),
        "os_caption": _text(os_info.get("caption")),
        "os_version": _text(
            f"{os_info.get('version', '')} {os_info.get('build', '')}".strip()
        ),
        "cpu_model": _text(cpu.get("name")),
        "cpu_cores": _int(cpu.get("cores")),
        "cpu_logical": _int(cpu.get("logical")),
        "cpu_percent": round(clamp(_num(cpu.get("percent"))), 1),
        "cpu_queue": max(0, _int(cpu.get("queue"))),
        "mem_total_mb": round(max(0.0, _num(memory.get("total_mb"))), 0),
        "mem_used_mb": round(max(0.0, _num(memory.get("used_mb"))), 0),
        "mem_percent": round(clamp(_num(memory.get("percent"))), 1),
        "uptime_s": max(0, _int(os_info.get("uptime_s"))),
        "process_count": max(0, _int(processes.get("total"))),
        "browser_processes": browser_processes,
        "console_user": console_user,
        "users": users,
        "disks": disks,
        "browsers": browsers,
        "top": top,
        "disk_total_gb": disk_total,
        "disk_free_gb": disk_free,
        "disk_max_percent": disk_max,
        "network": {"addresses": _str_list(network.get("addresses"))},
        "taken_at": now,
    }


# ------------------------------------------------------------------- devices


def _device_online(device: dict[str, Any]) -> bool:
    value = device.get("conn")
    if isinstance(value, str):
        return value not in {"", "0", "false", "False"}
    return bool(value)


def _device_agent_version(device: dict[str, Any]) -> str:
    agent = device.get("agent")
    if isinstance(agent, dict):
        return _text(agent.get("ver") or agent.get("version"), 64)
    if isinstance(agent, str):
        return _text(agent, 64)
    return ""


def _device_name(device: dict[str, Any]) -> tuple[str, str]:
    """Возвращает (hostname, отображаемое имя)."""
    hostname = _text(device.get("name"))
    display = _text(device.get("rname")) or hostname
    return hostname, display


def _host_key(name: str, fallback: str = "") -> str:
    """Ключ для слияния дублей агентов с одним и тем же hostname."""
    return (name or fallback).strip().casefold()


def _device_score(device: dict[str, Any], preferred_id: str) -> tuple[int, float, int]:
    """Онлайн важнее свежести, свежесть важнее стабильности node id."""
    online = 1 if _device_online(device) else 0
    age = _num(device.get("agct") or device.get("lastconnect") or 0)
    stable = int(_text(device.get("_id"), 100) == preferred_id)
    return (online, age, stable)


def upsert_hosts(db: Database, devices: list[dict[str, Any]], now: datetime) -> int:
    seen: set[str] = set()
    with db.session() as session:
        # В MeshCentral после переустановок агентов копятся дубли с одинаковым
        # именем. Оставляем один (онлайн + самый свежий), остальные не показываем.
        existing: dict[str, list[Host]] = {}
        for host in session.scalars(select(Host)).all():
            existing.setdefault(_host_key(host.hostname, host.name), []).append(host)

        groups: dict[str, list[dict[str, Any]]] = {}
        for device in devices:
            node_id = _text(device.get("_id"), 100)
            if not node_id:
                continue
            hostname, _ = _device_name(device)
            groups.setdefault(_host_key(hostname, node_id), []).append(device)

        chosen: list[dict[str, Any]] = []
        deleted_ids: set[str] = set()
        for key, group in groups.items():
            preferred_ids = {h.id for h in existing.get(key, [])}
            best = max(
                group,
                key=lambda d: max(
                    (_device_score(d, pid) for pid in preferred_ids),
                    default=_device_score(d, ""),
                ),
            )
            best_id = _text(best.get("_id"), 100)
            chosen.append(best)
            seen.add(best_id)
            for host in existing.get(key, []):
                if host.id != best_id:
                    session.delete(host)
                    deleted_ids.add(host.id)

        for device in chosen:
            node_id = _text(device.get("_id"), 100)
            host = session.get(Host, node_id)
            if host is None:
                host = Host(id=node_id, first_seen_at=now, last_snapshot_status="never")
                session.add(host)

            hostname, display = _device_name(device)
            host.hostname = hostname or host.hostname
            host.name = display or hostname or host.name or node_id[-12:]
            group = _text(device.get("groupname"))
            if group:
                host.group_name = group
            host.mesh_id = _text(device.get("meshid"), 100) or host.mesh_id
            host.ip = _text(device.get("ip"), 64) or host.ip
            host.os_desc = _text(device.get("osdesc")) or host.os_desc
            host.agent_version = _device_agent_version(device) or host.agent_version

            online = _device_online(device)
            was_online = bool(host.is_online)
            host.is_online = online
            host.last_inventory_at = now
            if online:
                host.last_seen_online = now
            elif was_online:
                host.last_offline_at = now
            host.updated_at = now

        # Устройства, пропавшие из выдачи MeshCentral, больше не считаем онлайн.
        if seen:
            for host in session.scalars(select(Host)).all():
                if host.id in deleted_ids:
                    continue
                if host.id not in seen and host.is_online:
                    host.is_online = False
                    host.last_offline_at = now
                    host.updated_at = now
        session.commit()
    return len(seen)


# ---------------------------------------------------------------- job runners


def start_job_run(db: Database, kind: str, *, host_id: str | None = None) -> int:
    with db.session() as session:
        run = JobRun(kind=kind, status="running", started_at=utcnow(), host_id=host_id)
        session.add(run)
        session.flush()
        run_id = run.id
        session.commit()
    return run_id


def finish_job_run(
    db: Database,
    run_id: int,
    *,
    status: str,
    ok_count: int = 0,
    fail_count: int = 0,
    message: str | None = None,
) -> None:
    with db.session() as session:
        run = session.get(JobRun, run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = utcnow()
        run.ok_count = ok_count
        run.fail_count = fail_count
        run.message = (message or "")[:1000] or None
        session.commit()


def run_inventory(ctx: CollectorContext) -> None:
    run_id = start_job_run(ctx.db, "inventory")
    try:
        devices = ctx.client.list_devices()
        count = upsert_hosts(ctx.db, devices, utcnow())
        finish_job_run(ctx.db, run_id, status="ok", ok_count=count)
        ctx.log.info("инвентарь: %s устройств", count)
    except Exception as exc:  # noqa: BLE001 - задача не должна падать в планировщике
        message = redact(str(exc), (ctx.settings.mc_pass, ctx.settings.mc_token))
        finish_job_run(ctx.db, run_id, status="error", message=message)
        ctx.log.error("инвентарь не обновлён: %s", message)


def _snapshot_is_fresh(host: Host, now: datetime, interval: int) -> bool:
    if host.last_snapshot_at is None:
        return False
    return (now - host.last_snapshot_at).total_seconds() < interval * 0.8


def run_snapshots(
    ctx: CollectorContext,
    *,
    host_ids: list[str] | None = None,
    force: bool = False,
) -> None:
    run_id = start_job_run(ctx.db, "snapshot")
    now = utcnow()
    with ctx.db.session() as session:
        hosts = list(
            session.scalars(
                select(Host)
                .where(Host.is_online.is_(True))
                .order_by(Host.last_snapshot_at.asc(), Host.name.asc())
            ).all()
        )

    if host_ids is not None:
        wanted = set(host_ids)
        hosts = [h for h in hosts if h.id in wanted]
    hosts = [h for h in hosts if not h.os_desc or "windows" in h.os_desc.lower()]

    skipped = 0
    if not force:
        fresh = [h for h in hosts if _snapshot_is_fresh(h, now, ctx.settings.snapshot_interval)]
        skipped = len(fresh)
        hosts = [h for h in hosts if h not in fresh]

    if ctx.settings.snapshot_max_hosts > 0:
        hosts = hosts[: ctx.settings.snapshot_max_hosts]

    if not hosts:
        finish_job_run(
            ctx.db,
            run_id,
            status="ok",
            ok_count=0,
            fail_count=0,
            message=f"нет хостов для опроса (пропущено свежих: {skipped})",
        )
        return

    results: list[SnapshotResult] = []
    workers = max(1, min(ctx.settings.snapshot_concurrency, len(hosts)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="snapshot") as pool:
        futures = {
            pool.submit(snapshot_host, ctx, host.id, force=force, blocking=True): host.id
            for host in hosts
        }
        for future in as_completed(futures):
            host_id = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - поток не должен ронять job
                results.append(SnapshotResult(host_id=host_id, status="error", error=str(exc)))

    ok = sum(1 for r in results if r.status == "ok")
    failed = [r for r in results if r.status == "error"]
    idle = sum(1 for r in results if r.status in {"skipped", "busy"})
    errors = "; ".join(
        f"{r.host_id.split('/')[-1]}: {r.error}" for r in failed[:3] if r.error
    )
    if ok and not failed:
        status = "ok"
    elif ok and failed:
        status = "partial"
    elif failed:
        status = "error"
    else:
        status = "ok"
    message = errors or (f"пропущено: {idle + skipped}" if (idle + skipped) else None)
    finish_job_run(
        ctx.db,
        run_id,
        status=status,
        ok_count=ok,
        fail_count=len(failed),
        message=message,
    )
    ctx.log.info("снимки: ok=%s ошибок=%s пропущено=%s", ok, len(failed), idle + skipped)


def _looks_like_missing_script(text: str) -> bool:
    low = text.lower()
    markers = (
        "cannot find",
        "not recognized",
        "does not exist",
        "no such file",
        "не удается найти",
        "не найден",
        # Скрипт на хосте может быть старым/пустым: маркеров в выводе нет,
        # хотя run_command отработал нормально. Тоже пробуем залить заново.
        "нет маркера снимка",
    )
    return any(marker in low for marker in markers)


def _upload_snapshot_script(
    ctx: CollectorContext, host_id: str, text: str, sha: str
) -> str:
    name = f"rmm-monitor-snapshot-{sha[:12]}.ps1"
    # Параллельные снимки используют один и тот же скрипт. Если все они пишут
    # в /tmp/<name>, один поток удаляет файл, пока meshctrl другого ещё читает
    # его (ENOENT). Локальный файл делаем уникальным, удалённое имя — стабильным.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=f"rmm-monitor-{sha[:12]}-", suffix=".ps1", delete=False
    ) as handle:
        handle.write(text)
        local = Path(handle.name)
    try:
        try:
            local.chmod(0o600)
        except OSError:
            pass
        remote = ctx.client.upload_file(host_id, local, REMOTE_SCRIPT_DIR, remote_name=name)
    finally:
        local.unlink(missing_ok=True)
    with ctx.db.session() as session:
        host = session.get(Host, host_id)
        if host is not None:
            host.snapshot_script_sha = sha
            session.commit()
    return remote


def _snapshot_command(remote: str, top: int) -> str:
    return (
        "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f'-File "{remote}" -Top {max(1, int(top))}'
    )


def agent_fetch_command(base_url: str, top: int, sha: str) -> str:
    """Команда агенту: скачать snapshot.ps1, сверить SHA256 и выполнить.

    Один короткий run_command вместо загрузки 7 КБ бинарными кадрами meshctrl:
    эти кадры с хоста до агента не доходят. Эндпоинт скрипта открыт без Basic,
    поэтому хэш защищает выполнение от подмены по пути (агент работает как SYSTEM).
    """
    url = base_url.rstrip("/") + "/agent/snapshot.ps1"
    if any(ch in url for ch in ("'", '"', "\n", "\r")):
        raise SnapshotError("MON_AGENT_SCRIPT_URL содержит недопустимые символы")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SnapshotError("некорректный SHA256 скрипта снимка")
    # Скачиваем байты, а не строку: WebClient.DownloadString на Russian Windows
    # игнорирует charset и портит кириллицу, из-за чего хэш не сходится.
    return (
        "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        "-Command \"$w=New-Object Net.WebClient; $b=$w.DownloadData('"
        + url
        + "'); $h=[BitConverter]::ToString([Security.Cryptography.SHA256]"
        + "::Create().ComputeHash($b)).Replace('-','').ToLowerInvariant(); "
        + "if ($h -ne '"
        + sha
        + "') { throw 'snapshot.ps1 hash mismatch' }; "
        + "$s=[Text.Encoding]::UTF8.GetString($b); "
        + "& ([ScriptBlock]::Create($s)) "
        + str(max(1, int(top)))
        + "\""
    )


def _collect_snapshot(ctx: CollectorContext, host_id: str, command: str) -> dict[str, Any]:
    output = ctx.client.run_command(
        host_id,
        command,
        timeout=ctx.settings.snapshot_timeout,
    )
    return parse_snapshot_output(output)


def _run_uploaded_snapshot(
    ctx: CollectorContext, host_id: str, text: str, sha: str, cached: bool
) -> dict[str, Any]:
    """Старый путь: залить скрипт через meshctrl и запустить локальный файл."""
    remote = remote_script_path(sha)
    if not cached:
        remote = _upload_snapshot_script(ctx, host_id, text, sha)
    try:
        return _collect_snapshot(
            ctx, host_id, _snapshot_command(remote, ctx.settings.snapshot_top_processes)
        )
    except Exception as exc:  # noqa: BLE001
        # Temp могли почистить или залить пустой файл — перезаливаем
        # один раз даже если SHA помечен как кэшированный.
        if _looks_like_missing_script(str(exc)):
            remote = _upload_snapshot_script(ctx, host_id, text, sha)
            return _collect_snapshot(
                ctx,
                host_id,
                _snapshot_command(remote, ctx.settings.snapshot_top_processes),
            )
        raise


def snapshot_host(
    ctx: CollectorContext,
    host_id: str,
    *,
    force: bool = False,
    blocking: bool = True,
) -> SnapshotResult:
    lock = ctx.host_lock(host_id)
    if not lock.acquire(blocking=blocking):
        return SnapshotResult(host_id=host_id, status="busy")

    try:
        now = utcnow()
        with ctx.db.session() as session:
            host = session.get(Host, host_id)
            if host is None:
                return SnapshotResult(host_id=host_id, status="skipped", error="хост не найден")
            if not force and not host.is_online:
                return SnapshotResult(host_id=host_id, status="skipped", error="хост офлайн")
            if not force and _snapshot_is_fresh(host, now, ctx.settings.snapshot_interval):
                return SnapshotResult(host_id=host_id, status="skipped", error="снимок свежий")

        text = snapshot_script()
        sha = snapshot_script_sha()
        with ctx.db.session() as session:
            host = session.get(Host, host_id)
            cached = bool(host and host.snapshot_script_sha == sha)

        try:
            base_url = ctx.settings.agent_script_url.rstrip("/")
            if base_url:
                command = agent_fetch_command(
                    base_url, ctx.settings.snapshot_top_processes, sha
                )
                try:
                    raw = _collect_snapshot(ctx, host_id, command)
                except Exception as exc:  # noqa: BLE001
                    # Агент не смог достучаться до мониторинга: один раз
                    # откатываемся на классическую загрузку через meshctrl.
                    try:
                        raw = _run_uploaded_snapshot(ctx, host_id, text, sha, cached)
                    except Exception as upload_exc:  # noqa: BLE001
                        raise SnapshotError(
                            f"по HTTP: {exc}; через upload: {upload_exc}"
                        ) from upload_exc
            else:
                raw = _run_uploaded_snapshot(ctx, host_id, text, sha, cached)
            data = normalise_snapshot(raw, now)
        except Exception as exc:  # noqa: BLE001 - фиксируем ошибку на хосте
            message = redact(
                str(exc)[:500], (ctx.settings.mc_pass, ctx.settings.mc_token)
            )
            with ctx.db.session() as session:
                host = session.get(Host, host_id)
                if host is not None:
                    host.last_snapshot_attempt_at = utcnow()
                    host.last_snapshot_status = "error"
                    host.last_snapshot_error = message
                    session.commit()
            ctx.log.warning("снимок %s не получен: %s", host_id[-12:], message)
            return SnapshotResult(host_id=host_id, status="error", error=message)

        snapshot_id = _store_snapshot(ctx.db, host_id, data)
        ctx.log.debug("снимок %s сохранён (#%s)", host_id[-12:], snapshot_id)

        with ctx.db.session() as session:
            host = session.get(Host, host_id)
            if host is not None:
                host.last_snapshot_attempt_at = now
                host.last_snapshot_status = "ok"
                host.last_snapshot_error = None
                session.commit()

        return SnapshotResult(host_id=host_id, status="ok", snapshot_id=snapshot_id)
    finally:
        lock.release()


def _store_snapshot(db: Database, host_id: str, data: dict[str, Any]) -> int:
    with db.session() as session:
        host = session.get(Host, host_id)
        if host is None:
            raise SnapshotError("хост исчез из инвентаря")

        snapshot = Snapshot(
            host_id=host_id,
            taken_at=data["taken_at"],
            reported_at=data["reported_at"],
            cpu_percent=data["cpu_percent"],
            mem_total_mb=data["mem_total_mb"],
            mem_used_mb=data["mem_used_mb"],
            mem_percent=data["mem_percent"],
            uptime_s=data["uptime_s"],
            process_count=data["process_count"],
            browser_processes=data["browser_processes"],
            console_user=data["console_user"] or None,
            users_json=json.dumps(data["users"], ensure_ascii=False),
            disks_json=json.dumps(data["disks"], ensure_ascii=False),
            network_json=json.dumps(data["network"], ensure_ascii=False),
        )
        session.add(snapshot)
        session.flush()

        for item in data["top"]:
            session.add(
                ProcessSnapshot(
                    snapshot_id=snapshot.id,
                    host_id=host_id,
                    name=item["name"],
                    count=item["count"],
                    ws_mb=item["ws_mb"],
                    cpu_s=item["cpu_s"],
                )
            )
        for item in data["browsers"]:
            session.add(
                BrowserSession(
                    snapshot_id=snapshot.id,
                    host_id=host_id,
                    name=item["name"],
                    processes=item["processes"],
                    memory_mb=item["memory_mb"],
                    users_json=json.dumps(item["users"], ensure_ascii=False),
                )
            )

        # Детали процессов/браузеров нужны только для последнего снимка:
        # историю нагрузки храним в snapshots, а не тысячами строк процессов.
        session.execute(
            delete(ProcessSnapshot).where(
                ProcessSnapshot.host_id == host_id,
                ProcessSnapshot.snapshot_id != snapshot.id,
            )
        )
        session.execute(
            delete(BrowserSession).where(
                BrowserSession.host_id == host_id,
                BrowserSession.snapshot_id != snapshot.id,
            )
        )

        # Денормализованное «последнее состояние» для дашборда.
        host.os_caption = data["os_caption"] or host.os_caption
        host.os_version = data["os_version"] or host.os_version
        host.cpu_model = data["cpu_model"] or host.cpu_model
        host.cpu_cores = data["cpu_cores"] or host.cpu_cores
        host.cpu_logical = data["cpu_logical"] or host.cpu_logical
        host.console_user = data["console_user"] or host.console_user
        host.uptime_s = data["uptime_s"]
        host.cpu_percent = data["cpu_percent"]
        host.mem_total_mb = data["mem_total_mb"]
        host.mem_used_mb = data["mem_used_mb"]
        host.mem_percent = data["mem_percent"]
        host.disk_total_gb = data["disk_total_gb"]
        host.disk_free_gb = data["disk_free_gb"]
        host.disk_max_percent = data["disk_max_percent"]
        host.process_count = data["process_count"]
        host.browser_processes = data["browser_processes"]
        host.browser_kinds = len(data["browsers"])
        host.last_snapshot_at = data["taken_at"]
        if data["hostname"] and not host.hostname:
            host.hostname = data["hostname"]
        if (not host.name or host.name == host.id[-12:]) and data["hostname"]:
            host.name = data["hostname"]
        host.last_snapshot_status = "ok"
        host.last_snapshot_error = None
        host.updated_at = utcnow()

        session.commit()
        snapshot_id = snapshot.id
    return snapshot_id


def run_cleanup(ctx: CollectorContext) -> None:
    run_id = start_job_run(ctx.db, "cleanup")
    now = utcnow()
    snap_cutoff = now - timedelta(days=ctx.settings.retention_days)
    runs_cutoff = now - timedelta(days=ctx.settings.runs_retention_days)
    try:
        with ctx.db.session() as session:
            deleted_snaps = session.execute(
                delete(Snapshot).where(Snapshot.taken_at < snap_cutoff)
            ).rowcount
            deleted_runs = session.execute(
                delete(JobRun).where(
                    JobRun.started_at < runs_cutoff, JobRun.status != "running"
                )
            ).rowcount
            session.commit()
        finish_job_run(
            ctx.db,
            run_id,
            status="ok",
            ok_count=int(deleted_snaps or 0),
            fail_count=0,
            message=f"удалено снимков: {deleted_snaps or 0}, записей задач: {deleted_runs or 0}",
        )
        ctx.log.info(
            "очистка: снимков=%s задач=%s", deleted_snaps or 0, deleted_runs or 0
        )
    except Exception as exc:  # noqa: BLE001
        finish_job_run(ctx.db, run_id, status="error", message=str(exc)[:1000])
        ctx.log.exception("очистка не удалась")


# ------------------------------------------------------------------ helpers


def latest_snapshot(session, host_id: str) -> Snapshot | None:
    return session.scalars(
        select(Snapshot)
        .where(Snapshot.host_id == host_id)
        .order_by(desc(Snapshot.taken_at))
        .limit(1)
    ).first()


def recent_snapshots(session, host_id: str, limit: int = 48) -> list[Snapshot]:
    rows = list(
        session.scalars(
            select(Snapshot)
            .where(Snapshot.host_id == host_id)
            .order_by(desc(Snapshot.taken_at))
            .limit(limit)
        ).all()
    )
    rows.reverse()
    return rows


def snapshot_error_for_log(exc: Exception) -> str:
    return redact(str(exc))


__all__ = [
    "CollectorContext",
    "MeshCentralError",
    "SnapshotError",
    "SnapshotResult",
    "latest_snapshot",
    "normalise_snapshot",
    "parse_snapshot_output",
    "recent_snapshots",
    "run_cleanup",
    "run_inventory",
    "run_snapshots",
    "snapshot_host",
    "snapshot_script",
    "upsert_hosts",
]
