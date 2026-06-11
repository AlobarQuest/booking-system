"""Slot computation engine.

Orchestrates everything needed to answer "which start times are available
for this appointment type on this date": availability rules, blocked
periods, Google Calendar busy intervals, webcal feeds, calendar-window
mode, drive-time trimming, advance-notice filtering and group-showing
capacity.

All Google Calendar / webcal data is fetched in UTC and converted to naive
local time before interval math (see app.services.timeutils).
"""
import logging
from datetime import date, datetime, time as time_type, timedelta, timezone as dt_timezone
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import get_settings
from app.dependencies import get_conflict_calendars, get_setting
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod, Booking
from app.services.availability import (
    _build_free_windows,
    filter_by_advance_notice,
    intersect_windows,
    split_into_slots,
    trim_windows_for_drive_time,
)
from app.services.cache import availability_cache
from app.services.calendar import build_calendar_service, fetch_webcal_events
from app.services.timeutils import get_timezone, local_day_bounds_utc, utc_to_local

logger = logging.getLogger(__name__)


def _cache_ttl(settings) -> int:
    """Cache TTL in seconds; 0 (bypass) when unset or not a number."""
    try:
        return int(settings.slots_cache_ttl_seconds)
    except (TypeError, ValueError):
        return 0


def _format_slots(slots: list[time_type]) -> list[dict]:
    """Format start times for the slot-button partials."""
    return [
        {"value": s.strftime("%H:%M"), "display": s.strftime("%-I:%M %p")}
        for s in slots
    ]


def _localize_event(ev: dict, tz) -> dict:
    """Convert an event dict's naive-UTC start/end to naive local time."""
    return {**ev, "start": utc_to_local(ev["start"], tz), "end": utc_to_local(ev["end"], tz)}


def _apply_group_capacity(
    slots: list[time_type],
    same_type_bookings: list[Booking],
    target_date: date,
    duration_minutes: int,
    max_concurrent: int,
) -> list[time_type]:
    """Group showings: re-admit booked start times, then enforce capacity.

    Why inject instead of un-blocking freebusy intervals: Google Calendar's
    freebusy API merges adjacent events (e.g. a drive-time block ending at
    1:00 PM and a booking starting at 1:00 PM become one interval 12:30-1:20).
    Exact-tuple removal cannot split merged intervals, so the slot stays
    blocked even when capacity remains. Injecting the booking start times
    directly bypasses this limitation — the DB is the source of truth for
    which same-type slots have been taken.
    """
    booking_times = {b.start_datetime.time() for b in same_type_bookings}
    candidates = sorted(set(slots) | booking_times)

    def _overlapping_count(slot_time: time_type) -> int:
        slot_start_dt = datetime.combine(target_date, slot_time)
        slot_end_dt = slot_start_dt + timedelta(minutes=duration_minutes)
        return sum(
            1 for b in same_type_bookings
            if b.start_datetime < slot_end_dt and b.end_datetime > slot_start_dt
        )

    return [s for s in candidates if _overlapping_count(s) < max_concurrent]


