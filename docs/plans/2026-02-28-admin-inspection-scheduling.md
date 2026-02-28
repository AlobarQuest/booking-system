# Admin Inspection Scheduling — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an admin-only "Schedule Inspection" flow that lets the admin enter an inspection address + date, see available time slots with real-time drive time calculation, pick a slot (or override with any time), and confirm a booking that creates a calendar event with drive time blocks.

**Architecture:** Add `admin_initiated` flag to `AppointmentType` and `location` to `Booking`. Add per-type `AvailabilityRule` via nullable FK. New admin route `GET /admin/schedule-inspection` + `GET /admin/inspection-slots` + `POST /admin/schedule-inspection`. Reuse existing slot computation and drive time infrastructure. No guest email, no guest calendar invite, no reminders.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy mapped columns, HTMX, Jinja2, pytest. All existing patterns apply.

---

### Task 1: Schema — three new columns + `create_booking` location param

**Files:**
- Modify: `app/models.py`
- Modify: `app/database.py`
- Modify: `app/services/booking.py`
- Test: `tests/test_models.py`

**Step 1: Write failing tests**

Add to `tests/test_models.py`:

```python
def test_appointment_type_has_admin_initiated():
    t = AppointmentType()
    assert hasattr(t, "admin_initiated")
    assert t.admin_initiated is False or t.admin_initiated == 0

def test_booking_has_location():
    b = Booking()
    assert hasattr(b, "location")

def test_availability_rule_has_appointment_type_id():
    r = AvailabilityRule()
    assert hasattr(r, "appointment_type_id")
    assert r.appointment_type_id is None
```

**Step 2: Run to confirm failure**

```
pytest tests/test_models.py::test_appointment_type_has_admin_initiated tests/test_models.py::test_booking_has_location tests/test_models.py::test_availability_rule_has_appointment_type_id -v
```

Expected: FAIL (AttributeError or assertion error)

**Step 3: Add columns to `app/models.py`**

In `AppointmentType` after `owner_reminders_enabled`:
```python
admin_initiated: Mapped[bool] = mapped_column(Boolean, default=False)
```

In `Booking` after `created_at`:
```python
location: Mapped[str] = mapped_column(Text, default="")
```

In `AvailabilityRule` after `active`:
```python
from sqlalchemy import ForeignKey  # already imported
appointment_type_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("appointment_types.id"), nullable=True, default=None)
```

**Step 4: Add migrations to `app/database.py`**

In the `appointment_types` PRAGMA loop, append:
```python
("admin_initiated", "BOOLEAN NOT NULL DEFAULT 0"),
```

After the `appointment_types` block, add a new block for `bookings`:
```python
existing_b = {row[1] for row in conn.execute(text("PRAGMA table_info(bookings)"))}
for col, definition in [
    ("location", "TEXT NOT NULL DEFAULT ''"),
]:
    if col not in existing_b:
        conn.execute(text(f"ALTER TABLE bookings ADD COLUMN {col} {definition}"))

existing_ar = {row[1] for row in conn.execute(text("PRAGMA table_info(availability_rules)"))}
for col, definition in [
    ("appointment_type_id", "INTEGER REFERENCES appointment_types(id)"),
]:
    if col not in existing_ar:
        conn.execute(text(f"ALTER TABLE availability_rules ADD COLUMN {col} {definition}"))
conn.commit()
```

**Step 5: Update `app/services/booking.py`**

Add `location: str = ""` parameter and set it on the booking:
```python
def create_booking(
    db: Session,
    appt_type: AppointmentType,
    start_dt: datetime,
    end_dt: datetime,
    guest_name: str,
    guest_email: str,
    guest_phone: str,
    notes: str,
    custom_responses: dict,
    google_event_id: str = "",
    location: str = "",
) -> Booking:
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
    )
    booking.custom_field_responses = custom_responses
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking
```

**Step 6: Run tests to confirm passing**

```
pytest tests/test_models.py::test_appointment_type_has_admin_initiated tests/test_models.py::test_booking_has_location tests/test_models.py::test_availability_rule_has_appointment_type_id -v
```
Expected: PASS

**Step 7: Run full suite to check no regressions**

```
pytest -v
```
Expected: all pass

**Step 8: Commit**

```bash
git add app/models.py app/database.py app/services/booking.py tests/test_models.py
git commit -m "feat: add admin_initiated, booking.location, rule.appointment_type_id columns"
```

---

### Task 2: Per-type availability rule filtering in `_build_free_windows`

**Files:**
- Modify: `app/services/availability.py`
- Test: `tests/test_availability.py`

**Step 1: Write failing tests**

Add to `tests/test_availability.py`:

```python
def make_rule_for_type(day: int, start: str, end: str, type_id: int) -> AvailabilityRule:
    r = AvailabilityRule()
    r.day_of_week = day
    r.start_time = start
    r.end_time = end
    r.active = True
    r.appointment_type_id = type_id
    return r


def test_build_free_windows_uses_type_specific_rules_when_present():
    """If type has its own rules, use only those — ignore global rules."""
    global_rule = make_rule(0, "09:00", "17:00")        # global (appointment_type_id=None)
    type_rule   = make_rule_for_type(0, "10:00", "12:00", type_id=5)  # type-specific
    all_rules = [global_rule, type_rule]
    windows = _build_free_windows(date(2025, 3, 3), all_rules, [], [], appointment_type_id=5)
    # Should use 10:00-12:00 not 09:00-17:00
    assert windows == [(time(10, 0), time(12, 0))]


def test_build_free_windows_falls_back_to_global_when_no_type_rules():
    """If type has no rules, fall back to global rules."""
    global_rule = make_rule(0, "09:00", "17:00")  # global
    all_rules = [global_rule]
    windows = _build_free_windows(date(2025, 3, 3), all_rules, [], [], appointment_type_id=99)
    # No rules for type 99, so use global
    assert windows == [(time(9, 0), time(17, 0))]
```

