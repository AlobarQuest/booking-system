import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType, Booking
from app.dependencies import require_csrf
from app.routers.admin import require_admin


def make_admin_client_with_booking():
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
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.commit()

    from datetime import datetime
    booking = Booking(
        appointment_type_id=appt.id,
        start_datetime=datetime(2025, 9, 1, 10, 0),
        end_datetime=datetime(2025, 9, 1, 10, 30),
        guest_name="Admin Test Guest",
        guest_email="admin_guest@example.com",
        guest_phone="",
        notes="",
        status="confirmed",
        reschedule_token="admin-token-1234-5678-abcd-efgh90123456",
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
    app.dependency_overrides[require_admin] = lambda: "admin"
    return TestClient(app), Session


def test_admin_reschedule_page_loads():
    client, Session = make_admin_client_with_booking()
    db = Session()
    booking_id = db.query(Booking).first().id
    db.close()

    response = client.get(f"/admin/bookings/{booking_id}/reschedule")
    assert response.status_code == 200
    assert "Admin Test Guest" in response.text
    assert "Home Tour" in response.text
    app.dependency_overrides.clear()


def test_admin_reschedule_updates_booking():
    client, Session = make_admin_client_with_booking()
    db = Session()
    booking_id = db.query(Booking).first().id
    db.close()

    response = client.post(
        f"/admin/bookings/{booking_id}/reschedule",
        data={"start_datetime": "2025-09-25T09:00:00"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/admin/bookings" in response.headers.get("location", "")

    from datetime import datetime
    db2 = Session()
    booking = db2.query(Booking).filter_by(id=booking_id).first()
    assert booking.start_datetime == datetime(2025, 9, 25, 9, 0, 0)
    db2.close()
    app.dependency_overrides.clear()


def test_admin_cancel_deletes_drive_time_events():
    """Admin cancel route should delete drive time block events stored on the booking."""
    from unittest.mock import patch, MagicMock

    client, Session = make_admin_client_with_booking()
    db = Session()
    booking = db.query(Booking).first()
    booking_id = booking.id
    booking.google_event_id = "main-admin-event-id"
    booking._drive_time_event_ids = '["dt-admin-before", "dt-admin-after"]'
    db.commit()
    db.close()

    deleted_ids = []

    def fake_delete(refresh_token, calendar_id, event_id):
        deleted_ids.append(event_id)

    mock_settings = MagicMock()
    mock_settings.google_client_id = "fake-id"
    mock_settings.google_client_secret = "fake-secret"
    mock_settings.google_redirect_uri = "http://localhost/callback"
    mock_settings.resend_api_key = ""
    mock_settings.from_email = "test@example.com"

    with patch("app.routers.admin.get_settings", return_value=mock_settings), \
         patch("app.services.calendar.CalendarService.delete_event", side_effect=fake_delete):
        db2 = Session()
        from app.dependencies import set_setting
        set_setting(db2, "google_refresh_token", "fake-token")
        db2.commit()
        db2.close()

        response = client.post(
            f"/admin/bookings/{booking_id}/cancel",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "main-admin-event-id" in deleted_ids
    assert "dt-admin-before" in deleted_ids
    assert "dt-admin-after" in deleted_ids
    app.dependency_overrides.clear()


def test_admin_cancel_deletes_drive_time_events_even_if_main_delete_fails():
    """Drive time block events should be deleted even if the main event deletion raises."""
    from unittest.mock import patch, MagicMock

    client, Session = make_admin_client_with_booking()
    db = Session()
    booking = db.query(Booking).first()
    booking_id = booking.id
    booking.google_event_id = "main-event-that-will-404"
    booking._drive_time_event_ids = '["dt-resilient-before", "dt-resilient-after"]'
    db.commit()
    db.close()

    deleted_ids = []

    def fake_delete(refresh_token, calendar_id, event_id):
        if event_id == "main-event-that-will-404":
            raise Exception("404 Not Found")
        deleted_ids.append(event_id)

    mock_settings = MagicMock()
    mock_settings.google_client_id = "fake-id"
    mock_settings.google_client_secret = "fake-secret"
    mock_settings.google_redirect_uri = "http://localhost/callback"
    mock_settings.resend_api_key = ""
    mock_settings.from_email = "test@example.com"

    with patch("app.routers.admin.get_settings", return_value=mock_settings), \
         patch("app.services.calendar.CalendarService.delete_event", side_effect=fake_delete):
        db2 = Session()
        from app.dependencies import set_setting
        set_setting(db2, "google_refresh_token", "fake-token")
        db2.commit()
        db2.close()

        response = client.post(
            f"/admin/bookings/{booking_id}/cancel",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "dt-resilient-before" in deleted_ids
    assert "dt-resilient-after" in deleted_ids
    app.dependency_overrides.clear()


def test_admin_reschedule_slots_bypass_advance_notice():
    """Admin slot endpoint should return slots even inside the advance-notice window.

    Sets min_advance_hours=999 (far future) so no slots pass the public
    advance-notice filter, then verifies the admin slots endpoint still returns
    HTML (not an empty no-slots response) for a date with availability rules.
    """
    from app.dependencies import set_setting

    client, Session = make_admin_client_with_booking()
    db = Session()
    booking_id = db.query(Booking).first().id
    # Set a huge advance notice so public slots would all be blocked
    set_setting(db, "min_advance_hours", "999")
    # Add an availability rule so there are actual slots to compute
    from app.models import AvailabilityRule
    rule = AvailabilityRule(
        day_of_week=0,  # Monday
        start_time="09:00",
        end_time="17:00",
        active=True,
    )
    db.add(rule)
    db.commit()
    db.close()

    # Find the next Monday at least 1 day from today so the 0h past-filter doesn't remove all slots
    from datetime import date, timedelta
    today = date.today()
    days_ahead = (0 - today.weekday()) % 7 or 7
    next_monday = (today + timedelta(days=days_ahead)).isoformat()

    response = client.get(f"/admin/bookings/{booking_id}/reschedule/slots?date={next_monday}")
    assert response.status_code == 200
    # Should have slot buttons, not just "no available times"
    assert "slot-btn" in response.text
    app.dependency_overrides.clear()
