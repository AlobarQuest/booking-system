# Drive Time Block Calendar Events Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When a booking is confirmed, create up to two "BLOCK - Drive Time for …" calendar events on the owner's Google Calendar to make drive time windows visible.

**Architecture:** A new `_create_drive_time_blocks()` helper in `app/routers/booking.py` is called right after the main appointment event is created in `submit_booking()`. It fetches the 1-hour window of events before and after the appointment from the same calendar, computes drive times using the existing `get_drive_time()` function, and creates block events via the existing `cal.create_event()`. All failures are non-fatal.

**Tech Stack:** Python, Google Calendar API (via existing `CalendarService`), `get_drive_time()` from `app/services/drive_time.py`, pytest with `unittest.mock`

---

### Task 1: Write and run failing tests for `_create_drive_time_blocks`

**Files:**
- Create: `tests/test_drive_time_blocks.py`

**Step 1: Write the failing tests**

Create `tests/test_drive_time_blocks.py` with this exact content:

```python
# tests/test_drive_time_blocks.py
"""Unit tests for _create_drive_time_blocks in app/routers/booking.py."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call


def _make_cal():
    """Return a mock CalendarService with a controllable create_event."""
    cal = MagicMock()
    cal.get_events_for_day.return_value = []
    cal.create_event.return_value = "block-evt-id"
    return cal


def _run(cal, nearby_events, drive_minutes, appt_name="Consultation",
         appt_location="456 Property Ln", home_address="123 Home St",
         start_utc=None, end_utc=None):
    """Helper: patch get_events_for_day and get_drive_time, then call _create_drive_time_blocks."""
    from app.routers.booking import _create_drive_time_blocks

    if start_utc is None:
        start_utc = datetime(2026, 3, 1, 15, 0)   # 3:00 PM UTC
    if end_utc is None:
        end_utc = start_utc + timedelta(minutes=30)

    cal.get_events_for_day.return_value = nearby_events

    db = MagicMock()
    with patch("app.routers.booking.get_drive_time", return_value=drive_minutes):
        _create_drive_time_blocks(
            cal=cal,
            refresh_token="tok",
            calendar_id="primary",
            appt_name=appt_name,
            appt_location=appt_location,
            start_utc=start_utc,
            end_utc=end_utc,
            home_address=home_address,
            db=db,
        )


def test_before_block_created_from_preceding_event():
    """Before block uses preceding event's location; title references the new appointment."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)
    preceding_end = start_utc - timedelta(minutes=30)

    preceding = {
        "start": preceding_end - timedelta(hours=1),
        "end": preceding_end,
        "summary": "Previous Showing",
        "location": "789 Other St",
    }
    _run(cal, nearby_events=[preceding], drive_minutes=20,
         start_utc=start_utc, end_utc=end_utc)

    cal.create_event.assert_any_call(
        refresh_token="tok",
        calendar_id="primary",
        summary="BLOCK - Drive Time for Consultation",
        description="",
        start=start_utc - timedelta(minutes=20),
        end=start_utc,
        show_as="busy",
        disable_reminders=True,
    )


def test_before_block_falls_back_to_home_address():
    """Before block uses home_address when no preceding event is found."""
    cal = _make_cal()
    _run(cal, nearby_events=[], drive_minutes=15)

    # create_event must have been called for the before block
    calls = cal.create_event.call_args_list
    before_calls = [c for c in calls if "Drive Time for Consultation" in c.kwargs.get("summary", "")]
    assert len(before_calls) == 1


def test_before_block_not_created_when_drive_time_zero():
    """No before block when drive time is 0 (same location or no API key)."""
    cal = _make_cal()
    _run(cal, nearby_events=[], drive_minutes=0)
    cal.create_event.assert_not_called()


def test_before_block_not_created_when_no_origin():
    """No before block when both preceding location and home_address are empty."""
    cal = _make_cal()
    _run(cal, nearby_events=[], drive_minutes=20, home_address="")
    cal.create_event.assert_not_called()


def test_after_block_created_from_following_event():
    """After block uses following event's location; title references the following event."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)
    following_start = end_utc + timedelta(minutes=20)

    following = {
        "start": following_start,
        "end": following_start + timedelta(hours=1),
        "summary": "Next Meeting",
        "location": "999 Far Away Rd",
    }
    _run(cal, nearby_events=[following], drive_minutes=25,
         start_utc=start_utc, end_utc=end_utc, home_address="")

    cal.create_event.assert_any_call(
        refresh_token="tok",
        calendar_id="primary",
        summary="BLOCK - Drive Time for Next Meeting",
        description="",
        start=end_utc,
        end=end_utc + timedelta(minutes=25),
        show_as="busy",
        disable_reminders=True,
    )


def test_after_block_not_created_when_following_has_no_location():
    """No after block when the following event has no location."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)

    following = {
        "start": end_utc + timedelta(minutes=10),
        "end": end_utc + timedelta(hours=1),
        "summary": "No Location Meeting",
        "location": "",
    }
    _run(cal, nearby_events=[following], drive_minutes=20,
         start_utc=start_utc, end_utc=end_utc, home_address="")

    cal.create_event.assert_not_called()


def test_both_blocks_created_when_both_neighbors_exist():
    """Both before and after blocks are created when adjacent events have locations."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)

    preceding = {
        "start": start_utc - timedelta(hours=1),
        "end": start_utc - timedelta(minutes=20),
        "summary": "Prior",
        "location": "111 Before St",
    }
    following = {
        "start": end_utc + timedelta(minutes=15),
        "end": end_utc + timedelta(hours=1),
        "summary": "After Meeting",
        "location": "222 After Ave",
    }
    _run(cal, nearby_events=[preceding, following], drive_minutes=10,
         start_utc=start_utc, end_utc=end_utc)

    assert cal.create_event.call_count == 2
    summaries = {c.kwargs["summary"] for c in cal.create_event.call_args_list}
    assert "BLOCK - Drive Time for Consultation" in summaries
    assert "BLOCK - Drive Time for After Meeting" in summaries


def test_calendar_fetch_failure_is_silent():
    """If get_events_for_day raises, no exception propagates and create_event is never called."""
    cal = _make_cal()
    cal.get_events_for_day.side_effect = Exception("API error")
    _run(cal, nearby_events=[], drive_minutes=20)
    cal.create_event.assert_not_called()
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_drive_time_blocks.py -v
```

