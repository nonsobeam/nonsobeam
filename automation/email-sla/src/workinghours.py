"""Working-hours arithmetic for the SLA scan.

The SLA clock runs Monday to Friday, 09:00-18:00 Europe/Lisbon, and nowhere
else. Nine working hours is therefore exactly one working day: a mail landing
Tuesday 15:00 is due Wednesday 15:00, and one landing Friday 16:00 is due
Monday 16:00.

Everything here works on timezone-aware datetimes. Outlook hands out UTC, so
convert with `to_lisbon` before doing anything else -- zoneinfo handles the
WET/WEST changeover, which is why we never hardcode a UTC offset.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

LISBON = ZoneInfo("Europe/Lisbon")
UTC = ZoneInfo("UTC")

DAY_START = time(9, 0)
DAY_END = time(18, 0)
HOURS_PER_DAY = 9.0

#: The SLA target, in working hours.
TARGET_HOURS = 9.0


def to_lisbon(dt: datetime) -> datetime:
    """Return `dt` in Europe/Lisbon. Naive input is assumed to be UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(LISBON)


def _is_workday(d: datetime) -> bool:
    return d.weekday() < 5


def _window(d: datetime) -> tuple[datetime, datetime]:
    """The working window of `d`'s calendar day, in Lisbon time."""
    return (
        d.replace(hour=DAY_START.hour, minute=0, second=0, microsecond=0),
        d.replace(hour=DAY_END.hour, minute=0, second=0, microsecond=0),
    )


def next_working_start(dt: datetime) -> datetime:
    """The first working moment at or after `dt`.

    Inside a working window this is `dt` itself; outside it, 09:00 on the next
    working day. This is what starts the clock for out-of-hours mail.
    """
    dt = to_lisbon(dt)
    while True:
        start, end = _window(dt)
        if _is_workday(dt) and dt < end:
            return max(dt, start)
        # Past close, or a weekend: try 09:00 tomorrow.
        dt = _window(dt + timedelta(days=1))[0]


def working_hours_between(start: datetime, end: datetime) -> float:
    """Working hours elapsed from `start` to `end`.

    Both ends are clamped into working time first, so out-of-hours arrivals and
    out-of-hours replies both measure from the working moments that bracket
    them. Returns 0.0 if `end` precedes `start`.
    """
    start, end = to_lisbon(start), to_lisbon(end)
    if end <= start:
        return 0.0

    cursor = next_working_start(start)
    total = 0.0
    while cursor < end:
        _, day_end = _window(cursor)
        segment_end = min(day_end, end)
        if segment_end > cursor:
            total += (segment_end - cursor).total_seconds() / 3600.0
        cursor = next_working_start(day_end)
    return total


def add_working_hours(start: datetime, hours: float) -> datetime:
    """The wall-clock moment `hours` working hours after `start`.

    Used to work out when a thread breaches, which drives the "Due today" list.
    """
    cursor = next_working_start(start)
    remaining = float(hours)
    while remaining > 1e-9:
        _, day_end = _window(cursor)
        available = (day_end - cursor).total_seconds() / 3600.0
        if remaining <= available:
            return cursor + timedelta(hours=remaining)
        remaining -= available
        cursor = next_working_start(day_end)
    return cursor


def week_start(dt: datetime) -> datetime:
    """Midnight on the Monday of `dt`'s week, in Lisbon time."""
    d = to_lisbon(dt)
    monday = d - timedelta(days=d.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)
