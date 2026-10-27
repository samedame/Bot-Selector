from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)


def now_et() -> datetime:
    return datetime.now(ET)


def to_et(dt: datetime) -> datetime:
    return dt.astimezone(ET)


def parse_iso(s: str) -> datetime:
    """ISO-8601 with or without offset. Naive values are taken as Eastern."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.astimezone(ET) if dt.tzinfo else dt.replace(tzinfo=ET)


def at_et(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=ET)


def minute_to_dt(d: date, minute: int) -> datetime:
    """Bot minute index 0 = the 09:30 bar. The bar closes (and the alert can fire) one minute later."""
    return at_et(d, OPEN) + timedelta(minutes=minute + 1)


def next_weekday(d: date) -> date:
    """Next Mon-Fri after d. Exchange holidays are not modelled; a bot's own calendar still governs."""
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n
