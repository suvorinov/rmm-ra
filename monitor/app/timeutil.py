"""Время и форматирование. В БД храним naive UTC, показываем в MON_TZ."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def utcnow() -> datetime:
    """Naive UTC — так SQLite не путается с часовыми поясами."""
    return datetime.now(UTC).replace(tzinfo=None)


def as_local(value: datetime | None, tz_name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(tz_name))


def fmt_local(value: datetime | None, tz_name: str, fmt: str = "%d.%m.%Y %H:%M:%S") -> str:
    local = as_local(value, tz_name)
    return local.strftime(fmt) if local else "—"


def human_ago(value: datetime | None, tz_name: str = "Europe/Moscow") -> str:
    if value is None:
        return "никогда"
    local = as_local(value, tz_name)
    now = datetime.now(UTC).astimezone(ZoneInfo(tz_name))
    delta = now - local
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "только что"
    if seconds < 60:
        return f"{seconds} с назад"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    days = hours // 24
    if days < 30:
        return f"{days} дн назад"
    return fmt_local(value, tz_name, "%d.%m.%Y")


def human_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    if total < 0:
        return "—"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes and len(parts) < 2:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{secs} с")
    return " ".join(parts)


def human_mb(value: float | int | None) -> str:
    if value is None:
        return "—"
    value = float(value)
    if value >= 1024:
        return f"{value / 1024:.1f} ГБ"
    return f"{value:.0f} МБ"


def pct(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}%"


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def stale_cutoff(now: datetime, snapshot_interval: int) -> datetime:
    """Хост считается устаревшим, если снимок старше трёх интервалов (но не 15 минут)."""
    return now - timedelta(seconds=max(snapshot_interval * 3, 900))
