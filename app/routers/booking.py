import logging
import os
from datetime import datetime, date as date_type, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_email_config, get_setting, require_csrf
from app.limiter import limiter
from app.models import AppointmentType, Booking
from app.services import email
from app.services.booking import create_booking
from app.services.calendar import build_calendar_service
from app.services.scheduling import (
    create_drive_time_blocks,
    delete_booking_calendar_events,
    perform_reschedule,
)
from app.services.slots import compute_slots_for_type
from app.services.timeutils import get_timezone, local_to_utc, now_local
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

RESCHEDULE_TOKEN_ERROR = (
    "This link has already been used or the appointment was cancelled. "
    "If you need to book a new appointment, use the link below."
)
CANCEL_TOKEN_ERROR = (
    "This appointment has already been cancelled or this link has expired. "
    "If you need to make changes to a current booking, please contact us."
)


def _booking_by_token(db: Session, token: str) -> Booking | None:
    return db.query(Booking).filter_by(reschedule_token=token, status="confirmed").first()


def _token_error(request: Request, message: str):
    return templates.TemplateResponse("booking/token_error.html", {
        "request": request,
        "message": message,
    })


def _format_booking_start(booking: Booking) -> str:
    return booking.start_datetime.strftime("%A, %B %-d, %Y at %-I:%M %p")


@router.get("/reschedule/{token}/slots", response_class=HTMLResponse)
def reschedule_slots(
    request: Request,
    token: str,
    date: str,
    db: Session = Depends(get_db),
):
    booking = _booking_by_token(db, token)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")
    if not date:
        return HTMLResponse("")
    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("")

    slot_data = compute_slots_for_type(
        booking.appointment_type,
        target_date,
        db,
        destination=booking.location,
    )
    return templates.TemplateResponse(
        "booking/reschedule_slots_partial.html",
        {"request": request, "slots": slot_data},
    )


