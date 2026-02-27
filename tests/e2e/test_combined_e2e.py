"""
Combined E2E test — drive time + calendar window features together.

Exercises both features on a single appointment type via e2e_client GET /slots.
"""
from datetime import datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest

from app.dependencies import set_setting
from app.services.drive_time import get_drive_time
from tests.e2e.conftest import (
    ADDR_BUCKHEAD,
    ADDR_MIDTOWN,
    calendar_event,
    future_monday,
    seed_appt_type,
    seed_rule,
)

TZ = ZoneInfo("America/New_York")


def _local_to_utc(target_date, hour: int, minute: int = 0) -> datetime:
    """Convert a local time on target_date (America/New_York) to naive UTC datetime."""
    local_dt = datetime.combine(target_date, time(hour, minute)).replace(tzinfo=TZ)
    return local_dt.astimezone(dt_timezone.utc).replace(tzinfo=None)


def test_combined_calendar_window_and_drive_time(
    e2e_client, e2e_db, cal_service, refresh_token
):
    """Both features active: drive time trims the start of a calendar window.

    Setup:
    - AppointmentType: requires_drive_time=True, location=ADDR_MIDTOWN,
      calendar_window_enabled=True, calendar_window_title="RENTAL SHOWING"
    - home_address=ADDR_BUCKHEAD (no preceding event, so home is the origin)
    - Availability rule: 09:00-17:00 Monday
    - Calendar event: "RENTAL SHOWING" 10:00-14:00 local on target_date

    Expected:
    - 9:00 AM not in response (outside calendar window)
    - 10:00 AM not in response (drive time from home trims window start forward)
    - First available slot corresponds to 10:00 + drive_mins, rounded to next 15-min boundary
    - 3:00 PM not in response (after calendar window ends at 2:00 PM — see note below)
    """
    target_date = future_monday()

    # Seed appointment type with both features enabled
    seed_rule(e2e_db, day_of_week=0, start="09:00", end="17:00")
    appt = seed_appt_type(
        e2e_db,
        name="Rental Showing",
        duration_minutes=60,
        requires_drive_time=True,
        location=ADDR_MIDTOWN,
        calendar_window_enabled=True,
        calendar_window_title="RENTAL SHOWING",
        calendar_window_calendar_id="",
        calendar_id="primary",
    )
    set_setting(e2e_db, "home_address", ADDR_BUCKHEAD)
    set_setting(e2e_db, "google_refresh_token", refresh_token)
    set_setting(e2e_db, "timezone", "America/New_York")
    set_setting(e2e_db, "min_advance_hours", "0")

    # Pre-compute drive time to predict the trimmed start
    drive_mins = get_drive_time(ADDR_BUCKHEAD, ADDR_MIDTOWN, e2e_db)
    assert drive_mins > 0, "Expected positive drive time from Buckhead to Midtown"

    # Expected trimmed window start = 10:00 + drive_mins
    raw_trimmed_mins = 10 * 60 + drive_mins
    # Round up to next 15-min boundary (same logic as split_into_slots)
    remainder = raw_trimmed_mins % 15
    if remainder == 0:
        first_slot_mins = raw_trimmed_mins
    else:
        first_slot_mins = raw_trimmed_mins + (15 - remainder)
    first_slot_hour = first_slot_mins // 60
    first_slot_min = first_slot_mins % 60

    # Format as "H:MM AM/PM" for response matching
    first_slot_dt = datetime(2000, 1, 1, first_slot_hour, first_slot_min)
    first_slot_display = first_slot_dt.strftime("%-I:%M %p")

    # Calendar window: 10:00-14:00 local
    window_start_utc = _local_to_utc(target_date, 10, 0)
    window_end_utc = _local_to_utc(target_date, 14, 0)

    with calendar_event(
        cal_service, refresh_token, "RENTAL SHOWING", window_start_utc, window_end_utc
    ):
        resp = e2e_client.get(
            "/slots", params={"type_id": appt.id, "date": target_date.isoformat()}
        )

    assert resp.status_code == 200
    assert "9:00 AM" not in resp.text   # before calendar window
    assert "10:00 AM" not in resp.text  # drive time trims window start past 10:00

    # First available slot should be present
    assert first_slot_display in resp.text, (
        f"Expected first slot '{first_slot_display}' (10:00 + {drive_mins} min drive, "
        f"rounded to 15-min boundary) to be in response"
    )

    # 3:00 PM is 15:00 local; window ends at 14:00, so 3:00 PM should not be in response
    assert "3:00 PM" not in resp.text
