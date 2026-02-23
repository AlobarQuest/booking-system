from datetime import datetime, date as date_type, time as time_type
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

    busy_intervals = []
    if refresh_token and settings.google_client_id:
        from app.services.calendar import CalendarService
        from datetime import timedelta
        cal = CalendarService(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )
        day_start = datetime.combine(target_date, time_type(0, 0))
        day_end = day_start + timedelta(days=1)
        try:
            busy_intervals = cal.get_busy_intervals(
                refresh_token, [appt_type.calendar_id], day_start, day_end
            )
        except Exception:
            pass  # Degrade gracefully if Calendar API fails

    slots = compute_slots(
        target_date=target_date,
        rules=rules,
        blocked_periods=blocked,
        busy_intervals=busy_intervals,
        duration_minutes=appt_type.duration_minutes,
        buffer_before_minutes=appt_type.buffer_before_minutes,
        buffer_after_minutes=appt_type.buffer_after_minutes,
        min_advance_hours=min_advance,
        now=datetime.utcnow(),
    )

    slot_strings = [s.strftime("%H:%M") for s in slots]
    return templates.TemplateResponse(
        "booking/slots_partial.html",
        {"request": request, "slots": slot_strings, "type_id": type_id, "date": date},
    )
