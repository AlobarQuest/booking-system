# Drive Time Buffers and Calendar-Window Availability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add automatic drive time buffers (via Google Maps API with local cache) and calendar-window availability (restrict booking slots to specific Google Calendar events by title).

**Architecture:** Drive time is calculated per availability window using the preceding event's location (from Google Calendar full event details) with a DB cache. Calendar windows intersect an appointment type's regular availability rules with matching Google Calendar events, excluding those events from busy-time blocking. Both features integrate into a refactored slots.py that uses new availability service helpers instead of a monolithic `compute_slots()`.

**Tech Stack:** FastAPI, SQLAlchemy, Google Calendar API (`events.list`), Google Maps Distance Matrix API, httpx, existing pytest test suite.

---

### Task 1: Data model + migrations

**Files:**
- Modify: `app/models.py`
- Modify: `app/database.py`
- Test: `tests/test_models.py`

**Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_appointment_type_has_drive_time_fields():
    from app.models import AppointmentType
    t = AppointmentType()
    assert hasattr(t, "requires_drive_time")
    assert hasattr(t, "calendar_window_enabled")
    assert hasattr(t, "calendar_window_title")
    assert hasattr(t, "calendar_window_calendar_id")


def test_drive_time_cache_model_exists():
    from app.models import DriveTimeCache
    entry = DriveTimeCache()
    assert hasattr(entry, "origin_address")
    assert hasattr(entry, "destination_address")
    assert hasattr(entry, "drive_minutes")
    assert hasattr(entry, "cached_at")
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_models.py::test_appointment_type_has_drive_time_fields tests/test_models.py::test_drive_time_cache_model_exists -v
```

Expected: `FAIL` — `ImportError` or `AttributeError`

**Step 3: Add DriveTimeCache model and new AppointmentType fields**

In `app/models.py`, add the `DriveTimeCache` class after the `Setting` class:

```python
class DriveTimeCache(Base):
    __tablename__ = "drive_time_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    origin_address: Mapped[str] = mapped_column(Text, nullable=False)
    destination_address: Mapped[str] = mapped_column(Text, nullable=False)
    drive_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
```

In `app/models.py`, add four new fields to `AppointmentType` after `guest_event_title`:

```python
    requires_drive_time: Mapped[bool] = mapped_column(Boolean, default=False)
    calendar_window_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    calendar_window_title: Mapped[str] = mapped_column(Text, default="")
    calendar_window_calendar_id: Mapped[str] = mapped_column(Text, default="")
```

**Step 4: Add migrations in database.py**

In `app/database.py`, extend the migration loop in `init_db()`. Replace the existing loop with:

```python
    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(appointment_types)"))}
    for col, definition in [
        ("location", "TEXT NOT NULL DEFAULT ''"),
        ("show_as", "VARCHAR(20) NOT NULL DEFAULT 'busy'"),
        ("visibility", "VARCHAR(20) NOT NULL DEFAULT 'default'"),
        ("owner_event_title", "TEXT NOT NULL DEFAULT ''"),
        ("guest_event_title", "TEXT NOT NULL DEFAULT ''"),
        ("requires_drive_time", "BOOLEAN NOT NULL DEFAULT 0"),
        ("calendar_window_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
        ("calendar_window_title", "TEXT NOT NULL DEFAULT ''"),
        ("calendar_window_calendar_id", "TEXT NOT NULL DEFAULT ''"),
    ]:
        if col not in existing:
            conn.execute(text(f"ALTER TABLE appointment_types ADD COLUMN {col} {definition}"))
    conn.commit()
```

**Step 5: Run test to verify it passes**

```bash
pytest tests/test_models.py::test_appointment_type_has_drive_time_fields tests/test_models.py::test_drive_time_cache_model_exists -v
```

Expected: `PASS`

**Step 6: Run full test suite to check for regressions**

```bash
pytest -v
```

Expected: All existing tests pass.

**Step 7: Commit**

```bash
git add app/models.py app/database.py tests/test_models.py
git commit -m "feat: add DriveTimeCache model and new AppointmentType fields for drive time and calendar windows"
```

---

### Task 2: Config — add GOOGLE_MAPS_API_KEY

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_google_maps_api_key_defaults_empty():
    import os
    os.environ.pop("GOOGLE_MAPS_API_KEY", None)
    from app.config import Settings
    s = Settings()
    assert s.google_maps_api_key == ""
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_config.py::test_google_maps_api_key_defaults_empty -v
```

Expected: `FAIL` — `AttributeError`

**Step 3: Add the setting**

In `app/config.py`, add after `google_redirect_uri`:

```python
    google_maps_api_key: str = ""
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/test_config.py::test_google_maps_api_key_defaults_empty -v
```

Expected: `PASS`

**Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat: add GOOGLE_MAPS_API_KEY config setting"
```

---

### Task 3: Drive time service

**Files:**
- Create: `app/services/drive_time.py`
- Create: `tests/test_drive_time.py`

**Step 1: Write the failing tests**

Create `tests/test_drive_time.py`:

```python
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_get_drive_time_returns_zero_without_api_key():
    from app.services.drive_time import get_drive_time
    db = make_db()
    with patch("app.services.drive_time.get_settings") as mock_settings:
        mock_settings.return_value.google_maps_api_key = ""
        result = get_drive_time("123 Main St", "456 Oak Ave", db)
    assert result == 0


def test_get_drive_time_calls_maps_api_and_caches():
    from app.services.drive_time import get_drive_time
    db = make_db()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "rows": [{"elements": [{"status": "OK", "duration": {"value": 1500}}]}]
    }
    with patch("app.services.drive_time.get_settings") as mock_settings, \
         patch("app.services.drive_time.httpx.get", return_value=mock_response) as mock_get:
        mock_settings.return_value.google_maps_api_key = "fake-key"
        result = get_drive_time("123 Main St", "456 Oak Ave", db)
    assert result == 25  # 1500 seconds = 25 minutes (rounded up)
    mock_get.assert_called_once()


