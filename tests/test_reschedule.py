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
