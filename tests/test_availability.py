from datetime import date, datetime, time
from app.services.availability import compute_slots, subtract_intervals, split_into_slots
from app.models import AvailabilityRule, BlockedPeriod


def make_rule(day: int, start: str, end: str) -> AvailabilityRule:
    r = AvailabilityRule()
    r.day_of_week = day
    r.start_time = start
    r.end_time = end
    r.active = True
    return r


def test_subtract_intervals_removes_busy_time():
    windows = [(time(9, 0), time(17, 0))]
    busy = [(datetime(2025, 3, 3, 12, 0), datetime(2025, 3, 3, 13, 0))]
    result = subtract_intervals(windows, busy, date(2025, 3, 3))
    assert (time(9, 0), time(12, 0)) in result
    assert (time(13, 0), time(17, 0)) in result


def test_subtract_intervals_no_overlap():
    windows = [(time(9, 0), time(17, 0))]
    busy = [(datetime(2025, 3, 4, 12, 0), datetime(2025, 3, 4, 13, 0))]
    result = subtract_intervals(windows, busy, date(2025, 3, 3))
    assert result == [(time(9, 0), time(17, 0))]


def test_split_into_slots_basic():
    windows = [(time(9, 0), time(11, 0))]
    slots = split_into_slots(windows, duration_minutes=60, buffer_after_minutes=0)
    assert slots == [time(9, 0), time(10, 0)]


def test_split_respects_buffer():
    windows = [(time(9, 0), time(11, 0))]
    slots = split_into_slots(windows, duration_minutes=60, buffer_after_minutes=15)
    assert slots == [time(9, 0)]


def test_compute_slots_no_rules_returns_empty():
    result = compute_slots(
        target_date=date(2025, 3, 3),
        rules=[],
        blocked_periods=[],
        busy_intervals=[],
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        min_advance_hours=0,
        now=datetime(2025, 3, 3, 8, 0),
    )
    assert result == []


def test_compute_slots_returns_correct_times():
    rule = make_rule(0, "09:00", "10:30")  # Monday
    result = compute_slots(
        target_date=date(2025, 3, 3),   # Monday
        rules=[rule],
        blocked_periods=[],
        busy_intervals=[],
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        min_advance_hours=0,
        now=datetime(2025, 3, 2, 8, 0),
    )
    assert len(result) == 3
    assert time(9, 0) in result
    assert time(9, 30) in result
    assert time(10, 0) in result


def test_compute_slots_advance_notice_filters():
    rule = make_rule(0, "09:00", "17:00")  # Monday
    # now is Monday 8:30am, min_advance is 2 hours → cutoff is 10:30am
    result = compute_slots(
        target_date=date(2025, 3, 3),
        rules=[rule],
        blocked_periods=[],
        busy_intervals=[],
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        min_advance_hours=2,
        now=datetime(2025, 3, 3, 8, 30),
    )
    assert time(9, 0) not in result
    assert time(10, 0) not in result
    assert time(10, 30) in result
