"""Ссылки на карточки хостов: id содержит /, $, @ и должен попадать в путь как есть."""

from __future__ import annotations

from urllib.parse import quote


def host_url(host_id: str) -> str:
    return "/host/" + quote(host_id, safe="/$@+=")


def rustdesk_connect_url(host_id: str) -> str:
    """Кнопка идёт через API: он проверит ID и запишет событие в аудит."""
    return "/api/rustdesk/connect/" + quote(host_id, safe="/$@+=")