Note: `make_rule` creates rules with `appointment_type_id = None` by default (not set). The new `_build_free_windows` must treat `None` appointment_type_id on a rule as a global rule.

**Step 2: Run to confirm failure**

```
pytest tests/test_availability.py::test_build_free_windows_uses_type_specific_rules_when_present tests/test_availability.py::test_build_free_windows_falls_back_to_global_when_no_type_rules -v
```
Expected: FAIL (TypeError — unexpected keyword argument)

**Step 3: Update `_build_free_windows` in `app/services/availability.py`**

Change the signature and filtering logic:

```python
def _build_free_windows(
    target_date: date,
    rules: list,
    blocked_periods: list,
    busy_intervals: list[tuple[datetime, datetime]],
    appointment_type_id: int | None = None,
) -> list[tuple[time, time]]:
    """Compute available time windows after subtracting blocked periods and busy intervals.

    If appointment_type_id is provided and any rules exist for that type, those
    type-specific rules are used exclusively. Otherwise falls back to global rules
    (rules with appointment_type_id IS None).
    """
    day_of_week = target_date.weekday()  # 0=Monday

    if appointment_type_id is not None:
        type_rules = [r for r in rules if r.appointment_type_id == appointment_type_id and r.active]
        if type_rules:
            day_rules = [r for r in type_rules if r.day_of_week == day_of_week]
        else:
            day_rules = [r for r in rules if r.appointment_type_id is None and r.day_of_week == day_of_week and r.active]
    else:
        day_rules = [r for r in rules if r.appointment_type_id is None and r.day_of_week == day_of_week and r.active]

    if not day_rules:
        return []

    windows = [
        (time.fromisoformat(r.start_time), time.fromisoformat(r.end_time))
        for r in day_rules
    ]

    blocked = [
        (bp.start_datetime, bp.end_datetime)
        for bp in blocked_periods
        if bp.start_datetime.date() <= target_date <= bp.end_datetime.date()
    ]
    windows = subtract_intervals(windows, blocked, target_date)
    windows = subtract_intervals(windows, busy_intervals, target_date)
    return windows
```

Also update `compute_slots` to accept and pass through `appointment_type_id`:

```python
def compute_slots(
    target_date: date,
    rules: list,
    blocked_periods: list,
    busy_intervals: list[tuple[datetime, datetime]],
    duration_minutes: int,
    buffer_before_minutes: int,
    buffer_after_minutes: int,
    min_advance_hours: int,
    now: datetime,
    appointment_type_id: int | None = None,
) -> list[time]:
    """Compute available appointment start times for a given date."""
    windows = _build_free_windows(target_date, rules, blocked_periods, busy_intervals, appointment_type_id)
    if not windows:
        return []
    slots = split_into_slots(windows, duration_minutes, buffer_before_minutes, buffer_after_minutes)
    return filter_by_advance_notice(slots, target_date, min_advance_hours, now)
```

**Step 4: Run targeted tests**

```
pytest tests/test_availability.py -v
```
Expected: all pass (new tests pass; existing tests still pass because `make_rule` leaves `appointment_type_id=None`, which is treated as a global rule)

**Step 5: Run full suite**

```
pytest -v
```
Expected: all pass

**Step 6: Commit**

```bash
git add app/services/availability.py tests/test_availability.py
git commit -m "feat: per-type availability rules in _build_free_windows"
```

---

### Task 3: Update `/slots` endpoint — `destination` param + admin_initiated awareness

**Files:**
- Modify: `app/routers/slots.py`
- Test: `tests/test_slots_route.py`

**Step 1: Write failing test**

Add to `tests/test_slots_route.py`:

```python
def test_slots_uses_destination_for_admin_initiated_type():
    """For admin_initiated types, drive time uses the supplied destination param, not appt_type.location."""
    from unittest.mock import patch
    from app.models import AppointmentType, AvailabilityRule, Setting
    from app.database import Base, get_db
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    appt = AppointmentType(
        name="Inspection",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        calendar_id="primary",
        active=True,
        admin_initiated=True,
        requires_drive_time=True,
        color="#fff",
        description="",
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.add(AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True))
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
    client = TestClient(app)

    with patch("app.routers.slots.datetime") as mock_dt, \
         patch("app.services.availability.get_drive_time", return_value=0):
        mock_dt.now.return_value = datetime(2025, 3, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        mock_dt.combine = datetime.combine
        resp = client.get(f"/slots?type_id={appt_id}&date=2025-03-03&destination=123+Main+St")
    assert resp.status_code == 200
    app.dependency_overrides.clear()
```

**Step 2: Run to confirm it passes with 200 (it should, but the destination is currently ignored)**

```
pytest tests/test_slots_route.py::test_slots_uses_destination_for_admin_initiated_type -v
```

