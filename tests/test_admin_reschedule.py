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
