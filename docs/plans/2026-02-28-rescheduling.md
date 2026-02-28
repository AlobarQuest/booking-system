# Rescheduling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Allow guests to reschedule their own appointments via a secure UUID link in their confirmation email, and allow the admin to reschedule any upcoming booking from the admin panel.

**Architecture:** A `reschedule_token` UUID4 stored on each `Booking` authenticates guest reschedule requests without login. Both guest and admin paths share a `_perform_reschedule()` helper (in `app/routers/booking.py`) that: creates the new calendar event first, deletes the old one (non-fatal), updates the booking record, and emails a new confirmation. A shared slot-computation helper `_compute_slots_for_type()` (extracted from `app/routers/slots.py`) serves both the existing `/slots` endpoint and the new `/reschedule/{token}/slots` endpoint.

**Tech Stack:** FastAPI, SQLAlchemy, Jinja2, HTMX (slot loading), Google Calendar API (existing `CalendarService.delete_event()` already exists), Resend email.

---

## Key Files

| File | Role |
|------|------|
| `app/models.py` | Add `reschedule_token` to `Booking` |
| `app/database.py` | Migration for `reschedule_token` + backfill |
| `app/services/booking.py` | Generate token in `create_booking()` |
| `app/services/email.py` | Add `reschedule_url` param to `send_guest_confirmation` |
| `app/routers/booking.py` | Guest reschedule routes + `_perform_reschedule()` helper |
| `app/routers/slots.py` | Extract `_compute_slots_for_type()` helper |
| `app/routers/admin.py` | Admin reschedule routes |
| `app/templates/booking/reschedule.html` | Guest reschedule page |
| `app/templates/booking/reschedule_slots_partial.html` | Slot buttons for reschedule (no HTMX, custom onclick) |
| `app/templates/booking/reschedule_success.html` | Guest success page |
| `app/templates/admin/admin_reschedule.html` | Admin reschedule page |
| `app/templates/admin/bookings.html` | Add Reschedule button |
| `tests/test_reschedule.py` | Guest reschedule tests |
| `tests/test_admin_reschedule.py` | Admin reschedule tests |

---

## How the existing code works (context)

- `app/routers/slots.py:get_slots()` — computes available slots for a given `type_id` + `date`. Has 120 lines of slot computation. We will extract the core logic into `_compute_slots_for_type()` and call that from both `get_slots()` and the new reschedule slots endpoint.
- `app/services/calendar.py:CalendarService.delete_event()` — already exists (lines 122–125). No changes needed.
- `app/services/booking.py:create_booking()` — creates a `Booking` in the DB. We add `reschedule_token=str(uuid.uuid4())` here.
- `app/database.py:init_db()` — PRAGMA loop pattern to add columns to existing tables. We add `reschedule_token` to bookings, then backfill NULL/empty rows with UUIDs.
- `app/services/email.py:send_guest_confirmation()` — sends the confirmation email. We add a `reschedule_url: str = ""` parameter and include it in the default template.
- `app/templates/booking/confirmation_partial.html` — currently just shows "Confirmed!". No change needed; the reschedule link is in the email.
- `app/routers/admin.py` — cancel route pattern (lines 406–448) is the model for the admin reschedule route.
- `tests/conftest.py` — provides `client` fixture (in-memory SQLite, overrides `get_db`). Use this fixture in new test files.

---

## Task 1: Schema — `reschedule_token` on Booking

**Files:**
- Modify: `app/models.py`
- Modify: `app/database.py`
- Modify: `app/services/booking.py`
- Test: `tests/test_booking_route.py`

**Step 1: Write the failing test**

Add to `tests/test_booking_route.py`:

```python
def test_booking_has_reschedule_token():
    client, Session = setup_client()
    db = Session()
    appt_id = db.query(AppointmentType).first().id
    db.close()

    client.post("/book", data={
        "type_id": str(appt_id),
        "start_datetime": "2025-06-01T10:00:00",
        "guest_name": "Token Test",
        "guest_email": "token@example.com",
    })

    from app.models import Booking
    db2 = Session()
    booking = db2.query(Booking).first()
    assert booking is not None
    assert len(booking.reschedule_token) == 36  # UUID4 string
    assert "-" in booking.reschedule_token
    db2.close()
    app.dependency_overrides.clear()
```

**Step 2: Run test to verify it fails**

```bash
cd /home/devon/Projects/BookingAssistant
pytest tests/test_booking_route.py::test_booking_has_reschedule_token -v
```

Expected: FAIL — `AttributeError: 'Booking' object has no attribute 'reschedule_token'`

**Step 3: Add column to `app/models.py`**

In the `Booking` class, after `location: Mapped[str] = mapped_column(Text, default="")`:

```python
reschedule_token: Mapped[str] = mapped_column(String(36), default="", index=True)
```

**Step 4: Add migration in `app/database.py`**

In `init_db()`, find the existing `bookings` PRAGMA block:

```python
existing_b = {row[1] for row in conn.execute(text("PRAGMA table_info(bookings)"))}
for col, definition in [
    ("location", "TEXT NOT NULL DEFAULT ''"),
]:
    if col not in existing_b:
        conn.execute(text(f"ALTER TABLE bookings ADD COLUMN {col} {definition}"))
```

Change it to:

```python
existing_b = {row[1] for row in conn.execute(text("PRAGMA table_info(bookings)"))}
for col, definition in [
    ("location", "TEXT NOT NULL DEFAULT ''"),
    ("reschedule_token", "VARCHAR(36) NOT NULL DEFAULT ''"),
]:
    if col not in existing_b:
        conn.execute(text(f"ALTER TABLE bookings ADD COLUMN {col} {definition}"))

# Backfill reschedule_token for existing bookings that have none
import uuid as _uuid
rows = conn.execute(text("SELECT id FROM bookings WHERE reschedule_token = ''")).fetchall()
for (row_id,) in rows:
    conn.execute(
        text("UPDATE bookings SET reschedule_token = :tok WHERE id = :id"),
        {"tok": str(_uuid.uuid4()), "id": row_id},
    )
```

**Step 5: Generate token in `app/services/booking.py`**

Add `import uuid` at the top. In `create_booking()`, add `reschedule_token=str(uuid.uuid4())` to the `Booking(...)` constructor:

```python
import uuid

def create_booking(...) -> Booking:
    booking = Booking(
        appointment_type_id=appt_type.id,
        start_datetime=start_dt,
        end_datetime=end_dt,
        guest_name=guest_name,
        guest_email=guest_email,
        guest_phone=guest_phone,
        notes=notes,
        google_event_id=google_event_id,
        location=location,
        status="confirmed",
        reschedule_token=str(uuid.uuid4()),
    )
    ...
```

**Step 6: Run all tests**

```bash
pytest -v
```

Expected: all pass (including new test).

**Step 7: Commit**

```bash
git add app/models.py app/database.py app/services/booking.py tests/test_booking_route.py
git commit -m "feat: add reschedule_token to Booking"
```

---

## Task 2: Include reschedule link in confirmation email

**Files:**
- Modify: `app/services/email.py`
- Modify: `app/routers/booking.py`
- Test: `tests/test_booking_route.py`

**Step 1: Write the failing test**

Add to `tests/test_booking_route.py`:

```python
def test_confirmation_email_includes_reschedule_link():
    from unittest.mock import patch
    from app.config import Settings

    client, Session = setup_client()
    db = Session()
    appt_id = db.query(AppointmentType).first().id
    db.close()

    mock_settings = Settings(
        resend_api_key="fake-key",
        from_email="from@example.com",
    )

    with patch("app.routers.booking.get_settings", return_value=mock_settings), \
         patch("app.services.email.resend.Emails.send") as mock_send:
        client.post("/book", data={
            "type_id": str(appt_id),
            "start_datetime": "2025-06-01T11:00:00",
            "guest_name": "Link Test",
            "guest_email": "link@example.com",
        })

    assert mock_send.called
    sent_html = mock_send.call_args[0][0]["html"]
    assert "/reschedule/" in sent_html
    app.dependency_overrides.clear()
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_booking_route.py::test_confirmation_email_includes_reschedule_link -v
```

Expected: FAIL — reschedule link not in email HTML.

**Step 3: Update `app/services/email.py`**

Update `_GUEST_CONFIRMATION_DEFAULT` to include the reschedule link:

```python
_GUEST_CONFIRMATION_DEFAULT = """\
<h2>Your appointment is confirmed</h2>
<p>Hi {guest_name},</p>
<p>Your <strong>{appt_type}</strong> is confirmed:</p>
<p><strong>Date/Time:</strong> {date_time}</p>
{custom_fields}
<p>Need to reschedule? <a href="{reschedule_url}">Click here to pick a new time</a></p>
<p>If you need to cancel, please reply to this email.</p>
<p>— {owner_name}</p>"""
```

Add `reschedule_url: str = ""` to `send_guest_confirmation`'s signature (after `template`). Pass it into the `.format()` call:

```python
def send_guest_confirmation(
    api_key: str,
    from_email: str,
    guest_email: str,
    guest_name: str,
    appt_type_name: str,
    start_dt: datetime,
    end_dt: datetime,
    custom_responses: dict,
    owner_name: str,
    template: str = "",
    reschedule_url: str = "",
):
    resend.api_key = api_key
    custom_html = "".join(
        f"<p><strong>{escape(str(k))}:</strong> {escape(str(v))}</p>"
        for k, v in custom_responses.items() if v
    )
    try:
        html = (template or _GUEST_CONFIRMATION_DEFAULT).format(
            guest_name=escape(guest_name),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
            owner_name=escape(owner_name),
            custom_fields=custom_html,
            reschedule_url=reschedule_url,
        )
    except (KeyError, ValueError, IndexError):
        html = _GUEST_CONFIRMATION_DEFAULT.format(
            guest_name=escape(guest_name),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
            owner_name=escape(owner_name),
            custom_fields=custom_html,
            reschedule_url=reschedule_url,
        )
    resend.Emails.send({
        "from": from_email,
        "to": [guest_email],
        "subject": f"Your {appt_type_name} is confirmed — {start_dt.strftime('%b %-d')}",
        "html": html,
    })
```

**Step 4: Pass `reschedule_url` in `app/routers/booking.py`**

In `submit_booking()`, find the `send_guest_confirmation(...)` call (around line 297). Before it, build the URL from the booking token:

```python
reschedule_url = str(request.base_url).rstrip('/') + f"/reschedule/{booking.reschedule_token}"
```

Then add `reschedule_url=reschedule_url` to the `send_guest_confirmation(...)` call.

**Step 5: Run all tests**

```bash
pytest -v
```

Expected: all pass.

**Step 6: Commit**

```bash
git add app/services/email.py app/routers/booking.py tests/test_booking_route.py
git commit -m "feat: include reschedule link in confirmation email"
```

---

## Task 3: Slot computation helper + reschedule slots endpoint

**Files:**
- Modify: `app/routers/slots.py` (extract `_compute_slots_for_type()`)
- Modify: `app/routers/booking.py` (add `GET /reschedule/{token}/slots`)
- Create: `app/templates/booking/reschedule_slots_partial.html`
- Test: `tests/test_reschedule.py`

**Step 1: Create test file and write failing test**

Create `tests/test_reschedule.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType, Booking
from app.dependencies import require_csrf


def make_client_with_booking():
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
        admin_initiated=False,
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.commit()

    from datetime import datetime
    booking = Booking(
        appointment_type_id=appt.id,
        start_datetime=datetime(2025, 9, 1, 10, 0),
        end_datetime=datetime(2025, 9, 1, 10, 30),
        guest_name="Jane Smith",
        guest_email="jane@example.com",
        guest_phone="",
        notes="",
        status="confirmed",
        reschedule_token="test-token-1234-abcd-5678-efgh90123456",
    )
    booking._custom_field_responses = "{}"
    db.add(booking)
    db.commit()
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[require_csrf] = lambda: None
    return TestClient(app), Session


def test_reschedule_slots_returns_html():
    client, _ = make_client_with_booking()
    response = client.get(
        "/reschedule/test-token-1234-abcd-5678-efgh90123456/slots?date=2025-09-15"
    )
    assert response.status_code == 200
    assert "slot-btn" in response.text or "no-slots" in response.text
    app.dependency_overrides.clear()


def test_reschedule_slots_404_for_bad_token():
    client, _ = make_client_with_booking()
    response = client.get("/reschedule/bad-token/slots?date=2025-09-15")
    assert response.status_code == 404
    app.dependency_overrides.clear()
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_reschedule.py::test_reschedule_slots_returns_html -v
```