This test checks the endpoint returns 200. It will pass but the destination is ignored. That's fine — the behavior test is implicit (no crash). This test is mainly a smoke test; the unit tests in test_availability.py cover the actual trimming logic.

**Step 3: Update `app/routers/slots.py`**

Add `destination` query parameter and pass `appointment_type_id` to slot computation:

```python
@router.get("/slots", response_class=HTMLResponse)
def get_slots(
    request: Request,
    type_id: int = Query(...),
    date: str = Query(...),
    destination: str = Query(""),   # NEW: used by admin_initiated types
    db: Session = Depends(get_db),
):
```

In the drive time section, change:
```python
    # --- Drive time: fetch full events to find preceding event location ---
    effective_location = destination if appt_type.admin_initiated else appt_type.location
    if appt_type.requires_drive_time and effective_location:
```
(was: `if appt_type.requires_drive_time and appt_type.location:`)

And in the trim section:
```python
    if appt_type.requires_drive_time and effective_location:
        home_address = get_setting(db, "home_address", "")
        windows = trim_windows_for_drive_time(
            windows, target_date, local_day_events,
            destination=effective_location,
            home_address=home_address,
            db=db,
        )
```

Also pass `appointment_type_id` to `compute_slots` (instead of calling `_build_free_windows` directly, `compute_slots` now handles it):

Change the `windows = _build_free_windows(...)` call in slots.py to pass through `appointment_type_id`:

```python
    # Build availability windows
    windows = _build_free_windows(target_date, rules, blocked, busy_intervals, appointment_type_id=appt_type.id)
```

Note: `slots.py` calls `_build_free_windows` directly (not via `compute_slots`), so add `appointment_type_id=appt_type.id` to that call.

**Step 4: Run full suite**

```
pytest -v
```
Expected: all pass

**Step 5: Commit**

```bash
git add app/routers/slots.py tests/test_slots_route.py
git commit -m "feat: slots endpoint accepts destination param for admin-initiated types"
```

---

### Task 4: Hide admin-initiated types from public booking page

**Files:**
- Modify: `app/routers/booking.py`
- Test: `tests/test_booking_page.py`

**Step 1: Write failing test**

Add to `tests/test_booking_page.py`:

```python
def test_admin_initiated_type_hidden_from_public_booking(client):
    """AppointmentType with admin_initiated=True must not appear on the public booking page."""
    from app.models import AppointmentType
    from app.database import get_db
    db = next(client.app.dependency_overrides[get_db]())
    t = AppointmentType(
        name="Damage Inspection",
        duration_minutes=30,
        active=True,
        admin_initiated=True,
        color="#fff",
        description="",
    )
    t._custom_fields = "[]"
    db.add(t)
    db.commit()
    resp = client.get("/book")
    assert "Damage Inspection" not in resp.text
```

**Step 2: Run to confirm failure**

```
pytest tests/test_booking_page.py::test_admin_initiated_type_hidden_from_public_booking -v
```
Expected: FAIL (the type appears on the page)

**Step 3: Update `app/routers/booking.py`**

In `_booking_page`, change:
```python
appointment_types = db.query(AppointmentType).filter_by(active=True).all()
```
to:
```python
appointment_types = db.query(AppointmentType).filter_by(active=True, admin_initiated=False).all()
```

**Step 4: Run tests**

```
pytest tests/test_booking_page.py -v
```
Expected: all pass

**Step 5: Run full suite**

```
pytest -v
```
Expected: all pass

**Step 6: Commit**

```bash
git add app/routers/booking.py tests/test_booking_page.py
git commit -m "feat: hide admin_initiated appointment types from public booking page"
```

---

### Task 5: Appointment type admin form + routes — `admin_initiated` support + per-type rules

**Files:**
- Modify: `app/routers/admin.py`
- Modify: `app/templates/admin/appointment_types.html`
- Test: `tests/test_admin_appt_types.py`

**Step 1: Write failing test**

Add to `tests/test_admin_appt_types.py`:

```python
def test_create_admin_initiated_type(admin_client):
    """Can create an admin-initiated appointment type via the admin form."""
    client, SessionFactory, _ = admin_client
    resp = client.post(
        "/admin/appointment-types",
        data={
            "name": "Property Inspection",
            "description": "",
            "duration_minutes": "45",
            "buffer_before_minutes": "0",
            "buffer_after_minutes": "15",
            "calendar_id": "primary",
            "color": "#3b82f6",
            "owner_event_title": "Damage Inspection — {address}",
            "admin_initiated": "true",
        },
    )
    assert resp.status_code == 302
    db = SessionFactory()
    t = db.query(AppointmentType).filter_by(name="Property Inspection").first()
    assert t is not None
    assert t.admin_initiated is True
    assert t.requires_drive_time is True  # always true for admin_initiated
    db.close()


def test_create_type_rule_for_admin_initiated(admin_client):
    """Can add a type-specific availability rule to an admin-initiated type."""
    from app.models import AvailabilityRule
    client, SessionFactory, _ = admin_client
    # Create the type first
    client.post(
        "/admin/appointment-types",
        data={"name": "Inspection", "duration_minutes": "30", "admin_initiated": "true",
              "color": "#fff", "calendar_id": "primary", "owner_event_title": "Insp"},
    )
    db = SessionFactory()
    t = db.query(AppointmentType).filter_by(name="Inspection").first()
    type_id = t.id
    db.close()

    resp = client.post(
        f"/admin/appointment-types/{type_id}/rules",
        data={"day_of_week": "1", "start_time": "08:00", "end_time": "16:00"},
    )
    assert resp.status_code == 302

    db = SessionFactory()
    rule = db.query(AvailabilityRule).filter_by(appointment_type_id=type_id).first()
    assert rule is not None
    assert rule.day_of_week == 1
    assert rule.start_time == "08:00"
    db.close()
```

