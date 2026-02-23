from datetime import datetime
from sqlalchemy.orm import Session
from app.models import AppointmentType, Booking


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
        status="confirmed",
    )
    booking.custom_field_responses = custom_responses
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking


def cancel_booking(db: Session, booking_id: int) -> Booking | None:
    booking = db.query(Booking).filter_by(id=booking_id).first()
    if booking:
        booking.status = "cancelled"
        db.commit()
        db.refresh(booking)
    return booking