def test_get_drive_time_uses_cache_on_second_call():
    from app.services.drive_time import get_drive_time
    db = make_db()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "rows": [{"elements": [{"status": "OK", "duration": {"value": 1500}}]}]
    }
    with patch("app.services.drive_time.get_settings") as mock_settings, \
         patch("app.services.drive_time.httpx.get", return_value=mock_response) as mock_get:
        mock_settings.return_value.google_maps_api_key = "fake-key"
        get_drive_time("123 Main St", "456 Oak Ave", db)
        result = get_drive_time("123 Main St", "456 Oak Ave", db)
    assert result == 25
    assert mock_get.call_count == 1  # Only called once; second call used cache


def test_get_drive_time_returns_zero_on_maps_api_failure():
    from app.services.drive_time import get_drive_time
    db = make_db()
    with patch("app.services.drive_time.get_settings") as mock_settings, \
         patch("app.services.drive_time.httpx.get", side_effect=Exception("network error")):
        mock_settings.return_value.google_maps_api_key = "fake-key"
        result = get_drive_time("123 Main St", "456 Oak Ave", db)
    assert result == 0


def test_get_drive_time_refreshes_stale_cache():
    from app.services.drive_time import get_drive_time
    from app.models import DriveTimeCache
    db = make_db()
    # Insert a stale cache entry (31 days old)
    stale = DriveTimeCache(
        origin_address="123 Main St",
        destination_address="456 Oak Ave",
        drive_minutes=10,
        cached_at=datetime.utcnow() - timedelta(days=31),
    )
    db.add(stale)
    db.commit()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "rows": [{"elements": [{"status": "OK", "duration": {"value": 1800}}]}]
    }
    with patch("app.services.drive_time.get_settings") as mock_settings, \
         patch("app.services.drive_time.httpx.get", return_value=mock_response):
        mock_settings.return_value.google_maps_api_key = "fake-key"
        result = get_drive_time("123 Main St", "456 Oak Ave", db)
    assert result == 30  # Fresh from API, not the stale 10
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_drive_time.py -v
```

Expected: `FAIL` — `ModuleNotFoundError: No module named 'app.services.drive_time'`

**Step 3: Create the drive time service**

Create `app/services/drive_time.py`:

```python
from datetime import datetime, timedelta

import httpx

from app.config import get_settings

MAPS_DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"
CACHE_TTL_DAYS = 30


