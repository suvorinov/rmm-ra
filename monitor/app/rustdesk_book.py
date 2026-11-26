"""Импорт HTML-адресной книги RustDesk в SQLite.

Источник — ``reports/rustdesk-book.html``, который собирает
``make rustdesk-book`` из отчётов Ansible. В книге есть IP, RustDesk ID,
версия и признак постоянного пароля; самих паролей нет. Здесь мы сознательно
не читаем и не сохраняем ничего похожего на секрет — только ID и флаг.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from sqlalchemy import delete, select

from .db import Database
from .models import RustdeskHost
from .timeutil import utcnow

DEFAULT_BOOK_PATH = Path("/app/reports/rustdesk-book.html")
DEFAULT_DB_PATH = Path("/data/monitor.db")


@dataclass(frozen=True)
class BookEntry:
    ip: str
    rustdesk_id: str
    version: str
    has_password: bool | None
    source_mtime: datetime


@dataclass(frozen=True)
class ImportStats:
    total: int
    inserted: int
    updated: int
    removed: int
    source_mtime: datetime | None


class _BookParser(HTMLParser):
    """Достаём ``data-*`` из строк таблицы и текст бейджа «да/нет»."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[dict[str, object]] = []
        self._row: dict[str, object] | None = None
        self._badge: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "tr" and "data-host" in values:
            self._row = {"attrs": values, "password": None}
            self._badge = None
        elif (
            tag == "span"
            and self._row is not None
            and "badge" in values.get("class", "").split()
        ):
            self._badge = {"text": ""}

    def handle_data(self, data: str) -> None:
        if self._badge is not None:
            self._badge["text"] = str(self._badge["text"]) + data

    def handle_endtag(self, tag: str) -> None:
        if self._badge is not None and tag == "span":
            text = str(self._badge["text"]).strip().lower()
            if self._row is not None:
                if text == "да":
                    self._row["password"] = True
                elif text == "нет":
                    self._row["password"] = False
            self._badge = None
        elif self._row is not None and tag == "tr":
            self.entries.append(self._row)
            self._row = None
            self._badge = None


def _file_mtime(path: Path) -> datetime:
    """В БД храним naive UTC — как и везде в мониторинге."""
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(tzinfo=None)


def parse_book(path: Path) -> tuple[list[BookEntry], datetime]:
    """Разбирает HTML-книгу. Пустой/сломанный файл не возвращает пустоту молча."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Нет файла адресной книги: {path}")

    mtime = _file_mtime(path)
    parser = _BookParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    parser.close()

    by_ip: dict[str, BookEntry] = {}
    for row in parser.entries:
        attrs = row["attrs"]
        if not isinstance(attrs, dict):
            continue
        raw_ip = str(attrs.get("data-host", "")).strip()
        try:
            ip = str(ipaddress.ip_address(raw_ip))
        except ValueError:
            # В книге могут оказаться хосты не по IP — для привязки они не нужны.
            continue
        rid = re.sub(r"\D", "", str(attrs.get("data-id", "")))
        has_password = row.get("password")
        entry = BookEntry(
            ip=ip,
            rustdesk_id=rid,
            version=str(attrs.get("data-version", "")).strip(),
            has_password=has_password if isinstance(has_password, bool) else None,
            source_mtime=mtime,
        )
        by_ip[ip] = entry  # последняя строка с IP побеждает

    if not by_ip:
        raise ValueError(
            f"В адресной книге {path} не найдено ни одной строки с IP — импорт отменён"
        )
    return list(by_ip.values()), mtime


def import_book(path: Path, db_path: Path) -> ImportStats:
    """Полная синхронизация таблицы rustdesk_hosts с содержимым HTML-книги."""
    entries, mtime = parse_book(path)
    db = Database(Path(db_path))
    db.init()
    try:
        with db.session() as session:
            existing = {
                row.ip: row
                for row in session.scalars(select(RustdeskHost)).all()
            }
            inserted = 0
            updated = 0
            seen: set[str] = set()
            for entry in entries:
                seen.add(entry.ip)
                row = existing.get(entry.ip)
                if row is None:
                    session.add(
                        RustdeskHost(
                            ip=entry.ip,
                            rustdesk_id=entry.rustdesk_id,
                            version=entry.version,
                            has_password=entry.has_password,
                            source_mtime=entry.source_mtime,
                            imported_at=utcnow(),
                        )
                    )
                    inserted += 1
                    continue
                row.rustdesk_id = entry.rustdesk_id
                row.version = entry.version
                row.has_password = entry.has_password
                row.source_mtime = entry.source_mtime
                row.imported_at = utcnow()
                updated += 1

            removed = 0
            for ip, row in existing.items():
                if ip not in seen:
                    session.delete(row)
                    removed += 1
            session.flush()
    finally:
        db.dispose()

    return ImportStats(
        total=len(entries),
        inserted=inserted,
        updated=updated,
        removed=removed,
        source_mtime=mtime,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт RustDesk-книги в SQLite мониторинга")
    parser.add_argument(
        "--file",
        default=os.environ.get("MON_RUSTDESK_BOOK", str(DEFAULT_BOOK_PATH)),
        help="путь к reports/rustdesk-book.html (или MON_RUSTDESK_BOOK)",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("MON_DB_PATH", str(DEFAULT_DB_PATH)),
        help="путь к SQLite (или MON_DB_PATH)",
    )
    args = parser.parse_args(argv)

    stats = import_book(Path(args.file), Path(args.db))
    stamp = stats.source_mtime.strftime("%d.%m.%Y %H:%M UTC") if stats.source_mtime else "—"
    print(
        f"RustDesk-книга импортирована: всего {stats.total}, "
        f"новых {stats.inserted}, обновлено {stats.updated}, удалено {stats.removed}; "
        f"файл от {stamp}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