Expected: All 8 tests FAIL with `ImportError: cannot import name '_create_drive_time_blocks' from 'app.routers.booking'`

---

### Task 2: Implement `_create_drive_time_blocks` in `booking.py`

**Files:**
- Modify: `app/routers/booking.py`

**Step 1: Add the import and function**

At the top of `app/routers/booking.py`, the existing imports already include `timedelta`. Verify `from datetime import datetime, timedelta, timezone as dt_timezone` is present (it is, on line 2).

Add the `get_drive_time` import inside the function (to avoid circular imports — same pattern used elsewhere in the file). The function itself goes after the router/templates setup, before the route handlers:

Add this block after line 19 (after `templates.env.globals["csrf_token"] = _get_csrf_token`):

```python
def _create_drive_time_blocks(
    cal,
    refresh_token: str,
    calendar_id: str,
    appt_name: str,
    appt_location: str,
    start_utc,
    end_utc,
    home_address: str,
    db,
) -> None:
    """Create BLOCK calendar events for drive time before and after the appointment.

    All datetimes must be naive UTC. Failures are fully silent — this is a
    best-effort calendar annotation, never blocking the booking confirmation.
    """
    from app.services.drive_time import get_drive_time

    window_start = start_utc - timedelta(hours=1)
    window_end = end_utc + timedelta(hours=1)

    try:
        nearby_events = cal.get_events_for_day(refresh_token, calendar_id, window_start, window_end)
    except Exception:
        return

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
                cal.create_event(
                    refresh_token=refresh_token,
                    calendar_id=calendar_id,
                    summary=f"BLOCK - Drive Time for {appt_name}",
                    description="",
                    start=start_utc - timedelta(minutes=drive_mins),
                    end=start_utc,
                    show_as="busy",
                    disable_reminders=True,
                )
            except Exception:
                pass

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
                    cal.create_event(
                        refresh_token=refresh_token,
                        calendar_id=calendar_id,
                        summary=f"BLOCK - Drive Time for {following['summary']}",
                        description="",
                        start=end_utc,
                        end=end_utc + timedelta(minutes=drive_mins),
                        show_as="busy",
                        disable_reminders=True,
                    )
                except Exception:
                    pass
```