**Step 2: Run to confirm failure**

```
pytest tests/test_admin_appt_types.py::test_create_admin_initiated_type tests/test_admin_appt_types.py::test_create_type_rule_for_admin_initiated -v
```
Expected: FAIL

**Step 3: Update `app/routers/admin.py`**

Add `admin_initiated: str = Form("false")` to both `create_appt_type` and `update_appt_type` signatures.

In `create_appt_type`, add to the `AppointmentType(...)` constructor:
```python
admin_initiated=(admin_initiated == "true"),
requires_drive_time=True if (admin_initiated == "true") else (requires_drive_time == "true"),
```

In `update_appt_type`, add:
```python
t.admin_initiated = (admin_initiated == "true")
if t.admin_initiated:
    t.requires_drive_time = True
else:
    t.requires_drive_time = (requires_drive_time == "true")
```

Add two new routes at the end of the appointment types section:

```python
@router.post("/appointment-types/{type_id}/rules")
def create_type_rule(
    request: Request,
    type_id: int,
    day_of_week: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    if t:
        db.add(AvailabilityRule(
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
            active=True,
            appointment_type_id=type_id,
        ))
        db.commit()
        _flash(request, "Availability window added.")
    return RedirectResponse(f"/admin/appointment-types/{type_id}/edit", status_code=302)


@router.post("/appointment-types/{type_id}/rules/{rule_id}/delete")
def delete_type_rule(
    request: Request,
    type_id: int,
    rule_id: int,
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    rule = db.query(AvailabilityRule).filter_by(id=rule_id, appointment_type_id=type_id).first()
    if rule:
        db.delete(rule)
        db.commit()
        _flash(request, "Rule deleted.")
    return RedirectResponse(f"/admin/appointment-types/{type_id}/edit", status_code=302)
```

Also update `edit_appt_type_page` to pass type-specific rules to the template:
```python
@router.get("/appointment-types/{type_id}/edit", response_class=HTMLResponse)
def edit_appt_type_page(
    request: Request, type_id: int, db: Session = Depends(get_db), _=AuthDep
):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    types = db.query(AppointmentType).order_by(AppointmentType.id).all()
    type_rules = db.query(AvailabilityRule).filter_by(appointment_type_id=type_id).order_by(AvailabilityRule.day_of_week).all() if t else []
    return templates.TemplateResponse("admin/appointment_types.html", {
        "request": request, "types": types, "edit_type": t,
        "type_rules": type_rules, "flash": _get_flash(request),
    })
```

**Step 4: Update `app/templates/admin/appointment_types.html`**

Add "Admin-initiated" checkbox at the top of the form (before Name), and use JS to toggle irrelevant fields:

```html
<label style="flex-direction:row;align-items:center;gap:.5rem;cursor:pointer;">
  <input type="checkbox" name="admin_initiated" value="true" id="admin-initiated-check"
         {% if edit_type and edit_type.admin_initiated %}checked{% endif %}
         style="width:auto;"
         onchange="toggleAdminInitiated()">
  Admin-initiated inspection (address entered at booking time)
</label>
<small style="color:#64748b;margin-top:-.5rem;">
  When checked: hides public-booking fields, always calculates drive time, no guest invite or email.
</small>
```

Add a `<div id="standard-fields">` wrapper around all the fields that should be hidden for admin-initiated types (Location, listing URL, photo, guest event title, calendar window, rental requirements, owner reminders), and a `<div id="admin-fields" style="display:none;">` wrapper for the per-type availability rules section.

Add JS at the bottom of the form:
```html
<script>
function toggleAdminInitiated() {
  const isAdmin = document.getElementById('admin-initiated-check').checked;
  document.getElementById('standard-fields').style.display = isAdmin ? 'none' : 'block';
  document.getElementById('admin-fields').style.display = isAdmin ? 'block' : 'none';
}
// Run on load to set initial state
toggleAdminInitiated();
</script>
```

