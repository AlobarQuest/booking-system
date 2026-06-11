import threading
import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import AppointmentType, Booking
from app.services.cache import availability_cache

# Serializes the capacity check + insert so two simultaneous guests cannot
# both pass the max_concurrent check for the same slot. A process-level lock
# is sufficient because the app deliberately runs as a single writer process
# (one container, SQLite). If the deployment ever moves to multiple
# processes/Postgres, replace this with a database-level guard
# (BEGIN IMMEDIATE on SQLite, SELECT ... FOR UPDATE on Postgres).
_booking_write_lock = threading.Lock()


def create_booking(
    db: Session,
    appt_type: AppointmentType,
    start_dt: datetime,
    end_dt: datetime,
    guest_name: str,
    guest_email: str,
    guest_phone: str,
    notes: str,
    custom_responses: dict,
    google_event_id: str = "",
    location: str = "",
) -> Booking:
    booking = Booking(
        appointment_type_id=appt_type.id,
        start_datetime=start_dt,
        end_datetime=end_dt,
        guest_name=guest_name,
        guest_email=guest_email,
        guest_phone=guest_phone,
        notes=notes,
        google_event_id=google_event_id,
        location=location,
        status="confirmed",
        reschedule_token=str(uuid.uuid4()),
    )
    booking.custom_field_responses = custom_responses
    db.add(booking)
    db.commit()
    db.refresh(booking)
    availability_cache.clear()
    return booking


def count_overlapping_confirmed(
    db: Session, appointment_type_id: int, start_dt: datetime, end_dt: datetime
) -> int:
    return db.query(Booking).filter(
        Booking.appointment_type_id == appointment_type_id,
        Booking.status == "confirmed",
        Booking.start_datetime < end_dt,
        Booking.end_datetime > start_dt,
    ).count()


def try_create_booking(
    db: Session,
    appt_type: AppointmentType,
    start_dt: datetime,
    end_dt: datetime,
    guest_name: str,
    guest_email: str,
    guest_phone: str,
    notes: str,
    custom_responses: dict,
    location: str = "",
) -> Booking | None:
    """Atomically check slot capacity and create the booking.

    Returns None when the slot is already at appt_type.max_concurrent
    confirmed bookings. The check and the insert run under a write lock so
    concurrent submissions for the same slot cannot both succeed.
    """
    with _booking_write_lock:
        overlap_count = count_overlapping_confirmed(db, appt_type.id, start_dt, end_dt)
        if overlap_count >= appt_type.max_concurrent:
            return None
        return create_booking(
            db=db,
            appt_type=appt_type,
            start_dt=start_dt,
            end_dt=end_dt,
            guest_name=guest_name,
            guest_email=guest_email,
            guest_phone=guest_phone,
            notes=notes,
            custom_responses=custom_responses,
            location=location,
        )


def cancel_booking(db: Session, booking_id: int) -> Booking | None:
    booking = db.query(Booking).filter_by(id=booking_id).first()
    if booking:
        booking.status = "cancelled"
        db.commit()
        db.refresh(booking)
        availability_cache.clear()
    return booking
