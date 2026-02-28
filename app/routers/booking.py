import os
from datetime import datetime, date as date_type, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_setting, require_csrf
from app.limiter import limiter
from app.models import AppointmentType, Booking
from app.routers.slots import _compute_slots_for_type
from app.services.booking import create_booking
from app.services.drive_time import get_drive_time

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
from app.dependencies import get_csrf_token as _get_csrf_token
templates.env.globals["csrf_token"] = _get_csrf_token


def _create_drive_time_blocks(
    cal,
    refresh_token: str,
    calendar_id: str,
    appt_name: str,
    appt_location: str,
    start_utc,
    end_utc,
    home_address: str,
    db,
) -> None:
    """Create BLOCK calendar events for drive time before and after the appointment.

    All datetimes must be naive UTC. Failures are fully silent — this is a
    best-effort calendar annotation, never blocking the booking confirmation.
    """
    window_start = start_utc - timedelta(hours=1)
    window_end = end_utc + timedelta(hours=1)

    try:
        nearby_events = cal.get_events_for_day(refresh_token, calendar_id, window_start, window_end)
    except Exception:
        return

    # --- Before block: drive TO this appointment ---
    preceding = None
    for ev in nearby_events:
        if window_start <= ev["end"] <= start_utc:
            if preceding is None or ev["end"] > preceding["end"]:
                preceding = ev

    origin = (preceding.get("location") or "").strip() if preceding else ""
    if not origin:
        origin = home_address

    if origin and origin.lower() != appt_location.lower():
        drive_mins = get_drive_time(origin, appt_location, db)
        if drive_mins > 0:
            try:
                cal.create_event(
                    refresh_token=refresh_token,
                    calendar_id=calendar_id,
                    summary=f"BLOCK - Drive Time for {appt_name}",
                    description="",
                    start=start_utc - timedelta(minutes=drive_mins),
                    end=start_utc,
                    show_as="busy",
                    disable_reminders=True,
                )
            except Exception:
                pass

    # --- After block: drive FROM this appointment to the next one ---
    following = None
    for ev in nearby_events:
        if end_utc <= ev["start"] <= window_end:
            if following is None or ev["start"] < following["start"]:
                following = ev

    if following:
        dest = (following.get("location") or "").strip()
        if dest and dest.lower() != appt_location.lower():
            drive_mins = get_drive_time(appt_location, dest, db)
            if drive_mins > 0:
                try:
                    cal.create_event(
                        refresh_token=refresh_token,
                        calendar_id=calendar_id,
                        summary=f"BLOCK - Drive Time for {following['summary']}",
                        description="",
                        start=end_utc,
                        end=end_utc + timedelta(minutes=drive_mins),
                        show_as="busy",
                        disable_reminders=True,
                    )
                except Exception:
                    pass


def _perform_reschedule(
    db: Session,
    booking: Booking,
    new_start_dt: datetime,
    settings,
    base_url: str,
) -> None:
    """Reschedule a booking to a new start time.

    Operation order (guards booking integrity):
    1. Create new calendar event — raises ValueError on failure (booking unchanged).
    2. Delete old calendar event — non-fatal (new event already exists).
    3. Update booking record in DB.
    4. Send new confirmation email — non-fatal.
    base_url: scheme + host with no trailing slash, e.g. "https://booking.devonwatkins.com"
    """
    from app.services.calendar import CalendarService

    appt_type = booking.appointment_type
    new_end_dt = new_start_dt + timedelta(minutes=appt_type.duration_minutes)

    tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))
    start_utc = new_start_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)
    end_utc = new_end_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)

    refresh_token = get_setting(db, "google_refresh_token", "")
    old_event_id = booking.google_event_id
    new_event_id = ""

    if refresh_token and settings.google_client_id:
        cal = CalendarService(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )
        description_lines = [
            f"Guest: {booking.guest_name}",
            f"Email: {booking.guest_email}",
            f"Phone: {booking.guest_phone or 'not provided'}",
            f"Notes: {booking.notes or 'none'}",
            "(Rescheduled)",
        ]
        try:
            new_event_id = cal.create_event(
                refresh_token=refresh_token,
                calendar_id=appt_type.calendar_id,
                summary=appt_type.owner_event_title or f"{appt_type.name} — {booking.guest_name}",
                description="\n".join(description_lines),
                start=start_utc,
                end=end_utc,
                attendee_email=booking.guest_email if not appt_type.admin_initiated else "",
                location=appt_type.location if not appt_type.admin_initiated else booking.location,
                show_as=appt_type.show_as,
                visibility=appt_type.visibility,
                disable_reminders=not appt_type.owner_reminders_enabled,
            )
        except Exception as exc:
            raise ValueError(f"Could not create a new calendar event: {exc}") from exc

        # Delete old event after new one is confirmed (non-fatal)
        if old_event_id:
            try:
                cal.delete_event(refresh_token, appt_type.calendar_id, old_event_id)
            except Exception:
                pass

    # Update booking
    booking.start_datetime = new_start_dt
    booking.end_datetime = new_end_dt
    booking.google_event_id = new_event_id
    db.commit()

    # Send new confirmation email (non-fatal; only if guest email present)
    if booking.guest_email:
        notify_enabled = get_setting(db, "notifications_enabled", "true") == "true"
        if notify_enabled and settings.resend_api_key:
            from app.services.email import send_guest_confirmation
            reschedule_url = base_url + f"/reschedule/{booking.reschedule_token}"
            try:
                send_guest_confirmation(
                    api_key=settings.resend_api_key,
                    from_email=settings.from_email,
                    guest_email=booking.guest_email,
                    guest_name=booking.guest_name,
                    appt_type_name=appt_type.guest_event_title or appt_type.name,
                    start_dt=new_start_dt,
                    end_dt=new_end_dt,
                    custom_responses=booking.custom_field_responses,
                    owner_name=get_setting(db, "owner_name", ""),
                    template=get_setting(db, "email_guest_confirmation", ""),
                    reschedule_url=reschedule_url,
                )
            except Exception:
                pass


