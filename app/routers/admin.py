import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from datetime import date as date_type
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import (
    get_conflict_calendars,
    get_email_config,
    get_setting,
    require_admin,
    require_csrf,
    set_conflict_calendars,
    set_setting,
)
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod, Booking
from app.services import email
from app.services.booking import cancel_booking, create_booking
from app.services.cache import availability_cache
from app.services.calendar import build_calendar_service
from app.services.scheduling import (
    create_drive_time_blocks,
    delete_booking_calendar_events,
    perform_reschedule,
)
from app.services.slots import compute_inspection_slots, compute_slots_for_type
from app.services.timeutils import get_timezone, local_to_utc
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")
AuthDep = Depends(require_admin)


def _validate_url(url: str) -> str:
    """Return the URL only if its scheme is http or https; blank it otherwise."""
    if not url:
        return ""
    scheme = urlparse(url).scheme.lower()
    return url if scheme in ("http", "https") else ""


def _flash(request: Request, message: str, type: str = "success"):
    request.session["flash"] = {"message": message, "type": type}


def _get_flash(request: Request):
    return request.session.pop("flash", None)


def _save_photo(photo: UploadFile, contents: bytes) -> str:
    """Store an uploaded photo under a random name; return the filename."""
    ext = os.path.splitext(photo.filename)[1].lower() or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    upload_dir = get_settings().upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    with open(os.path.join(upload_dir, filename), "wb") as f:
        f.write(contents)
    return filename


def _delete_photo(filename: str) -> None:
    if not filename:
        return
    path = os.path.join(get_settings().upload_dir, filename)
    if os.path.isfile(path):
        os.remove(path)


def _parse_rental_requirements(raw_json: str) -> list:
    try:
        return json.loads(raw_json)
    except (json.JSONDecodeError, ValueError):
        return []


# ---------- Dashboard ----------

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), _=AuthDep):
    now = datetime.utcnow()
    week_ahead = now + timedelta(days=7)
    upcoming_count = db.query(Booking).filter(
        Booking.status == "confirmed",
        Booking.start_datetime >= now,
        Booking.start_datetime <= week_ahead,
    ).count()
    total_count = db.query(Booking).filter_by(status="confirmed").count()
    next_bookings = (
        db.query(Booking)
        .filter(Booking.status == "confirmed", Booking.start_datetime >= now)
        .order_by(Booking.start_datetime)
        .limit(5)
        .all()
    )
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "upcoming_count": upcoming_count,
        "total_count": total_count,
        "next_bookings": next_bookings,
        "flash": _get_flash(request),
    })


# ---------- Appointment Types ----------

@router.get("/appointment-types", response_class=HTMLResponse)
def list_appt_types(request: Request, db: Session = Depends(get_db), _=AuthDep):
    types = db.query(AppointmentType).order_by(AppointmentType.id).all()
    return templates.TemplateResponse("admin/appointment_types.html", {
        "request": request, "types": types, "edit_type": None, "type_rules": [], "flash": _get_flash(request),
    })