The `admin-fields` div contains the per-type availability rules section (only shown when editing an existing admin-initiated type):
```html
<div id="admin-fields" style="display:{% if edit_type and edit_type.admin_initiated %}block{% else %}none{% endif %};">
  {% if edit_type and edit_type.admin_initiated %}
  <hr style="margin:1rem 0;border:none;border-top:1px solid #e2e8f0;">
  <h3 style="font-size:.95rem;font-weight:600;margin-bottom:.75rem;">Inspection Availability Windows</h3>
  <small style="color:#64748b;display:block;margin-bottom:.75rem;">
    These windows replace global availability for this appointment type.
    Leave empty to fall back to global availability rules.
  </small>
  {% if type_rules %}
  <table style="margin-bottom:1rem;">
    <thead><tr><th>Day</th><th>Start</th><th>End</th><th></th></tr></thead>
    <tbody>
    {% for rule in type_rules %}
    <tr>
      <td>{{ ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][rule.day_of_week] }}</td>
      <td>{{ rule.start_time_display }}</td>
      <td>{{ rule.end_time_display }}</td>
      <td>
        <form method="post" action="/admin/appointment-types/{{ edit_type.id }}/rules/{{ rule.id }}/delete">
          <input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
          <button class="btn btn-danger" style="font-size:.8rem;padding:.3rem .6rem;">Delete</button>
        </form>
      </td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p style="color:#64748b;margin-bottom:1rem;">No type-specific windows. Using global availability.</p>
  {% endif %}
  <div class="card" style="cursor:default;max-width:400px;margin-bottom:1rem;">
    <h4 style="margin-bottom:.75rem;">Add Window</h4>
    <form method="post" action="/admin/appointment-types/{{ edit_type.id }}/rules">
      <input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
      <label>Day
        <select name="day_of_week">
          {% for i, d in [('0','Monday'),('1','Tuesday'),('2','Wednesday'),('3','Thursday'),('4','Friday'),('5','Saturday'),('6','Sunday')] %}
          <option value="{{ i }}">{{ d }}</option>
          {% endfor %}
        </select>
      </label>
      <label>Start Time <input type="time" name="start_time" value="08:00" required></label>
      <label>End Time <input type="time" name="end_time" value="17:00" required></label>
      <button type="submit" class="btn btn-primary" style="margin-top:.25rem;">Add Window</button>
    </form>
  </div>
  {% endif %}
</div>
```

**Step 5: Run tests**

```
pytest tests/test_admin_appt_types.py -v
```
Expected: all pass

**Step 6: Run full suite**

```
pytest -v
```
Expected: all pass

**Step 7: Commit**

```bash
git add app/routers/admin.py app/templates/admin/appointment_types.html tests/test_admin_appt_types.py
git commit -m "feat: admin_initiated flag on appointment types with per-type availability rules"
```

---

### Task 6: `GET /admin/schedule-inspection` page + `GET /admin/inspection-slots` partial

**Files:**
- Create: `app/templates/admin/schedule_inspection.html`
- Create: `app/templates/admin/inspection_slots_partial.html`
- Modify: `app/routers/admin.py`
- Modify: `app/templates/admin_base.html`
- Test: `tests/test_admin_inspection.py` (new file)

**Step 1: Write failing test**

Create `tests/test_admin_inspection.py`:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.main import app
from app.dependencies import require_admin, require_csrf
from app.models import AppointmentType, AvailabilityRule


@pytest.fixture
def insp_client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_admin] = lambda: True
    app.dependency_overrides[require_csrf] = lambda: None

    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        yield c, TestSession

    app.dependency_overrides.clear()


def test_schedule_inspection_page_loads(insp_client):
    client, _ = insp_client
    resp = client.get("/admin/schedule-inspection")
    assert resp.status_code == 200
    assert "Schedule Inspection" in resp.text


def test_inspection_slots_returns_partial(insp_client):
    """GET /admin/inspection-slots returns slot HTML for a valid date."""
    from unittest.mock import patch
    from datetime import datetime, timezone as dt_timezone
    client, SessionFactory = insp_client
    db = SessionFactory()
    t = AppointmentType(
        name="Inspection", duration_minutes=30, active=True,
        admin_initiated=True, requires_drive_time=True, color="#fff",
        calendar_id="primary", description="",
    )
    t._custom_fields = "[]"
    db.add(t)
    db.add(AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True))
    db.commit()
    type_id = t.id
    db.close()

    with patch("app.routers.slots.datetime") as mock_dt, \
         patch("app.services.availability.get_drive_time", return_value=0):
        mock_dt.now.return_value = datetime(2025, 3, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        mock_dt.combine = datetime.combine
        resp = client.get(
            f"/admin/inspection-slots?type_id={type_id}&date=2025-03-03&destination=123+Main+St"
        )
    assert resp.status_code == 200
```

**Step 2: Run to confirm failure**

```
pytest tests/test_admin_inspection.py::test_schedule_inspection_page_loads tests/test_admin_inspection.py::test_inspection_slots_returns_partial -v
```
Expected: FAIL (routes don't exist)

**Step 3: Add routes to `app/routers/admin.py`**

```python
# ---------- Schedule Inspection ----------

@router.get("/schedule-inspection", response_class=HTMLResponse)
def schedule_inspection_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    admin_types = db.query(AppointmentType).filter_by(active=True, admin_initiated=True).order_by(AppointmentType.id).all()
    return templates.TemplateResponse("admin/schedule_inspection.html", {
        "request": request,
        "admin_types": admin_types,
        "flash": _get_flash(request),
    })
```

For the inspection slots, reuse the existing `/slots` endpoint but serve a different partial. Add a separate admin slots route that calls the same computation but renders an admin-specific partial:

```python
@router.get("/inspection-slots", response_class=HTMLResponse)
def inspection_slots(
    request: Request,
    type_id: int = Query(...),
    date: str = Query(...),
    destination: str = Query(""),
    db: Session = Depends(get_db),
    _=AuthDep,
):
    """Proxy to the public /slots computation but render the admin inspection partial."""
    # Forward internally to the slots logic
    from app.routers.slots import get_slots as _get_slots
    # Re-use the same endpoint logic by making an internal call
    # Instead, duplicate the call for clarity — import the needed pieces
    import json as _json
    from datetime import date as date_type, time as time_type, timedelta, timezone as dt_timezone
    from zoneinfo import ZoneInfo
    from app.models import AvailabilityRule, BlockedPeriod
    from app.services.availability import (
        _build_free_windows, intersect_windows, split_into_slots,
        filter_by_advance_notice, trim_windows_for_drive_time,
    )
    from app.services.calendar import CalendarService
    from app.config import get_settings
    from app.dependencies import get_setting

    settings = get_settings()
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True, admin_initiated=True).first()
    if not appt_type:
        return HTMLResponse("<p class='no-slots'>Appointment type not found.</p>")
    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        return HTMLResponse("<p class='no-slots'>Invalid date.</p>")

    # [Same computation as /slots endpoint, using destination for drive time]
    # ... (full slot computation — see Step 4 for the full template-based approach)
    ...
    return templates.TemplateResponse("admin/inspection_slots_partial.html", {
        "request": request, "slots": slot_data,
        "type_id": type_id, "date": date, "destination": destination,
    })
