import bcrypt
import json
import os
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_setting, require_admin, set_setting
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod, Booking
from app.services.calendar import CalendarService

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["enumerate"] = enumerate
AuthDep = Depends(require_admin)


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
        "request": request, "types": types, "edit_type": None, "flash": _get_flash(request),
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
    photo: UploadFile | None = File(None),
    remove_photo: str = Form(""),
    db: Session = Depends(get_db),
    _=AuthDep,
):
    t = AppointmentType(
        name=name, description=description, duration_minutes=duration_minutes,
        buffer_before_minutes=buffer_before_minutes, buffer_after_minutes=buffer_after_minutes,
        calendar_id=calendar_id, color=color, location=location, show_as=show_as,
        visibility=visibility, owner_event_title=owner_event_title, guest_event_title=guest_event_title,
        requires_drive_time=(requires_drive_time == "true"),
        calendar_window_enabled=(calendar_window_enabled == "true"),
        calendar_window_title=calendar_window_title,
        calendar_window_calendar_id=calendar_window_calendar_id,
        listing_url=listing_url,
        rental_application_url=rental_application_url,
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
    return templates.TemplateResponse("admin/appointment_types.html", {
        "request": request, "types": types, "edit_type": t, "flash": _get_flash(request),
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
    photo: UploadFile | None = File(None),
    remove_photo: str = Form(""),
    db: Session = Depends(get_db), _=AuthDep,
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
        t.requires_drive_time = (requires_drive_time == "true")
        t.calendar_window_enabled = (calendar_window_enabled == "true")
        t.calendar_window_title = calendar_window_title
        t.calendar_window_calendar_id = calendar_window_calendar_id
        t.listing_url = listing_url
        t.rental_application_url = rental_application_url
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
    request: Request, type_id: int, db: Session = Depends(get_db), _=AuthDep
):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    if t:
        t.active = not t.active
        db.commit()
        _flash(request, f"{'Enabled' if t.active else 'Disabled'} '{t.name}'.")
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
):
    db.add(AvailabilityRule(day_of_week=day_of_week, start_time=start_time, end_time=end_time, active=True))
    db.commit()
    _flash(request, "Availability rule added.")
    return RedirectResponse("/admin/availability", status_code=302)


@router.post("/availability/rules/{rule_id}/delete")
def delete_rule(request: Request, rule_id: int, db: Session = Depends(get_db), _=AuthDep):
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
    request: Request, block_id: int, db: Session = Depends(get_db), _=AuthDep
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
    request: Request, booking_id: int, db: Session = Depends(get_db), _=AuthDep
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
    request: Request, index: int, db: Session = Depends(get_db), _=AuthDep
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
):
    set_setting(db, "email_guest_confirmation", email_guest_confirmation)
    set_setting(db, "email_admin_alert", email_admin_alert)
    set_setting(db, "email_guest_cancellation", email_guest_cancellation)
    _flash(request, "Email templates saved.")
    return RedirectResponse("/admin/settings", status_code=302)


# ---------- Google OAuth ----------

@router.get("/google/authorize")
def google_authorize(_=AuthDep):
    settings = get_settings()
    cal = CalendarService(
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_redirect_uri,
    )
    url = cal.get_auth_url()
    return RedirectResponse(url, status_code=302)


@router.get("/google/callback")
def google_callback(
    request: Request, code: str, db: Session = Depends(get_db), _=AuthDep
):
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
