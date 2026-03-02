from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType
from app.dependencies import require_csrf


def setup_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    appt = AppointmentType(
        name="Phone Call", duration_minutes=30, buffer_before_minutes=0,
        buffer_after_minutes=0, calendar_id="primary",
        active=True, color="#3b82f6", description="Quick call",
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.commit()
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[require_csrf] = lambda: None
    return TestClient(app), Session


def test_booking_page_shows_appointment_types():
    client, _ = setup_client()
    response = client.get("/book")
    assert response.status_code == 200
    assert "Phone Call" in response.text
    app.dependency_overrides.clear()


def test_root_redirects_or_shows_booking():
    client, _ = setup_client()
    response = client.get("/")
    assert response.status_code == 200
    app.dependency_overrides.clear()


def test_form_partial_returns_html():
    client, Session = setup_client()
    db = Session()
    appt_id = db.query(AppointmentType).first().id
    db.close()
    response = client.get(f"/book/form?type_id={appt_id}&date=2025-03-03&time=09:00")
    assert response.status_code == 200
    assert "guest_name" in response.text
    assert "Phone Call" in response.text
    app.dependency_overrides.clear()


def test_submit_booking_creates_booking():
    client, Session = setup_client()
    db = Session()
    appt_id = db.query(AppointmentType).first().id
    db.close()

    response = client.post("/book", data={
        "type_id": str(appt_id),
        "start_datetime": "2025-03-03T09:00:00",
        "guest_name": "Jane Smith",
        "guest_email": "jane@example.com",
        "guest_phone": "555-1234",
        "notes": "Test booking",
    })
    assert response.status_code == 200
    assert "Confirmed" in response.text

    from app.models import Booking
    db2 = Session()
    booking = db2.query(Booking).first()
    assert booking is not None
    assert booking.guest_name == "Jane Smith"
    assert booking.status == "confirmed"
    db2.close()
    app.dependency_overrides.clear()


def test_submit_booking_calendar_event_uses_utc():
    """Calendar create_event must receive UTC datetimes, not local naive datetimes.

    9:30 AM on 2025-03-03 in America/New_York (EST = UTC-5) should produce
    a calendar event starting at 14:30 UTC.
    """
    from unittest.mock import patch
    from datetime import datetime
    from app.config import Settings
    from app.dependencies import set_setting

    client, Session = setup_client()
    db = Session()
    set_setting(db, "timezone", "America/New_York")
    set_setting(db, "google_refresh_token", "fake-refresh-token")
    appt_id = db.query(AppointmentType).first().id
    db.close()

    mock_settings = Settings(
        google_client_id="fake-client-id",
        google_client_secret="fake-secret",
        google_redirect_uri="http://localhost/callback",
    )

    with patch("app.routers.booking.get_settings", return_value=mock_settings), \
         patch("app.services.calendar.CalendarService.create_event", return_value="evt-id") as mock_create:
        response = client.post("/book", data={
            "type_id": str(appt_id),
            "start_datetime": "2025-03-03T09:30:00",  # 9:30 AM EST (UTC-5)
            "guest_name": "Test User",
            "guest_email": "test@example.com",
            "guest_phone": "555-1234",
        })
    assert response.status_code == 200
    assert mock_create.called
    # start kwarg should be 14:30 UTC (9:30 EST + 5h = 14:30 UTC)
    start_arg = mock_create.call_args.kwargs["start"]
    assert start_arg == datetime(2025, 3, 3, 14, 30, 0), (
        f"Expected 14:30 UTC but got {start_arg} — calendar event is using local time instead of UTC"
    )
    app.dependency_overrides.clear()


def test_submit_booking_conflict_returns_error():
    client, Session = setup_client()
    db = Session()
    appt_id = db.query(AppointmentType).first().id
    db.close()

    data = {
        "type_id": str(appt_id),
        "start_datetime": "2025-03-03T10:00:00",
        "guest_name": "Jane Smith",
        "guest_email": "jane@example.com",
    }
    client.post("/book", data=data)
    # Second booking for same slot
    response = client.post("/book", data=data)
    assert response.status_code == 200
    assert "just booked" in response.text.lower() or "error" in response.text.lower()
    app.dependency_overrides.clear()


def test_booking_has_reschedule_token():
    client, Session = setup_client()
    db = Session()
    appt_id = db.query(AppointmentType).first().id
    db.close()

    client.post("/book", data={
        "type_id": str(appt_id),
        "start_datetime": "2025-06-01T10:00:00",
        "guest_name": "Token Test",
        "guest_email": "token@example.com",
        "guest_phone": "555-1234",
    })

    import uuid as _uuid
    from app.models import Booking
    db2 = Session()
    booking = db2.query(Booking).first()
    assert booking is not None
    _uuid.UUID(booking.reschedule_token, version=4)  # raises ValueError if invalid UUID4
    db2.close()
    app.dependency_overrides.clear()


def test_booking_model_has_drive_time_event_ids():
    from app.models import Booking
    b = Booking()
    b._drive_time_event_ids = "[]"
    assert b.drive_time_event_ids == []
    b.drive_time_event_ids = ["abc", "def"]
    assert b._drive_time_event_ids == '["abc", "def"]'


def test_create_drive_time_blocks_returns_list():
    """_create_drive_time_blocks should return a list (possibly empty)."""
    from unittest.mock import MagicMock, patch
    from app.routers.booking import _create_drive_time_blocks
    import datetime

    cal = MagicMock()
    cal.get_events_for_day.return_value = []
    cal.create_event.return_value = "event-id-123"

    with patch("app.routers.booking.get_drive_time", return_value=0):
        result = _create_drive_time_blocks(
            cal=cal,
            refresh_token="tok",
            calendar_id="primary",
            appt_name="Home Tour",
            appt_location="456 Elm St",
            start_utc=datetime.datetime(2030, 9, 20, 14, 0),
            end_utc=datetime.datetime(2030, 9, 20, 14, 30),
            home_address="123 Main St",
            db=MagicMock(),
        )

    assert isinstance(result, list)


def test_booking_requires_phone():
    """Submitting /book without a phone number should return a validation error."""
    client, Session = setup_client()
    db = Session()
    appt_type = db.query(AppointmentType).first()
    appt_id = appt_type.id
    db.close()

    response = client.post("/book", data={
        "type_id": str(appt_id),
        "start_datetime": "2030-09-20T14:00:00",
        "guest_name": "Jane Smith",
        "guest_email": "jane@example.com",
        "guest_phone": "",
    })
    assert response.status_code == 200
    # Should get an error partial, not a confirmation
    assert "confirmation" not in response.text.lower()
    assert "fill in" in response.text.lower() or "required" in response.text.lower()
    app.dependency_overrides.clear()


def test_appointment_type_has_max_concurrent():
    from app.models import AppointmentType
    # Verify round-trip persistence of max_concurrent column
    _, Session = setup_client()
    db = Session()
    at = AppointmentType(
        name="Group Test", duration_minutes=30,
        buffer_before_minutes=0, buffer_after_minutes=0,
        calendar_id="primary", active=True, color="#3b82f6",
        max_concurrent=3,
    )
    at._custom_fields = "[]"
    db.add(at)
    db.commit()
    at_id = at.id
    db.close()

    db2 = Session()
    reloaded = db2.query(AppointmentType).filter_by(id=at_id).first()
    assert reloaded.max_concurrent == 3
    db2.close()
    app.dependency_overrides.clear()


def test_confirmation_email_includes_reschedule_link():
    from unittest.mock import patch
    from app.config import Settings
    from app.models import Booking

    client, Session = setup_client()
    db = Session()
    appt_id = db.query(AppointmentType).first().id
    db.close()

    mock_settings = Settings(
        resend_api_key="fake-key",
        from_email="from@example.com",
    )

    with patch("app.routers.booking.get_settings", return_value=mock_settings), \
         patch("app.services.email.resend.Emails.send") as mock_send:
        client.post("/book", data={
            "type_id": str(appt_id),
            "start_datetime": "2025-06-01T11:00:00",
            "guest_name": "Link Test",
            "guest_email": "link@example.com",
            "guest_phone": "555-1234",
        })

    assert mock_send.called
    sent_html = mock_send.call_args[0][0]["html"]

    db2 = Session()
    booking = db2.query(Booking).filter_by(guest_email="link@example.com").first()
    db2.close()
    assert booking is not None
    assert f"/reschedule/{booking.reschedule_token}" in sent_html
    app.dependency_overrides.clear()
