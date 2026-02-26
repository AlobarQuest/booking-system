# Design: Drive Time Buffers and Calendar-Window Availability

**Date:** 2026-02-26
**Status:** Approved

---

## Overview

Two new features for the booking system:

1. **Drive Time Buffers** — automatically block travel time before an appointment based on the previous appointment's location, calculated via Google Maps Distance Matrix API with a local cache.
2. **Calendar-Window Availability** — restrict an appointment type to only be bookable during specific Google Calendar events matching a configured title (e.g. "POSSIBLE RENTAL SHOWINGS").

---

## Feature 1: Drive Time Buffers

### Purpose

When appointments require travel to a physical location, the system automatically calculates and blocks drive time before the appointment. The origin is either the previous appointment's location (if it ended within the last hour) or the admin's home address.

### Data Model

**New DB table: `DriveTimeCache`**
```
id              int, PK
origin_address  str
destination_address str
drive_minutes   int
cached_at       datetime
```
Index on `(origin_address, destination_address)`. Entries older than 30 days are considered stale and re-fetched.

**New field on `AppointmentType`:**
- `requires_drive_time` (bool, default `False`) — enables drive time calculation. Leave off for phone/Zoom appointments.

**New setting:** `home_address` (str) — admin's home address, used as origin when no prior appointment is found within the lookback window.

**New env var:** `GOOGLE_MAPS_API_KEY` — set in Coolify environment variables.

### Drive Time Logic

When computing slots for a day where `requires_drive_time = True` on the appointment type:

1. Fetch all Google Calendar events for the day using `events.list()` (full event details, including `location` field).
2. For each available time window (gap between busy periods):
   a. Find the most recent event that ended within **1 hour** before the window start. Check both Google Calendar events (any event with a `location` field) and local database bookings (linked to appointment types with a `location`).
   b. If found and has a location: `origin = that event's location`
   c. If nothing found within 1 hour: `origin = home_address` (from settings)
   d. `destination = appointment_type.location`
   e. If `origin == destination`: `drive_minutes = 0` (no travel needed)
   f. Otherwise: call `get_drive_time(origin, destination, db)` — checks cache, calls Google Maps if stale/missing, stores result.
3. Trim the window's start forward by `drive_minutes`.
4. Generate slots from the trimmed window as normal.

### `get_drive_time()` Service

New file: `app/services/drive_time.py`

```
get_drive_time(origin: str, destination: str, db: Session) -> int
```

1. Query `DriveTimeCache` for matching `(origin, destination)` pair.
2. If found and `cached_at` is within 30 days: return `drive_minutes`.
3. Otherwise: call Google Maps Distance Matrix API with `mode=driving`.
4. Upsert result into `DriveTimeCache`.
5. Return `drive_minutes`.

---

## Feature 2: Calendar-Window Availability

### Purpose

Some appointment types (e.g. rental showings) should only be bookable during windows the admin explicitly opens on their Google Calendar. The admin creates calendar events with a specific title to signal availability. These events may be marked "busy" on the calendar (to block other systems), but the booking system treats them as available windows.

### Data Model

**New fields on `AppointmentType`:**
- `calendar_window_enabled` (bool, default `False`)
- `calendar_window_title` (str, nullable) — exact title to match, case-insensitive (e.g. `POSSIBLE RENTAL SHOWINGS`)
- `calendar_window_calendar_id` (str, nullable) — Google Calendar to search; defaults to the appointment type's `calendar_id`

### Calendar-Window Logic

When computing slots for a day where `calendar_window_enabled = True`:

1. Fetch full event details for the day from `calendar_window_calendar_id` using `events.list()`.
2. Filter to events whose title exactly matches `calendar_window_title` (case-insensitive).
3. Collect those events' time ranges as "window intervals."
4. Intersect window intervals with the appointment type's regular availability rules (both must allow the time).
5. When subtracting busy intervals: **exclude any event whose title matches `calendar_window_title`** from the busy interval list — these events are intentionally marked busy to block other systems but must not block our own slots.
6. Subtract remaining busy intervals as normal.
7. Generate slots from the resulting windows. Slots may start anywhere within a window even if the appointment would extend past the window's end.

If no matching calendar events exist on a given day, zero slots are offered — the day appears fully unavailable for that appointment type.

---

## Admin UI Changes

### Settings Page
- New field: **"Home Address"** — plain text input, stored as `home_address` setting. Used as drive origin when no prior appointment is found within the 1-hour lookback window.

### Appointment Type Form — two new sections

**Drive Time section:**
- Checkbox: "Calculate drive time to this location" (`requires_drive_time`)
- Relevant only when the appointment type has a physical `location` set.

**Calendar Window section:**
- Checkbox: "Only allow bookings during specific calendar events" (`calendar_window_enabled`)
- When checked, two fields appear:
  - "Event Title" — exact title to match (e.g. `POSSIBLE RENTAL SHOWINGS`)
  - "Calendar" — dropdown/text field for which Google Calendar to check (defaults to booking calendar)

---

## Files to Create or Modify

| File | Change |
|------|--------|
| `app/models.py` | Add `DriveTimeCache` model; add 3 new fields to `AppointmentType` |
| `app/database.py` | Add migration entries for new columns |
| `app/config.py` | Add `google_maps_api_key` setting |
| `app/services/drive_time.py` | New file — `get_drive_time()` with cache logic |
| `app/services/calendar.py` | Add `get_events_for_day()` returning full event details including location and title |
| `app/services/availability.py` | Update `compute_slots()` to accept and apply drive time and calendar windows |
| `app/routers/slots.py` | Fetch drive time and calendar window data, pass to availability service |
| `app/routers/admin.py` | Handle new fields on appointment type form and settings |
| `app/templates/admin/settings.html` | Add home address field |
| `app/templates/admin/appointment_types.html` | Add drive time and calendar window sections |
| `tests/test_drive_time.py` | New — test cache logic, same-location shortcut, 1-hour lookback |
| `tests/test_availability.py` | Add tests for drive-time-trimmed windows and calendar-window intersection |

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `GOOGLE_MAPS_API_KEY` | Google Maps Distance Matrix API key — set in Coolify |

---

## Out of Scope

- Automatic address geocoding or validation
- Drive time from/to appointments not made through this system that lack a `location` field in Google Calendar
- Return drive time (from new appointment back to next location) — only inbound drive time is calculated
