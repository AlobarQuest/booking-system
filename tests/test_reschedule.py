import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType, Booking
from app.dependencies import require_csrf


def make_client_with_booking():
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
        admin_initiated=False,
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.commit()

    from datetime import datetime
    booking = Booking(
        appointment_type_id=appt.id,
        start_datetime=datetime(2025, 9, 1, 10, 0),
        end_datetime=datetime(2025, 9, 1, 10, 30),
        guest_name="Jane Smith",
        guest_email="jane@example.com",
        guest_phone="",
        notes="",
        status="confirmed",
        reschedule_token="test-token-1234-abcd-5678-efgh90123456",
    )
    booking._custom_field_responses = "{}"
    db.add(booking)
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


def test_reschedule_slots_returns_html():
    client, _ = make_client_with_booking()
    response = client.get(
        "/reschedule/test-token-1234-abcd-5678-efgh90123456/slots?date=2025-09-15"
    )
    assert response.status_code == 200
    assert "slot-btn" in response.text or "no-slots" in response.text
    app.dependency_overrides.clear()


def test_reschedule_slots_404_for_bad_token():
    client, _ = make_client_with_booking()
    response = client.get("/reschedule/bad-token/slots?date=2025-09-15")
    assert response.status_code == 404
    app.dependency_overrides.clear()


def test_reschedule_page_loads_for_valid_token():
    client, _ = make_client_with_booking()
    response = client.get("/reschedule/test-token-1234-abcd-5678-efgh90123456")
    assert response.status_code == 200
    assert "Jane Smith" in response.text or "Home Tour" in response.text
    app.dependency_overrides.clear()


def test_reschedule_page_404_for_invalid_token():
    client, _ = make_client_with_booking()
    response = client.get("/reschedule/no-such-token")
    assert response.status_code == 404
    app.dependency_overrides.clear()


def test_reschedule_page_too_close():
    from datetime import datetime, timedelta
    client, Session = make_client_with_booking()
    db = Session()
    from app.dependencies import set_setting
    set_setting(db, "min_advance_hours", "24")
    # Update booking start to be 1 hour from now (within cutoff)
    booking = db.query(Booking).first()
    booking.start_datetime = datetime.utcnow() + timedelta(hours=1)
    db.commit()
    db.close()

    response = client.get("/reschedule/test-token-1234-abcd-5678-efgh90123456")
    assert response.status_code == 200
    assert "cannot be rescheduled" in response.text.lower() or "contact" in response.text.lower()
    app.dependency_overrides.clear()


def test_reschedule_post_updates_booking():
    client, Session = make_client_with_booking()
    response = client.post(
        "/reschedule/test-token-1234-abcd-5678-efgh90123456",
        data={"start_datetime": "2025-09-20T14:00:00"},
        follow_redirects=False,
    )
    # Should return 200 (success page)
    assert response.status_code in (200, 302)

    from datetime import datetime
    db = Session()
    booking = db.query(Booking).first()
    assert booking.start_datetime == datetime(2025, 9, 20, 14, 0, 0)
    db.close()
    app.dependency_overrides.clear()


def test_reschedule_post_invalid_token():
    client, _ = make_client_with_booking()
    response = client.post(
        "/reschedule/bad-token",
        data={"start_datetime": "2025-09-20T14:00:00"},
    )
    assert response.status_code == 404
    app.dependency_overrides.clear()


def test_reschedule_creates_event_before_deleting_old():
    from unittest.mock import patch, MagicMock
    from app.config import Settings

    client, Session = make_client_with_booking()
    # Pre-set an old event ID
    db = Session()
    booking = db.query(Booking).first()
    booking.google_event_id = "old-event-id"
    db.commit()
    db.close()

    call_order = []
    mock_settings = Settings(
        google_client_id="fake-id", google_client_secret="fake-secret",
        google_redirect_uri="http://localhost/callback",
    )

    def fake_create(**kwargs):
        call_order.append("create")
        return "new-event-id"

    def fake_delete(refresh_token, calendar_id, event_id):
        call_order.append("delete")

    with patch("app.routers.booking.get_settings", return_value=mock_settings), \
         patch("app.services.calendar.CalendarService.create_event", side_effect=fake_create), \
         patch("app.services.calendar.CalendarService.delete_event", side_effect=fake_delete):
        from app.dependencies import set_setting
        db2 = Session()
        set_setting(db2, "google_refresh_token", "fake-token")
        db2.close()

        client.post(
            "/reschedule/test-token-1234-abcd-5678-efgh90123456",
            data={"start_datetime": "2025-09-20T14:00:00"},
        )

    assert call_order.index("create") < call_order.index("delete"), (
        f"Expected create before delete, got order: {call_order}"
    )
    app.dependency_overrides.clear()
