"""Timezone conversion helpers.

The app stores and computes everything as *naive* datetimes in two frames:
local wall-clock time (bookings, availability rules, slots shown to guests)
and UTC (Google Calendar API, webcal feeds). These helpers are the single
place where conversion between the two frames happens.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def utc_to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    """Convert a naive UTC datetime to a naive local datetime."""
    return dt.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)


def local_to_utc(dt: datetime, tz: ZoneInfo) -> datetime:
    """Convert a naive local datetime to a naive UTC datetime."""
    return dt.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def now_local(tz: ZoneInfo) -> datetime:
    """Return the current time as a naive local datetime."""
    return datetime.now(timezone.utc).astimezone(tz).replace(tzinfo=None)


def local_day_bounds_utc(target_date: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Return (start, end) of a local calendar day as naive UTC datetimes."""
    local_midnight = datetime.combine(target_date, time(0, 0)).replace(tzinfo=tz)
    day_start = local_midnight.astimezone(timezone.utc).replace(tzinfo=None)
    day_end = (local_midnight + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
    return day_start, day_end
