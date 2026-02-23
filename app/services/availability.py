from datetime import date, datetime, time, timedelta
from app.models import AvailabilityRule, BlockedPeriod


def _time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _minutes_to_time(m: int) -> time:
    return time(m // 60, m % 60)


def subtract_intervals(
    windows: list[tuple[time, time]],
    busy: list[tuple[datetime, datetime]],
    target_date: date,
) -> list[tuple[time, time]]:
    """Remove busy datetime intervals from time windows on a given date."""
    result = []
    for w_start, w_end in windows:
        segments = [(w_start, w_end)]
        for busy_start, busy_end in busy:
            if busy_start.date() > target_date or busy_end.date() < target_date:
                continue
            b_start = busy_start.time() if busy_start.date() == target_date else time(0, 0)
            b_end = busy_end.time() if busy_end.date() == target_date else time(23, 59)
            new_segments = []
            for s, e in segments:
                if b_end <= s or b_start >= e:
                    new_segments.append((s, e))
                else:
                    if s < b_start:
                        new_segments.append((s, b_start))
                    if b_end < e:
                        new_segments.append((b_end, e))
            segments = new_segments
        result.extend(segments)
    return result


def split_into_slots(
    windows: list[tuple[time, time]],
    duration_minutes: int,
    buffer_after_minutes: int,
) -> list[time]:
    """Split time windows into appointment start times."""
    slot_duration = duration_minutes + buffer_after_minutes
    slots = []
    for w_start, w_end in windows:
        current = _time_to_minutes(w_start)
        end = _time_to_minutes(w_end)
        while current + duration_minutes <= end:
            slots.append(_minutes_to_time(current))
            current += slot_duration
    return slots


def compute_slots(
    target_date: date,
    rules: list,
    blocked_periods: list,
    busy_intervals: list[tuple[datetime, datetime]],
    duration_minutes: int,
    buffer_before_minutes: int,
    buffer_after_minutes: int,
    min_advance_hours: int,
    now: datetime,
) -> list[time]:
    """Compute available appointment start times for a given date."""
    day_of_week = target_date.weekday()  # 0=Monday
    day_rules = [r for r in rules if r.day_of_week == day_of_week and r.active]
    if not day_rules:
        return []

    windows = [
        (time.fromisoformat(r.start_time), time.fromisoformat(r.end_time))
        for r in day_rules
    ]

    # Subtract blocked periods
    blocked = [
        (bp.start_datetime, bp.end_datetime)
        for bp in blocked_periods
        if bp.start_datetime.date() <= target_date <= bp.end_datetime.date()
    ]
    windows = subtract_intervals(windows, blocked, target_date)

    # Subtract Google Calendar busy intervals
    windows = subtract_intervals(windows, busy_intervals, target_date)

    # Apply buffer_before by shrinking window starts
    if buffer_before_minutes:
        windows = [
            (_minutes_to_time(_time_to_minutes(s) + buffer_before_minutes), e)
            for s, e in windows
            if _time_to_minutes(e) - _time_to_minutes(s) > buffer_before_minutes
        ]

    # Split into slots
    slots = split_into_slots(windows, duration_minutes, buffer_after_minutes)

    # Filter out slots too soon
    if now + timedelta(hours=min_advance_hours) > datetime.combine(target_date, time(23, 59)):
        return []
    cutoff = now + timedelta(hours=min_advance_hours)
    if cutoff.date() == target_date:
        cutoff_time = cutoff.time()
        slots = [s for s in slots if s >= cutoff_time]

    return slots