**Step 2: Run tests to verify they pass**

```bash
pytest tests/test_drive_time_blocks.py -v
```

Expected: All 8 tests PASS.

**Step 3: Commit**

```bash
git add app/routers/booking.py tests/test_drive_time_blocks.py
git commit -m "feat: add _create_drive_time_blocks helper with tests"
```

---

### Task 3: Wire `_create_drive_time_blocks` into `submit_booking`

**Files:**
- Modify: `app/routers/booking.py:175-192` (the existing calendar event creation block)

**Step 1: Write a failing integration test**

Add to `tests/test_drive_time_blocks.py`:

```python
def test_submit_booking_calls_drive_time_blocks_when_requires_drive_time():
    """submit_booking calls _create_drive_time_blocks when requires_drive_time is True."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from app.database import Base, get_db
    from app.main import app
    from app.models import AppointmentType
    from app.dependencies import require_csrf

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    appt = AppointmentType(
        name="Property Showing",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        calendar_id="primary",
        active=True,
        color="#3b82f6",
        description="",
        requires_drive_time=True,
        location="456 Property Ln",
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.commit()
    appt_id = appt.id
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[require_csrf] = lambda: None

    with patch("app.routers.booking._create_drive_time_blocks") as mock_blocks:
        with patch("app.routers.booking.get_settings") as mock_settings:
            from app.config import Settings
            mock_settings.return_value = Settings(
                google_client_id="fake-id",
                google_client_secret="fake-secret",
                google_redirect_uri="http://localhost/callback",
            )
            with patch("app.services.calendar.CalendarService.create_event", return_value="evt-1"):
                from app.dependencies import set_setting
                s = Session()
                set_setting(s, "google_refresh_token", "fake-token")
                set_setting(s, "timezone", "America/New_York")
                s.close()

                client = TestClient(app)
                response = client.post("/book", data={
                    "type_id": str(appt_id),
                    "start_datetime": "2026-03-01T10:00:00",
                    "guest_name": "Alice",
                    "guest_email": "alice@example.com",
                })

    assert response.status_code == 200
    assert mock_blocks.called

    app.dependency_overrides.clear()
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_drive_time_blocks.py::test_submit_booking_calls_drive_time_blocks_when_requires_drive_time -v
```

Expected: FAIL — `mock_blocks.called` is False because the wiring doesn't exist yet.

**Step 3: Wire it into `submit_booking`**

In `app/routers/booking.py`, inside `submit_booking()`, find the block that ends with:

```python
        except Exception:
            pass  # Booking saved; calendar failure is non-fatal
```

Immediately after that `except` block (still inside the `if refresh_token and settings.google_client_id:` block), add:

```python
        # Drive time block events (owner-only, non-fatal)
        if appt_type.requires_drive_time and appt_type.location:
            home_address = get_setting(db, "home_address", "")
            _create_drive_time_blocks(
                cal=cal,
                refresh_token=refresh_token,
                calendar_id=appt_type.calendar_id,
                appt_name=appt_type.name,
                appt_location=appt_type.location,
                start_utc=start_utc,
                end_utc=end_utc,
                home_address=home_address,
                db=db,
            )
```

**Step 4: Run the full test suite**

```bash
pytest -v
```

Expected: All 104 existing tests + the new integration test PASS (112 total, since we added 8 + 1 = 9 new tests).

**Step 5: Commit**

```bash
git add app/routers/booking.py tests/test_drive_time_blocks.py
git commit -m "feat: create drive time BLOCK events on booking confirmation"
```

---

### Task 4: Push and sync preview

```bash
git push origin master
git branch -f preview master && git push origin preview --force
```

Preview at https://preview.booking.devonwatkins.com will auto-deploy for end-to-end testing with a real Google Calendar connection.
