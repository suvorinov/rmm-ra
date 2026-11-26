from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import RustdeskHost
from app.rustdesk_book import import_book, parse_book


def book_html(rows: list[tuple[str, str, str, str]]) -> str:
    body = []
    for host, rid, version, password in rows:
        badge = {"да": "ok", "нет": "warn"}.get(password, "unknown")
        body.append(
            f'<tr data-host="{host}" data-id="{rid}" data-version="{version}">'
            f'<td>{host}</td><td><a href="rustdesk://connection/new/{rid}">{rid}</a></td>'
            f'<td>{version}</td><td><span class="badge {badge}">{password}</span></td>'
            f"</tr>"
        )
    return "<!doctype html><html><body><table><tbody>" + "".join(body) + "</tbody></table></body></html>"


def write_book(path: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    path.write_text(book_html(rows), encoding="utf-8")
    return path


def test_parse_book_normalizes_id_and_password(tmp_path: Path):
    path = write_book(
        tmp_path / "book.html",
        [
            ("192.168.0.50", "123 456", "1.5.3", "да"),
            ("192.168.0.51", "99-999", "1.5.4", "нет"),
            ("not-an-ip", "123", "1.0", "да"),
        ],
    )
    old = time.time() - 3600
    os.utime(path, (old, old))

    entries, mtime = parse_book(path)
    assert len(entries) == 2
    by_ip = {entry.ip: entry for entry in entries}
    assert by_ip["192.168.0.50"].rustdesk_id == "123456"
    assert by_ip["192.168.0.50"].has_password is True
    assert by_ip["192.168.0.51"].rustdesk_id == "99999"
    assert by_ip["192.168.0.51"].has_password is False
    assert mtime == datetime.fromtimestamp(old, timezone.utc).replace(tzinfo=None)


def test_import_syncs_rows_and_refuses_empty_book(tmp_path: Path, db):
    path = write_book(
        tmp_path / "book.html",
        [
            ("192.168.0.50", "111", "1.5.3", "да"),
            ("192.168.0.51", "222", "1.5.3", "да"),
        ],
    )
    stats = import_book(path, db.path)
    assert stats.total == 2
    assert stats.inserted == 2
    assert stats.updated == 0
    assert stats.removed == 0

    with db.session() as session:
        rows = list(session.scalars(select(RustdeskHost).order_by(RustdeskHost.ip)))
        assert [row.ip for row in rows] == ["192.168.0.50", "192.168.0.51"]
        assert rows[0].rustdesk_id == "111"
        assert rows[0].has_password is True
        assert rows[0].source_mtime == stats.source_mtime
        assert rows[0].imported_at is not None

    write_book(
        path,
        [
            ("192.168.0.50", "333", "1.5.4", "нет"),
            ("192.168.0.52", "444", "1.5.3", "да"),
        ],
    )
    stats = import_book(path, db.path)
    assert stats.inserted == 1
    assert stats.updated == 1
    assert stats.removed == 1

    with db.session() as session:
        rows = {row.ip: row for row in session.scalars(select(RustdeskHost))}
        assert set(rows) == {"192.168.0.50", "192.168.0.52"}
        assert rows["192.168.0.50"].rustdesk_id == "333"
        assert rows["192.168.0.50"].version == "1.5.4"
        assert rows["192.168.0.50"].has_password is False

    path.write_text("<html><body>пусто</body></html>", encoding="utf-8")
    with pytest.raises(ValueError, match="не найдено ни одной строки"):
        import_book(path, db.path)
    with db.session() as session:
        assert len(list(session.scalars(select(RustdeskHost)))) == 2
