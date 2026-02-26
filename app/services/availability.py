from datetime import date, datetime, time, timedelta
from app.services.drive_time import get_drive_time


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
            b_end = busy_end.time() if busy_end.date() == target_date else time(23, 59, 59)
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
    buffer_before_minutes: int,
    buffer_after_minutes: int,
) -> list[time]:
    """Split time windows into appointment start times.

    Block starts are aligned to the nearest multiple of duration_minutes from midnight
    so that slot times remain on a regular grid (e.g. :00 and :30 for 30-min appointments)
    even when windows are shifted by drive-time trimming.

    buffer_before_minutes: free time required before each appointment start.
    The returned slot time is the appointment start (after the buffer).
    Each slot consumes buffer_before + duration + buffer_after minutes.
    """
    slot_total = buffer_before_minutes + duration_minutes + buffer_after_minutes
    if duration_minutes <= 0:
        return []
    slots = []
    for w_start, w_end in windows:
        start_mins = _time_to_minutes(w_start)
        end_mins = _time_to_minutes(w_end)
        # Align the first block start to the next multiple of duration_minutes from midnight.
        # This keeps appointment times on a predictable grid regardless of where the window
        # starts (which may be an odd time after drive-time trimming).
        first_block = ((start_mins + duration_minutes - 1) // duration_minutes) * duration_minutes
        current = first_block
        while current + buffer_before_minutes + duration_minutes <= end_mins:
            slots.append(_minutes_to_time(current + buffer_before_minutes))
            current += slot_total
    return slots


def intersect_windows(
    windows_a: list[tuple[time, time]],
    windows_b: list[tuple[time, time]],
) -> list[tuple[time, time]]:
    """Return the intersection of two sets of time windows."""
    result = []
    for a_start, a_end in windows_a:
        for b_start, b_end in windows_b:
            start = max(a_start, b_start)
            end = min(a_end, b_end)
            if start < end:
                result.append((start, end))
    return sorted(result)


def filter_by_advance_notice(
    slots: list[time],
    target_date: date,
    min_advance_hours: int,
    now: datetime,
) -> list[time]:
    """Remove slots that fall within the min_advance_hours cutoff from now."""
    end_of_day = datetime.combine(target_date, time(23, 59, 59))
    cutoff = now + timedelta(hours=min_advance_hours)
    if cutoff > end_of_day:
        return []
    if cutoff.date() == target_date:
        cutoff_time = cutoff.time()
        return [s for s in slots if s >= cutoff_time]
    return slots


def trim_windows_for_drive_time(
    windows: list[tuple[time, time]],
    target_date: date,
    day_events: list[dict],
    destination: str,
    home_address: str,
    db,
) -> list[tuple[time, time]]:
    """Trim the start of each window by drive time from the preceding event's location.

    day_events: list of dicts with keys start (datetime), end (datetime), location (str), summary (str).
    All datetimes must be in local time (naive). Only considers events that ended within
    1 hour before the window start. Falls back to home_address if nothing found.
    """
    result = []
    for w_start, w_end in windows:
        window_start_dt = datetime.combine(target_date, w_start)
        lookback_cutoff = window_start_dt - timedelta(hours=1)

        # Find the most recent event ending within 1 hour before window start
        preceding = None
        for ev in day_events:
            if lookback_cutoff <= ev["end"] <= window_start_dt:
                if preceding is None or ev["end"] > preceding["end"]:
                    preceding = ev

        origin = (preceding.get("location") or "").strip() if preceding else ""
        if not origin:
            origin = home_address

        if not origin or not destination:
            result.append((w_start, w_end))
            continue

        if origin.lower() == destination.lower():
            drive_mins = 0
        else:
            drive_mins = get_drive_time(origin, destination, db)

        trimmed_start_mins = _time_to_minutes(w_start) + drive_mins
        trimmed_start = _minutes_to_time(min(trimmed_start_mins, 23 * 60 + 59))
        if trimmed_start < w_end:
            result.append((trimmed_start, w_end))
        # If drive time consumes the entire window, it is dropped

    return result


def _build_free_windows(
    target_date: date,
    rules: list,
    blocked_periods: list,
    busy_intervals: list[tuple[datetime, datetime]],
) -> list[tuple[time, time]]:
    """Compute available time windows after subtracting blocked periods and busy intervals."""
    day_of_week = target_date.weekday()  # 0=Monday
    day_rules = [r for r in rules if r.day_of_week == day_of_week and r.active]
    if not day_rules:
        return []

    windows = [
        (time.fromisoformat(r.start_time), time.fromisoformat(r.end_time))
        for r in day_rules
    ]

    blocked = [
        (bp.start_datetime, bp.end_datetime)
        for bp in blocked_periods
        if bp.start_datetime.date() <= target_date <= bp.end_datetime.date()
    ]
    windows = subtract_intervals(windows, blocked, target_date)
    windows = subtract_intervals(windows, busy_intervals, target_date)
    return windows


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
    windows = _build_free_windows(target_date, rules, blocked_periods, busy_intervals)
    if not windows:
        return []
    slots = split_into_slots(windows, duration_minutes, buffer_before_minutes, buffer_after_minutes)
    return filter_by_advance_notice(slots, target_date, min_advance_hours, now)
