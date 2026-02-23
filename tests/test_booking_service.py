from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base
from app.models import AppointmentType, Booking
from app.services.booking import create_booking, cancel_booking


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_appt(db):
    appt = AppointmentType(
        name="Call", duration_minutes=30, buffer_before_minutes=0,
        buffer_after_minutes=0, calendar_id="primary",
        active=True, color="#fff", description="",
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.commit()
    return appt


def test_create_booking_saves_to_db():
    db = make_db()
    appt = make_appt(db)
    start = datetime(2025, 3, 3, 9, 0)
    end = datetime(2025, 3, 3, 9, 30)
    booking = create_booking(db, appt, start, end, "Jane", "jane@example.com", "555", "notes", {})
    assert booking.id is not None
    assert booking.status == "confirmed"
    assert booking.guest_name == "Jane"
    assert booking.guest_email == "jane@example.com"


def test_cancel_booking_updates_status():
    db = make_db()
    appt = make_appt(db)
    start = datetime(2025, 3, 3, 9, 0)
    end = datetime(2025, 3, 3, 9, 30)
    booking = create_booking(db, appt, start, end, "Jane", "jane@example.com", "", "", {})
    cancel_booking(db, booking.id)
    db.refresh(booking)
    assert booking.status == "cancelled"


def test_cancel_nonexistent_booking_returns_none():
    db = make_db()
    result = cancel_booking(db, 9999)
    assert result is None