```

**Implementation note:** Rather than duplicating the full slot computation in the admin route, extract the slot computation from `slots.py` into a shared helper function `compute_slots_for_request(appt_type, target_date, destination, db)` that both `/slots` and `/admin/inspection-slots` call. However, to keep this plan focused, the simpler approach is to have `/admin/inspection-slots` call the `/slots` endpoint internally and return its own partial. The cleanest practical approach: duplicate the computation in the admin route file (it's ~40 lines of slot logic, the partial template is what differs).

Copy the slot computation block from `slots.py` into the admin route handler. The only difference is:
1. `effective_location = destination` (always, since admin_initiated)
2. Render `admin/inspection_slots_partial.html` instead of `booking/slots_partial.html`

**Step 4: Create `app/templates/admin/inspection_slots_partial.html`**

```html
<input type="hidden" id="ctx-type-id" value="{{ type_id }}">
<input type="hidden" id="ctx-destination" value="{{ destination }}">
<input type="hidden" id="ctx-date" value="{{ date }}">

{% if slots %}
<div class="slots-grid" style="margin-bottom:1rem;">
  {% for slot in slots %}
  <button type="button" class="slot-btn"
          onclick="selectInspectionSlot('{{ slot.value }}', '{{ slot.display }}')">
    {{ slot.display }}
  </button>
  {% endfor %}
</div>
{% else %}
<p class="no-slots">No available times on this date.</p>
{% endif %}

<div style="margin-top:.75rem;">
  <a href="#" onclick="showManualTimeInput(); return false;" style="font-size:.9rem;color:#6366f1;">
    Use a specific time instead →
  </a>
  <div id="manual-time-wrapper" style="display:none;margin-top:.5rem;display:flex;gap:.5rem;align-items:center;">
    <input type="time" id="manual-time-input" step="900" style="width:auto;">
    <button type="button" class="btn btn-secondary" onclick="applyManualTime()">Use this time</button>
  </div>
</div>
```

**Step 5: Create `app/templates/admin/schedule_inspection.html`**

```html
{% extends "admin_base.html" %}
{% block title %}Schedule Inspection{% endblock %}
{% block content %}
<h1>Schedule Inspection</h1>

<div class="card" style="cursor:default;max-width:520px;margin-bottom:2rem;">
  <h2 style="margin-bottom:1rem;">Find Available Times</h2>
  <form hx-get="/admin/inspection-slots"
        hx-target="#slot-area"
        hx-swap="innerHTML"
        hx-indicator="#slot-loading">
    <label>Appointment Type *
      <select name="type_id" id="insp-type-id" required>
        {% for t in admin_types %}
        <option value="{{ t.id }}">{{ t.name }}</option>
        {% endfor %}
      </select>
    </label>
    {% if not admin_types %}
    <p style="color:#dc2626;">No admin-initiated appointment types found. Create one under Appointment Types first.</p>
    {% endif %}
    <label>Inspection Address *
      <input type="text" name="destination" id="insp-destination" required
             placeholder="123 Main St, Atlanta GA 30301">
    </label>
    <label>Date *
      <input type="date" name="date" id="insp-date" required
             min="{{ today }}">
    </label>
    <button type="submit" class="btn btn-primary" {% if not admin_types %}disabled{% endif %}>
      Find Available Times
    </button>
    <span id="slot-loading" class="htmx-indicator" style="display:none;">Loading...</span>
  </form>
</div>

<div id="slot-area" style="margin-bottom:2rem;"></div>

<!-- Confirmation section — hidden until a slot is selected -->
<div id="confirmation-section" style="display:none;max-width:520px;">
  <div class="card" style="cursor:default;">
    <h2>Confirm Booking</h2>
    <p id="confirm-summary" style="color:#059669;font-weight:600;margin-bottom:1rem;"></p>
    <form method="post" action="/admin/schedule-inspection">
      <input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
      <input type="hidden" name="type_id" id="confirm-type-id">
      <input type="hidden" name="destination" id="confirm-destination">
      <input type="hidden" name="start_datetime" id="confirm-start-datetime">
      <label>Guest Name (optional)
        <input type="text" name="guest_name" placeholder="Property owner / tenant name">
      </label>
      <label>Guest Email (optional)
        <input type="email" name="guest_email" placeholder="owner@example.com">
      </label>
      <label>Guest Phone (optional)
        <input type="tel" name="guest_phone" placeholder="(404) 555-0100">
      </label>
      <label>Notes (optional)
        <textarea name="notes" rows="2" placeholder="Unit number, access instructions, etc."></textarea>
      </label>
      <div style="display:flex;gap:.75rem;margin-top:.25rem;">
        <button type="submit" class="btn btn-primary">Confirm Booking</button>
        <button type="button" class="btn btn-secondary" onclick="cancelSelection()">Cancel</button>
      </div>
    </form>
  </div>
