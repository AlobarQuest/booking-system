import bcrypt
import json
import os
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_setting, require_admin, require_csrf, set_setting
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod, Booking
from app.services.calendar import CalendarService

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["enumerate"] = enumerate
from app.dependencies import get_csrf_token as _get_csrf_token
templates.env.globals["csrf_token"] = _get_csrf_token
AuthDep = Depends(require_admin)


def _validate_url(url: str) -> str:
    """Return the URL only if its scheme is http or https; blank it otherwise."""
    if not url:
        return ""
    from urllib.parse import urlparse
    scheme = urlparse(url).scheme.lower()
    return url if scheme in ("http", "https") else ""


def _flash(request: Request, message: str, type: str = "success"):
    request.session["flash"] = {"message": message, "type": type}


def _get_flash(request: Request):
    return request.session.pop("flash", None)


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
    photo: UploadFile | None = File(None),
    remove_photo: str = Form(""),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    t = AppointmentType(
        name=name, description=description, duration_minutes=duration_minutes,
        buffer_before_minutes=buffer_before_minutes, buffer_after_minutes=buffer_after_minutes,
        calendar_id=calendar_id, color=color, location=location, show_as=show_as,
        visibility=visibility, owner_event_title=owner_event_title, guest_event_title=guest_event_title,
        admin_initiated=(admin_initiated == "true"),
        requires_drive_time=True if (admin_initiated == "true") else (requires_drive_time == "true"),
        calendar_window_enabled=(calendar_window_enabled == "true"),
        calendar_window_title=calendar_window_title,
        calendar_window_calendar_id=calendar_window_calendar_id,
        listing_url=_validate_url(listing_url),
        rental_application_url=_validate_url(rental_application_url),
        owner_reminders_enabled=(owner_reminders_enabled == "true"),
        active=True,
    )
    t.custom_fields = []
    try:
        t.rental_requirements = json.loads(rental_requirements_json)
    except (json.JSONDecodeError, ValueError):
        t.rental_requirements = []
    db.add(t)
    db.commit()
    db.refresh(t)
    if photo and photo.filename:
        from app.config import get_settings as _gs
        ext = os.path.splitext(photo.filename)[1].lower() or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        upload_dir = _gs().upload_dir
        os.makedirs(upload_dir, exist_ok=True)
        with open(os.path.join(upload_dir, filename), "wb") as f:
            f.write(await photo.read())
        t.photo_filename = filename
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
    photo: UploadFile | None = File(None),
    remove_photo: str = Form(""),
    db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    if t:
        t.name = name; t.description = description; t.duration_minutes = duration_minutes
        t.buffer_before_minutes = buffer_before_minutes
        t.buffer_after_minutes = buffer_after_minutes
        t.calendar_id = calendar_id; t.color = color
        t.location = location; t.show_as = show_as; t.visibility = visibility
        t.owner_event_title = owner_event_title
        t.guest_event_title = guest_event_title
        t.admin_initiated = (admin_initiated == "true")
        if t.admin_initiated:
            t.requires_drive_time = True
        else:
            t.requires_drive_time = (requires_drive_time == "true")
        t.calendar_window_enabled = (calendar_window_enabled == "true")
        t.calendar_window_title = calendar_window_title
        t.calendar_window_calendar_id = calendar_window_calendar_id
        t.listing_url = _validate_url(listing_url)
        t.rental_application_url = _validate_url(rental_application_url)
        t.owner_reminders_enabled = (owner_reminders_enabled == "true")
        try:
            t.rental_requirements = json.loads(rental_requirements_json)
        except (json.JSONDecodeError, ValueError):
            t.rental_requirements = []
        from app.config import get_settings as _gs
        upload_dir = _gs().upload_dir
        if remove_photo == "true" and (t.photo_filename or ""):
            old_path = os.path.join(upload_dir, t.photo_filename)
            if os.path.isfile(old_path):
                os.remove(old_path)
            t.photo_filename = ""
        elif photo and photo.filename:
            if t.photo_filename or "":
                old_path = os.path.join(upload_dir, t.photo_filename)
                if os.path.isfile(old_path):
                    os.remove(old_path)
            ext = os.path.splitext(photo.filename)[1].lower() or ".jpg"
            filename = f"{uuid.uuid4().hex}{ext}"
            os.makedirs(upload_dir, exist_ok=True)
            with open(os.path.join(upload_dir, filename), "wb") as f:
                f.write(await photo.read())
            t.photo_filename = filename
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
            appointment_type_id=type_id,
        ))
        db.commit()
        _flash(request, "Availability window added.")
    return RedirectResponse(f"/admin/appointment-types/{type_id}/edit", status_code=302)


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
        db.delete(rule)
        db.commit()
        _flash(request, "Rule deleted.")
    return RedirectResponse(f"/admin/appointment-types/{type_id}/edit", status_code=302)


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
    return templates.TemplateResponse("admin/bookings.html", {
        "request": request, "upcoming": upcoming, "past": past, "flash": _get_flash(request),
    })


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking_route(
    request: Request, booking_id: int, db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    from app.services.booking import cancel_booking
    booking = db.query(Booking).filter_by(id=booking_id).first()
    if not booking:
        _flash(request, "Booking not found.", "error")
        return RedirectResponse("/admin/bookings", status_code=302)

    settings = get_settings()
    refresh_token = get_setting(db, "google_refresh_token", "")
    if booking.google_event_id and refresh_token and settings.google_client_id:
        try:
            cal = CalendarService(
                settings.google_client_id,
                settings.google_client_secret,
                settings.google_redirect_uri,
            )
            cal.delete_event(refresh_token, booking.appointment_type.calendar_id, booking.google_event_id)
        except Exception:
            pass

    notify_enabled = get_setting(db, "notifications_enabled", "true") == "true"
    if notify_enabled and settings.resend_api_key:
        from app.services.email import send_cancellation_notice
        try:
            send_cancellation_notice(
                api_key=settings.resend_api_key,
                from_email=settings.from_email,
                guest_email=booking.guest_email,
                guest_name=booking.guest_name,
                appt_type_name=booking.appointment_type.name,
                start_dt=booking.start_datetime,
                template=get_setting(db, "email_guest_cancellation", ""),
            )
        except Exception:
            pass

    cancel_booking(db, booking_id)
    _flash(request, f"Booking for {booking.guest_name} cancelled.")
    return RedirectResponse("/admin/bookings", status_code=302)


# ---------- Settings ----------

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    import json as _json
    settings = get_settings()
    refresh_token = get_setting(db, "google_refresh_token", "")
    cal = CalendarService(
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_redirect_uri,
    )
    conflict_cals_raw = get_setting(db, "conflict_calendars", "[]")
    try:
        conflict_cals = _json.loads(conflict_cals_raw)
    except (ValueError, TypeError):
        conflict_cals = []
    return templates.TemplateResponse("admin/settings.html", {
        "request": request,
        "owner_name": get_setting(db, "owner_name", ""),
        "notify_email": get_setting(db, "notify_email", ""),
        "notifications_enabled": get_setting(db, "notifications_enabled", "true") == "true",
        "timezone": get_setting(db, "timezone", "America/New_York"),
        "home_address": get_setting(db, "home_address", ""),
        "google_authorized": cal.is_authorized(refresh_token),
        "conflict_cals": conflict_cals,
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
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    set_setting(db, "owner_name", owner_name)
    set_setting(db, "notify_email", notify_email)
    set_setting(db, "notifications_enabled", "true" if notifications_enabled == "true" else "false")
    set_setting(db, "timezone", timezone)
    set_setting(db, "home_address", home_address)
    _flash(request, "Settings saved.")
    return RedirectResponse("/admin/settings", status_code=302)


@router.post("/settings/password")
def change_password(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(...),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    if password != confirm:
        _flash(request, "Passwords do not match.", "error")
        return RedirectResponse("/admin/settings", status_code=302)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    set_setting(db, "admin_password_hash", hashed)
    _flash(request, "Password changed successfully.")
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
    import json as _json
    raw = get_setting(db, "conflict_calendars", "[]")
    try:
        cals = _json.loads(raw)
    except (ValueError, TypeError):
        cals = []
    cal_id = cal_id.strip()
    if cal_id:
        cals.append({"type": cal_type, "id": cal_id, "name": cal_name.strip() or cal_id})
        set_setting(db, "conflict_calendars", _json.dumps(cals))
        _flash(request, "Conflict calendar added.")
    return RedirectResponse("/admin/settings", status_code=302)


@router.post("/settings/conflict-calendars/{index}/delete")
def delete_conflict_calendar(
    request: Request, index: int, db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    import json as _json
    raw = get_setting(db, "conflict_calendars", "[]")
    try:
        cals = _json.loads(raw)
    except (ValueError, TypeError):
        cals = []
    if 0 <= index < len(cals):
        cals.pop(index)
        set_setting(db, "conflict_calendars", _json.dumps(cals))
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
    settings = get_settings()
    cal = CalendarService(
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_redirect_uri,
    )
    url, state = cal.get_auth_url()
    request.session["oauth_state"] = state
    return RedirectResponse(url, status_code=302)


@router.get("/google/callback")
def google_callback(
    request: Request, code: str, db: Session = Depends(get_db), _=AuthDep
):
    received_state = request.query_params.get("state", "")
    expected_state = request.session.pop("oauth_state", "")
    if not expected_state or received_state != expected_state:
        _flash(request, "OAuth state mismatch — possible CSRF. Please try again.", "error")
        return RedirectResponse("/admin/settings", status_code=302)
    settings = get_settings()
    cal = CalendarService(
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_redirect_uri,
    )
    try:
        refresh_token = cal.exchange_code(code)
        set_setting(db, "google_refresh_token", refresh_token)
        _flash(request, "Google Calendar connected successfully.")
    except Exception as e:
        _flash(request, f"Google Calendar connection failed: {e}", "error")
    return RedirectResponse("/admin/settings", status_code=302)


# ---------- Schedule Inspection ----------

@router.get("/schedule-inspection", response_class=HTMLResponse)
def schedule_inspection_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    from datetime import date as _date
    admin_types = (
        db.query(AppointmentType)
        .filter_by(active=True, admin_initiated=True)
        .order_by(AppointmentType.id)
        .all()
    )
    return templates.TemplateResponse("admin/schedule_inspection.html", {
        "request": request,
        "admin_types": admin_types,
        "today": _date.today().isoformat(),
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
    import json as _json
    from datetime import date as date_type, time as time_type, timedelta, timezone as dt_timezone
    from zoneinfo import ZoneInfo
    from app.models import AvailabilityRule, BlockedPeriod
    from app.services.availability import (
        _build_free_windows,
        intersect_windows,
        split_into_slots,
        filter_by_advance_notice,
        trim_windows_for_drive_time,
    )
    from app.services.calendar import CalendarService
    from app.config import get_settings

    settings = get_settings()
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True, admin_initiated=True).first()
    if not appt_type:
        return HTMLResponse("<p class='no-slots'>Appointment type not found.</p>")
    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("<p class='no-slots'>Invalid date.</p>")

    tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))
    local_midnight = datetime.combine(target_date, time_type(0, 0)).replace(tzinfo=tz)
    day_start = local_midnight.astimezone(dt_timezone.utc).replace(tzinfo=None)
    day_end = (local_midnight + timedelta(days=1)).astimezone(dt_timezone.utc).replace(tzinfo=None)

    busy_intervals = []
    local_day_events = []

    refresh_token = get_setting(db, "google_refresh_token", "")
    if refresh_token and settings.google_client_id:
        cal = CalendarService(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )
        try:
            utc_busy = cal.get_busy_intervals(
                refresh_token, [appt_type.calendar_id], day_start, day_end
            )
            for utc_start, utc_end in utc_busy:
                local_start = utc_start.replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                local_end = utc_end.replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                busy_intervals.append((local_start, local_end))
        except Exception:
            pass

        if destination:
            try:
                day_events_utc = cal.get_events_for_day(refresh_token, "primary", day_start, day_end)
                for ev in day_events_utc:
                    local_start = ev["start"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    local_end = ev["end"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    local_day_events.append({**ev, "start": local_start, "end": local_end})
            except Exception:
                pass

    rules = db.query(AvailabilityRule).filter_by(active=True).all()
    blocked = db.query(BlockedPeriod).all()
    windows = _build_free_windows(target_date, rules, blocked, busy_intervals, appointment_type_id=appt_type.id)

    if destination and windows:
        home_address = get_setting(db, "home_address", "")
        windows = trim_windows_for_drive_time(
            windows, target_date, local_day_events,
            destination=destination,
            home_address=home_address,
            db=db,
        )

    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    now_local = datetime.now(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
    slots = split_into_slots(
        windows, appt_type.duration_minutes,
        appt_type.buffer_before_minutes, appt_type.buffer_after_minutes,
    )
    slots = filter_by_advance_notice(slots, target_date, min_advance, now_local)

    slot_data = [
        {"value": s.strftime("%H:%M"), "display": s.strftime("%-I:%M %p")}
        for s in slots
    ]
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
    from datetime import timedelta, timezone as dt_timezone
    from zoneinfo import ZoneInfo
    from app.services.booking import create_booking
    from app.services.calendar import CalendarService
    from app.routers.booking import _create_drive_time_blocks

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
        cal = CalendarService(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )
        tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))
        start_utc = start_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)
        end_utc = end_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)

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
            pass

        home_address = get_setting(db, "home_address", "")
        _create_drive_time_blocks(
            cal=cal,
            refresh_token=refresh_token,
            calendar_id=appt_type.calendar_id,
            appt_name=appt_type.name,
            appt_location=destination,
            start_utc=start_utc,
            end_utc=end_utc,
            home_address=home_address,
            db=db,
        )

    start_display = start_dt.strftime("%A, %B %-d, %Y at %-I:%M %p")
    _flash(request, f"Inspection booked for {start_display} at {destination}.")
    return RedirectResponse("/admin/schedule-inspection", status_code=302)
