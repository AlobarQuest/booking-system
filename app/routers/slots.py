from datetime import datetime, date as date_type, time as time_type, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_setting
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod
from app.services.availability import compute_slots
from app.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/slots", response_class=HTMLResponse)
def get_slots(
    request: Request,
    type_id: int = Query(...),
    date: str = Query(...),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return HTMLResponse("<p class='no-slots'>Appointment type not found.</p>")

    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("<p class='no-slots'>Invalid date format.</p>")
    rules = db.query(AvailabilityRule).filter_by(active=True).all()
    blocked = db.query(BlockedPeriod).all()
    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    refresh_token = get_setting(db, "google_refresh_token", "")

    tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))

    # Load conflict calendars from settings
    import json as _json
    conflict_cals_raw = get_setting(db, "conflict_calendars", "[]")
    try:
        conflict_cals = _json.loads(conflict_cals_raw)
    except (ValueError, TypeError):
        conflict_cals = []

    # Collect additional Google calendar IDs and webcal URLs from global conflict list
    extra_google_ids = [c["id"] for c in conflict_cals if c.get("type") == "google" and c.get("id")]
    webcal_urls = [c["id"] for c in conflict_cals if c.get("type") == "webcal" and c.get("id")]

    busy_intervals = []
    if refresh_token and settings.google_client_id:
        from app.services.calendar import CalendarService
        cal = CalendarService(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )
        local_midnight = datetime.combine(target_date, time_type(0, 0)).replace(tzinfo=tz)
        day_start = local_midnight.astimezone(dt_timezone.utc).replace(tzinfo=None)
        day_end = (local_midnight + timedelta(days=1)).astimezone(dt_timezone.utc).replace(tzinfo=None)

        # Build the full list of Google calendar IDs to check
        google_ids = list({appt_type.calendar_id} | set(extra_google_ids))
        try:
            utc_busy = cal.get_busy_intervals(refresh_token, google_ids, day_start, day_end)
            for utc_start, utc_end in utc_busy:
                local_start = utc_start.replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                local_end = utc_end.replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                busy_intervals.append((local_start, local_end))
        except Exception:
            pass

    # Fetch webcal/ICS conflict calendars
    for webcal_url in webcal_urls:
        try:
            from app.services.calendar import fetch_webcal_busy
            from datetime import timezone as _utc
            local_midnight = datetime.combine(target_date, time_type(0, 0)).replace(tzinfo=tz)
            day_start_utc = local_midnight.astimezone(_utc.utc).replace(tzinfo=None)
            day_end_utc = (local_midnight + timedelta(days=1)).astimezone(_utc.utc).replace(tzinfo=None)
            utc_busy = fetch_webcal_busy(webcal_url, day_start_utc, day_end_utc)
            for utc_start, utc_end in utc_busy:
                local_start = utc_start.replace(tzinfo=_utc.utc).astimezone(tz).replace(tzinfo=None)
                local_end = utc_end.replace(tzinfo=_utc.utc).astimezone(tz).replace(tzinfo=None)
                busy_intervals.append((local_start, local_end))
        except Exception:
            pass  # Degrade gracefully if webcal fetch fails

    # Use local time for advance-notice cutoff so it aligns with local slot times
    now_local = datetime.now(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)

    slots = compute_slots(
        target_date=target_date,
        rules=rules,
        blocked_periods=blocked,
        busy_intervals=busy_intervals,
        duration_minutes=appt_type.duration_minutes,
        buffer_before_minutes=appt_type.buffer_before_minutes,
        buffer_after_minutes=appt_type.buffer_after_minutes,
        min_advance_hours=min_advance,
        now=now_local,
    )

    slot_data = [
        {"value": s.strftime("%H:%M"), "display": s.strftime("%-I:%M %p")}
        for s in slots
    ]
    return templates.TemplateResponse(
        "booking/slots_partial.html",
        {"request": request, "slots": slot_data, "type_id": type_id, "date": date},
    )