def compute_slots_for_type(
    appt_type: AppointmentType,
    target_date: date,
    db: Session,
    destination: str = "",
    skip_advance_notice: bool = False,
) -> list[dict]:
    """Compute available time slots for a given appointment type and date.

    Returns a list of {"value": "HH:MM", "display": "H:MM AM/PM"} dicts.
    destination: override location (used for admin_initiated types).
    skip_advance_notice: when True, omit the advance-notice cutoff filter (used by admin).
    """
    settings = get_settings()
    effective_location = destination if appt_type.admin_initiated else appt_type.location

    rules = db.query(AvailabilityRule).filter_by(active=True).all()
    blocked = db.query(BlockedPeriod).all()
    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    refresh_token = get_setting(db, "google_refresh_token", "")
    tz = get_timezone(db)
    day_start, day_end = local_day_bounds_utc(target_date, tz)

    conflict_cals = get_conflict_calendars(db)
    extra_google_ids = [c["id"] for c in conflict_cals if c.get("type") == "google" and c.get("id")]
    webcal_urls = [c["id"] for c in conflict_cals if c.get("type") == "webcal" and c.get("id")]

    busy_intervals: list[tuple[datetime, datetime]] = []
    window_intervals: list[tuple[time_type, time_type]] = []
    local_day_events: list[dict] = []
    calendar_window_active = bool(
        appt_type.calendar_window_enabled and (appt_type.calendar_window_title or "").strip()
    )

    google_ids_for_freebusy = {appt_type.calendar_id, *extra_google_ids}
    ttl = _cache_ttl(settings)

    if refresh_token and settings.google_client_id:
        cal = build_calendar_service(settings)

        if calendar_window_active:
            window_cal_id = appt_type.calendar_window_calendar_id or appt_type.calendar_id
            google_ids_for_freebusy.discard(window_cal_id)
            try:
                window_cal_events = availability_cache.get_or_fetch(
                    ("events", refresh_token, window_cal_id, day_start, day_end, True),
                    lambda: cal.get_events_for_day(
                        refresh_token,
                        window_cal_id,
                        day_start,
                        day_end,
                        include_all_day=True,
                    ),
                    ttl,
                )
                title_lower = appt_type.calendar_window_title.lower().strip()
                for ev in window_cal_events:
                    local_ev = _localize_event(ev, tz)
                    local_start, local_end = local_ev["start"], local_ev["end"]
                    if ev["summary"].lower().strip() == title_lower:
                        window_start = time_type(0, 0) if local_start.date() < target_date else local_start.time()
                        window_end = time_type(23, 59, 59) if local_end.date() > target_date else local_end.time()
                        window_intervals.append((window_start, window_end))
                    else:
                        busy_intervals.append((local_start, local_end))
            except Exception:
                logger.warning("Calendar-window event fetch failed for calendar %s", window_cal_id, exc_info=True)

        if google_ids_for_freebusy:
            freebusy_ids = sorted(google_ids_for_freebusy)
            try:
                utc_busy = availability_cache.get_or_fetch(
                    ("freebusy", refresh_token, tuple(freebusy_ids), day_start, day_end),
                    lambda: cal.get_busy_intervals(refresh_token, freebusy_ids, day_start, day_end),
                    ttl,
                )
                busy_intervals.extend(
                    (utc_to_local(utc_start, tz), utc_to_local(utc_end, tz))
                    for utc_start, utc_end in utc_busy
                )
            except Exception:
                logger.warning("Google freebusy query failed", exc_info=True)

        if appt_type.requires_drive_time and effective_location:
            try:
                day_events_utc = availability_cache.get_or_fetch(
                    ("events", refresh_token, "primary", day_start, day_end, False),
                    lambda: cal.get_events_for_day(refresh_token, "primary", day_start, day_end),
                    ttl,
                )
                local_day_events.extend(_localize_event(ev, tz) for ev in day_events_utc)
            except Exception:
                logger.warning("Drive-time day-event fetch failed", exc_info=True)

    for webcal_url in webcal_urls:
        try:
            webcal_events = availability_cache.get_or_fetch(
                ("webcal", webcal_url, day_start, day_end),
                lambda url=webcal_url: fetch_webcal_events(url, day_start, day_end),
                ttl,
            )
            for ev in webcal_events:
                local_ev = _localize_event(ev, tz)
                busy_intervals.append((local_ev["start"], local_ev["end"]))
                if appt_type.requires_drive_time and effective_location and ev["location"]:
                    local_day_events.append(local_ev)
        except Exception as exc:
            # Log host + exception class only: webcal URLs are capability
            # secrets, and httpx exception messages embed the full URL.
            logger.warning(
                "Webcal fetch failed for feed host %s (%s)",
                urlparse(webcal_url).netloc, type(exc).__name__,
            )

    # Group showings: query confirmed same-type bookings for post-filtering.
    same_type_bookings: list[Booking] = []
    if appt_type.max_concurrent > 1:
        same_type_bookings = db.query(Booking).filter(
            Booking.appointment_type_id == appt_type.id,
            Booking.status == "confirmed",
            Booking.start_datetime >= datetime.combine(target_date, time_type(0, 0)),
            Booking.start_datetime <= datetime.combine(target_date, time_type(23, 59, 59)),
        ).all()

    windows = _build_free_windows(target_date, rules, blocked, busy_intervals, appointment_type_id=appt_type.id)

    if calendar_window_active:
        windows = intersect_windows(windows, window_intervals)

    if appt_type.requires_drive_time and effective_location:
        windows = trim_windows_for_drive_time(
            windows, target_date, local_day_events,
            destination=effective_location,
            home_address=get_setting(db, "home_address", ""),
            db=db,
        )

    now_local = datetime.now(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
    slots = split_into_slots(
        windows, appt_type.duration_minutes,
        appt_type.buffer_before_minutes, appt_type.buffer_after_minutes,
    )
    min_advance_effective = 0 if skip_advance_notice else min_advance
    slots = filter_by_advance_notice(slots, target_date, min_advance_effective, now_local)

    if appt_type.max_concurrent > 1 and same_type_bookings:
        slots = _apply_group_capacity(
            slots, same_type_bookings, target_date,
            appt_type.duration_minutes, appt_type.max_concurrent,
        )

    return _format_slots(slots)


def compute_inspection_slots(
    appt_type: AppointmentType,
    target_date: date,
    db: Session,
    destination: str = "",
) -> list[dict]:
    """Compute slots for the admin "schedule inspection" flow.

    A deliberately narrower variant of compute_slots_for_type: it checks only
    the appointment type's own calendar for conflicts (no conflict calendars,
    webcal feeds, calendar-window mode, or group capacity) and trims for
    drive time to the ad-hoc destination.
    """
    settings = get_settings()
    tz = get_timezone(db)
    day_start, day_end = local_day_bounds_utc(target_date, tz)

    busy_intervals: list[tuple[datetime, datetime]] = []
    local_day_events: list[dict] = []

    refresh_token = get_setting(db, "google_refresh_token", "")
    ttl = _cache_ttl(settings)
    if refresh_token and settings.google_client_id:
        cal = build_calendar_service(settings)
        try:
            utc_busy = availability_cache.get_or_fetch(
                ("freebusy", refresh_token, (appt_type.calendar_id,), day_start, day_end),
                lambda: cal.get_busy_intervals(refresh_token, [appt_type.calendar_id], day_start, day_end),
                ttl,
            )
            busy_intervals.extend(
                (utc_to_local(utc_start, tz), utc_to_local(utc_end, tz))
                for utc_start, utc_end in utc_busy
            )
        except Exception:
            logger.warning("Google freebusy query failed", exc_info=True)

        if destination:
            try:
                day_events_utc = availability_cache.get_or_fetch(
                    ("events", refresh_token, "primary", day_start, day_end, False),
                    lambda: cal.get_events_for_day(refresh_token, "primary", day_start, day_end),
                    ttl,
                )
                local_day_events.extend(_localize_event(ev, tz) for ev in day_events_utc)
            except Exception:
                logger.warning("Drive-time day-event fetch failed", exc_info=True)

    rules = db.query(AvailabilityRule).filter_by(active=True).all()
    blocked = db.query(BlockedPeriod).all()
    windows = _build_free_windows(target_date, rules, blocked, busy_intervals, appointment_type_id=appt_type.id)

    if destination and windows:
        windows = trim_windows_for_drive_time(
            windows, target_date, local_day_events,
            destination=destination,
            home_address=get_setting(db, "home_address", ""),
            db=db,
        )

    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    now_local = datetime.now(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
    slots = split_into_slots(
        windows, appt_type.duration_minutes,
        appt_type.buffer_before_minutes, appt_type.buffer_after_minutes,
    )
    slots = filter_by_advance_notice(slots, target_date, min_advance, now_local)
    return _format_slots(slots)
