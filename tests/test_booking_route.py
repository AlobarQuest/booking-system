from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType


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
