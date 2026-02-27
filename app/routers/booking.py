import os
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_setting
from app.limiter import limiter
from app.models import AppointmentType, Booking
from app.services.booking import create_booking

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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
    appointment_types = db.query(AppointmentType).filter_by(active=True).all()
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
):
    form_data = await request.form()
    type_id_str = form_data.get("type_id", "")
    start_datetime_str = form_data.get("start_datetime", "")
    guest_name = str(form_data.get("guest_name", "")).strip()
    guest_email = str(form_data.get("guest_email", "")).strip()
    guest_phone = str(form_data.get("guest_phone", "")).strip()
    notes = str(form_data.get("notes", "")).strip()

    if not all([type_id_str, start_datetime_str, guest_name, guest_email]):
        return templates.TemplateResponse("booking/error_partial.html", {
            "request": request, "message": "Please fill in all required fields."
        })

    try:
        type_id = int(type_id_str)
        start_dt = datetime.fromisoformat(start_datetime_str)
    except (ValueError, TypeError):
        return templates.TemplateResponse("booking/error_partial.html", {
            "request": request, "message": "Invalid booking data."
        })

    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return templates.TemplateResponse("booking/error_partial.html", {
            "request": request, "message": "Appointment type not found."
        })

    end_dt = start_dt + timedelta(minutes=appt_type.duration_minutes)

    # Check for conflicts
    conflict = db.query(Booking).filter(
        Booking.appointment_type_id == type_id,
        Booking.status == "confirmed",
        Booking.start_datetime < end_dt,
        Booking.end_datetime > start_dt,
    ).first()
    if conflict:
        return templates.TemplateResponse("booking/error_partial.html", {
            "request": request,
            "message": "That time slot was just booked. Please go back and choose another.",
        })

    # Extract custom field responses
    custom_responses = {}
    for field in appt_type.custom_fields:
        key = f"custom_{field['label']}"
        custom_responses[field["label"]] = str(form_data.get(key, ""))

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
        from app.services.calendar import CalendarService
        cal = CalendarService(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )
        description_lines = [
            f"Guest: {guest_name}",
            f"Email: {guest_email}",
            f"Phone: {guest_phone or 'not provided'}",
            f"Notes: {notes or 'none'}",
        ]
        for k, v in custom_responses.items():
            description_lines.append(f"{k}: {v}")
        # start_dt/end_dt are naive local datetimes; convert to naive UTC for the calendar API
        tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))
        start_utc = start_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)
        end_utc = end_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)
        try:
            event_id = cal.create_event(
                refresh_token=refresh_token,
                calendar_id=appt_type.calendar_id,
                summary=appt_type.owner_event_title or f"{appt_type.name} — {guest_name}",
                description="\n".join(description_lines),
                start=start_utc,
                end=end_utc,
                attendee_email=guest_email,
                location=appt_type.location,
                show_as=appt_type.show_as,
                visibility=appt_type.visibility,
            )
            booking.google_event_id = event_id
            db.commit()
        except Exception:
            pass  # Booking saved; calendar failure is non-fatal

    # Email notifications
    notify_email = get_setting(db, "notify_email", "")
    notifications_enabled = get_setting(db, "notifications_enabled", "true") == "true"
    owner_name = get_setting(db, "owner_name", "")
    guest_appt_name = appt_type.guest_event_title or appt_type.name
    if notifications_enabled and settings.resend_api_key:
        from app.services.email import send_guest_confirmation, send_admin_alert
        try:
            send_guest_confirmation(
                api_key=settings.resend_api_key,
                from_email=settings.from_email,
                guest_email=guest_email,
                guest_name=guest_name,
                appt_type_name=guest_appt_name,
                start_dt=start_dt,
                end_dt=end_dt,
                custom_responses=custom_responses,
                owner_name=owner_name,
            )
        except Exception:
            pass
        if notify_email:
            try:
                send_admin_alert(
                    api_key=settings.resend_api_key,
                    from_email=settings.from_email,
                    notify_email=notify_email,
                    guest_name=guest_name,
                    guest_email=guest_email,
                    guest_phone=guest_phone,
                    appt_type_name=guest_appt_name,
                    start_dt=start_dt,
                    notes=notes,
                    custom_responses=custom_responses,
                )
            except Exception:
                pass

    start_display = start_dt.strftime("%A, %B %-d, %Y at %-I:%M %p")
    return templates.TemplateResponse("booking/confirmation_partial.html", {
        "request": request,
        "booking": booking,
        "start_display": start_display,
    })