@router.post("/appointment-types")
async def create_appt_type(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    duration_minutes: int = Form(...),
    buffer_before_minutes: int = Form(0),
    buffer_after_minutes: int = Form(0),
    calendar_id: str = Form("primary"),
    color: str = Form("#3b82f6"),
    location: str = Form(""),
    show_as: str = Form("busy"),
    visibility: str = Form("default"),
    owner_event_title: str = Form(""),
    guest_event_title: str = Form(""),
    requires_drive_time: str = Form("false"),
    calendar_window_enabled: str = Form("false"),
    calendar_window_title: str = Form(""),
    calendar_window_calendar_id: str = Form(""),
    listing_url: str = Form(""),
    rental_application_url: str = Form(""),
    rental_requirements_json: str = Form("[]"),
    owner_reminders_enabled: str = Form("false"),
    admin_initiated: str = Form("false"),
    max_concurrent: int = Form(1),
    photo: UploadFile | None = File(None),
    remove_photo: str = Form(""),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    is_admin_initiated = admin_initiated == "true"
    t = AppointmentType(
        name=name, description=description, duration_minutes=duration_minutes,
        buffer_before_minutes=buffer_before_minutes, buffer_after_minutes=buffer_after_minutes,
        calendar_id=calendar_id, color=color, location=location, show_as=show_as,
        visibility=visibility, owner_event_title=owner_event_title, guest_event_title=guest_event_title,
        admin_initiated=is_admin_initiated,
        requires_drive_time=is_admin_initiated or requires_drive_time == "true",
        calendar_window_enabled=(calendar_window_enabled == "true"),
        calendar_window_title=calendar_window_title,
        calendar_window_calendar_id=calendar_window_calendar_id,
        listing_url=_validate_url(listing_url),
        rental_application_url=_validate_url(rental_application_url),
        owner_reminders_enabled=(owner_reminders_enabled == "true"),
        max_concurrent=max(1, max_concurrent),
        active=True,
    )
    t.custom_fields = []
    t.rental_requirements = _parse_rental_requirements(rental_requirements_json)
    db.add(t)
    db.commit()
    db.refresh(t)
    if photo and photo.filename:
        t.photo_filename = _save_photo(photo, await photo.read())
        db.commit()
    _flash(request, f"Created '{name}'.")
    return RedirectResponse("/admin/appointment-types", status_code=302)


@router.get("/appointment-types/{type_id}/edit", response_class=HTMLResponse)
def edit_appt_type_page(
    request: Request, type_id: int, db: Session = Depends(get_db), _=AuthDep
):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    types = db.query(AppointmentType).order_by(AppointmentType.id).all()
    type_rules = (
        db.query(AvailabilityRule)
        .filter_by(appointment_type_id=type_id)
        .order_by(AvailabilityRule.day_of_week)
        .all()
        if t else []
    )
    return templates.TemplateResponse("admin/appointment_types.html", {
        "request": request, "types": types, "edit_type": t,
        "type_rules": type_rules, "flash": _get_flash(request),
    })


@router.post("/appointment-types/{type_id}")
async def update_appt_type(
    request: Request, type_id: int,
    name: str = Form(...), description: str = Form(""),
    duration_minutes: int = Form(...), buffer_before_minutes: int = Form(0),
    buffer_after_minutes: int = Form(0), calendar_id: str = Form("primary"),
    color: str = Form("#3b82f6"), location: str = Form(""),
    show_as: str = Form("busy"), visibility: str = Form("default"),
    owner_event_title: str = Form(""),
    guest_event_title: str = Form(""),
    requires_drive_time: str = Form("false"),
    calendar_window_enabled: str = Form("false"),
    calendar_window_title: str = Form(""),
    calendar_window_calendar_id: str = Form(""),
    listing_url: str = Form(""),
    rental_application_url: str = Form(""),
    rental_requirements_json: str = Form("[]"),
    owner_reminders_enabled: str = Form("false"),
    admin_initiated: str = Form("false"),
    max_concurrent: int = Form(1),
    photo: UploadFile | None = File(None),
    remove_photo: str = Form(""),
    db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    if t:
        t.name = name
        t.description = description
        t.duration_minutes = duration_minutes
        t.buffer_before_minutes = buffer_before_minutes
        t.buffer_after_minutes = buffer_after_minutes
        t.calendar_id = calendar_id
        t.color = color
        t.location = location
        t.show_as = show_as
        t.visibility = visibility
        t.owner_event_title = owner_event_title
        t.guest_event_title = guest_event_title
        t.admin_initiated = admin_initiated == "true"
        t.requires_drive_time = t.admin_initiated or requires_drive_time == "true"
        t.calendar_window_enabled = calendar_window_enabled == "true"
        t.calendar_window_title = calendar_window_title
        t.calendar_window_calendar_id = calendar_window_calendar_id
        t.listing_url = _validate_url(listing_url)
        t.rental_application_url = _validate_url(rental_application_url)
        t.owner_reminders_enabled = owner_reminders_enabled == "true"
        t.max_concurrent = max(1, max_concurrent)
        t.rental_requirements = _parse_rental_requirements(rental_requirements_json)
        if remove_photo == "true" and t.photo_filename:
            _delete_photo(t.photo_filename)
            t.photo_filename = ""
        elif photo and photo.filename:
            _delete_photo(t.photo_filename)
            t.photo_filename = _save_photo(photo, await photo.read())
        db.commit()
        _flash(request, f"Updated '{name}'.")
    return RedirectResponse("/admin/appointment-types", status_code=302)


@router.post("/appointment-types/{type_id}/toggle")
def toggle_appt_type(
    request: Request, type_id: int, db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    if t:
        t.active = not t.active
        db.commit()
        _flash(request, f"{'Enabled' if t.active else 'Disabled'} '{t.name}'.")
    return RedirectResponse("/admin/appointment-types", status_code=302)


@router.post("/appointment-types/{type_id}/rules")
def create_type_rule(
    request: Request,
    type_id: int,
    day_of_week: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    if t:
        db.add(AvailabilityRule(
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
            active=True,
            appointment_type_id=t.id,
        ))
        db.commit()
        _flash(request, "Availability window added.")
        return RedirectResponse(f"/admin/appointment-types/{t.id}/edit", status_code=302)
    return RedirectResponse("/admin/appointment-types", status_code=302)


@router.post("/appointment-types/{type_id}/rules/{rule_id}/delete")
def delete_type_rule(
    request: Request,
    type_id: int,
    rule_id: int,
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    rule = db.query(AvailabilityRule).filter_by(id=rule_id, appointment_type_id=type_id).first()
    if rule:
        appt_type_id = rule.appointment_type_id
        db.delete(rule)
        db.commit()
        _flash(request, "Rule deleted.")
        return RedirectResponse(f"/admin/appointment-types/{appt_type_id}/edit", status_code=302)
    return RedirectResponse("/admin/appointment-types", status_code=302)


# ---------- Availability ----------

@router.get("/availability", response_class=HTMLResponse)
def availability_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    rules = db.query(AvailabilityRule).order_by(AvailabilityRule.day_of_week).all()
    blocks = db.query(BlockedPeriod).order_by(BlockedPeriod.start_datetime).all()
    return templates.TemplateResponse("admin/availability.html", {
        "request": request, "rules": rules, "blocks": blocks,
        "min_advance": get_setting(db, "min_advance_hours", "24"),
        "max_future": get_setting(db, "max_future_days", "30"),
        "flash": _get_flash(request),
    })


@router.post("/availability/rules")
def create_rule(
    request: Request,
    day_of_week: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    db.add(AvailabilityRule(day_of_week=day_of_week, start_time=start_time, end_time=end_time, active=True))
    db.commit()
    _flash(request, "Availability rule added.")
    return RedirectResponse("/admin/availability", status_code=302)


@router.post("/availability/rules/{rule_id}/delete")
def delete_rule(
    request: Request, rule_id: int, db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    rule = db.query(AvailabilityRule).filter_by(id=rule_id).first()
    if rule:
        db.delete(rule)
        db.commit()
    _flash(request, "Rule deleted.")
    return RedirectResponse("/admin/availability", status_code=302)


@router.post("/availability/blocks")
def create_block(
    request: Request,
    start_datetime: str = Form(...),
    end_datetime: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    db.add(BlockedPeriod(
        start_datetime=datetime.fromisoformat(start_datetime),
        end_datetime=datetime.fromisoformat(end_datetime),
        reason=reason,
    ))
    db.commit()
    _flash(request, "Period blocked.")
    return RedirectResponse("/admin/availability", status_code=302)


@router.post("/availability/blocks/{block_id}/delete")
def delete_block(
    request: Request, block_id: int, db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    b = db.query(BlockedPeriod).filter_by(id=block_id).first()
    if b:
        db.delete(b)
        db.commit()
    _flash(request, "Block removed.")
    return RedirectResponse("/admin/availability", status_code=302)


@router.post("/availability/settings")
def save_availability_settings(
    request: Request,
    min_advance_hours: str = Form(...),
    max_future_days: str = Form(...),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    set_setting(db, "min_advance_hours", min_advance_hours)
    set_setting(db, "max_future_days", max_future_days)
    _flash(request, "Booking window settings saved.")
    return RedirectResponse("/admin/availability", status_code=302)


# ---------- Bookings ----------

@router.get("/bookings", response_class=HTMLResponse)
def bookings_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    now = datetime.utcnow()
    upcoming = (
        db.query(Booking)
        .filter(Booking.status == "confirmed", Booking.start_datetime >= now)
        .order_by(Booking.start_datetime)
        .all()
    )
    past = (
        db.query(Booking)
        .filter(Booking.start_datetime < now)
        .order_by(Booking.start_datetime.desc())
        .limit(50)
        .all()
    )

    # Identify bookings that overlap with another confirmed booking of the same type
    confirmed_shown = upcoming + [b for b in past if b.status == "confirmed"]
    group_showing_ids: set[int] = set()
    for b in confirmed_shown:
        for other in confirmed_shown:
            if (other.id != b.id
                    and other.appointment_type_id == b.appointment_type_id
                    and b.start_datetime < other.end_datetime
                    and b.end_datetime > other.start_datetime):
                group_showing_ids.add(b.id)
                break

    return templates.TemplateResponse("admin/bookings.html", {
        "request": request, "upcoming": upcoming, "past": past,
        "group_showing_ids": group_showing_ids, "flash": _get_flash(request),
    })


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking_route(
    request: Request, booking_id: int, db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    booking = db.query(Booking).filter_by(id=booking_id).first()
    if not booking:
        _flash(request, "Booking not found.", "error")
        return RedirectResponse("/admin/bookings", status_code=302)

    settings = get_settings()
    delete_booking_calendar_events(db, booking, settings)

    email_config = get_email_config(db, settings)
    if email_config.can_send:
        try:
            email.send_cancellation_notice(
                api_key=email_config.api_key,
                from_email=email_config.from_email,
                guest_email=booking.guest_email,
                guest_name=booking.guest_name,
                appt_type_name=booking.appointment_type.name,
                start_dt=booking.start_datetime,
                template=get_setting(db, "email_guest_cancellation", ""),
            )
        except Exception:
            logger.warning("Admin cancel: cancellation email failed", exc_info=True)

    cancel_booking(db, booking_id)
    _flash(request, f"Booking for {booking.guest_name} cancelled.")
    return RedirectResponse("/admin/bookings", status_code=302)


# ---------- Reschedule ----------

@router.get("/bookings/{booking_id}/reschedule/slots", response_class=HTMLResponse)
def admin_reschedule_slots(
    request: Request, booking_id: int, date: str = Query(""), db: Session = Depends(get_db), _=AuthDep,
):
    booking = db.query(Booking).filter_by(id=booking_id, status="confirmed").first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")
    if not date:
        return HTMLResponse("")
    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("")
    slot_data = compute_slots_for_type(
        booking.appointment_type, target_date, db,
        destination=booking.location, skip_advance_notice=True,
    )
    return templates.TemplateResponse(
        "booking/reschedule_slots_partial.html",
        {"request": request, "slots": slot_data},
    )


@router.get("/bookings/{booking_id}/reschedule", response_class=HTMLResponse)
def admin_reschedule_page(
    request: Request, booking_id: int, db: Session = Depends(get_db), _=AuthDep,
):
    booking = db.query(Booking).filter_by(id=booking_id, status="confirmed").first()
    if not booking:
        _flash(request, "Booking not found.", "error")
        return RedirectResponse("/admin/bookings", status_code=302)
    max_future = int(get_setting(db, "max_future_days", "30"))
    now = datetime.utcnow()
    return templates.TemplateResponse("admin/admin_reschedule.html", {
        "request": request,
        "booking": booking,
        "min_date": now.date().isoformat(),
        "max_date": (now + timedelta(days=max_future)).date().isoformat(),
        "current_display": booking.start_datetime.strftime("%A, %B %-d, %Y at %-I:%M %p"),
        "flash": _get_flash(request),
    })


@router.post("/bookings/{booking_id}/reschedule")
def admin_reschedule_booking(
    request: Request, booking_id: int, db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
    start_datetime: str = Form(...),
):
    booking = db.query(Booking).filter_by(id=booking_id, status="confirmed").first()
    if not booking:
        _flash(request, "Booking not found.", "error")
        return RedirectResponse("/admin/bookings", status_code=302)
    try:
        new_start_dt = datetime.fromisoformat(start_datetime)
    except (ValueError, TypeError):
        _flash(request, "Invalid date/time.", "error")
        return RedirectResponse(f"/admin/bookings/{booking.id}/reschedule", status_code=302)
    settings = get_settings()
    base_url = str(request.base_url).rstrip('/')
    try:
        perform_reschedule(db, booking, new_start_dt, settings, base_url)
        _flash(request, f"Booking for {booking.guest_name} rescheduled to {new_start_dt.strftime('%b %-d at %-I:%M %p')}.")
    except ValueError as exc:
        _flash(request, f"Reschedule failed: {exc}", "error")
    return RedirectResponse("/admin/bookings", status_code=302)


# ---------- Settings ----------

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    settings = get_settings()
    refresh_token = get_setting(db, "google_refresh_token", "")
    return templates.TemplateResponse("admin/settings.html", {
        "request": request,
        "owner_name": get_setting(db, "owner_name", ""),
        "notify_email": get_setting(db, "notify_email", ""),
        "notifications_enabled": get_setting(db, "notifications_enabled", "true") == "true",
        "timezone": get_setting(db, "timezone", "America/New_York"),
        "home_address": get_setting(db, "home_address", ""),
        "resend_api_key_set": bool(get_setting(db, "resend_api_key", settings.resend_api_key)),
        "from_email": get_setting(db, "from_email", settings.from_email),
        "contact_phone": get_setting(db, "contact_phone", ""),
        "google_authorized": bool(refresh_token),
        "conflict_cals": get_conflict_calendars(db),
        "email_guest_confirmation": get_setting(db, "email_guest_confirmation", ""),
        "email_admin_alert": get_setting(db, "email_admin_alert", ""),
        "email_guest_cancellation": get_setting(db, "email_guest_cancellation", ""),
        "flash": _get_flash(request),
    })


@router.post("/settings")
def save_settings(
    request: Request,
    owner_name: str = Form(""),
    notify_email: str = Form(""),
    notifications_enabled: str = Form("false"),
    timezone: str = Form("America/New_York"),
    home_address: str = Form(""),
    resend_api_key: str = Form(""),
    from_email: str = Form(""),
    contact_phone: str = Form(""),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    set_setting(db, "owner_name", owner_name)
    set_setting(db, "notify_email", notify_email)
    set_setting(db, "notifications_enabled", "true" if notifications_enabled == "true" else "false")
    set_setting(db, "timezone", timezone)
    set_setting(db, "home_address", home_address)
    set_setting(db, "contact_phone", contact_phone.strip())
    if resend_api_key.strip():
        set_setting(db, "resend_api_key", resend_api_key.strip())
    if from_email.strip():
        set_setting(db, "from_email", from_email.strip())
    _flash(request, "Settings saved.")
    return RedirectResponse("/admin/settings", status_code=302)


@router.post("/settings/conflict-calendars")
def add_conflict_calendar(
    request: Request,
    cal_type: str = Form(...),
    cal_id: str = Form(...),
    cal_name: str = Form(""),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    cals = get_conflict_calendars(db)
    cal_id = cal_id.strip()
    if cal_id:
        cals.append({"type": cal_type, "id": cal_id, "name": cal_name.strip() or cal_id})
        set_conflict_calendars(db, cals)
        _flash(request, "Conflict calendar added.")
    return RedirectResponse("/admin/settings", status_code=302)


@router.post("/settings/conflict-calendars/{index}/delete")
def delete_conflict_calendar(
    request: Request, index: int, db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    cals = get_conflict_calendars(db)
    if 0 <= index < len(cals):
        cals.pop(index)
        set_conflict_calendars(db, cals)
        _flash(request, "Conflict calendar removed.")
    return RedirectResponse("/admin/settings", status_code=302)


@router.post("/settings/email-templates")
def save_email_templates(
    request: Request,
    email_guest_confirmation: str = Form(""),
    email_admin_alert: str = Form(""),
    email_guest_cancellation: str = Form(""),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    set_setting(db, "email_guest_confirmation", email_guest_confirmation)
    set_setting(db, "email_admin_alert", email_admin_alert)
    set_setting(db, "email_guest_cancellation", email_guest_cancellation)
    _flash(request, "Email templates saved.")
    return RedirectResponse("/admin/settings", status_code=302)


# ---------- Google OAuth ----------

@router.get("/google/authorize")
def google_authorize(request: Request, _=AuthDep):
    cal = build_calendar_service(get_settings())
    url, state, code_verifier = cal.get_auth_url()
    request.session["oauth_state"] = state
    request.session["oauth_code_verifier"] = code_verifier
    return RedirectResponse(url, status_code=302)


@router.get("/google/callback")
def google_callback(
    request: Request, code: str, db: Session = Depends(get_db), _=AuthDep
):
    received_state = request.query_params.get("state", "")
    expected_state = request.session.pop("oauth_state", "")
    code_verifier = request.session.pop("oauth_code_verifier", "")
    if not expected_state or received_state != expected_state:
        _flash(request, "OAuth state mismatch — possible CSRF. Please try again.", "error")
        return RedirectResponse("/admin/settings", status_code=302)
    cal = build_calendar_service(get_settings())
    try:
        refresh_token = cal.exchange_code(code, code_verifier=code_verifier)
        set_setting(db, "google_refresh_token", refresh_token)
        _flash(request, "Google Calendar connected successfully.")
    except Exception as e:
        logger.warning("Google OAuth code exchange failed", exc_info=True)
        _flash(request, f"Google Calendar connection failed: {e}", "error")
    return RedirectResponse("/admin/settings", status_code=302)


# ---------- Schedule Inspection ----------

@router.get("/schedule-inspection", response_class=HTMLResponse)
def schedule_inspection_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    admin_types = (
        db.query(AppointmentType)
        .filter_by(active=True, admin_initiated=True)
        .order_by(AppointmentType.id)
        .all()
    )
    return templates.TemplateResponse("admin/schedule_inspection.html", {
        "request": request,
        "admin_types": admin_types,
        "today": date_type.today().isoformat(),
        "flash": _get_flash(request),
    })


@router.get("/inspection-slots", response_class=HTMLResponse)
def inspection_slots(
    request: Request,
    type_id: int = Query(...),
    date: str = Query(...),
    destination: str = Query(""),
    db: Session = Depends(get_db),
    _=AuthDep,
):
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True, admin_initiated=True).first()
    if not appt_type:
        return HTMLResponse("<p class='no-slots'>Appointment type not found.</p>")
    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("<p class='no-slots'>Invalid date.</p>")

    slot_data = compute_inspection_slots(appt_type, target_date, db, destination=destination)
    return templates.TemplateResponse("admin/inspection_slots_partial.html", {
        "request": request,
        "slots": slot_data,
        "type_id": type_id,
        "date": date,
        "destination": destination,
    })


@router.post("/schedule-inspection")
async def submit_inspection(
    request: Request,
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    form = await request.form()
    type_id_str = str(form.get("type_id", ""))
    destination = str(form.get("destination", "")).strip()
    start_datetime_str = str(form.get("start_datetime", ""))
    guest_name = str(form.get("guest_name", "")).strip()
    guest_email = str(form.get("guest_email", "")).strip()
    guest_phone = str(form.get("guest_phone", "")).strip()
    notes = str(form.get("notes", "")).strip()

    if not type_id_str or not destination or not start_datetime_str:
        _flash(request, "Missing required fields.", "error")
        return RedirectResponse("/admin/schedule-inspection", status_code=302)

    try:
        type_id = int(type_id_str)
        start_dt = datetime.fromisoformat(start_datetime_str)
    except (ValueError, TypeError):
        _flash(request, "Invalid data.", "error")
        return RedirectResponse("/admin/schedule-inspection", status_code=302)

    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True, admin_initiated=True).first()
    if not appt_type:
        _flash(request, "Appointment type not found.", "error")
        return RedirectResponse("/admin/schedule-inspection", status_code=302)

    end_dt = start_dt + timedelta(minutes=appt_type.duration_minutes)

    booking = create_booking(
        db=db,
        appt_type=appt_type,
        start_dt=start_dt,
        end_dt=end_dt,
        guest_name=guest_name or "N/A",
        guest_email=guest_email,
        guest_phone=guest_phone,
        notes=notes,
        custom_responses={},
        location=destination,
    )

    settings = get_settings()
    refresh_token = get_setting(db, "google_refresh_token", "")
    if refresh_token and settings.google_client_id:
        cal = build_calendar_service(settings)
        tz = get_timezone(db)
        start_utc = local_to_utc(start_dt, tz)
        end_utc = local_to_utc(end_dt, tz)

        description_lines = [f"Inspection at: {destination}"]
        if guest_name:
            description_lines.append(f"Contact: {guest_name}")
        if guest_email:
            description_lines.append(f"Email: {guest_email}")
        if guest_phone:
            description_lines.append(f"Phone: {guest_phone}")
        if notes:
            description_lines.append(f"Notes: {notes}")

        try:
            event_id = cal.create_event(
                refresh_token=refresh_token,
                calendar_id=appt_type.calendar_id,
                summary=appt_type.owner_event_title or f"Inspection — {destination}",
                description="\n".join(description_lines),
                start=start_utc,
                end=end_utc,
                location=destination,
                show_as=appt_type.show_as,
                visibility=appt_type.visibility,
                disable_reminders=True,
            )
            booking.google_event_id = event_id
            db.commit()
        except Exception:
            logger.warning("Inspection booking %s: calendar event creation failed", booking.id, exc_info=True)

        create_drive_time_blocks(
            cal=cal,
            refresh_token=refresh_token,
            calendar_id=appt_type.calendar_id,
            appt_name=appt_type.name,
            appt_location=destination,
            start_utc=start_utc,
            end_utc=end_utc,
            home_address=get_setting(db, "home_address", ""),
            db=db,
        )
        # The owner-calendar events created above postdate create_booking's
        # invalidation; clear again so /slots never serves the pre-event state.
        availability_cache.clear()

    start_display = start_dt.strftime("%A, %B %-d, %Y at %-I:%M %p")
    _flash(request, f"Inspection booked for {start_display} at {destination}.")
    return RedirectResponse("/admin/schedule-inspection", status_code=302)