</div>

<script>
function selectInspectionSlot(value, display) {
  const date = document.getElementById('ctx-date').value;
  document.getElementById('confirm-start-datetime').value = date + 'T' + value + ':00';
  document.getElementById('confirm-type-id').value = document.getElementById('ctx-type-id').value;
  document.getElementById('confirm-destination').value = document.getElementById('ctx-destination').value;
  document.getElementById('confirm-summary').textContent =
    display + ' · ' + document.getElementById('insp-destination').value;
  document.getElementById('confirmation-section').style.display = 'block';
  document.getElementById('confirmation-section').scrollIntoView({behavior: 'smooth'});
  // Deselect all slot buttons, highlight chosen
  document.querySelectorAll('.slot-btn').forEach(b => b.classList.remove('slot-btn-selected'));
  event.target.classList.add('slot-btn-selected');
}

function showManualTimeInput() {
  document.getElementById('manual-time-wrapper').style.display = 'flex';
}

function applyManualTime() {
  const t = document.getElementById('manual-time-input').value;
  if (!t) return;
  const [h, m] = t.split(':');
  const hour = parseInt(h);
  const display = (hour % 12 || 12) + ':' + m + ' ' + (hour < 12 ? 'AM' : 'PM');
  selectInspectionSlot(t, display);
}

function cancelSelection() {
  document.getElementById('confirmation-section').style.display = 'none';
  document.querySelectorAll('.slot-btn').forEach(b => b.classList.remove('slot-btn-selected'));
}
</script>
{% endblock %}
```

Also pass `today` from the route:
```python
from datetime import date as _date
return templates.TemplateResponse("admin/schedule_inspection.html", {
    "request": request,
    "admin_types": admin_types,
    "today": _date.today().isoformat(),
    "flash": _get_flash(request),
})
```

**Step 6: Add "Schedule Inspection" to nav in `app/templates/admin_base.html`**

```html
<a href="/admin/schedule-inspection">Schedule Inspection</a>
```
Add after `<a href="/admin/bookings">Bookings</a>`.

**Step 7: Run tests**

```
pytest tests/test_admin_inspection.py -v
```
Expected: `test_schedule_inspection_page_loads` and `test_inspection_slots_returns_partial` pass

**Step 8: Run full suite**

```
pytest -v
```
Expected: all pass

**Step 9: Commit**

```bash
git add app/routers/admin.py app/templates/admin/schedule_inspection.html \
    app/templates/admin/inspection_slots_partial.html app/templates/admin_base.html \
    tests/test_admin_inspection.py
git commit -m "feat: add Schedule Inspection admin page and inspection slots endpoint"
```

---

### Task 7: `POST /admin/schedule-inspection` — create the booking

**Files:**
- Modify: `app/routers/admin.py`
- Test: `tests/test_admin_inspection.py`

**Step 1: Write failing test**

Add to `tests/test_admin_inspection.py`:

```python
def test_schedule_inspection_creates_booking(insp_client):
    """POST /admin/schedule-inspection creates a Booking with the inspection address as location."""
    from unittest.mock import patch
    from app.models import Booking
    client, SessionFactory = insp_client

    db = SessionFactory()
    t = AppointmentType(
        name="Inspection", duration_minutes=30, active=True,
        admin_initiated=True, requires_drive_time=True, color="#fff",
        calendar_id="primary", description="", owner_event_title="Inspection",
    )
    t._custom_fields = "[]"
    db.add(t)
    db.commit()
    type_id = t.id
    db.close()

    with patch("app.routers.admin.CalendarService"):
        resp = client.post("/admin/schedule-inspection", data={
            "type_id": str(type_id),
            "destination": "456 Oak Ave, Atlanta GA 30318",
            "start_datetime": "2025-03-03T10:00:00",
            "guest_name": "Jane Smith",
            "guest_email": "",
            "guest_phone": "",
            "notes": "Unit 4B",
        })
    assert resp.status_code == 302

    db = SessionFactory()
    booking = db.query(Booking).first()
    assert booking is not None
    assert booking.location == "456 Oak Ave, Atlanta GA 30318"
    assert booking.guest_name == "Jane Smith"
    assert booking.status == "confirmed"
    db.close()


def test_schedule_inspection_no_email_sent(insp_client):
    """POST /admin/schedule-inspection never sends email."""
    from unittest.mock import patch, MagicMock
    from app.models import Booking
    client, SessionFactory = insp_client

    db = SessionFactory()
    t = AppointmentType(
        name="Inspection", duration_minutes=30, active=True,
        admin_initiated=True, requires_drive_time=True, color="#fff",
        calendar_id="primary", description="", owner_event_title="Inspection",
    )
    t._custom_fields = "[]"
    db.add(t)
    db.commit()
    type_id = t.id
    db.close()

    with patch("app.routers.admin.CalendarService"), \
         patch("app.services.email.send_guest_confirmation") as mock_email:
        client.post("/admin/schedule-inspection", data={
            "type_id": str(type_id),
            "destination": "456 Oak Ave",
            "start_datetime": "2025-03-03T10:00:00",
            "guest_name": "Jane",
            "guest_email": "jane@example.com",
            "guest_phone": "",
            "notes": "",
        })
    mock_email.assert_not_called()