def get_drive_time(origin: str, destination: str, db) -> int:
    """Return drive time in minutes from origin to destination.

    Checks DriveTimeCache first. Calls Google Maps Distance Matrix API if
    the cache entry is missing or older than 30 days. Returns 0 if the API
    key is not configured or the request fails.
    """
    from app.models import DriveTimeCache

    settings = get_settings()
    if not settings.google_maps_api_key:
        return 0

    now = datetime.utcnow()
    cache_entry = (
        db.query(DriveTimeCache)
        .filter_by(origin_address=origin, destination_address=destination)
        .first()
    )

    if cache_entry and (now - cache_entry.cached_at) < timedelta(days=CACHE_TTL_DAYS):
        return cache_entry.drive_minutes

    # Call Google Maps Distance Matrix API
    try:
        resp = httpx.get(
            MAPS_DISTANCE_MATRIX_URL,
            params={
                "origins": origin,
                "destinations": destination,
                "mode": "driving",
                "key": settings.google_maps_api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        element = data["rows"][0]["elements"][0]
        if element["status"] != "OK":
            return 0
        duration_seconds = element["duration"]["value"]
        drive_minutes = (duration_seconds + 59) // 60  # round up to nearest minute
    except Exception:
        return 0

    # Upsert cache
    if cache_entry:
        cache_entry.drive_minutes = drive_minutes
        cache_entry.cached_at = now
    else:
        db.add(DriveTimeCache(
            origin_address=origin,
            destination_address=destination,
            drive_minutes=drive_minutes,
            cached_at=now,
        ))
    db.commit()
    return drive_minutes
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_drive_time.py -v
```

Expected: All 5 tests `PASS`

**Step 5: Run full suite**

```bash
pytest -v
```

Expected: All tests pass.

**Step 6: Commit**

```bash
git add app/services/drive_time.py tests/test_drive_time.py
git commit -m "feat: add drive time service with Google Maps API and 30-day cache"
```

---

### Task 4: Calendar service — get_events_for_day()

**Files:**
- Modify: `app/services/calendar.py`
- Modify: `tests/test_calendar.py`

**Step 1: Write the failing test**

Add to `tests/test_calendar.py`:

```python
def test_get_events_for_day_returns_event_list():
    service = make_service()
    mock_api_result = {
        "items": [
            {
                "summary": "Doctor Appointment",
                "location": "123 Medical Dr",
                "start": {"dateTime": "2025-03-03T10:00:00Z"},
                "end": {"dateTime": "2025-03-03T11:00:00Z"},
            },
            {
                "summary": "All Day Event",
                "start": {"date": "2025-03-03"},  # all-day — no dateTime
                "end": {"date": "2025-03-04"},
            },
        ]
    }
    with patch.object(service, "_build_service") as mock_build:
        mock_svc = MagicMock()
        mock_svc.events().list().execute.return_value = mock_api_result
        mock_build.return_value = mock_svc
        events = service.get_events_for_day(
            "fake-token", "primary",
            datetime(2025, 3, 3, 0, 0), datetime(2025, 3, 4, 0, 0)
        )
    assert len(events) == 1  # all-day event is skipped
    assert events[0]["summary"] == "Doctor Appointment"
    assert events[0]["location"] == "123 Medical Dr"
    assert events[0]["start"] == datetime(2025, 3, 3, 10, 0)
    assert events[0]["end"] == datetime(2025, 3, 3, 11, 0)


def test_get_events_for_day_missing_location_returns_empty_string():
    service = make_service()
    mock_api_result = {
        "items": [
            {
                "summary": "Meeting",
                "start": {"dateTime": "2025-03-03T14:00:00Z"},
                "end": {"dateTime": "2025-03-03T15:00:00Z"},
            }
        ]
    }
    with patch.object(service, "_build_service") as mock_build:
        mock_svc = MagicMock()
        mock_svc.events().list().execute.return_value = mock_api_result
        mock_build.return_value = mock_svc
        events = service.get_events_for_day(
            "fake-token", "primary",
            datetime(2025, 3, 3, 0, 0), datetime(2025, 3, 4, 0, 0)
        )
    assert events[0]["location"] == ""
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_calendar.py::test_get_events_for_day_returns_event_list tests/test_calendar.py::test_get_events_for_day_missing_location_returns_empty_string -v
```

Expected: `FAIL` — `AttributeError: 'CalendarService' object has no attribute 'get_events_for_day'`

**Step 3: Add get_events_for_day to CalendarService**

In `app/services/calendar.py`, add after the `delete_event` method (before `fetch_webcal_busy`):

```python
    def get_events_for_day(
        self,
        refresh_token: str,
        calendar_id: str,
        day_start: datetime,
        day_end: datetime,
    ) -> list[dict]:
        """Return all timed events for a day as dicts with keys: start, end, summary, location.

        All datetimes are returned as naive UTC. All-day events (date-only) are excluded.
        day_start and day_end must be naive UTC datetimes.
        """
        service = self._build_service(refresh_token)
        result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=day_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                timeMax=day_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = []
        for item in result.get("items", []):
            start_str = item["start"].get("dateTime")
            end_str = item["end"].get("dateTime")
            if not start_str or not end_str:
                continue  # skip all-day events
            ev_start = datetime.fromisoformat(start_str.replace("Z", "+00:00")).replace(tzinfo=None)
            ev_end = datetime.fromisoformat(end_str.replace("Z", "+00:00")).replace(tzinfo=None)
            events.append({
                "start": ev_start,
                "end": ev_end,
                "summary": item.get("summary", ""),
                "location": item.get("location", ""),
            })
        return events
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_calendar.py -v
```

Expected: All tests `PASS`

**Step 5: Commit**

```bash
git add app/services/calendar.py tests/test_calendar.py
git commit -m "feat: add CalendarService.get_events_for_day() for full event details including location"
```

---

### Task 5: Availability service — refactor + new helpers

**Files:**
- Modify: `app/services/availability.py`
- Modify: `tests/test_availability.py`

**Step 1: Write the failing tests**

Add to `tests/test_availability.py`:

```python
from app.services.availability import (
    compute_slots, subtract_intervals, split_into_slots,
    intersect_windows, trim_windows_for_drive_time, filter_by_advance_notice,
    _build_free_windows,
)


def test_intersect_windows_overlapping():
    from datetime import time
    a = [(time(9, 0), time(17, 0))]
    b = [(time(11, 0), time(15, 0))]
    result = intersect_windows(a, b)
    assert result == [(time(11, 0), time(15, 0))]


def test_intersect_windows_no_overlap():
    from datetime import time
    a = [(time(9, 0), time(12, 0))]
    b = [(time(13, 0), time(17, 0))]
    result = intersect_windows(a, b)
    assert result == []


def test_intersect_windows_partial():
    from datetime import time
    a = [(time(9, 0), time(14, 0))]
    b = [(time(11, 0), time(17, 0))]
    result = intersect_windows(a, b)
    assert result == [(time(11, 0), time(14, 0))]


def test_filter_by_advance_notice_filters_past_cutoff():
    from datetime import date, datetime, time
    slots = [time(9, 0), time(10, 0), time(11, 0)]
    # now is 8:30, min_advance is 2 hours -> cutoff is 10:30
    result = filter_by_advance_notice(slots, date(2025, 3, 3), 2, datetime(2025, 3, 3, 8, 30))
    assert time(9, 0) not in result
    assert time(10, 0) not in result
    assert time(11, 0) in result


def test_trim_windows_for_drive_time_trims_by_drive_minutes():
    from datetime import date, time, datetime
    from unittest.mock import patch, MagicMock
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    windows = [(time(11, 0), time(14, 0))]
    # Preceding event ended at 10:45 (within 1 hour of 11:00 window start)
    day_events = [
        {"start": datetime(2025, 3, 3, 9, 0), "end": datetime(2025, 3, 3, 10, 45), "summary": "Previous Appt", "location": "123 Main St"}
    ]
    with patch("app.services.availability.get_drive_time", return_value=20):
        result = trim_windows_for_drive_time(
            windows, date(2025, 3, 3), day_events,
            destination="456 Oak Ave", home_address="789 Home Rd", db=db
        )
    assert result == [(time(11, 20), time(14, 0))]


def test_trim_windows_for_drive_time_uses_home_when_no_preceding_event():
    from datetime import date, time, datetime
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    windows = [(time(9, 0), time(12, 0))]
    day_events = []  # No preceding events
    with patch("app.services.availability.get_drive_time", return_value=30) as mock_dt:
        result = trim_windows_for_drive_time(
            windows, date(2025, 3, 3), day_events,
            destination="456 Oak Ave", home_address="789 Home Rd", db=db
        )
    mock_dt.assert_called_with("789 Home Rd", "456 Oak Ave", db)
    assert result == [(time(9, 30), time(12, 0))]


def test_trim_windows_for_drive_time_skips_event_outside_1hr_lookback():
    from datetime import date, time, datetime
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    windows = [(time(14, 0), time(17, 0))]
    # Event ended at 9am — more than 1 hour before 2pm window start
    day_events = [
        {"start": datetime(2025, 3, 3, 8, 0), "end": datetime(2025, 3, 3, 9, 0), "summary": "Morning Appt", "location": "Far Away Place"}
    ]
    with patch("app.services.availability.get_drive_time", return_value=25) as mock_dt:
        result = trim_windows_for_drive_time(
            windows, date(2025, 3, 3), day_events,
            destination="456 Oak Ave", home_address="789 Home Rd", db=db
        )
    # Should use home_address since event is outside 1-hour lookback
    mock_dt.assert_called_with("789 Home Rd", "456 Oak Ave", db)


def test_trim_windows_for_drive_time_zero_for_same_location():
    from datetime import date, time, datetime
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    windows = [(time(11, 0), time(14, 0))]
    day_events = [
        {"start": datetime(2025, 3, 3, 9, 0), "end": datetime(2025, 3, 3, 10, 45), "summary": "Previous", "location": "456 Oak Ave"}
    ]
    with patch("app.services.availability.get_drive_time") as mock_dt:
        result = trim_windows_for_drive_time(
            windows, date(2025, 3, 3), day_events,
            destination="456 Oak Ave", home_address="789 Home Rd", db=db
        )
    mock_dt.assert_not_called()  # Same location — no API call
    assert result == [(time(11, 0), time(14, 0))]  # No trimming
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_availability.py -v
```

Expected: `FAIL` — `ImportError` on new function names

**Step 3: Refactor availability.py**

Replace the entire contents of `app/services/availability.py` with:

```python
from datetime import date, datetime, time, timedelta


def _time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _minutes_to_time(m: int) -> time:
    return time(m // 60, m % 60)


def subtract_intervals(
    windows: list[tuple[time, time]],
    busy: list[tuple[datetime, datetime]],
    target_date: date,
) -> list[tuple[time, time]]:
    """Remove busy datetime intervals from time windows on a given date."""
    result = []
    for w_start, w_end in windows:
        segments = [(w_start, w_end)]
        for busy_start, busy_end in busy:
            if busy_start.date() > target_date or busy_end.date() < target_date:
                continue
            b_start = busy_start.time() if busy_start.date() == target_date else time(0, 0)
            b_end = busy_end.time() if busy_end.date() == target_date else time(23, 59, 59)
            new_segments = []
            for s, e in segments:
                if b_end <= s or b_start >= e:
                    new_segments.append((s, e))
                else:
                    if s < b_start:
                        new_segments.append((s, b_start))
                    if b_end < e:
                        new_segments.append((b_end, e))
            segments = new_segments
        result.extend(segments)
    return result


def split_into_slots(
    windows: list[tuple[time, time]],
    duration_minutes: int,
    buffer_before_minutes: int,
    buffer_after_minutes: int,
) -> list[time]:
    """Split time windows into appointment start times.

    buffer_before_minutes: free time required before each appointment start.
    The returned slot time is the appointment start (after the buffer).
    Each slot consumes buffer_before + duration + buffer_after minutes.
    """
    slot_total = buffer_before_minutes + duration_minutes + buffer_after_minutes
    slots = []
    for w_start, w_end in windows:
        current = _time_to_minutes(w_start)
        end = _time_to_minutes(w_end)
        while current + buffer_before_minutes + duration_minutes <= end:
            slots.append(_minutes_to_time(current + buffer_before_minutes))
            current += slot_total
    return slots


def intersect_windows(
    windows_a: list[tuple[time, time]],
    windows_b: list[tuple[time, time]],
) -> list[tuple[time, time]]:
    """Return the intersection of two sets of time windows."""
    result = []
    for a_start, a_end in windows_a:
        for b_start, b_end in windows_b:
            start = max(a_start, b_start)
            end = min(a_end, b_end)
            if start < end:
                result.append((start, end))
    return sorted(result)


def filter_by_advance_notice(
    slots: list[time],
    target_date: date,
    min_advance_hours: int,
    now: datetime,
) -> list[time]:
    """Remove slots that fall within the min_advance_hours cutoff from now."""
    end_of_day = datetime.combine(target_date, time(23, 59, 59))
    cutoff = now + timedelta(hours=min_advance_hours)
    if cutoff > end_of_day:
        return []
    if cutoff.date() == target_date:
        cutoff_time = cutoff.time()
        return [s for s in slots if s >= cutoff_time]
    return slots


def trim_windows_for_drive_time(
    windows: list[tuple[time, time]],
    target_date: date,
    day_events: list[dict],
    destination: str,
    home_address: str,
    db,
) -> list[tuple[time, time]]:
    """Trim the start of each window by drive time from the preceding event's location.

    day_events: list of dicts with keys start (datetime), end (datetime), location (str), summary (str).
    All datetimes must be in local time (naive). Only considers events that ended within
    1 hour before the window start. Falls back to home_address if nothing found.
    """
    from app.services.drive_time import get_drive_time

    result = []
    for w_start, w_end in windows:
        window_start_dt = datetime.combine(target_date, w_start)
        lookback_cutoff = window_start_dt - timedelta(hours=1)

        # Find the most recent event ending within 1 hour before window start
        preceding = None
        for ev in day_events:
            if lookback_cutoff <= ev["end"] <= window_start_dt:
                if preceding is None or ev["end"] > preceding["end"]:
                    preceding = ev

        origin = (preceding.get("location") or "").strip() if preceding else ""
        if not origin:
            origin = home_address

        if not origin or not destination:
            result.append((w_start, w_end))
            continue

        if origin.lower() == destination.lower():
            drive_mins = 0
        else:
            drive_mins = get_drive_time(origin, destination, db)

        trimmed_start_mins = _time_to_minutes(w_start) + drive_mins
        trimmed_start = _minutes_to_time(min(trimmed_start_mins, 23 * 60 + 59))
        if trimmed_start < w_end:
            result.append((trimmed_start, w_end))
        # If drive time consumes the entire window, it is dropped

    return result


def _build_free_windows(
    target_date: date,
    rules: list,
    blocked_periods: list,
    busy_intervals: list[tuple[datetime, datetime]],
) -> list[tuple[time, time]]:
    """Compute available time windows after subtracting blocked periods and busy intervals."""
    day_of_week = target_date.weekday()  # 0=Monday
    day_rules = [r for r in rules if r.day_of_week == day_of_week and r.active]
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
) -> list[time]:
    """Compute available appointment start times for a given date."""
    windows = _build_free_windows(target_date, rules, blocked_periods, busy_intervals)
    if not windows:
        return []
    slots = split_into_slots(windows, duration_minutes, buffer_before_minutes, buffer_after_minutes)
    return filter_by_advance_notice(slots, target_date, min_advance_hours, now)
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_availability.py -v
```

Expected: All tests `PASS`

**Step 5: Run full suite**

```bash
pytest -v
```

Expected: All tests pass.

**Step 6: Commit**

```bash
git add app/services/availability.py tests/test_availability.py
git commit -m "feat: refactor availability service — extract helpers for drive time, calendar windows, and advance notice filtering"
```

---

### Task 6: Slots endpoint — integrate drive time and calendar windows

**Files:**
- Modify: `app/routers/slots.py`
- Modify: `tests/test_slots_route.py`

**Step 1: Write failing tests**

Read `tests/test_slots_route.py` first to understand the existing pattern, then add:

```python
def test_slots_applies_drive_time_when_enabled(client):
    """When requires_drive_time=True, trim_windows_for_drive_time is called."""
    from unittest.mock import patch, MagicMock
    from app.models import AppointmentType, AvailabilityRule
    from app.database import get_db

    db = next(client.app.dependency_overrides[get_db]())
    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True)
    db.add(rule)
    appt = AppointmentType(
        name="Showing", duration_minutes=60, active=True,
        location="456 Oak Ave", requires_drive_time=True,
        buffer_before_minutes=0, buffer_after_minutes=0,
    )
    db.add(appt)
    db.commit()

    with patch("app.routers.slots.trim_windows_for_drive_time", return_value=[]) as mock_trim, \
         patch("app.routers.slots._build_free_windows", return_value=[]):
        resp = client.get(f"/slots?type_id={appt.id}&date=2025-03-03")
    mock_trim.assert_called_once()


def test_slots_calendar_window_filters_slots(client):
    """When calendar_window_enabled=True and no matching events, return no slots."""
    from unittest.mock import patch
    from app.models import AppointmentType, AvailabilityRule
    from app.database import get_db

    db = next(client.app.dependency_overrides[get_db]())
    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True)
    db.add(rule)
    appt = AppointmentType(
        name="Rental Showing", duration_minutes=60, active=True,
        location="456 Oak Ave",
        calendar_window_enabled=True,
        calendar_window_title="POSSIBLE RENTAL SHOWINGS",
        buffer_before_minutes=0, buffer_after_minutes=0,
    )
    db.add(appt)
    from app.dependencies import set_setting
    set_setting(db, "google_refresh_token", "fake-token")
    db.commit()

    with patch("app.routers.slots.CalendarService") as MockCal:
        MockCal.return_value.get_events_for_day.return_value = []  # No matching events
        MockCal.return_value.get_busy_intervals.return_value = []
        resp = client.get(f"/slots?type_id={appt.id}&date=2025-03-03")
    assert resp.status_code == 200
    assert "no-slots" in resp.text or resp.text.count("slot") == 0
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_slots_route.py::test_slots_applies_drive_time_when_enabled tests/test_slots_route.py::test_slots_calendar_window_filters_slots -v
```

Expected: `FAIL`

**Step 3: Rewrite slots.py**

Replace the contents of `app/routers/slots.py` with:

```python
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
        from app.services.calendar import CalendarService
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
        if appt_type.requires_drive_time and appt_type.location:
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
            from app.services.calendar import fetch_webcal_busy
            utc_busy = fetch_webcal_busy(webcal_url, day_start, day_end)
            for utc_start, utc_end in utc_busy:
                local_start = utc_start.replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                local_end = utc_end.replace(tzinfo=dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
                busy_intervals.append((local_start, local_end))
        except Exception:
            pass

    # Build availability windows
    windows = _build_free_windows(target_date, rules, blocked, busy_intervals)

    # Apply calendar window constraint (intersect with matching calendar events)
    if window_intervals:
        windows = intersect_windows(windows, window_intervals)

    # Apply drive time trimming
    if appt_type.requires_drive_time and appt_type.location:
        home_address = get_setting(db, "home_address", "")
        windows = trim_windows_for_drive_time(
            windows, target_date, local_day_events,
            destination=appt_type.location,
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
```

**Step 4: Run all slots tests**

```bash
pytest tests/test_slots_route.py -v
```

Expected: All tests pass.

**Step 5: Run full suite**

```bash
pytest -v
```

Expected: All tests pass.

**Step 6: Commit**

```bash
git add app/routers/slots.py tests/test_slots_route.py
git commit -m "feat: integrate drive time and calendar window availability into slots endpoint"
```

---

### Task 7: Admin UI + router — home address setting

**Files:**
- Modify: `app/templates/admin/settings.html`
- Modify: `app/routers/admin.py`

**Step 1: Add home_address to the settings page template**

In `app/templates/admin/settings.html`, add after the Timezone label and before the Save button:

```html
    <label>Home Address (used for drive time calculation)
      <input type="text" name="home_address" value="{{ home_address }}"
             placeholder="e.g. 123 Main St, Atlanta, GA 30301">
    </label>
```

**Step 2: Update settings_page() to pass home_address**

In `app/routers/admin.py`, in `settings_page()`, add `"home_address"` to the template context:

```python
        "home_address": get_setting(db, "home_address", ""),
```

**Step 3: Update save_settings() to save home_address**

In `app/routers/admin.py`, update the `save_settings` function signature to accept `home_address`:

```python
    home_address: str = Form(""),
```

And in the body, add:

```python
    set_setting(db, "home_address", home_address)
```

**Step 4: Manual test**

Start the app locally and visit `/admin/settings`. Verify the "Home Address" field appears and saves correctly.

**Step 5: Commit**

```bash
git add app/templates/admin/settings.html app/routers/admin.py
git commit -m "feat: add home address field to admin settings for drive time origin"
```

---

### Task 8: Admin UI + router — appointment type new fields

**Files:**
- Modify: `app/templates/admin/appointment_types.html`
- Modify: `app/routers/admin.py`

**Step 1: Add drive time and calendar window sections to the appointment type form**

In `app/templates/admin/appointment_types.html`, add after the `guest_event_title` label and before the submit button:

```html
    <hr style="margin:1rem 0;border:none;border-top:1px solid #e2e8f0;">
    <label style="flex-direction:row;align-items:center;gap:.5rem;cursor:pointer;">
      <input type="checkbox" name="requires_drive_time" value="true"
             {% if edit_type and edit_type.requires_drive_time %}checked{% endif %}
             style="width:auto;">
      Calculate drive time to this location before each appointment
    </label>
    <small style="color:#64748b;margin-top:-.5rem;">
      Requires a physical location above and GOOGLE_MAPS_API_KEY configured in the environment.
    </small>

    <hr style="margin:1rem 0;border:none;border-top:1px solid #e2e8f0;">
    <label style="flex-direction:row;align-items:center;gap:.5rem;cursor:pointer;">
      <input type="checkbox" name="calendar_window_enabled" value="true"
             id="cal_window_check"
             {% if edit_type and edit_type.calendar_window_enabled %}checked{% endif %}
             style="width:auto;"
             onchange="document.getElementById('cal_window_fields').style.display=this.checked?'block':'none'">
      Only allow bookings during specific Google Calendar events
    </label>
    <div id="cal_window_fields" style="display:{% if edit_type and edit_type.calendar_window_enabled %}block{% else %}none{% endif %};">
      <label>Event Title (exact match, case-insensitive)
        <input type="text" name="calendar_window_title"
               value="{{ edit_type.calendar_window_title if edit_type else '' }}"
               placeholder="e.g. POSSIBLE RENTAL SHOWINGS">
      </label>
      <label>Calendar ID (leave blank to use same calendar as booking)
        <input type="text" name="calendar_window_calendar_id"
               value="{{ edit_type.calendar_window_calendar_id if edit_type else '' }}"
               placeholder="e.g. primary or user@gmail.com">
      </label>
    </div>
```

**Step 2: Update create_appt_type() in admin.py**

Add the new form parameters:

```python
    requires_drive_time: str = Form("false"),
    calendar_window_enabled: str = Form("false"),
    calendar_window_title: str = Form(""),
    calendar_window_calendar_id: str = Form(""),
```

Update the `AppointmentType(...)` constructor call to include:

```python
        requires_drive_time=(requires_drive_time == "true"),
        calendar_window_enabled=(calendar_window_enabled == "true"),
        calendar_window_title=calendar_window_title,
        calendar_window_calendar_id=calendar_window_calendar_id,
```

**Step 3: Update update_appt_type() in admin.py**

Add the same four parameters to the function signature, then in the body add:

```python
        t.requires_drive_time = (requires_drive_time == "true")
        t.calendar_window_enabled = (calendar_window_enabled == "true")
        t.calendar_window_title = calendar_window_title
        t.calendar_window_calendar_id = calendar_window_calendar_id
```

**Step 4: Run full test suite**

```bash
pytest -v
```

Expected: All tests pass.

**Step 5: Manual test**

Visit `/admin/appointment-types`, create a new appointment type, and verify:
- The "Calculate drive time" checkbox appears and saves correctly.
- The "Calendar window" checkbox shows/hides the title and calendar ID fields.
- Saving with calendar window enabled persists the title and calendar ID.

**Step 6: Commit**

```bash
git add app/templates/admin/appointment_types.html app/routers/admin.py
git commit -m "feat: add drive time and calendar window fields to appointment type admin UI"
```

---

### Post-Implementation: Set GOOGLE_MAPS_API_KEY in Coolify

After all tasks are complete:

1. Go to the Coolify dashboard → booking app → Environment Variables
2. Add `GOOGLE_MAPS_API_KEY=<your key from Google Cloud Console>`
3. In Google Cloud Console, enable the **Distance Matrix API** for your project
4. Redeploy

To get a Maps API key: Google Cloud Console → APIs & Services → Credentials → Create Credentials → API Key → restrict to "Distance Matrix API".
