import json as _json
from datetime import datetime, date as date_type, time as time_type, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_setting
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod
from app.services.availability import (
    _build_free_windows,
    intersect_windows,
    split_into_slots,
    filter_by_advance_notice,
    trim_windows_for_drive_time,
)
from app.services.calendar import CalendarService
from app.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/slots", response_class=HTMLResponse)
def get_slots(
    request: Request,
    type_id: int = Query(...),
    date: str = Query(...),
    destination: str = Query(""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return HTMLResponse("<p class='no-slots'>Appointment type not found.</p>")

    effective_location = destination if appt_type.admin_initiated else appt_type.location

    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("<p class='no-slots'>Invalid date format.</p>")

    rules = db.query(AvailabilityRule).filter_by(active=True).all()
    blocked = db.query(BlockedPeriod).all()
    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    refresh_token = get_setting(db, "google_refresh_token", "")
    tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))

    # Compute UTC day boundaries
    local_midnight = datetime.combine(target_date, time_type(0, 0)).replace(tzinfo=tz)
    day_start = local_midnight.astimezone(dt_timezone.utc).replace(tzinfo=None)
    day_end = (local_midnight + timedelta(days=1)).astimezone(dt_timezone.utc).replace(tzinfo=None)

    # Load conflict calendars
    conflict_cals_raw = get_setting(db, "conflict_calendars", "[]")
    try:
        conflict_cals = _json.loads(conflict_cals_raw)
    except (ValueError, TypeError):
        conflict_cals = []
    extra_google_ids = [c["id"] for c in conflict_cals if c.get("type") == "google" and c.get("id")]
    webcal_urls = [c["id"] for c in conflict_cals if c.get("type") == "webcal" and c.get("id")]

    busy_intervals = []
    window_intervals = []  # populated only when calendar_window_enabled
    local_day_events = []  # populated only when requires_drive_time

    # Determine which Google Calendar IDs to query via freebusy
    google_ids_for_freebusy = set()
    google_ids_for_freebusy.add(appt_type.calendar_id)
    google_ids_for_freebusy.update(extra_google_ids)

    if refresh_token and settings.google_client_id:
        cal = CalendarService(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )

        # --- Calendar window: fetch full events and split into windows vs. busy ---
        if appt_type.calendar_window_enabled and appt_type.calendar_window_title:
            window_cal_id = appt_type.calendar_window_calendar_id or appt_type.calendar_id
            # Handle this calendar manually — exclude from freebusy query
            google_ids_for_freebusy.discard(window_cal_id)
            try:
                window_cal_events = cal.get_events_for_day(refresh_token, window_cal_id, day_start, day_end)
                title_lower = appt_type.calendar_window_title.lower().strip()
                for ev in window_cal_events:
                    local_start = ev["start"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    local_end = ev["end"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    if ev["summary"].lower().strip() == title_lower:
                        # This is a valid booking window
                        window_intervals.append((local_start.time(), local_end.time()))
                    else:
                        # Non-matching event is still busy
                        busy_intervals.append((local_start, local_end))
            except Exception:
                pass

        # --- Freebusy for remaining Google calendars ---
        if google_ids_for_freebusy:
            try:
                utc_busy = cal.get_busy_intervals(refresh_token, list(google_ids_for_freebusy), day_start, day_end)
                for utc_start, utc_end in utc_busy:
                    local_start = utc_start.replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    local_end = utc_end.replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    busy_intervals.append((local_start, local_end))
            except Exception:
                pass

        # --- Drive time: fetch full events to find preceding event location ---
        if appt_type.requires_drive_time and effective_location:
            try:
                day_events_utc = cal.get_events_for_day(refresh_token, "primary", day_start, day_end)
                for ev in day_events_utc:
                    local_start = ev["start"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    local_end = ev["end"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    local_day_events.append({**ev, "start": local_start, "end": local_end})
            except Exception:
                pass

    # Fetch webcal/ICS conflict calendars
    for webcal_url in webcal_urls:
        try:
            from app.services.calendar import fetch_webcal_events
            wc_events = fetch_webcal_events(webcal_url, day_start, day_end)
            for ev in wc_events:
                local_start = ev["start"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                local_end = ev["end"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                busy_intervals.append((local_start, local_end))
                # Include located events in drive time calculation
                if appt_type.requires_drive_time and effective_location and ev["location"]:
                    local_day_events.append({**ev, "start": local_start, "end": local_end})
        except Exception:
            pass

    # Build availability windows
    windows = _build_free_windows(target_date, rules, blocked, busy_intervals, appointment_type_id=appt_type.id)

    # Apply calendar window constraint (intersect with matching calendar events)
    if window_intervals:
        windows = intersect_windows(windows, window_intervals)

    # Apply drive time trimming
    if appt_type.requires_drive_time and effective_location:
        home_address = get_setting(db, "home_address", "")
        windows = trim_windows_for_drive_time(
            windows, target_date, local_day_events,
            destination=effective_location,
            home_address=home_address,
            db=db,
        )

    # Generate slots and apply advance notice filter
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
    return templates.TemplateResponse(
        "booking/slots_partial.html",
        {"request": request, "slots": slot_data, "type_id": type_id, "date": date},
    )