Expected: FAIL — 404 (route doesn't exist yet).

**Step 3: Extract `_compute_slots_for_type()` from `app/routers/slots.py`**

Add this helper function to `app/routers/slots.py` ABOVE the `get_slots` route. It contains the core computation extracted from `get_slots()`:

```python
def _compute_slots_for_type(
    appt_type: "AppointmentType",
    target_date: "date_type",
    db: "Session",
    destination: str = "",
) -> list[dict]:
    """Compute available time slots for a given appointment type and date.

    Returns a list of {"value": "HH:MM", "display": "H:MM AM/PM"} dicts.
    destination: override location (used for admin_initiated types).
    """
    import json as _json
    settings = get_settings()
    effective_location = destination if appt_type.admin_initiated else appt_type.location

    rules = db.query(AvailabilityRule).filter_by(active=True).all()
    blocked = db.query(BlockedPeriod).all()
    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    refresh_token = get_setting(db, "google_refresh_token", "")
    tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))

    local_midnight = datetime.combine(target_date, time_type(0, 0)).replace(tzinfo=tz)
    day_start = local_midnight.astimezone(dt_timezone.utc).replace(tzinfo=None)
    day_end = (local_midnight + timedelta(days=1)).astimezone(dt_timezone.utc).replace(tzinfo=None)

    conflict_cals_raw = get_setting(db, "conflict_calendars", "[]")
    try:
        conflict_cals = _json.loads(conflict_cals_raw)
    except (ValueError, TypeError):
        conflict_cals = []
    extra_google_ids = [c["id"] for c in conflict_cals if c.get("type") == "google" and c.get("id")]
    webcal_urls = [c["id"] for c in conflict_cals if c.get("type") == "webcal" and c.get("id")]

    busy_intervals = []
    window_intervals = []
    local_day_events = []

    google_ids_for_freebusy = set()
    google_ids_for_freebusy.add(appt_type.calendar_id)
    google_ids_for_freebusy.update(extra_google_ids)

    if refresh_token and settings.google_client_id:
        cal = CalendarService(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )

        if appt_type.calendar_window_enabled and appt_type.calendar_window_title:
            window_cal_id = appt_type.calendar_window_calendar_id or appt_type.calendar_id
            google_ids_for_freebusy.discard(window_cal_id)
            try:
                window_cal_events = cal.get_events_for_day(refresh_token, window_cal_id, day_start, day_end)
                title_lower = appt_type.calendar_window_title.lower().strip()
                for ev in window_cal_events:
                    local_start = ev["start"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    local_end = ev["end"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    if ev["summary"].lower().strip() == title_lower:
                        window_intervals.append((local_start.time(), local_end.time()))
                    else:
                        busy_intervals.append((local_start, local_end))
            except Exception:
                pass

        if google_ids_for_freebusy:
            try:
                utc_busy = cal.get_busy_intervals(refresh_token, list(google_ids_for_freebusy), day_start, day_end)
                for utc_start, utc_end in utc_busy:
                    local_start = utc_start.replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    local_end = utc_end.replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    busy_intervals.append((local_start, local_end))
            except Exception:
                pass

        if appt_type.requires_drive_time and effective_location:
            try:
                day_events_utc = cal.get_events_for_day(refresh_token, "primary", day_start, day_end)
                for ev in day_events_utc:
                    local_start = ev["start"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    local_end = ev["end"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                    local_day_events.append({**ev, "start": local_start, "end": local_end})
            except Exception:
                pass

    for webcal_url in webcal_urls:
        try:
            from app.services.calendar import fetch_webcal_events
            wc_events = fetch_webcal_events(webcal_url, day_start, day_end)
            for ev in wc_events:
                local_start = ev["start"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                local_end = ev["end"].replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                busy_intervals.append((local_start, local_end))
                if appt_type.requires_drive_time and effective_location and ev["location"]:
                    local_day_events.append({**ev, "start": local_start, "end": local_end})
        except Exception:
            pass

    windows = _build_free_windows(target_date, rules, blocked, busy_intervals, appointment_type_id=appt_type.id)

    if window_intervals:
        windows = intersect_windows(windows, window_intervals)

    if appt_type.requires_drive_time and effective_location:
        home_address = get_setting(db, "home_address", "")
        windows = trim_windows_for_drive_time(
            windows, target_date, local_day_events,
            destination=effective_location,
            home_address=home_address,
            db=db,
        )

    now_local = datetime.now(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
    slots = split_into_slots(
        windows, appt_type.duration_minutes,
        appt_type.buffer_before_minutes, appt_type.buffer_after_minutes,
    )
    slots = filter_by_advance_notice(slots, target_date, min_advance, now_local)

    return [
        {"value": s.strftime("%H:%M"), "display": s.strftime("%-I:%M %p")}
        for s in slots
    ]
```

Then simplify `get_slots()` to call this helper:

```python
@router.get("/slots", response_class=HTMLResponse)
def get_slots(
    request: Request,
    type_id: int = Query(...),
    date: str = Query(...),
    destination: str = Query(""),
    db: Session = Depends(get_db),
):
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return HTMLResponse("<p class='no-slots'>Appointment type not found.</p>")
    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("<p class='no-slots'>Invalid date format.</p>")

    slot_data = _compute_slots_for_type(appt_type, target_date, db, destination=destination)
    return templates.TemplateResponse(
        "booking/slots_partial.html",
        {"request": request, "slots": slot_data, "type_id": type_id, "date": date},
    )
```

**Step 4: Create `app/templates/booking/reschedule_slots_partial.html`**

```html
{% if slots %}
<div class="slots-grid">
  {% for slot in slots %}
  <button type="button" class="slot-btn"
          onclick="selectRescheduleSlot('{{ slot.value }}', '{{ slot.display }}')">
    {{ slot.display }}
  </button>
  {% endfor %}
</div>
{% else %}
<p class="no-slots">No available times on this date. Please choose another day.</p>
{% endif %}
```

**Step 5: Add `GET /reschedule/{token}/slots` to `app/routers/booking.py`**

Add this import at the top of `app/routers/booking.py`:

```python
from app.routers.slots import _compute_slots_for_type
from datetime import date as date_type
```

Add the route:

```python
@router.get("/reschedule/{token}/slots", response_class=HTMLResponse)
def reschedule_slots(
    request: Request,
    token: str,
    date: str,
    db: Session = Depends(get_db),
):
    booking = db.query(Booking).filter_by(reschedule_token=token, status="confirmed").first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")
    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("<p class='no-slots'>Invalid date format.</p>")

    slot_data = _compute_slots_for_type(
        booking.appointment_type,
        target_date,
        db,
        destination=booking.location,
    )
    return templates.TemplateResponse(
        "booking/reschedule_slots_partial.html",
        {"request": request, "slots": slot_data},
    )
```

Also add `from fastapi import HTTPException` to the imports in `booking.py` if not already present (check first).

**Step 6: Run all tests**

```bash
pytest -v
```

Expected: all pass.

**Step 7: Commit**

```bash
git add app/routers/slots.py app/routers/booking.py \
        app/templates/booking/reschedule_slots_partial.html \
        tests/test_reschedule.py
git commit -m "feat: extract _compute_slots_for_type, add reschedule slots endpoint"
```

---

## Task 4: Guest reschedule page (GET) + POST + success page

**Files:**
- Modify: `app/routers/booking.py` (add GET/POST /reschedule/{token}, add `_perform_reschedule()`)
- Create: `app/templates/booking/reschedule.html`
- Create: `app/templates/booking/reschedule_success.html`
- Test: `tests/test_reschedule.py`

**Step 1: Write failing tests**

Add to `tests/test_reschedule.py`:

```python
def test_reschedule_page_loads_for_valid_token():
    client, _ = make_client_with_booking()
    response = client.get("/reschedule/test-token-1234-abcd-5678-efgh90123456")
    assert response.status_code == 200
    assert "Jane Smith" in response.text or "Home Tour" in response.text
    app.dependency_overrides.clear()


def test_reschedule_page_404_for_invalid_token():
    client, _ = make_client_with_booking()
    response = client.get("/reschedule/no-such-token")
    assert response.status_code == 404
    app.dependency_overrides.clear()


def test_reschedule_page_too_close():
    from datetime import datetime, timedelta
    client, Session = make_client_with_booking()
    db = Session()
    from app.dependencies import set_setting
    set_setting(db, "min_advance_hours", "24")
    # Update booking start to be 1 hour from now (within cutoff)
    booking = db.query(Booking).first()
    booking.start_datetime = datetime.utcnow() + timedelta(hours=1)
    db.commit()
    db.close()

    response = client.get("/reschedule/test-token-1234-abcd-5678-efgh90123456")
    assert response.status_code == 200
    assert "cannot be rescheduled" in response.text.lower() or "contact" in response.text.lower()
    app.dependency_overrides.clear()


def test_reschedule_post_updates_booking():
    client, Session = make_client_with_booking()
    response = client.post(
        "/reschedule/test-token-1234-abcd-5678-efgh90123456",
        data={"start_datetime": "2025-09-20T14:00:00"},
        follow_redirects=False,
    )
    # Should redirect to success page
    assert response.status_code in (200, 302)

    from datetime import datetime
    db = Session()
    booking = db.query(Booking).first()
    assert booking.start_datetime == datetime(2025, 9, 20, 14, 0, 0)
    db.close()
    app.dependency_overrides.clear()


def test_reschedule_post_invalid_token():
    client, _ = make_client_with_booking()
    response = client.post(
        "/reschedule/bad-token",
        data={"start_datetime": "2025-09-20T14:00:00"},
    )
    assert response.status_code == 404
    app.dependency_overrides.clear()


def test_reschedule_creates_event_before_deleting_old():
    from unittest.mock import patch, MagicMock
    from app.config import Settings

    client, Session = make_client_with_booking()
    # Pre-set an old event ID
    db = Session()
    booking = db.query(Booking).first()
    booking.google_event_id = "old-event-id"
    db.commit()
    db.close()

    call_order = []
    mock_settings = Settings(
        google_client_id="fake-id", google_client_secret="fake-secret",
        google_redirect_uri="http://localhost/callback",
    )

    def fake_create(**kwargs):
        call_order.append("create")
        return "new-event-id"

    def fake_delete(refresh_token, calendar_id, event_id):
        call_order.append("delete")

    with patch("app.routers.booking.get_settings", return_value=mock_settings), \
         patch("app.services.calendar.CalendarService.create_event", side_effect=fake_create), \
         patch("app.services.calendar.CalendarService.delete_event", side_effect=fake_delete):
        from app.dependencies import set_setting
        db2 = Session()
        set_setting(db2, "google_refresh_token", "fake-token")
        db2.close()

        client.post(
            "/reschedule/test-token-1234-abcd-5678-efgh90123456",
            data={"start_datetime": "2025-09-20T14:00:00"},
        )

    assert call_order.index("create") < call_order.index("delete"), (
        f"Expected create before delete, got order: {call_order}"
    )
    app.dependency_overrides.clear()
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_reschedule.py::test_reschedule_page_loads_for_valid_token \
       tests/test_reschedule.py::test_reschedule_post_updates_booking -v
```

Expected: FAIL — routes don't exist yet.

**Step 3: Create `app/templates/booking/reschedule.html`**

```html
{% extends "base.html" %}
{% block title %}Reschedule Appointment{% endblock %}
{% block header_title %}Reschedule Your Appointment{% endblock %}
{% block header_subtitle %}{{ booking.appointment_type.name }}{% endblock %}
{% block content %}

{% if error %}
<div class="card static" style="border-color:#dc2626;background:#fef2f2;margin-bottom:1rem;">
  <p style="color:#dc2626;">{{ error }}</p>
</div>
{% endif %}

<div class="card static" style="margin-bottom:1.5rem;">
  <h3 style="margin-bottom:.5rem;">Current Appointment</h3>
  <p><strong>{{ booking.appointment_type.name }}</strong><br>{{ current_display }}</p>
  {% if too_close %}
  <p style="color:#dc2626;margin-top:.75rem;">
    This appointment is within {{ min_advance_hours }} hours and cannot be rescheduled online.
    Please contact us directly to make changes.
  </p>
  {% endif %}
</div>

{% if not too_close %}
<div class="section">
  <label class="section-label" for="reschedule-date">Choose a new date</label>
  <input type="date" id="reschedule-date"
    min="{{ min_date }}" max="{{ max_date }}"
    hx-get="/reschedule/{{ token }}/slots"
    hx-target="#slot-area"
    hx-swap="innerHTML"
    hx-trigger="change"
    name="date">
</div>

<div id="slot-area" class="section"></div>

<div id="confirmation-section" style="display:none;" class="section">
  <div class="card" style="cursor:default;">
    <h2>Confirm Reschedule</h2>
    <p id="confirm-summary" style="color:#059669;font-weight:600;margin-bottom:1rem;"></p>
    <form method="post" action="/reschedule/{{ token }}">
      <input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
      <input type="hidden" name="start_datetime" id="confirm-datetime">
      <div style="display:flex;gap:.75rem;margin-top:.25rem;">
        <button type="submit" class="btn btn-primary">Confirm Reschedule</button>
        <button type="button" class="btn btn-secondary" onclick="cancelRescheduleSelection()">Cancel</button>
      </div>
    </form>
  </div>
</div>

<script>
function selectRescheduleSlot(value, display) {
  var date = document.getElementById('reschedule-date').value;
  document.getElementById('confirm-datetime').value = date + 'T' + value + ':00';
  document.getElementById('confirm-summary').textContent = display;
  document.getElementById('confirmation-section').style.display = 'block';
  document.getElementById('confirmation-section').scrollIntoView({behavior: 'smooth'});
  document.querySelectorAll('#slot-area .slot-btn').forEach(function(b) { b.classList.remove('selected'); });
  if (event && event.target) { event.target.classList.add('selected'); }
}
function cancelRescheduleSelection() {
  document.getElementById('confirmation-section').style.display = 'none';
  document.querySelectorAll('#slot-area .slot-btn').forEach(function(b) { b.classList.remove('selected'); });
}
</script>
{% endif %}

{% endblock %}
```

**Step 4: Create `app/templates/booking/reschedule_success.html`**

```html
{% extends "base.html" %}
{% block title %}Rescheduled{% endblock %}
{% block header_title %}Appointment Rescheduled{% endblock %}
{% block header_subtitle %}{{ booking.appointment_type.name }}{% endblock %}
{% block content %}
<div class="confirmation-box">
  <div class="confirmation-icon">✓</div>
  <h2>You're Rescheduled!</h2>
  <p>
    <strong>{{ booking.appointment_type.name }}</strong><br>
    {{ new_display }}<br><br>
    An updated confirmation has been sent to<br>
    <strong>{{ booking.guest_email }}</strong>
  </p>
</div>
{% endblock %}
```

**Step 5: Add `_perform_reschedule()` helper + GET/POST routes to `app/routers/booking.py`**

Add this helper function in `app/routers/booking.py`, after `_create_drive_time_blocks()`:

```python
def _perform_reschedule(
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
    from zoneinfo import ZoneInfo
    from app.services.calendar import CalendarService

    appt_type = booking.appointment_type
    new_end_dt = new_start_dt + timedelta(minutes=appt_type.duration_minutes)

    tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))
    start_utc = new_start_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)
    end_utc = new_end_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)

    refresh_token = get_setting(db, "google_refresh_token", "")
    old_event_id = booking.google_event_id
    new_event_id = ""

    if refresh_token and settings.google_client_id:
        cal = CalendarService(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )
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
                attendee_email=booking.guest_email if not appt_type.admin_initiated else "",
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
                pass

    # Update booking
    booking.start_datetime = new_start_dt
    booking.end_datetime = new_end_dt
    booking.google_event_id = new_event_id
    db.commit()

    # Send new confirmation email (non-fatal; only if guest email present)
    if booking.guest_email:
        notify_enabled = get_setting(db, "notifications_enabled", "true") == "true"
        if notify_enabled and settings.resend_api_key:
            from app.services.email import send_guest_confirmation
            reschedule_url = base_url + f"/reschedule/{booking.reschedule_token}"
            try:
                send_guest_confirmation(
                    api_key=settings.resend_api_key,
                    from_email=settings.from_email,
                    guest_email=booking.guest_email,
                    guest_name=booking.guest_name,
                    appt_type_name=appt_type.guest_event_title or appt_type.name,
                    start_dt=new_start_dt,
                    end_dt=new_end_dt,
                    custom_responses=booking.custom_field_responses,
                    owner_name=get_setting(db, "owner_name", ""),
                    template=get_setting(db, "email_guest_confirmation", ""),
                    reschedule_url=reschedule_url,
                )
            except Exception:
                pass
```

Add the GET route for the guest reschedule page:

```python
@router.get("/reschedule/{token}", response_class=HTMLResponse)
def reschedule_page(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    booking = db.query(Booking).filter_by(reschedule_token=token, status="confirmed").first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found or already cancelled.")

    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    max_future = int(get_setting(db, "max_future_days", "30"))
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))
    now_local = datetime.now(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
    cutoff = now_local + timedelta(hours=min_advance)
    too_close = booking.start_datetime <= cutoff

    min_date = cutoff.date().isoformat()
    max_date = (now_local + timedelta(days=max_future)).date().isoformat()
    current_display = booking.start_datetime.strftime("%A, %B %-d, %Y at %-I:%M %p")

    return templates.TemplateResponse("booking/reschedule.html", {
        "request": request,
        "booking": booking,
        "token": token,
        "too_close": too_close,
        "min_advance_hours": min_advance,
        "min_date": min_date,
        "max_date": max_date,
        "current_display": current_display,
    })
```

Add the POST route for performing the reschedule:

```python
@router.post("/reschedule/{token}", response_class=HTMLResponse)
@limiter.limit("10/hour")
async def submit_reschedule(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    _csrf_ok: None = Depends(require_csrf),
):
    form_data = await request.form()
    start_datetime_str = str(form_data.get("start_datetime", "")).strip()

    booking = db.query(Booking).filter_by(reschedule_token=token, status="confirmed").first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")

    try:
        new_start_dt = datetime.fromisoformat(start_datetime_str)
    except (ValueError, TypeError):
        return templates.TemplateResponse("booking/reschedule.html", {
            "request": request, "booking": booking, "token": token,
            "too_close": False, "min_advance_hours": 24,
            "min_date": "", "max_date": "",
            "current_display": booking.start_datetime.strftime("%A, %B %-d, %Y at %-I:%M %p"),
            "error": "Invalid date/time. Please try again.",
        })

    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))
    now_local = datetime.now(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
    cutoff = now_local + timedelta(hours=min_advance)
    if new_start_dt <= cutoff:
        return templates.TemplateResponse("booking/reschedule.html", {
            "request": request, "booking": booking, "token": token,
            "too_close": True, "min_advance_hours": min_advance,
            "min_date": cutoff.date().isoformat(),
            "max_date": (now_local + timedelta(days=int(get_setting(db, "max_future_days", "30")))).date().isoformat(),
            "current_display": booking.start_datetime.strftime("%A, %B %-d, %Y at %-I:%M %p"),
        })

    settings = get_settings()
    base_url = str(request.base_url).rstrip('/')
    try:
        _perform_reschedule(db, booking, new_start_dt, settings, base_url)
    except ValueError as exc:
        return templates.TemplateResponse("booking/reschedule.html", {
            "request": request, "booking": booking, "token": token,
            "too_close": False, "min_advance_hours": min_advance,
            "min_date": cutoff.date().isoformat(),
            "max_date": (now_local + timedelta(days=int(get_setting(db, "max_future_days", "30")))).date().isoformat(),
            "current_display": booking.start_datetime.strftime("%A, %B %-d, %Y at %-I:%M %p"),
            "error": str(exc),
        })

    new_display = new_start_dt.strftime("%A, %B %-d, %Y at %-I:%M %p")
    return templates.TemplateResponse("booking/reschedule_success.html", {
        "request": request,
        "booking": booking,
        "new_display": new_display,
    })
```

**Step 6: Run all tests**

```bash
pytest -v
```

Expected: all pass.

**Step 7: Commit**

```bash
git add app/routers/booking.py \
        app/templates/booking/reschedule.html \
        app/templates/booking/reschedule_success.html \
        tests/test_reschedule.py
git commit -m "feat: add guest reschedule page and POST route"
```

---

## Task 5: Admin reschedule

**Files:**
- Modify: `app/routers/admin.py`
- Create: `app/templates/admin/admin_reschedule.html`
- Modify: `app/templates/admin/bookings.html`
- Test: `tests/test_admin_reschedule.py`

**Step 1: Write failing tests**

Create `tests/test_admin_reschedule.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType, Booking
from app.dependencies import require_csrf
from app.routers.admin import require_admin


def make_admin_client_with_booking():
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
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.commit()

    from datetime import datetime
    booking = Booking(
        appointment_type_id=appt.id,
        start_datetime=datetime(2025, 9, 1, 10, 0),
        end_datetime=datetime(2025, 9, 1, 10, 30),
        guest_name="Admin Test Guest",
        guest_email="admin_guest@example.com",
        guest_phone="",
        notes="",
        status="confirmed",
        reschedule_token="admin-token-1234-5678-abcd-efgh90123456",
    )
    booking._custom_field_responses = "{}"
    db.add(booking)
    db.commit()
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[require_csrf] = lambda: None
    app.dependency_overrides[require_admin] = lambda: "admin"
    return TestClient(app), Session


def test_admin_reschedule_page_loads():
    client, Session = make_admin_client_with_booking()
    db = Session()
    booking_id = db.query(Booking).first().id
    db.close()

    response = client.get(f"/admin/bookings/{booking_id}/reschedule")
    assert response.status_code == 200
    assert "Admin Test Guest" in response.text
    assert "Home Tour" in response.text
    app.dependency_overrides.clear()


def test_admin_reschedule_updates_booking():
    client, Session = make_admin_client_with_booking()
    db = Session()
    booking_id = db.query(Booking).first().id
    db.close()

    response = client.post(
        f"/admin/bookings/{booking_id}/reschedule",
        data={"start_datetime": "2025-09-25T09:00:00"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/admin/bookings" in response.headers.get("location", "")

    from datetime import datetime
    db2 = Session()
    booking = db2.query(Booking).filter_by(id=booking_id).first()
    assert booking.start_datetime == datetime(2025, 9, 25, 9, 0, 0)
    db2.close()
    app.dependency_overrides.clear()
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_admin_reschedule.py -v
```

Expected: FAIL — routes don't exist.

**Step 3: Create `app/templates/admin/admin_reschedule.html`**

```html
{% extends "admin_base.html" %}
{% block title %}Reschedule Booking{% endblock %}
{% block content %}
<h1>Reschedule Booking</h1>

<div class="card" style="cursor:default;max-width:520px;margin-bottom:1.5rem;">
  <h2>Current Booking</h2>
  <p>
    <strong>{{ booking.guest_name }}</strong><br>
    {{ booking.appointment_type.name }}<br>
    <span style="color:#64748b;">{{ current_display }}</span>
  </p>
</div>

<div class="card" style="cursor:default;max-width:520px;">
  <h2>Pick a New Time</h2>
  <label>Date
    <input type="date" id="reschedule-date"
      min="{{ min_date }}" max="{{ max_date }}"
      hx-get="/reschedule/{{ booking.reschedule_token }}/slots"
      hx-target="#slot-area"
      hx-swap="innerHTML"
      hx-trigger="change">
  </label>
  <div id="slot-area" style="margin-top:1rem;"></div>
  <div id="confirmation-section" style="display:none;margin-top:1rem;">
    <p id="confirm-summary" style="color:#059669;font-weight:600;margin-bottom:.75rem;"></p>
    <form method="post" action="/admin/bookings/{{ booking.id }}/reschedule">
      <input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
      <input type="hidden" name="start_datetime" id="confirm-datetime">
      <div style="display:flex;gap:.75rem;">
        <button type="submit" class="btn btn-primary">Confirm Reschedule</button>
        <button type="button" class="btn btn-secondary" onclick="cancelRescheduleSelection()">← Back</button>
      </div>
    </form>
  </div>
</div>

<script>
function selectRescheduleSlot(value, display) {
  var date = document.getElementById('reschedule-date').value;
  document.getElementById('confirm-datetime').value = date + 'T' + value + ':00';
  document.getElementById('confirm-summary').textContent = display;
  document.getElementById('confirmation-section').style.display = 'block';
  document.querySelectorAll('#slot-area .slot-btn').forEach(function(b) { b.classList.remove('selected'); });
  if (event && event.target) { event.target.classList.add('selected'); }
}
function cancelRescheduleSelection() {
  document.getElementById('confirmation-section').style.display = 'none';
  document.querySelectorAll('#slot-area .slot-btn').forEach(function(b) { b.classList.remove('selected'); });
}
</script>
{% endblock %}
```

**Step 4: Add admin reschedule routes to `app/routers/admin.py`**

Add after the existing `cancel_booking_route` (around line 449). Import `_perform_reschedule` at the top of the function (inline import, same pattern as existing code):

```python
@router.get("/bookings/{booking_id}/reschedule", response_class=HTMLResponse)
def admin_reschedule_page(
    request: Request, booking_id: int, db: Session = Depends(get_db), _=AuthDep,
):
    booking = db.query(Booking).filter_by(id=booking_id, status="confirmed").first()
    if not booking:
        _flash(request, "Booking not found.", "error")
        return RedirectResponse("/admin/bookings", status_code=302)
    max_future = int(get_setting(db, "max_future_days", "30"))
    from datetime import datetime as _dt
    now = _dt.utcnow()
    min_date = now.date().isoformat()
    max_date = (now + timedelta(days=max_future)).date().isoformat()
    current_display = booking.start_datetime.strftime("%A, %B %-d, %Y at %-I:%M %p")
    return templates.TemplateResponse("admin/admin_reschedule.html", {
        "request": request,
        "booking": booking,
        "min_date": min_date,
        "max_date": max_date,
        "current_display": current_display,
        "flash": _get_flash(request),
    })


@router.post("/bookings/{booking_id}/reschedule")
def admin_reschedule_booking(
    request: Request, booking_id: int, db: Session = Depends(get_db), _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
    start_datetime: str = Form(...),
):
    from app.routers.booking import _perform_reschedule
    booking = db.query(Booking).filter_by(id=booking_id, status="confirmed").first()
    if not booking:
        _flash(request, "Booking not found.", "error")
        return RedirectResponse("/admin/bookings", status_code=302)
    try:
        new_start_dt = datetime.fromisoformat(start_datetime)
    except (ValueError, TypeError):
        _flash(request, "Invalid date/time.", "error")
        return RedirectResponse(f"/admin/bookings/{booking_id}/reschedule", status_code=302)
    settings = get_settings()
    base_url = str(request.base_url).rstrip('/')
    try:
        _perform_reschedule(db, booking, new_start_dt, settings, base_url)
        _flash(request, f"Booking for {booking.guest_name} rescheduled to {new_start_dt.strftime('%b %-d at %-I:%M %p')}.")
    except ValueError as exc:
        _flash(request, f"Reschedule failed: {exc}", "error")
    return RedirectResponse("/admin/bookings", status_code=302)
```

Note: `admin.py` already imports `datetime` and `timedelta` — verify at the top of the file and add any missing imports.

**Step 5: Add Reschedule button to `app/templates/admin/bookings.html`**

In the actions `<td>` for each upcoming booking (currently contains only the Cancel form), add a Reschedule link before the Cancel form:

```html
<td>
  <a href="/admin/bookings/{{ b.id }}/reschedule"
     class="btn btn-secondary" style="font-size:.8rem;padding:.3rem .6rem;">Reschedule</a>
  <form method="post" action="/admin/bookings/{{ b.id }}/cancel"
        onsubmit="return confirm('Cancel booking for {{ b.guest_name }}?')"
        style="display:inline;">
    <input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
    <button class="btn btn-danger" style="font-size:.8rem;padding:.3rem .6rem;">Cancel</button>
  </form>
</td>
```

**Step 6: Run all tests**

```bash
pytest -v
```

Expected: all 130+ pass, 0 failures.

**Step 7: Commit**

```bash
git add app/routers/admin.py \
        app/templates/admin/admin_reschedule.html \
        app/templates/admin/bookings.html \
        tests/test_admin_reschedule.py
git commit -m "feat: add admin reschedule routes and bookings table button"
```

---

## Final verification

Run the full test suite one more time:

```bash
pytest -v
```

All tests must pass before completing the branch.