```

**Step 2: Run to confirm failure**

```
pytest tests/test_admin_inspection.py::test_schedule_inspection_creates_booking tests/test_admin_inspection.py::test_schedule_inspection_no_email_sent -v
```
Expected: FAIL (route doesn't exist)

**Step 3: Add route to `app/routers/admin.py`**

Add at the end of the schedule inspection section:

```python
@router.post("/schedule-inspection")
async def submit_inspection(
    request: Request,
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
    from datetime import timedelta, timezone as dt_timezone
    from zoneinfo import ZoneInfo
    from app.services.booking import create_booking
    from app.services.calendar import CalendarService
    from app.services.drive_time import get_drive_time
    from app.routers.booking import _create_drive_time_blocks

    form = await request.form()
    type_id_str = str(form.get("type_id", ""))
    destination = str(form.get("destination", "")).strip()
    start_datetime_str = str(form.get("start_datetime", ""))
    guest_name = str(form.get("guest_name", "")).strip()
    guest_email = str(form.get("guest_email", "")).strip()
    guest_phone = str(form.get("guest_phone", "")).strip()
    notes = str(form.get("notes", "")).strip()

    if not type_id_str or not destination or not start_datetime_str:
        _flash(request, "Missing required fields.", "error")
        return RedirectResponse("/admin/schedule-inspection", status_code=302)

    try:
        type_id = int(type_id_str)
        start_dt = datetime.fromisoformat(start_datetime_str)
    except (ValueError, TypeError):
        _flash(request, "Invalid data.", "error")
        return RedirectResponse("/admin/schedule-inspection", status_code=302)

    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True, admin_initiated=True).first()
    if not appt_type:
        _flash(request, "Appointment type not found.", "error")
        return RedirectResponse("/admin/schedule-inspection", status_code=302)

    end_dt = start_dt + timedelta(minutes=appt_type.duration_minutes)

    booking = create_booking(
        db=db,
        appt_type=appt_type,
        start_dt=start_dt,
        end_dt=end_dt,
        guest_name=guest_name or "N/A",
        guest_email=guest_email,
        guest_phone=guest_phone,
        notes=notes,
        custom_responses={},
        location=destination,
    )

    settings = get_settings()
    refresh_token = get_setting(db, "google_refresh_token", "")
    if refresh_token and settings.google_client_id:
        cal = CalendarService(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )
        tz = ZoneInfo(get_setting(db, "timezone", "America/New_York"))
        start_utc = start_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)
        end_utc = end_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)

        description_lines = [
            f"Inspection at: {destination}",
            f"Contact: {guest_name or 'N/A'}",
        ]
        if guest_email:
            description_lines.append(f"Email: {guest_email}")
        if guest_phone:
            description_lines.append(f"Phone: {guest_phone}")
        if notes:
            description_lines.append(f"Notes: {notes}")

        try:
            event_id = cal.create_event(
                refresh_token=refresh_token,
                calendar_id=appt_type.calendar_id,
                summary=appt_type.owner_event_title or f"Inspection — {destination}",
                description="\n".join(description_lines),
                start=start_utc,
                end=end_utc,
                location=destination,
                show_as=appt_type.show_as,
                visibility=appt_type.visibility,
                disable_reminders=True,  # Always no reminders for admin-initiated
            )
            booking.google_event_id = event_id
            db.commit()
        except Exception:
            pass

        home_address = get_setting(db, "home_address", "")
        _create_drive_time_blocks(
            cal=cal,
            refresh_token=refresh_token,
            calendar_id=appt_type.calendar_id,
            appt_name=appt_type.name,
            appt_location=destination,
            start_utc=start_utc,
            end_utc=end_utc,
            home_address=home_address,
            db=db,
        )

    start_display = start_dt.strftime("%A, %B %-d, %Y at %-I:%M %p")
    _flash(request, f"Inspection booked for {start_display} at {destination}.")
    return RedirectResponse("/admin/schedule-inspection", status_code=302)
```

**Step 4: Run tests**

```
pytest tests/test_admin_inspection.py -v
```
Expected: all pass

**Step 5: Run full suite**

```
pytest -v
```
Expected: all pass

**Step 6: Commit**

```bash
git add app/routers/admin.py tests/test_admin_inspection.py
git commit -m "feat: POST /admin/schedule-inspection creates booking with drive time blocks"
```

---

### Task 8: Final verification

**Step 1: Run full test suite**

```
pytest -v
```
Expected: all pass, no regressions

**Step 2: Spot-check the admin UI locally**

```bash
uvicorn app.main:app --reload --port 8080
```

- Navigate to `http://localhost:8080/admin/appointment-types` → Create a new type with "Admin-initiated" checked. Confirm Location/Photo/etc. fields are hidden.
- Open the type's edit page. Confirm "Inspection Availability Windows" section appears. Add a Mon 8am–5pm window.
- Navigate to `http://localhost:8080/admin/schedule-inspection` → Confirm the type appears in the dropdown.
- Enter an address and Monday date → click Find Available Times → confirm slots appear between 8am–5pm (not the global window).
- Click a slot → confirm confirmation panel appears.
- Navigate to `http://localhost:8080/book` → confirm the admin-initiated type does NOT appear.

**Step 3: Final commit if any adjustments needed**

```bash
git add -A
git commit -m "fix: <describe any adjustments>"
```