@router.get("/reschedule/{token}/slots", response_class=HTMLResponse)
def reschedule_slots(
    request: Request,
    token: str,
    date: str,
    db: Session = Depends(get_db),
):
    booking = db.query(Booking).filter_by(reschedule_token=token, status="confirmed").first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")
    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("<p class='no-slots'>Invalid date format.</p>")

    slot_data = _compute_slots_for_type(
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
    booking = db.query(Booking).filter_by(reschedule_token=token, status="confirmed").first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found or already cancelled.")

    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    max_future = int(get_setting(db, "max_future_days", "30"))
    tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))
    now_local = datetime.now(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
    cutoff = now_local + timedelta(hours=min_advance)
    too_close = booking.start_datetime <= cutoff

    min_date = cutoff.date().isoformat()
    max_date = (now_local + timedelta(days=max_future)).date().isoformat()
    current_display = booking.start_datetime.strftime("%A, %B %-d, %Y at %-I:%M %p")

    return templates.TemplateResponse("booking/reschedule.html", {
        "request": request,
        "booking": booking,
        "token": token,
        "too_close": too_close,
        "min_advance_hours": min_advance,
        "min_date": min_date,
        "max_date": max_date,
        "current_display": current_display,
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

    booking = db.query(Booking).filter_by(reschedule_token=token, status="confirmed").first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")

    try:
        new_start_dt = datetime.fromisoformat(start_datetime_str)
    except (ValueError, TypeError):
        return templates.TemplateResponse("booking/reschedule.html", {
            "request": request, "booking": booking, "token": token,
            "too_close": False, "min_advance_hours": 24,
            "min_date": "", "max_date": "",
            "current_display": booking.start_datetime.strftime("%A, %B %-d, %Y at %-I:%M %p"),
            "error": "Invalid date/time. Please try again.",
        })

    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))
    now_local = datetime.now(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
    cutoff = now_local + timedelta(hours=min_advance)

    settings = get_settings()
    base_url = str(request.base_url).rstrip('/')
    try:
        _perform_reschedule(db, booking, new_start_dt, settings, base_url)
    except ValueError as exc:
        return templates.TemplateResponse("booking/reschedule.html", {
            "request": request, "booking": booking, "token": token,
            "too_close": False, "min_advance_hours": min_advance,
            "min_date": cutoff.date().isoformat(),
            "max_date": (now_local + timedelta(days=int(get_setting(db, "max_future_days", "30")))).date().isoformat(),
            "current_display": booking.start_datetime.strftime("%A, %B %-d, %Y at %-I:%M %p"),
            "error": str(exc),
        })

    new_display = new_start_dt.strftime("%A, %B %-d, %Y at %-I:%M %p")
    return templates.TemplateResponse("booking/reschedule_success.html", {
        "request": request,
        "booking": booking,
        "new_display": new_display,
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
                disable_reminders=not appt_type.owner_reminders_enabled,
            )
            booking.google_event_id = event_id
            db.commit()
        except Exception:
            pass  # Booking saved; calendar failure is non-fatal

        # Drive time block events (owner-only, non-fatal)
        if appt_type.requires_drive_time and appt_type.location:
            home_address = get_setting(db, "home_address", "")
            _create_drive_time_blocks(
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

    # Email notifications
    notify_email = get_setting(db, "notify_email", "")
    notifications_enabled = get_setting(db, "notifications_enabled", "true") == "true"
    owner_name = get_setting(db, "owner_name", "")
    guest_appt_name = appt_type.guest_event_title or appt_type.name
    if notifications_enabled and settings.resend_api_key:
        from app.services.email import send_guest_confirmation, send_admin_alert
        reschedule_url = str(request.base_url).rstrip('/') + f"/reschedule/{booking.reschedule_token}"
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
                template=get_setting(db, "email_guest_confirmation", ""),
                reschedule_url=reschedule_url,
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
                    template=get_setting(db, "email_admin_alert", ""),
                )
            except Exception:
                pass

    start_display = start_dt.strftime("%A, %B %-d, %Y at %-I:%M %p")
    return templates.TemplateResponse("booking/confirmation_partial.html", {
        "request": request,
        "booking": booking,
        "start_display": start_display,
    })
