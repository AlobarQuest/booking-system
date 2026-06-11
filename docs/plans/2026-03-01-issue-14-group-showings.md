# Group Showings (Concurrent Appointment Types) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow appointment types to accept multiple concurrent bookings at the same time slot, enabling "group showings" where multiple guests tour a property simultaneously (up to a configurable limit per appointment type).

**Architecture:** Add a `max_concurrent` integer field to `AppointmentType` (default 1 = existing behavior). When `max_concurrent > 1`, the slot engine un-blocks same-type confirmed booking intervals from Google Calendar busy time, generates candidate slots normally, then post-filters by concurrent count. When a new booking overlaps an existing same-type booking (it's a group showing), skip drive time block calendar events. Admin bookings list shows a "Group" badge on any booking that shares a time slot with another same-type booking.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy mapped columns, Jinja2 templates, SQLite, Google Calendar freebusy API, pytest

**Key files:** `app/models.py`, `app/database.py`, `app/routers/admin.py`, `app/routers/slots.py`, `app/routers/booking.py`, `app/templates/admin/appointment_types.html`, `app/templates/admin/bookings.html`

---

## Task 1: Add `max_concurrent` field to AppointmentType

**Files:**
- Modify: `app/models.py` (AppointmentType class, after `admin_initiated` field ~line 36)
- Modify: `app/database.py` (appointment_types migration loop ~line 36-55)
- Modify: `app/routers/admin.py` (`create_appt_type` ~line 83, `update_appt_type` ~line 169)
- Modify: `app/templates/admin/appointment_types.html` (inside `#standard-fields` div, before submit button ~line 239)
- Test: `tests/test_booking_route.py`

### Step 1: Write the failing test

Add to `tests/test_booking_route.py`:

```python
def test_appointment_type_has_max_concurrent():
    from app.models import AppointmentType
    # Default should be 1
    at = AppointmentType(
        name="Test", duration_minutes=30,
        buffer_before_minutes=0, buffer_after_minutes=0,
        calendar_id="primary", active=True, color="#3b82f6",
    )
    assert at.max_concurrent == 1
    # Should accept higher values
    at.max_concurrent = 3
    assert at.max_concurrent == 3
```

### Step 2: Run test to verify it fails

```bash
cd /home/devon/Projects/BookingAssistant
pytest tests/test_booking_route.py::test_appointment_type_has_max_concurrent -v
```

Expected: FAIL — `AttributeError: max_concurrent`

### Step 3: Add the column to AppointmentType in models.py

In `app/models.py`, add this line after `admin_initiated` (line ~36):

```python
max_concurrent: Mapped[int] = mapped_column(Integer, default=1)
```

### Step 4: Add the migration in database.py

In `app/database.py`, inside the `existing` migration loop (the `appointment_types` PRAGMA block), add as the last entry before the `]`:

```python
("max_concurrent", "INTEGER NOT NULL DEFAULT 1"),
```

So the list ends:
```python
    ("admin_initiated", "BOOLEAN NOT NULL DEFAULT 0"),
    ("max_concurrent", "INTEGER NOT NULL DEFAULT 1"),
]:
```

### Step 5: Run test to verify it passes

```bash
pytest tests/test_booking_route.py::test_appointment_type_has_max_concurrent -v
```

Expected: PASS

### Step 6: Add `max_concurrent` Form param to create_appt_type route

In `app/routers/admin.py`, add to the `create_appt_type` POST parameters (after `admin_initiated`):

```python
max_concurrent: int = Form(1),
```

And in the `AppointmentType(...)` constructor call, add:

```python
max_concurrent=max(1, max_concurrent),
```

### Step 7: Add `max_concurrent` to update_appt_type route

In `app/routers/admin.py`, add to the `update_appt_type` POST parameters (after `admin_initiated`):

```python
max_concurrent: int = Form(1),
```

And inside the `if t:` block (after `t.owner_reminders_enabled = ...`), add:

```python
t.max_concurrent = max(1, max_concurrent)
```

### Step 8: Add the form field to the appointment type template

In `app/templates/admin/appointment_types.html`, inside `<div id="standard-fields">`, add this block just before the closing `</div>` of `standard-fields` (after the `owner_reminders_enabled` checkbox and its `<small>` tag, around line 238):

```html
      <hr style="margin:1rem 0;border:none;border-top:1px solid #e2e8f0;">
      <label>Max concurrent bookings (Group Showings)
        <input type="number" name="max_concurrent" min="1" max="20"
               value="{{ edit_type.max_concurrent if edit_type else 1 }}"
               style="width:80px;">
      </label>
      <small style="color:#64748b;margin-top:-.5rem;">
        Set to 2 or more to allow multiple guests to book the same time slot
        (e.g. group property showings). Default: 1 (no overlap allowed).
      </small>
```

### Step 9: Run full suite

```bash
pytest -q
```

Expected: all existing tests pass (new column defaults to 1, no behavior change).

### Step 10: Commit

```bash
git add app/models.py app/database.py app/routers/admin.py \
        app/templates/admin/appointment_types.html tests/test_booking_route.py
git commit -m "feat: add max_concurrent field to AppointmentType for group showings (issue 14)"
```

---

## Task 2: Group showing slot computation

**Files:**
- Modify: `app/routers/slots.py` (`_compute_slots_for_type`, lines 26-149)
- Create: `tests/test_group_showings.py`

### Context

When `max_concurrent > 1`, an existing same-type confirmed booking appears as "busy" in Google Calendar, which blocks that slot for new bookings. The fix works in two phases inside `_compute_slots_for_type()`:

1. **Un-block phase:** Query DB for confirmed same-type bookings on the target date. Remove their exact time intervals from `busy_intervals` so `_build_free_windows()` sees that time as available again. Google Calendar events that are NOT same-type bookings remain as hard blocks.

2. **Post-filter phase:** After generating candidate slots, count how many same-type confirmed bookings overlap each slot. Only include the slot if `count < max_concurrent`.

**How the exact-match removal works:** When a booking is created, a Google Calendar event is created for `booking.start_datetime` to `booking.end_datetime` in UTC. The slot engine converts these back to local time. The `(local_start, local_end)` tuple from Google Calendar's freebusy response will exactly match `(booking.start_datetime, booking.end_datetime)` stored in the DB (both are local naive datetimes), so the set-membership check works correctly.

**Test date:** Use 2030-09-16 (a Monday) with timezone set to UTC in tests — this avoids the advance-notice filter (2030 is far enough in the future) and simplifies timezone math (UTC → local is identity transform).

### Step 1: Create the test file

Create `tests/test_group_showings.py`:

```python
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType, Booking, AvailabilityRule
from app.dependencies import require_csrf, set_setting


# 2030-09-16 is a Monday (day_of_week=0). Far enough in the future
# that advance-notice filtering (default 24h) will never block these slots.
TEST_DATE = "2030-09-16"
BOOKING_START = datetime(2030, 9, 16, 13, 0)   # 1:00 PM
BOOKING_END   = datetime(2030, 9, 16, 13, 30)  # 1:30 PM


def make_group_client(max_concurrent: int = 2):
    """Return (client, Session, type_id).

    Sets up an in-memory DB with:
    - One appointment type (Home Tour, 30 min, max_concurrent=<arg>)
    - One Monday availability rule 09:00-17:00
    - One confirmed booking at 1:00-1:30 PM on 2030-09-16
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    appt = AppointmentType(
        name="Home Tour", duration_minutes=30,
        buffer_before_minutes=0, buffer_after_minutes=0,
        calendar_id="primary", active=True, color="#3b82f6",
        location="123 Main St",
        max_concurrent=max_concurrent,
    )
    appt._custom_fields = "[]"
    db.add(appt)

    # Monday rule
    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True)
    db.add(rule)
    db.commit()

    existing = Booking(
        appointment_type_id=appt.id,
        start_datetime=BOOKING_START,
        end_datetime=BOOKING_END,
        guest_name="First Guest",
        guest_email="first@example.com",
        guest_phone="555-0001",
        status="confirmed",
    )
    existing._custom_field_responses = "{}"
    db.add(existing)
    db.commit()

    type_id = appt.id
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[require_csrf] = lambda: None
    return TestClient(app), Session, type_id


def _mock_cal_returning_booking_as_busy():
    """Return a mock CalendarService whose get_busy_intervals reports 1:00-1:30 PM as busy."""
    mock_cal = MagicMock()
    # Google Calendar returns the booking time as busy (naive datetimes = UTC when tz=UTC)
    mock_cal.get_busy_intervals.return_value = [
        (BOOKING_START, BOOKING_END),
    ]
    mock_cal.get_events_for_day.return_value = []
    return mock_cal


def _patch_slots_settings(mock_settings_fn):
    mock_settings_fn.return_value.google_client_id = "fake-id"
    mock_settings_fn.return_value.google_client_secret = "fake-secret"
    mock_settings_fn.return_value.google_redirect_uri = "http://localhost"


# ---- Tests ----------------------------------------------------------------

def test_group_showing_slot_appears_when_one_booking_exists():
    """max_concurrent=2 with 1 booking: the time slot should still be available."""
    client, Session, type_id = make_group_client(max_concurrent=2)

    db = Session()
    set_setting(db, "google_refresh_token", "fake-token")
    set_setting(db, "timezone", "UTC")
    db.commit()
    db.close()

    mock_cal = _mock_cal_returning_booking_as_busy()

    with patch("app.routers.slots.CalendarService", return_value=mock_cal), \
         patch("app.routers.slots.get_settings") as ms:
        _patch_slots_settings(ms)
        response = client.get(f"/slots?type_id={type_id}&date={TEST_DATE}")

    assert response.status_code == 200
    assert "1:00 PM" in response.text, \
        "1:00 PM slot should be available (only 1 of 2 concurrent slots taken)"
    app.dependency_overrides.clear()


def test_group_showing_slot_blocked_at_capacity():
    """max_concurrent=2 with 2 bookings: the time slot should NOT be available."""
    client, Session, type_id = make_group_client(max_concurrent=2)

    db = Session()
    set_setting(db, "google_refresh_token", "fake-token")
    set_setting(db, "timezone", "UTC")
    # Second booking — now at capacity
    second = Booking(
        appointment_type_id=type_id,
        start_datetime=BOOKING_START,
        end_datetime=BOOKING_END,
        guest_name="Second Guest",
        guest_email="second@example.com",
        guest_phone="555-0002",
        status="confirmed",
    )
    second._custom_field_responses = "{}"
    db.add(second)
    db.commit()
    db.close()

    mock_cal = _mock_cal_returning_booking_as_busy()

    with patch("app.routers.slots.CalendarService", return_value=mock_cal), \
         patch("app.routers.slots.get_settings") as ms:
        _patch_slots_settings(ms)
        response = client.get(f"/slots?type_id={type_id}&date={TEST_DATE}")

    assert response.status_code == 200
    assert "1:00 PM" not in response.text, \
        "1:00 PM slot should be blocked (at capacity: 2 of 2 taken)"
    app.dependency_overrides.clear()


def test_standard_type_blocks_slot_normally():
    """max_concurrent=1 (default): existing booking still blocks the slot."""
    client, Session, type_id = make_group_client(max_concurrent=1)

    db = Session()
    set_setting(db, "google_refresh_token", "fake-token")
    set_setting(db, "timezone", "UTC")
    db.commit()
    db.close()

    mock_cal = _mock_cal_returning_booking_as_busy()

    with patch("app.routers.slots.CalendarService", return_value=mock_cal), \
         patch("app.routers.slots.get_settings") as ms:
        _patch_slots_settings(ms)
        response = client.get(f"/slots?type_id={type_id}&date={TEST_DATE}")

    assert response.status_code == 200
    assert "1:00 PM" not in response.text, \
        "1:00 PM slot should be blocked for standard type (max_concurrent=1)"
    app.dependency_overrides.clear()
```

### Step 2: Run tests to verify they fail

```bash
pytest tests/test_group_showings.py -v
```

Expected: `test_group_showing_slot_appears_when_one_booking_exists` FAILS — 1:00 PM blocked. The other two may pass by coincidence; that's fine.

### Step 3: Add group showing logic to `_compute_slots_for_type()`

In `app/routers/slots.py`, the `Booking` model is not currently imported. Add it to the existing import at line ~11:

```python
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod, Booking
```

Then in `_compute_slots_for_type()`, add this block **after all `busy_intervals.append(...)` calls** (after the webcal loop, around line 122 — just before the call to `_build_free_windows()`):

```python
    # Group showings: un-block same-type confirmed booking intervals so the slot
    # engine sees them as available, then post-filter by concurrent count.
    same_type_bookings: list = []
    if appt_type.max_concurrent > 1:
        day_local_start = datetime.combine(target_date, time_type(0, 0))
        day_local_end = datetime.combine(target_date, time_type(23, 59, 59))
        same_type_bookings = db.query(Booking).filter(
            Booking.appointment_type_id == appt_type.id,
            Booking.status == "confirmed",
            Booking.start_datetime >= day_local_start,
            Booking.start_datetime <= day_local_end,
        ).all()
        if same_type_bookings:
            same_type_intervals = {
                (b.start_datetime, b.end_datetime) for b in same_type_bookings
            }
            busy_intervals = [
                (s, e) for (s, e) in busy_intervals
                if (s, e) not in same_type_intervals
            ]
```

Then, **after** the `slots = filter_by_advance_notice(...)` call (before the `return`), add:

```python
    # Group showings: post-filter — slot is only valid if concurrent count < max_concurrent
    if appt_type.max_concurrent > 1 and same_type_bookings:
        def _overlapping_count(slot_time: time_type) -> int:
            slot_start_dt = datetime.combine(target_date, slot_time)
            slot_end_dt = slot_start_dt + timedelta(minutes=appt_type.duration_minutes)
            return sum(
                1 for b in same_type_bookings
                if b.start_datetime < slot_end_dt and b.end_datetime > slot_start_dt
            )
        slots = [s for s in slots if _overlapping_count(s) < appt_type.max_concurrent]
```

### Step 4: Run tests to verify they pass

```bash
pytest tests/test_group_showings.py -v
```

Expected: all 3 tests PASS.

### Step 5: Run full suite

```bash
pytest -q
```

Expected: all existing tests still pass.

### Step 6: Commit

```bash
git add app/routers/slots.py tests/test_group_showings.py
git commit -m "feat: allow same-type concurrent bookings in slot engine (issue 14)"
```

---

## Task 3: Skip drive time blocks for group showings

**Files:**
- Modify: `app/routers/booking.py` (lines 610-626, the drive time block section of `submit_booking`)
- Test: `tests/test_group_showings.py` (add a test)

### Context

When a new booking overlaps with an existing confirmed same-type booking, the owner is already at the location — no drive time is needed. Skip `_create_drive_time_blocks()` entirely for group showings (both before and after blocks are omitted).

Detection: after the new booking is saved to the DB, query for any OTHER confirmed booking of the same `appointment_type_id` whose time range overlaps with the new booking (`other.start < new.end AND other.end > new.start`). The check short-circuits to False when `max_concurrent == 1`, so it only runs for group showing types.

### Step 1: Write the failing test

Add to `tests/test_group_showings.py`:

```python
def test_group_showing_skips_drive_time_blocks():
    """A group showing booking should not trigger _create_drive_time_blocks."""
    client, Session, type_id = make_group_client(max_concurrent=2)

    # Enable drive time on the appointment type
    db = Session()
    appt = db.query(AppointmentType).filter_by(id=type_id).first()
    appt.requires_drive_time = True
    db.commit()
    db.close()

    block_calls = []

    def fake_blocks(*args, **kwargs):
        block_calls.append(True)
        return []

    mock_settings = MagicMock()
    mock_settings.google_client_id = "fake-id"
    mock_settings.google_client_secret = "fake-secret"
    mock_settings.google_redirect_uri = "http://localhost"
    mock_settings.resend_api_key = ""
    mock_settings.from_email = "test@example.com"

    with patch("app.routers.booking.get_settings", return_value=mock_settings), \
         patch("app.routers.booking._create_drive_time_blocks", side_effect=fake_blocks), \
         patch("app.services.calendar.CalendarService.create_event", return_value="evt-id"):
        db2 = Session()
        set_setting(db2, "google_refresh_token", "fake-token")
        db2.commit()
        db2.close()

        response = client.post("/book", data={
            "type_id": str(type_id),
            "start_datetime": BOOKING_START.isoformat(),
            "guest_name": "Second Guest",
            "guest_email": "second@example.com",
            "guest_phone": "555-9999",
        })

    assert response.status_code == 200
    assert len(block_calls) == 0, \
        "Drive time block events must NOT be created for a group showing"
    app.dependency_overrides.clear()
```

### Step 2: Run test to verify it fails

```bash
pytest tests/test_group_showings.py::test_group_showing_skips_drive_time_blocks -v
```

Expected: FAIL — `_create_drive_time_blocks` is called (block_calls has 1 entry).

### Step 3: Wrap the drive time block call with a group showing check

In `app/routers/booking.py`, replace lines 610-626 (the drive time block section):

**Before:**
```python
        # Drive time block events (owner-only, non-fatal)
        if appt_type.requires_drive_time and appt_type.location:
            home_address = get_setting(db, "home_address", "")
            dt_ids = _create_drive_time_blocks(
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
            if dt_ids:
                booking.drive_time_event_ids = dt_ids
                db.commit()
```

**After:**
```python
        # Drive time block events (owner-only, non-fatal)
        # Skip if this is a group showing — owner is already at the location.
        is_group_showing = appt_type.max_concurrent > 1 and db.query(Booking).filter(
            Booking.appointment_type_id == appt_type.id,
            Booking.status == "confirmed",
            Booking.id != booking.id,
            Booking.start_datetime < booking.end_datetime,
            Booking.end_datetime > booking.start_datetime,
        ).first() is not None
        if appt_type.requires_drive_time and appt_type.location and not is_group_showing:
            home_address = get_setting(db, "home_address", "")
            dt_ids = _create_drive_time_blocks(
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
            if dt_ids:
                booking.drive_time_event_ids = dt_ids
                db.commit()
```

### Step 4: Run tests to verify they pass

```bash
pytest tests/test_group_showings.py -v
```

Expected: all pass.

### Step 5: Run full suite

```bash
pytest -q
```

Expected: all pass.

### Step 6: Commit

```bash
git add app/routers/booking.py tests/test_group_showings.py
git commit -m "fix: skip drive time block events for group showings (issue 14)"
```

---

## Task 4: "Group Showing" badge in admin bookings list

**Files:**
- Modify: `app/routers/admin.py` (`bookings_page`, lines 391-409)
- Modify: `app/templates/admin/bookings.html` (Type column in both upcoming and past tables)
- Test: `tests/test_group_showings.py` (add a test)

### Context

The admin bookings list should show a small green "Group" badge next to the appointment type name for any booking that overlaps with another confirmed same-type booking. This is computed in `bookings_page()` by comparing all shown bookings against each other and passing a `group_showing_ids` set to the template.

### Step 1: Write the failing test

Add to `tests/test_group_showings.py`:

```python
def test_admin_bookings_shows_group_badge():
    """Admin bookings list should show a 'Group' badge for overlapping same-type bookings."""
    from app.routers.admin import require_admin

    client, Session, type_id = make_group_client(max_concurrent=2)
    app.dependency_overrides[require_admin] = lambda: "admin"

    response = client.get("/admin/bookings")

    assert response.status_code == 200
    assert "Group" in response.text, \
        "Should show 'Group' badge for the overlapping booking in the bookings list"
    app.dependency_overrides.clear()
```

### Step 2: Run test to verify it fails

```bash
pytest tests/test_group_showings.py::test_admin_bookings_shows_group_badge -v
```

Expected: FAIL — "Group" not found in response text (badge not yet rendered).

### Step 3: Compute `group_showing_ids` in `bookings_page()`

In `app/routers/admin.py`, replace the `bookings_page` function (lines 391-409):

```python
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
    # Identify bookings that overlap with another confirmed booking of the same type
    confirmed_shown = upcoming + [b for b in past if b.status == "confirmed"]
    group_showing_ids: set[int] = set()
    for b in confirmed_shown:
        for other in confirmed_shown:
            if (other.id != b.id
                    and other.appointment_type_id == b.appointment_type_id
                    and b.start_datetime < other.end_datetime
                    and b.end_datetime > other.start_datetime):
                group_showing_ids.add(b.id)
                break
    return templates.TemplateResponse("admin/bookings.html", {
        "request": request, "upcoming": upcoming, "past": past,
        "group_showing_ids": group_showing_ids,
        "flash": _get_flash(request),
    })
```

### Step 4: Add badge to upcoming table in bookings.html

In `app/templates/admin/bookings.html`, change the Type `<td>` in the upcoming table (line ~19):

**Before:**
```html
    <td>{{ b.appointment_type.name }}</td>
```

**After:**
```html
    <td>
      {{ b.appointment_type.name }}
      {% if b.id in group_showing_ids %}
      <span style="display:inline-block;font-size:.7rem;font-weight:600;
                   padding:.1rem .4rem;border-radius:4px;
                   background:#dcfce7;color:#166534;margin-left:.25rem;">
        Group
      </span>
      {% endif %}
    </td>
```

### Step 5: Add badge to past bookings table in bookings.html

Change the Type `<td>` in the past table (line ~51) the same way:

**Before:**
```html
      <td>{{ b.appointment_type.name }}</td>
```

**After:**
```html
      <td>
        {{ b.appointment_type.name }}
        {% if b.id in group_showing_ids %}
        <span style="display:inline-block;font-size:.7rem;font-weight:600;
                     padding:.1rem .4rem;border-radius:4px;
                     background:#dcfce7;color:#166534;margin-left:.25rem;">
          Group
        </span>
        {% endif %}
      </td>
```

### Step 6: Run tests to verify they pass

```bash
pytest tests/test_group_showings.py -v
```

Expected: all pass including `test_admin_bookings_shows_group_badge`.

### Step 7: Run full suite

```bash
pytest -q
```

Expected: all pass.

### Step 8: Commit

```bash
git add app/routers/admin.py app/templates/admin/bookings.html tests/test_group_showings.py
git commit -m "feat: show Group badge in admin bookings list for concurrent bookings (issue 14)"
```

---

## Final verification

```bash
pytest -v
```

Expected: all tests pass (~157+ passing, 17 skipped).

**Manual verification checklist (on preview environment after deploy):**
1. Admin → Appointment Types → edit "Home Tour" → set Max concurrent bookings = 2 → Save
2. Open booking page as a guest → pick a date/time → complete booking
3. Open a new private browser tab → pick the same date and the same time slot → verify it still appears as available → complete booking
4. Open a third private tab → pick the same slot → verify it NO LONGER appears (at capacity)
5. Admin → Bookings → verify both overlapping bookings show a green "Group" badge
6. Admin → Appointment Types → set Max concurrent back to 1 → verify form saves correctly and the slot is now blocked after one booking
