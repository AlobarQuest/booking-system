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


def make_rule_for_type(day: int, start: str, end: str, type_id: int) -> AvailabilityRule:
    r = AvailabilityRule()
    r.day_of_week = day
    r.start_time = start
    r.end_time = end
    r.active = True
    r.appointment_type_id = type_id
    return r


def test_build_free_windows_uses_type_specific_rules_when_present():
    """If type has its own rules, use only those — ignore global rules."""
    global_rule = make_rule(0, "09:00", "17:00")               # global (appointment_type_id=None)
    type_rule   = make_rule_for_type(0, "10:00", "12:00", type_id=5)  # type-specific
    all_rules = [global_rule, type_rule]
    windows = _build_free_windows(date(2025, 3, 3), all_rules, [], [], appointment_type_id=5)
    # Should use 10:00-12:00, not 09:00-17:00
    assert windows == [(time(10, 0), time(12, 0))]


def test_build_free_windows_falls_back_to_global_when_no_type_rules():
    """If type has no rules, fall back to global rules."""
    global_rule = make_rule(0, "09:00", "17:00")  # global (appointment_type_id=None)
    all_rules = [global_rule]
    windows = _build_free_windows(date(2025, 3, 3), all_rules, [], [], appointment_type_id=99)
    # No rules for type 99, so use global
    assert windows == [(time(9, 0), time(17, 0))]


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
    # Slots step every 15 min; calendar busy checks prevent runtime conflicts
    windows = [(time(9, 0), time(11, 0))]
    slots = split_into_slots(windows, duration_minutes=60, buffer_before_minutes=0, buffer_after_minutes=0)
    assert time(9, 0) in slots
    assert time(9, 15) in slots
    assert time(10, 0) in slots
    assert time(10, 15) not in slots  # 10:15 + 60 min = 11:15, past window end


def test_split_respects_buffer_after():
    # buffer_after limits the last slot: last valid start is 9:45 (9:45+60+15=11:00 == window end)
    windows = [(time(9, 0), time(11, 0))]
    slots = split_into_slots(windows, duration_minutes=60, buffer_before_minutes=0, buffer_after_minutes=15)
    assert time(9, 0) in slots
    assert time(9, 45) in slots
    assert time(10, 0) not in slots  # 10:00 + 60 + 15 = 11:15, past window end


def test_split_respects_buffer_before():
    # 15-min buffer before: earliest appointment start is 9:15
    windows = [(time(9, 0), time(10, 0))]
    slots = split_into_slots(windows, duration_minutes=30, buffer_before_minutes=15, buffer_after_minutes=0)
    assert time(9, 0) not in slots   # window start, but buffer_before pushes earliest to 9:15
    assert time(9, 15) in slots
    assert time(9, 30) in slots      # 9:30 + 30 min = 10:00 == window end, still fits


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
    # Slots step every 15 min within the window (9:00-10:30 with 30-min duration)
    assert time(9, 0) in result
    assert time(9, 15) in result
    assert time(9, 30) in result
    assert time(9, 45) in result
    assert time(10, 0) in result
    assert time(10, 15) not in result  # 10:15 + 30 min = 10:45, past 10:30 window end


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


def test_split_aligns_slots_to_15min_boundaries():
    """After drive-time trimming shifts a window start to an odd time, slots must
    snap to 15-minute boundaries with a 3-minute round-down tolerance.
    e.g. 9:03 → 9:00 (within tolerance), 9:04-9:14 → 9:15 (past tolerance)."""
    # 9:03 is within 3-min tolerance of 9:00 → snaps down to 9:00
    windows_close = [(time(9, 3), time(10, 0))]
    slots_close = split_into_slots(windows_close, duration_minutes=30, buffer_before_minutes=0, buffer_after_minutes=0)
    assert time(9, 0) in slots_close, "9:03 should snap down to 9:00 (within 3-min tolerance)"

    # 9:21 is past the 3-min tolerance for 9:15 → snaps up to 9:30
    windows_far = [(time(9, 21), time(17, 0))]
    slots_far = split_into_slots(windows_far, duration_minutes=30, buffer_before_minutes=0, buffer_after_minutes=0)
    assert time(9, 21) not in slots_far, "9:21 should not appear as a slot"
    assert time(9, 30) in slots_far, "9:21 should snap up to 9:30"
    assert time(10, 0) in slots_far

    # Every slot should be on the 15-minute grid
    for s in slots_far:
        assert (s.hour * 60 + s.minute) % 15 == 0, f"Slot {s} is not on the 15-min grid"


def test_split_all_slots_on_15min_grid_with_non_15min_duration():
    """ALL slots must be on the 15-min grid even when duration is not a multiple of 15.
    The previous fix only snapped the first slot; subsequent slots incremented by
    slot_total (e.g. 20) and drifted off the grid (9:20, 9:40, ...)."""
    windows = [(time(9, 0), time(11, 0))]
    slots = split_into_slots(windows, duration_minutes=20, buffer_before_minutes=0, buffer_after_minutes=0)
    assert time(9, 20) not in slots, "9:20 should not appear — not on 15-min grid"
    assert time(9, 40) not in slots, "9:40 should not appear — not on 15-min grid"
    for s in slots:
        assert (s.hour * 60 + s.minute) % 15 == 0, f"Slot {s} is not on the 15-min grid"


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
