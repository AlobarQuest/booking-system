"""
Calendar window E2E tests — 6 scenarios.

All tests exercise the full route stack via e2e_client GET /slots, including
real get_events_for_day() calls against Google Calendar.

Each test:
- Seeds e2e_db with a Monday rule, appointment type, and required settings
- Uses future_monday() as the target date (8+ days out)
- Creates/deletes calendar events via the calendar_event() context manager
"""
from datetime import datetime, time, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest

from app.dependencies import set_setting
from tests.e2e.conftest import (
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


def _setup_db(db, refresh_token: str, calendar_window_enabled: bool = True, **appt_kwargs):
    """Seed DB with standard availability rule, appointment type, and settings."""
    seed_rule(db, day_of_week=0, start="09:00", end="17:00")
    defaults = {
        "name": "Rental Showing",
        "duration_minutes": 60,
        "calendar_window_enabled": calendar_window_enabled,
        "calendar_window_title": "POSSIBLE RENTAL SHOWINGS",
        "calendar_window_calendar_id": "",
        "calendar_id": "primary",
    }
    defaults.update(appt_kwargs)
    appt = seed_appt_type(db, **defaults)
    set_setting(db, "google_refresh_token", refresh_token)
    set_setting(db, "timezone", "America/New_York")
    set_setting(db, "min_advance_hours", "0")
    return appt


# ---------------------------------------------------------------------------
# 1. Calendar window disabled → normal slots
# ---------------------------------------------------------------------------


def test_calendar_window_disabled_normal_slots(e2e_client, e2e_db, refresh_token):
    """calendar_window_enabled=False → normal availability slots, 9 AM slot present."""
    appt = _setup_db(e2e_db, refresh_token, calendar_window_enabled=False)
    target_date = future_monday()

    resp = e2e_client.get("/slots", params={"type_id": appt.id, "date": target_date.isoformat()})
    assert resp.status_code == 200
    assert "9:00 AM" in resp.text


# ---------------------------------------------------------------------------
# 2. Matching calendar event creates booking window
# ---------------------------------------------------------------------------


def test_calendar_window_matching_event_creates_window(
    e2e_client, e2e_db, cal_service, refresh_token
):
    """Matching-title event 10:00-12:00 creates a window; only those slots available."""
    appt = _setup_db(e2e_db, refresh_token)
    target_date = future_monday()

    start_utc = _local_to_utc(target_date, 10, 0)
    end_utc = _local_to_utc(target_date, 12, 0)

    with calendar_event(cal_service, refresh_token, "POSSIBLE RENTAL SHOWINGS", start_utc, end_utc):
        resp = e2e_client.get(
            "/slots", params={"type_id": appt.id, "date": target_date.isoformat()}
        )

    assert resp.status_code == 200
    assert "10:00 AM" in resp.text
    assert "11:00 AM" in resp.text
    assert "9:00 AM" not in resp.text
    assert "2:00 PM" not in resp.text


# ---------------------------------------------------------------------------
# 3. No matching events → no slots
# ---------------------------------------------------------------------------


def test_calendar_window_no_matching_events_no_slots(e2e_client, e2e_db, refresh_token):
    """calendar_window_enabled=True but no matching events → no available slots."""
    appt = _setup_db(e2e_db, refresh_token)
    target_date = future_monday()

    resp = e2e_client.get("/slots", params={"type_id": appt.id, "date": target_date.isoformat()})
    assert resp.status_code == 200
    assert "no-slots" in resp.text


# ---------------------------------------------------------------------------
# 4. Non-matching event within window is still busy
# ---------------------------------------------------------------------------


def test_calendar_window_non_matching_event_is_busy(
    e2e_client, e2e_db, cal_service, refresh_token
):
    """A non-matching event inside the window blocks those slots."""
    appt = _setup_db(e2e_db, refresh_token)
    target_date = future_monday()

    window_start_utc = _local_to_utc(target_date, 10, 0)
    window_end_utc = _local_to_utc(target_date, 14, 0)
    busy_start_utc = _local_to_utc(target_date, 11, 0)
    busy_end_utc = _local_to_utc(target_date, 12, 0)

    with calendar_event(
        cal_service, refresh_token, "POSSIBLE RENTAL SHOWINGS", window_start_utc, window_end_utc
    ):
        with calendar_event(
            cal_service, refresh_token, "OTHER EVENT", busy_start_utc, busy_end_utc
        ):
            resp = e2e_client.get(
                "/slots", params={"type_id": appt.id, "date": target_date.isoformat()}
            )

    assert resp.status_code == 200
    assert "10:00 AM" in resp.text       # first slot in window, before busy
    assert "11:00 AM" not in resp.text   # blocked by OTHER EVENT
    assert "12:00 PM" in resp.text       # after busy period, inside window


# ---------------------------------------------------------------------------
# 5. Window title match is case-insensitive
# ---------------------------------------------------------------------------


def test_calendar_window_case_insensitive_match(
    e2e_client, e2e_db, cal_service, refresh_token
):
    """Event titled in lowercase matches uppercase calendar_window_title."""
    appt = _setup_db(e2e_db, refresh_token)
    target_date = future_monday()

    start_utc = _local_to_utc(target_date, 10, 0)
    end_utc = _local_to_utc(target_date, 12, 0)

    # Lowercase title — should still match uppercase calendar_window_title
    with calendar_event(
        cal_service, refresh_token, "possible rental showings", start_utc, end_utc
    ):
        resp = e2e_client.get(
            "/slots", params={"type_id": appt.id, "date": target_date.isoformat()}
        )

    assert resp.status_code == 200
    assert "10:00 AM" in resp.text
    assert "11:00 AM" in resp.text
    assert "9:00 AM" not in resp.text
    assert "2:00 PM" not in resp.text


# ---------------------------------------------------------------------------
# 6. Calendar window intersects availability rules correctly
# ---------------------------------------------------------------------------


def test_calendar_window_intersects_availability_rules(
    e2e_client, e2e_db, cal_service, refresh_token
):
    """Event 08:00-10:00 (partly before 09:00 rule) → only 09:00 slot, not 08:00."""
    appt = _setup_db(e2e_db, refresh_token)
    target_date = future_monday()

    # Event spans 08:00-10:00; availability rule starts at 09:00
    start_utc = _local_to_utc(target_date, 8, 0)
    end_utc = _local_to_utc(target_date, 10, 0)

    with calendar_event(
        cal_service, refresh_token, "POSSIBLE RENTAL SHOWINGS", start_utc, end_utc
    ):
        resp = e2e_client.get(
            "/slots", params={"type_id": appt.id, "date": target_date.isoformat()}
        )

    assert resp.status_code == 200
    assert "8:00 AM" not in resp.text    # outside availability rule
    assert "9:00 AM" in resp.text        # intersection of rule + window
    assert "10:00 AM" not in resp.text   # outside calendar window
