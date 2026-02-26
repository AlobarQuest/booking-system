from datetime import date, datetime, time
from app.services.availability import (
    compute_slots, subtract_intervals, split_into_slots,
    intersect_windows, trim_windows_for_drive_time, filter_by_advance_notice,
    _build_free_windows,
)
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
    slots = split_into_slots(windows, duration_minutes=60, buffer_before_minutes=0, buffer_after_minutes=0)
    assert slots == [time(9, 0), time(10, 0)]


def test_split_respects_buffer_after():
    windows = [(time(9, 0), time(11, 0))]
    slots = split_into_slots(windows, duration_minutes=60, buffer_before_minutes=0, buffer_after_minutes=15)
    assert slots == [time(9, 0)]


def test_split_respects_buffer_before():
    # 15-min buffer before + 30-min appointment = first slot at 9:15
    windows = [(time(9, 0), time(10, 0))]
    slots = split_into_slots(windows, duration_minutes=30, buffer_before_minutes=15, buffer_after_minutes=0)
    assert slots == [time(9, 15)]


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


def test_compute_slots_with_buffer_before():
    rule = make_rule(0, "09:00", "11:00")  # Monday
    result = compute_slots(
        target_date=date(2025, 3, 3),
        rules=[rule],
        blocked_periods=[],
        busy_intervals=[],
        duration_minutes=30,
        buffer_before_minutes=15,
        buffer_after_minutes=0,
        min_advance_hours=0,
        now=datetime(2025, 3, 2, 8, 0),
    )
    # 9:00 window: buffer 9:00-9:15, appointment 9:15-9:45, next buffer 9:45-10:00, appointment 10:00-10:30
    assert time(9, 15) in result
    assert time(10, 0) in result
    assert time(9, 0) not in result


def test_intersect_windows_overlapping():
    a = [(time(9, 0), time(17, 0))]
    b = [(time(11, 0), time(15, 0))]
    result = intersect_windows(a, b)
    assert result == [(time(11, 0), time(15, 0))]


def test_intersect_windows_no_overlap():
    a = [(time(9, 0), time(12, 0))]
    b = [(time(13, 0), time(17, 0))]
    result = intersect_windows(a, b)
    assert result == []


def test_intersect_windows_partial():
    a = [(time(9, 0), time(14, 0))]
    b = [(time(11, 0), time(17, 0))]
    result = intersect_windows(a, b)
    assert result == [(time(11, 0), time(14, 0))]


def test_filter_by_advance_notice_filters_past_cutoff():
    slots = [time(9, 0), time(10, 0), time(11, 0)]
    # now is 8:30, min_advance is 2 hours -> cutoff is 10:30
    result = filter_by_advance_notice(slots, date(2025, 3, 3), 2, datetime(2025, 3, 3, 8, 30))
    assert time(9, 0) not in result
    assert time(10, 0) not in result
    assert time(11, 0) in result


def test_trim_windows_for_drive_time_trims_by_drive_minutes():
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    windows = [(time(11, 0), time(14, 0))]
    # Preceding event ended at 10:45 (within 1 hour of 11:00 window start)
    day_events = [
        {"start": datetime(2025, 3, 3, 9, 0), "end": datetime(2025, 3, 3, 10, 45), "summary": "Previous Appt", "location": "123 Main St"}
    ]
    with patch("app.services.availability.get_drive_time", return_value=20):
        result = trim_windows_for_drive_time(
            windows, date(2025, 3, 3), day_events,
            destination="456 Oak Ave", home_address="789 Home Rd", db=db
        )
    assert result == [(time(11, 20), time(14, 0))]


def test_trim_windows_for_drive_time_uses_home_when_no_preceding_event():
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    windows = [(time(9, 0), time(12, 0))]
    day_events = []  # No preceding events
    with patch("app.services.availability.get_drive_time", return_value=30) as mock_dt:
        result = trim_windows_for_drive_time(
            windows, date(2025, 3, 3), day_events,
            destination="456 Oak Ave", home_address="789 Home Rd", db=db
        )
    mock_dt.assert_called_with("789 Home Rd", "456 Oak Ave", db)
    assert result == [(time(9, 30), time(12, 0))]


def test_trim_windows_for_drive_time_skips_event_outside_1hr_lookback():
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    windows = [(time(14, 0), time(17, 0))]
    # Event ended at 9am — more than 1 hour before 2pm window start
    day_events = [
        {"start": datetime(2025, 3, 3, 8, 0), "end": datetime(2025, 3, 3, 9, 0), "summary": "Morning Appt", "location": "Far Away Place"}
    ]
    with patch("app.services.availability.get_drive_time", return_value=25) as mock_dt:
        result = trim_windows_for_drive_time(
            windows, date(2025, 3, 3), day_events,
            destination="456 Oak Ave", home_address="789 Home Rd", db=db
        )
    # Should use home_address since event is outside 1-hour lookback
    mock_dt.assert_called_with("789 Home Rd", "456 Oak Ave", db)


def test_trim_windows_for_drive_time_zero_for_same_location():
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    windows = [(time(11, 0), time(14, 0))]
    day_events = [
        {"start": datetime(2025, 3, 3, 9, 0), "end": datetime(2025, 3, 3, 10, 45), "summary": "Previous", "location": "456 Oak Ave"}
    ]
    with patch("app.services.availability.get_drive_time") as mock_dt:
        result = trim_windows_for_drive_time(
            windows, date(2025, 3, 3), day_events,
            destination="456 Oak Ave", home_address="789 Home Rd", db=db
        )
    mock_dt.assert_not_called()  # Same location — no API call
    assert result == [(time(11, 0), time(14, 0))]  # No trimming