@router.get("/reschedule/{token}", response_class=HTMLResponse)
def reschedule_page(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    booking = _booking_by_token(db, token)
    if not booking:
        return _token_error(request, RESCHEDULE_TOKEN_ERROR)

    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    max_future = int(get_setting(db, "max_future_days", "30"))
    now = now_local(get_timezone(db))
    cutoff = now + timedelta(hours=min_advance)

    return templates.TemplateResponse("booking/reschedule.html", {
        "request": request,
        "booking": booking,
        "token": token,
        "too_close": booking.start_datetime <= cutoff,
        "min_advance_hours": min_advance,
        "min_date": cutoff.date().isoformat(),
        "max_date": (now + timedelta(days=max_future)).date().isoformat(),
        "current_display": _format_booking_start(booking),
    })


@router.post("/reschedule/{token}", response_class=HTMLResponse)
@limiter.limit("10/hour")
async def submit_reschedule(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    _csrf_ok: None = Depends(require_csrf),
):
    form_data = await request.form()
    start_datetime_str = str(form_data.get("start_datetime", "")).strip()

    booking = _booking_by_token(db, token)
    if not booking:
        return _token_error(request, RESCHEDULE_TOKEN_ERROR)

    def _reschedule_form(**overrides):
        context = {
            "request": request, "booking": booking, "token": token,
            "too_close": False, "min_advance_hours": 24,
            "min_date": "", "max_date": "",
            "current_display": _format_booking_start(booking),
        }
        context.update(overrides)
        return templates.TemplateResponse("booking/reschedule.html", context)

    try:
        new_start_dt = datetime.fromisoformat(start_datetime_str)
    except (ValueError, TypeError):
        return _reschedule_form(error="Invalid date/time. Please try again.")

    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    max_future = int(get_setting(db, "max_future_days", "30"))
    now = now_local(get_timezone(db))
    cutoff = now + timedelta(hours=min_advance)
    window_context = {
        "min_advance_hours": min_advance,
        "min_date": cutoff.date().isoformat(),
        "max_date": (now + timedelta(days=max_future)).date().isoformat(),
    }
    if new_start_dt <= cutoff:
        return _reschedule_form(too_close=True, **window_context)

    settings = get_settings()
    base_url = str(request.base_url).rstrip('/')
    try:
        perform_reschedule(db, booking, new_start_dt, settings, base_url)
    except ValueError as exc:
        return _reschedule_form(error=str(exc), **window_context)

    return templates.TemplateResponse("booking/reschedule_success.html", {
        "request": request,
        "booking": booking,
        "new_display": new_start_dt.strftime("%A, %B %-d, %Y at %-I:%M %p"),
    })


@router.get("/cancel/{token}", response_class=HTMLResponse)
def cancel_page(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    booking = _booking_by_token(db, token)
    if not booking:
        return _token_error(request, CANCEL_TOKEN_ERROR)
    return templates.TemplateResponse("booking/cancel_confirm.html", {
        "request": request,
        "booking": booking,
        "token": token,
        "current_display": _format_booking_start(booking),
    })


@router.post("/cancel/{token}", response_class=HTMLResponse)
@limiter.limit("10/hour")
async def submit_cancel(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    _csrf_ok: None = Depends(require_csrf),
):
    booking = _booking_by_token(db, token)
    if not booking:
        return _token_error(request, CANCEL_TOKEN_ERROR)

    appt_type = booking.appointment_type
    settings = get_settings()

    delete_booking_calendar_events(db, booking, settings)

    # Send cancellation email (non-fatal)
    email_config = get_email_config(db, settings)
    if email_config.can_send and booking.guest_email:
        try:
            email.send_cancellation_notice(
                api_key=email_config.api_key,
                from_email=email_config.from_email,
                guest_email=booking.guest_email,
                guest_name=booking.guest_name,
                appt_type_name=appt_type.guest_event_title or appt_type.name,
                start_dt=booking.start_datetime,
                template=get_setting(db, "email_guest_cancellation", ""),
            )
        except Exception:
            logger.warning("Cancel: cancellation email failed", exc_info=True)

    booking.status = "cancelled"
    db.commit()

    return templates.TemplateResponse("booking/cancel_success.html", {
        "request": request,
        "booking": booking,
    })


@router.get("/uploads/{filename}")
def serve_upload(filename: str):
    settings = get_settings()
    upload_dir = os.path.realpath(settings.upload_dir)
    path = os.path.realpath(os.path.join(upload_dir, filename))
    if not path.startswith(upload_dir + os.sep):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


@router.get("/", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    return _booking_page(request, db)


@router.get("/book", response_class=HTMLResponse)
def booking_page(request: Request, db: Session = Depends(get_db)):
    return _booking_page(request, db)


def _booking_page(request: Request, db: Session):
    appointment_types = db.query(AppointmentType).filter_by(active=True, admin_initiated=False).all()
    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    max_future = int(get_setting(db, "max_future_days", "30"))
    min_date = (datetime.utcnow() + timedelta(hours=min_advance)).date().isoformat()
    max_date = (datetime.utcnow() + timedelta(days=max_future)).date().isoformat()
    return templates.TemplateResponse("booking/index.html", {
        "request": request,
        "appointment_types": appointment_types,
        "min_date": min_date,
        "max_date": max_date,
    })


@router.get("/book/form", response_class=HTMLResponse)
def booking_form(
    request: Request,
    type_id: int,
    date: str,
    time: str,
    db: Session = Depends(get_db),
):
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return templates.TemplateResponse("booking/error_partial.html", {
            "request": request, "message": "Appointment type not found."
        })
    try:
        start_dt = datetime.fromisoformat(f"{date}T{time}:00")
    except ValueError:
        return templates.TemplateResponse("booking/error_partial.html", {
            "request": request, "message": "Invalid date or time."
        })
    return templates.TemplateResponse("booking/form_partial.html", {
        "request": request,
        "appt_type": appt_type,
        "date_display": start_dt.strftime("%A, %B %-d, %Y"),
        "time_display": start_dt.strftime("%-I:%M %p"),
        "start_datetime": f"{date}T{time}:00",
    })


@router.post("/book", response_class=HTMLResponse)
@limiter.limit("10/hour")
async def submit_booking(
    request: Request,
    db: Session = Depends(get_db),
    _csrf_ok: None = Depends(require_csrf),
):
    form_data = await request.form()
    type_id_str = form_data.get("type_id", "")
    start_datetime_str = form_data.get("start_datetime", "")
    guest_name = str(form_data.get("guest_name", "")).strip()
    guest_email = str(form_data.get("guest_email", "")).strip()
    guest_phone = str(form_data.get("guest_phone", "")).strip()
    notes = str(form_data.get("notes", "")).strip()

    def _error(message: str):
        return templates.TemplateResponse("booking/error_partial.html", {
            "request": request, "message": message
        })

    if not all([type_id_str, start_datetime_str, guest_name, guest_email, guest_phone]):
        return _error("Please fill in all required fields.")

    try:
        type_id = int(type_id_str)
        start_dt = datetime.fromisoformat(start_datetime_str)
    except (ValueError, TypeError):
        return _error("Invalid booking data.")

    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return _error("Appointment type not found.")

    end_dt = start_dt + timedelta(minutes=appt_type.duration_minutes)

    # Check for conflicts — respect max_concurrent for group showings
    overlap_count = db.query(Booking).filter(
        Booking.appointment_type_id == type_id,
        Booking.status == "confirmed",
        Booking.start_datetime < end_dt,
        Booking.end_datetime > start_dt,
    ).count()
    if overlap_count >= appt_type.max_concurrent:
        return _error("That time slot was just booked. Please go back and choose another.")

    # Extract custom field responses
    custom_responses = {
        field["label"]: str(form_data.get(f"custom_{field['label']}", ""))
        for field in appt_type.custom_fields
    }

    booking = create_booking(
        db=db,
        appt_type=appt_type,
        start_dt=start_dt,
        end_dt=end_dt,
        guest_name=guest_name,
        guest_email=guest_email,
        guest_phone=guest_phone,
        notes=notes,
        custom_responses=custom_responses,
    )

    # Google Calendar event creation
    settings = get_settings()
    refresh_token = get_setting(db, "google_refresh_token", "")
    if refresh_token and settings.google_client_id:
        cal = build_calendar_service(settings)
        description_lines = [
            f"Guest: {guest_name}",
            f"Email: {guest_email}",
            f"Phone: {guest_phone or 'not provided'}",
            f"Notes: {notes or 'none'}",
        ]
        for k, v in custom_responses.items():
            description_lines.append(f"{k}: {v}")
        # start_dt/end_dt are naive local datetimes; convert to naive UTC for the calendar API
        tz = get_timezone(db)
        start_utc = local_to_utc(start_dt, tz)
        end_utc = local_to_utc(end_dt, tz)
        try:
            event_id = cal.create_event(
                refresh_token=refresh_token,
                calendar_id=appt_type.calendar_id,
                summary=appt_type.owner_event_title or f"{appt_type.name} — {guest_name}",
                description="\n".join(description_lines),
                start=start_utc,
                end=end_utc,
                attendee_email="",
                location=appt_type.location,
                show_as=appt_type.show_as,
                visibility=appt_type.visibility,
                disable_reminders=not appt_type.owner_reminders_enabled,
            )
            booking.google_event_id = event_id
            db.commit()
        except Exception:
            # Booking saved; calendar failure is non-fatal
            logger.warning("Booking %s: calendar event creation failed", booking.id, exc_info=True)

        # Drive time block events (owner-only, non-fatal)
        # Skip if this is a group showing — owner is already at the location.
        is_group_showing = appt_type.max_concurrent > 1 and db.query(Booking).filter(
            Booking.appointment_type_id == appt_type.id,
            Booking.status == "confirmed",
            Booking.id != booking.id,
            Booking.start_datetime < booking.end_datetime,
            Booking.end_datetime > booking.start_datetime,
        ).first() is not None
        if appt_type.requires_drive_time and appt_type.location and not is_group_showing:
            home_address = get_setting(db, "home_address", "")
            dt_ids = create_drive_time_blocks(
                cal=cal,
                refresh_token=refresh_token,
                calendar_id=appt_type.calendar_id,
                appt_name=appt_type.name,
                appt_location=appt_type.location,
                start_utc=start_utc,
                end_utc=end_utc,
                home_address=home_address,
                db=db,
            )
            if dt_ids:
                booking.drive_time_event_ids = dt_ids
                db.commit()

    # Email notifications
    notify_email = get_setting(db, "notify_email", "")
    owner_name = get_setting(db, "owner_name", "")
    guest_appt_name = appt_type.guest_event_title or appt_type.name
    email_config = get_email_config(db, settings)
    if email_config.can_send:
        base_url = str(request.base_url).rstrip('/')
        try:
            email.send_guest_confirmation(
                api_key=email_config.api_key,
                from_email=email_config.from_email,
                guest_email=guest_email,
                guest_name=guest_name,
                appt_type_name=guest_appt_name,
                start_dt=start_dt,
                end_dt=end_dt,
                custom_responses=custom_responses,
                owner_name=owner_name,
                template=get_setting(db, "email_guest_confirmation", ""),
                reschedule_url=f"{base_url}/reschedule/{booking.reschedule_token}",
                cancel_url=f"{base_url}/cancel/{booking.reschedule_token}",
                location=appt_type.location or "",
                contact_phone=get_setting(db, "contact_phone", ""),
            )
        except Exception:
            logger.warning("Booking %s: guest confirmation email failed", booking.id, exc_info=True)
        if notify_email:
            try:
                email.send_admin_alert(
                    api_key=email_config.api_key,
                    from_email=email_config.from_email,
                    notify_email=notify_email,
                    guest_name=guest_name,
                    guest_email=guest_email,
                    guest_phone=guest_phone,
                    appt_type_name=guest_appt_name,
                    start_dt=start_dt,
                    notes=notes,
                    custom_responses=custom_responses,
                    template=get_setting(db, "email_admin_alert", ""),
                    location=appt_type.location or "",
                )
            except Exception:
                logger.warning("Booking %s: admin alert email failed", booking.id, exc_info=True)

    return templates.TemplateResponse("booking/confirmation_partial.html", {
        "request": request,
        "booking": booking,
        "start_display": start_dt.strftime("%A, %B %-d, %Y at %-I:%M %p"),
        "contact_phone": get_setting(db, "contact_phone", ""),
    })
