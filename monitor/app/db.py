"""SQLite: подключение, сессии, PRAGMA."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def _set_sqlite_pragmas(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA temp_store=MEMORY")
    finally:
        cursor.close()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(
            f"sqlite+pysqlite:///{self.path}",
            connect_args={"check_same_thread": False, "timeout": 15},
            future=True,
        )
        event.listen(self.engine, "connect", _set_sqlite_pragmas)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def init(self) -> None:
        Base.metadata.create_all(self.engine)
        # База содержит имена хостов и пользователей — только владельцу.
        # WAL и SHM — тоже база, закрываем их так же.
        for suffix in ("", "-wal", "-shm"):
            try:
                os.chmod(str(self.path) + suffix, 0o600)
            except OSError:
                pass

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
