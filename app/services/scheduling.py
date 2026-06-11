"""Booking lifecycle operations that touch Google Calendar and email.

These were previously private helpers inside app.routers.booking and were
imported across router modules; they live here so routers stay thin HTTP
adapters.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.dependencies import get_email_config, get_setting
from app.models import Booking
from app.services import email
from app.services.cache import availability_cache
from app.services.calendar import build_calendar_service
from app.services.drive_time import get_drive_time
from app.services.timeutils import get_timezone, local_to_utc

logger = logging.getLogger(__name__)


def create_drive_time_blocks(
    cal,
    refresh_token: str,
    calendar_id: str,
    appt_name: str,
    appt_location: str,
    start_utc: datetime,
    end_utc: datetime,
    home_address: str,
    db: Session,
) -> list[str]:
    """Create BLOCK calendar events for drive time before and after the appointment.

    Returns a list of created calendar event IDs (0-2 items).
    All datetimes must be naive UTC. Failures never propagate — this is a
    best-effort calendar annotation, never blocking the booking confirmation.
    """
    created_ids: list[str] = []
    window_start = start_utc - timedelta(hours=1)
    window_end = end_utc + timedelta(hours=1)

    try:
        nearby_events = cal.get_events_for_day(refresh_token, calendar_id, window_start, window_end)
    except Exception:
        logger.warning("Drive-time block: nearby-event fetch failed", exc_info=True)
        return created_ids

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
                event_id = cal.create_event(
                    refresh_token=refresh_token,
                    calendar_id=calendar_id,
                    summary=f"BLOCK - Drive Time for {appt_name}",
                    description="",
                    start=start_utc - timedelta(minutes=drive_mins),
                    end=start_utc,
                    show_as="busy",
                    disable_reminders=True,
                )
                if event_id:
                    created_ids.append(event_id)
            except Exception:
                logger.warning("Drive-time block: before-block creation failed", exc_info=True)

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
                    event_id = cal.create_event(
                        refresh_token=refresh_token,
                        calendar_id=calendar_id,
                        summary=f"BLOCK - Drive Time for {following['summary']}",
                        description="",
                        start=end_utc,
                        end=end_utc + timedelta(minutes=drive_mins),
                        show_as="busy",
                        disable_reminders=True,
                    )
                    if event_id:
                        created_ids.append(event_id)
                except Exception:
                    logger.warning("Drive-time block: after-block creation failed", exc_info=True)

    if created_ids:
        availability_cache.clear()
    return created_ids


def delete_drive_time_events(cal, refresh_token: str, calendar_id: str, event_ids: list[str]) -> None:
    """Delete stored drive time BLOCK calendar events. All failures are non-fatal."""
    for event_id in event_ids:
        try:
            cal.delete_event(refresh_token, calendar_id, event_id)
        except Exception:
            logger.warning("Drive-time block: deletion of event %s failed", event_id, exc_info=True)


def delete_booking_calendar_events(db: Session, booking: Booking, settings) -> None:
    """Delete a booking's Google event and its drive-time blocks. Non-fatal."""
    refresh_token = get_setting(db, "google_refresh_token", "")
    if not (refresh_token and settings.google_client_id):
        return
    cal = build_calendar_service(settings)
    calendar_id = booking.appointment_type.calendar_id
    if booking.google_event_id:
        try:
            cal.delete_event(refresh_token, calendar_id, booking.google_event_id)
        except Exception:
            logger.warning("Cancel: deletion of event %s failed", booking.google_event_id, exc_info=True)
    delete_drive_time_events(cal, refresh_token, calendar_id, booking.drive_time_event_ids)
    availability_cache.clear()


def perform_reschedule(
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
    appt_type = booking.appointment_type
    new_end_dt = new_start_dt + timedelta(minutes=appt_type.duration_minutes)

    tz = get_timezone(db)
    start_utc = local_to_utc(new_start_dt, tz)
    end_utc = local_to_utc(new_end_dt, tz)

    refresh_token = get_setting(db, "google_refresh_token", "")
    old_event_id = booking.google_event_id
    new_event_id = old_event_id  # preserve original if calendar branch is skipped

    if refresh_token and settings.google_client_id:
        cal = build_calendar_service(settings)
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
                attendee_email="",
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
                logger.warning("Reschedule: deletion of old event %s failed", old_event_id, exc_info=True)

    # Update booking
    booking.start_datetime = new_start_dt
    booking.end_datetime = new_end_dt
    booking.google_event_id = new_event_id
    db.commit()
    availability_cache.clear()

    # Send new confirmation email (non-fatal; only if guest email present)
    if booking.guest_email:
        email_config = get_email_config(db, settings)
        if email_config.can_send:
            try:
                email.send_guest_confirmation(
                    api_key=email_config.api_key,
                    from_email=email_config.from_email,
                    guest_email=booking.guest_email,
                    guest_name=booking.guest_name,
                    appt_type_name=appt_type.guest_event_title or appt_type.name,
                    start_dt=new_start_dt,
                    end_dt=new_end_dt,
                    custom_responses=booking.custom_field_responses,
                    owner_name=get_setting(db, "owner_name", ""),
                    template=get_setting(db, "email_guest_confirmation", ""),
                    reschedule_url=f"{base_url}/reschedule/{booking.reschedule_token}",
                    cancel_url=f"{base_url}/cancel/{booking.reschedule_token}",
                    location=appt_type.location or "",
                    contact_phone=get_setting(db, "contact_phone", ""),
                )
            except Exception:
                logger.warning("Reschedule: confirmation email failed", exc_info=True)
