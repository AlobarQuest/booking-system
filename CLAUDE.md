# BookingAssistant — Claude Context

## What this project is

A personal appointment booking system built with FastAPI + SQLite. It provides a public booking page for guests and an admin panel for the owner. Integrates with Google Calendar for availability checking and event creation, and sends email confirmations via Resend.

**Live URL:** `https://booking.devonwatkins.com`
**GitHub repo:** `AlobarQuest/booking-system`
**Local path:** `/home/devon/Projects/BookingAssistant`

---

## Deployment

- **Host:** Hetzner CX22 VPS at `178.156.247.239`
- **Platform:** Coolify (self-hosted PaaS) — dashboard at `http://178.156.247.239:8000`
- **SSH:** `ssh hetzner-coolify` (key at `~/.ssh/hetzner_ed25519`, passphrase in Bitwarden)
- **Auto-deploy:** Push to `master` branch triggers Coolify webhook → redeploy
- **Database:** SQLite at `/data/booking.db` inside the container (Coolify volume mount)
- **Previous host:** Fly.io (decommissioned — left as-is, no active machines)

---

## Tech Stack

- Python 3.12, FastAPI, SQLAlchemy (mapped columns style), Jinja2 templates
- HTMX for slot loading (no full SPA framework)
- SQLite database — schema managed manually via `app/database.py:init_db()`
- Google Calendar API (freebusy + events), Google OAuth2
- Resend for transactional email
- Docker (see `Dockerfile`) — app runs on port 8080
- pytest for tests

---

## Key Files

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app entry point, lifespan, router registration |
| `app/config.py` | Pydantic settings — reads from env vars |
| `app/models.py` | SQLAlchemy models: AppointmentType, Booking, AvailabilityRule, BlockedPeriod, Setting, DriveTimeCache |
| `app/database.py` | Engine, SessionLocal, `init_db()` with manual column migrations |
| `app/dependencies.py` | `get_setting()`, `set_setting()`, `require_admin()` |
| `app/routers/slots.py` | GET /slots — computes available time slots |
| `app/routers/booking.py` | GET/POST /book — public booking flow |
| `app/routers/admin.py` | All /admin/* routes |
| `app/services/availability.py` | `compute_slots()`, `_build_free_windows()`, `intersect_windows()`, `trim_windows_for_drive_time()`, `filter_by_advance_notice()` |
| `app/services/calendar.py` | `CalendarService` — Google Calendar API wrapper; `fetch_webcal_busy()` |
| `app/services/drive_time.py` | `get_drive_time()` — Google Maps Distance Matrix API + DriveTimeCache |
| `app/services/booking.py` | `create_booking()` |
| `app/services/email.py` | Email via Resend |
| `app/templates/` | Jinja2 templates — `base.html`, `admin_base.html`, booking/* and admin/* |
| `app/static/css/style.css` | All CSS |
| `Dockerfile` | `FROM python:3.12-slim`, runs uvicorn on port 8080 |
| `tests/` | pytest tests — run with `pytest -v` |

---

## Database Migration Pattern

SQLite doesn't support `IF NOT EXISTS` on `ALTER TABLE`. New columns are added in `app/database.py:init_db()` via a PRAGMA check loop:

```python
existing = {row[1] for row in conn.execute(text("PRAGMA table_info(appointment_types)"))}
for col, definition in [...]:
    if col not in existing:
        conn.execute(text(f"ALTER TABLE appointment_types ADD COLUMN {col} {definition}"))
conn.commit()
```

New columns added here **must also** be added as `mapped_column` fields in `app/models.py`.

---

## Environment Variables (set in Coolify)

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | Session signing |
| `GOOGLE_CLIENT_ID` | Google OAuth |
| `GOOGLE_CLIENT_SECRET` | Google OAuth |
| `GOOGLE_REDIRECT_URI` | `https://booking.devonwatkins.com/admin/auth/callback` |
| `GOOGLE_REDIRECT_URI` (calendar) | `https://booking.devonwatkins.com/admin/google/callback` |
| `DATABASE_URL` | `sqlite:////data/booking.db` |
| `ADMIN_EMAIL` | Admin login email |
| `GOOGLE_MAPS_API_KEY` | Drive time calculation (Distance Matrix API) — **needs to be set** |

---

## What Has Been Built (history)

- **Core app** — public booking UI, admin panel, Google Calendar integration, email via Resend
- **Issue #2** — 12-hour AM/PM time display on slot buttons
- **Issue #3** — Separate owner/guest calendar event titles per appointment type
- **Issue #4** — Multi-calendar conflict checking (webcal/ICS feeds + extra Google Calendars)
- **Issue #5** — Modern public booking UI redesign (Inter font, gradient header, step indicator)
- **Coolify migration** — Fully migrated from Fly.io to Hetzner + Coolify. App is live at `https://booking.devonwatkins.com` with auto-deploy from GitHub.

---

## Current Work: Drive Time + Calendar Windows

**Plan:** `docs/plans/2026-02-26-drive-time-and-calendar-windows.md`
**Design:** `docs/plans/2026-02-26-drive-time-and-calendar-windows-design.md`

### Feature 1: Drive Time Buffers
Automatically block travel time before appointments based on the preceding event's location. Uses Google Maps Distance Matrix API with a `DriveTimeCache` DB table (30-day TTL). Looks back up to 1 hour for a preceding Google Calendar event with a location; falls back to admin's home address.

### Feature 2: Calendar-Window Availability
Restrict an appointment type to only be bookable during specific Google Calendar events matching a configured title (exact, case-insensitive). Those events may be marked "busy" on the calendar (intentionally) — the app treats them as available windows and skips them when building the busy interval list.

### Plan Tasks (8 total)
1. Data model + migrations — `DriveTimeCache` model, 4 new `AppointmentType` fields
2. Config — `GOOGLE_MAPS_API_KEY` setting
3. Drive time service — `app/services/drive_time.py`
4. Calendar service — `get_events_for_day()` method
5. Availability service refactor — extract `_build_free_windows()`, add `intersect_windows()`, `trim_windows_for_drive_time()`, `filter_by_advance_notice()`
6. Slots endpoint — integrate both features into `app/routers/slots.py`
7. Admin UI — home address field in settings
8. Admin UI — drive time + calendar window fields on appointment type form

**To execute:** Use `superpowers:executing-plans` skill, follow `docs/plans/2026-02-26-drive-time-and-calendar-windows.md` task by task, TDD (write failing test → implement → pass → commit).

---

## Running Tests

```bash
cd /home/devon/Projects/BookingAssistant
pytest -v
```

All tests should pass before and after each task.

## Running Locally

```bash
cd /home/devon/Projects/BookingAssistant
uvicorn app.main:app --reload --port 8080
```
