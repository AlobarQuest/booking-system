import bcrypt
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Form, Request
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
def create_appt_type(
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
    db: Session = Depends(get_db),
    _=AuthDep,
):
    t = AppointmentType(
        name=name, description=description, duration_minutes=duration_minutes,
        buffer_before_minutes=buffer_before_minutes, buffer_after_minutes=buffer_after_minutes,
        calendar_id=calendar_id, color=color, location=location, show_as=show_as,
        visibility=visibility, active=True,
    )
    t.custom_fields = []
    db.add(t)
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
def update_appt_type(
    request: Request, type_id: int,
    name: str = Form(...), description: str = Form(""),
    duration_minutes: int = Form(...), buffer_before_minutes: int = Form(0),
    buffer_after_minutes: int = Form(0), calendar_id: str = Form("primary"),
    color: str = Form("#3b82f6"), location: str = Form(""),
    show_as: str = Form("busy"), visibility: str = Form("default"),
    db: Session = Depends(get_db), _=AuthDep,
):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    if t:
        t.name = name; t.description = description; t.duration_minutes = duration_minutes
        t.buffer_before_minutes = buffer_before_minutes
        t.buffer_after_minutes = buffer_after_minutes
        t.calendar_id = calendar_id; t.color = color
        t.location = location; t.show_as = show_as; t.visibility = visibility
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
            )
        except Exception:
            pass

    cancel_booking(db, booking_id)
    _flash(request, f"Booking for {booking.guest_name} cancelled.")
    return RedirectResponse("/admin/bookings", status_code=302)


# ---------- Settings ----------

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    settings = get_settings()
    refresh_token = get_setting(db, "google_refresh_token", "")
    cal = CalendarService(
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_redirect_uri,
    )
    return templates.TemplateResponse("admin/settings.html", {
        "request": request,
        "owner_name": get_setting(db, "owner_name", ""),
        "notify_email": get_setting(db, "notify_email", ""),
        "notifications_enabled": get_setting(db, "notifications_enabled", "true") == "true",
        "timezone": get_setting(db, "timezone", "America/New_York"),
        "google_authorized": cal.is_authorized(refresh_token),
        "flash": _get_flash(request),
    })


@router.post("/settings")
def save_settings(
    request: Request,
    owner_name: str = Form(""),
    notify_email: str = Form(""),
    notifications_enabled: str = Form("false"),
    timezone: str = Form("America/New_York"),
    db: Session = Depends(get_db),
    _=AuthDep,
):
    set_setting(db, "owner_name", owner_name)
    set_setting(db, "notify_email", notify_email)
    set_setting(db, "notifications_enabled", "true" if notifications_enabled == "true" else "false")
    set_setting(db, "timezone", timezone)
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
