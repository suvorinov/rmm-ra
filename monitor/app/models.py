"""Схема БД. Одна строка Host — текущее состояние, Snapshot — история."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .timeutil import utcnow

class Base(DeclarativeBase):
    pass


def _load_json(text: str | None) -> list:
    if not text:
        return []
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True, default="")
    hostname: Mapped[str] = mapped_column(String(255), default="")
    group_name: Mapped[str] = mapped_column(String(255), default="")
    mesh_id: Mapped[str] = mapped_column(String(100), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    os_desc: Mapped[str] = mapped_column(String(255), default="")
    agent_version: Mapped[str] = mapped_column(String(64), default="")

    is_online: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_inventory_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_online: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_offline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # Последний успешный снимок (денормализация ради быстрого дашборда)
    cpu_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cpu_cores: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_logical: Mapped[int | None] = mapped_column(Integer, nullable=True)
    os_caption: Mapped[str | None] = mapped_column(String(255), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    console_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uptime_s: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_total_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_used_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_total_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_free_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_max_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    process_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_processes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_kinds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    last_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_snapshot_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_snapshot_status: Mapped[str] = mapped_column(String(16), default="never")
    last_snapshot_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # SHA загруженного на хост скрипта снимка (кэш, чтобы не заливать каждый раз).
    snapshot_script_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - для отладки
        return f"<Host {self.name} online={self.is_online}>"


class RustdeskHost(Base):
    """Строка из HTML-адресной книги RustDesk. Паролей здесь нет и не будет."""

    __tablename__ = "rustdesk_hosts"

    ip: Mapped[str] = mapped_column(String(64), primary_key=True)
    rustdesk_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    version: Mapped[str] = mapped_column(String(64), default="")
    has_password: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source_mtime: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - для отладки
        return f"<RustdeskHost {self.ip} id={self.rustdesk_id or '-'}>"


class RustdeskEvent(Base):
    """Аудит нажатий «подключиться»: только IP/ID/пользователь, без секретов."""

    __tablename__ = "rustdesk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user: Mapped[str] = mapped_column(String(100), default="")
    host_id: Mapped[str] = mapped_column(String(100), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    rustdesk_id: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (Index("ix_snapshots_host_taken", "host_id", "taken_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_total_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_used_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    uptime_s: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    process_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_processes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    console_user: Mapped[str | None] = mapped_column(String(255), nullable=True)

    users_json: Mapped[str] = mapped_column(Text, default="[]")
    disks_json: Mapped[str] = mapped_column(Text, default="[]")
    network_json: Mapped[str] = mapped_column(Text, default="{}")

    processes: Mapped[list["ProcessSnapshot"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", passive_deletes=True
    )
    browsers: Mapped[list["BrowserSession"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def users(self) -> list[str]:
        return [str(x) for x in _load_json(self.users_json)]

    @property
    def disks(self) -> list[dict]:
        return [x for x in _load_json(self.disks_json) if isinstance(x, dict)]

    @property
    def network(self) -> dict:
        if not self.network_json:
            return {}
        try:
            value = json.loads(self.network_json)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}


class ProcessSnapshot(Base):
    __tablename__ = "process_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), index=True
    )
    host_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), default="")
    count: Mapped[int] = mapped_column(Integer, default=0)
    ws_mb: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_s: Mapped[float] = mapped_column(Float, default=0.0)

    snapshot: Mapped[Snapshot] = relationship(back_populates="processes")


class BrowserSession(Base):
    __tablename__ = "browser_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), index=True
    )
    host_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), default="")
    processes: Mapped[int] = mapped_column(Integer, default=0)
    memory_mb: Mapped[float] = mapped_column(Float, default=0.0)
    users_json: Mapped[str] = mapped_column(Text, default="[]")

    snapshot: Mapped[Snapshot] = relationship(back_populates="browsers")

    @property
    def users(self) -> list[str]:
        return [str(x) for x in _load_json(self.users_json)]


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_job_runs_kind_started", "kind", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    host_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ok_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
