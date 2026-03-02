from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType, Booking, AvailabilityRule
from app.dependencies import require_csrf, set_setting


# 2030-09-16 is a Monday (day_of_week=0). Far enough in the future
# that advance-notice filtering (default 24h) will never block these slots.
TEST_DATE = "2030-09-16"
BOOKING_START = datetime(2030, 9, 16, 13, 0)   # 1:00 PM
BOOKING_END   = datetime(2030, 9, 16, 13, 30)  # 1:30 PM


def make_group_client(max_concurrent: int = 2):
    """Return (client, Session, type_id).

    Sets up an in-memory DB with:
    - One appointment type (Home Tour, 30 min, max_concurrent=<arg>)
    - One Monday availability rule 09:00-17:00
    - One confirmed booking at 1:00-1:30 PM on 2030-09-16
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    appt = AppointmentType(
        name="Home Tour", duration_minutes=30,
        buffer_before_minutes=0, buffer_after_minutes=0,
        calendar_id="primary", active=True, color="#3b82f6",
        location="123 Main St",
        max_concurrent=max_concurrent,
    )
    appt._custom_fields = "[]"
    db.add(appt)

    # Monday rule
    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True)
    db.add(rule)
    db.commit()

    existing = Booking(
        appointment_type_id=appt.id,
        start_datetime=BOOKING_START,
        end_datetime=BOOKING_END,
        guest_name="First Guest",
        guest_email="first@example.com",
        guest_phone="555-0001",
        status="confirmed",
    )
    existing._custom_field_responses = "{}"
    db.add(existing)
    db.commit()

    type_id = appt.id
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[require_csrf] = lambda: None
    return TestClient(app), Session, type_id


def _mock_cal_returning_booking_as_busy():
    """Return a mock CalendarService whose get_busy_intervals reports 1:00-1:30 PM as busy."""
    mock_cal = MagicMock()
    # Google Calendar returns the booking time as busy (naive datetimes = UTC when tz=UTC)
    mock_cal.get_busy_intervals.return_value = [
        (BOOKING_START, BOOKING_END),
    ]
    mock_cal.get_events_for_day.return_value = []
    return mock_cal


def _patch_slots_settings(mock_settings_fn):
    mock_settings_fn.return_value.google_client_id = "fake-id"
    mock_settings_fn.return_value.google_client_secret = "fake-secret"
    mock_settings_fn.return_value.google_redirect_uri = "http://localhost"


# ---- Tests ----------------------------------------------------------------

def test_group_showing_slot_appears_when_one_booking_exists():
    """max_concurrent=2 with 1 booking: the time slot should still be available."""
    client, Session, type_id = make_group_client(max_concurrent=2)

    db = Session()
    set_setting(db, "google_refresh_token", "fake-token")
    set_setting(db, "timezone", "UTC")
    db.commit()
    db.close()

    mock_cal = _mock_cal_returning_booking_as_busy()

    with patch("app.routers.slots.CalendarService", return_value=mock_cal), \
         patch("app.routers.slots.get_settings") as ms:
        _patch_slots_settings(ms)
        response = client.get(f"/slots?type_id={type_id}&date={TEST_DATE}")

    assert response.status_code == 200
    assert "1:00 PM" in response.text, \
        "1:00 PM slot should be available (only 1 of 2 concurrent slots taken)"
    app.dependency_overrides.clear()


def test_group_showing_slot_blocked_at_capacity():
    """max_concurrent=2 with 2 bookings: the time slot should NOT be available."""
    client, Session, type_id = make_group_client(max_concurrent=2)

    db = Session()
    set_setting(db, "google_refresh_token", "fake-token")
    set_setting(db, "timezone", "UTC")
    # Second booking — now at capacity
    second = Booking(
        appointment_type_id=type_id,
        start_datetime=BOOKING_START,
        end_datetime=BOOKING_END,
        guest_name="Second Guest",
        guest_email="second@example.com",
        guest_phone="555-0002",
        status="confirmed",
    )
    second._custom_field_responses = "{}"
    db.add(second)
    db.commit()
    db.close()

    mock_cal = _mock_cal_returning_booking_as_busy()

    with patch("app.routers.slots.CalendarService", return_value=mock_cal), \
         patch("app.routers.slots.get_settings") as ms:
        _patch_slots_settings(ms)
        response = client.get(f"/slots?type_id={type_id}&date={TEST_DATE}")

    assert response.status_code == 200
    assert "1:00 PM" not in response.text, \
        "1:00 PM slot should be blocked (at capacity: 2 of 2 taken)"
    app.dependency_overrides.clear()


def test_standard_type_blocks_slot_normally():
    """max_concurrent=1 (default): existing booking still blocks the slot."""
    client, Session, type_id = make_group_client(max_concurrent=1)

    db = Session()
    set_setting(db, "google_refresh_token", "fake-token")
    set_setting(db, "timezone", "UTC")
    db.commit()
    db.close()

    mock_cal = _mock_cal_returning_booking_as_busy()

    with patch("app.routers.slots.CalendarService", return_value=mock_cal), \
         patch("app.routers.slots.get_settings") as ms:
        _patch_slots_settings(ms)
        response = client.get(f"/slots?type_id={type_id}&date={TEST_DATE}")

    assert response.status_code == 200
    assert "1:00 PM" not in response.text, \
        "1:00 PM slot should be blocked for standard type (max_concurrent=1)"
    app.dependency_overrides.clear()


def test_group_showing_skips_drive_time_blocks():
    """A group showing booking should not trigger _create_drive_time_blocks."""
    from app.limiter import limiter
    limiter._storage.reset()

    client, Session, type_id = make_group_client(max_concurrent=2)

    # Enable drive time on the appointment type
    db = Session()
    appt = db.query(AppointmentType).filter_by(id=type_id).first()
    appt.requires_drive_time = True
    db.commit()
    db.close()

    block_calls = []

    def fake_blocks(*args, **kwargs):
        block_calls.append(True)
        return []

    mock_settings = MagicMock()
    mock_settings.google_client_id = "fake-id"
    mock_settings.google_client_secret = "fake-secret"
    mock_settings.google_redirect_uri = "http://localhost"
    mock_settings.resend_api_key = ""
    mock_settings.from_email = "test@example.com"

    with patch("app.routers.booking.get_settings", return_value=mock_settings), \
         patch("app.routers.booking._create_drive_time_blocks", side_effect=fake_blocks), \
         patch("app.services.calendar.CalendarService.create_event", return_value="evt-id"):
        db2 = Session()
        set_setting(db2, "google_refresh_token", "fake-token")
        db2.commit()
        db2.close()

        response = client.post("/book", data={
            "type_id": str(type_id),
            "start_datetime": BOOKING_START.isoformat(),
            "guest_name": "Second Guest",
            "guest_email": "second@example.com",
            "guest_phone": "555-9999",
        })

    assert response.status_code == 200
    assert len(block_calls) == 0, \
        "Drive time block events must NOT be created for a group showing"
    app.dependency_overrides.clear()


def test_first_group_showing_booking_still_creates_drive_time_blocks():
    """The first booking on a group-showing type must still trigger drive time blocks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    appt = AppointmentType(
        name="First Tour", duration_minutes=30,
        buffer_before_minutes=0, buffer_after_minutes=0,
        calendar_id="primary", active=True, color="#3b82f6",
        location="456 Oak Ave",
        max_concurrent=2,
        requires_drive_time=True,
    )
    appt._custom_fields = "[]"
    db.add(appt)
    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True)
    db.add(rule)
    db.commit()
    type_id = appt.id
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[require_csrf] = lambda: None
    client = TestClient(app)

    block_calls = []

    def fake_blocks(*args, **kwargs):
        block_calls.append(True)
        return []

    mock_settings = MagicMock()
    mock_settings.google_client_id = "fake-id"
    mock_settings.google_client_secret = "fake-secret"
    mock_settings.google_redirect_uri = "http://localhost"
    mock_settings.resend_api_key = ""
    mock_settings.from_email = "test@example.com"

    with patch("app.routers.booking.get_settings", return_value=mock_settings), \
         patch("app.routers.booking._create_drive_time_blocks", side_effect=fake_blocks), \
         patch("app.services.calendar.CalendarService.create_event", return_value="evt-id"):
        db2 = Session()
        set_setting(db2, "google_refresh_token", "fake-token")
        db2.commit()
        db2.close()

        response = client.post("/book", data={
            "type_id": str(type_id),
            "start_datetime": BOOKING_START.isoformat(),
            "guest_name": "First Guest",
            "guest_email": "first@example.com",
            "guest_phone": "555-1111",
        })

    assert response.status_code == 200
    assert len(block_calls) == 1, \
        "Drive time block events MUST be created for the first booking on a group-showing type"
    app.dependency_overrides.clear()
