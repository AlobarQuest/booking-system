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
- **Auto-deploy:** Push to `master` → production auto-deploys; push to `preview` → preview auto-deploys
- **Database:** SQLite at `/data/booking.db` inside the container (Coolify volume mount — separate volume per service)
- **Previous host:** Fly.io (decommissioned — left as-is, no active machines)

### Environments

| Environment | Branch | URL | Coolify app ID |
|-------------|--------|-----|----------------|
| Production | `master` | `https://booking.devonwatkins.com` | `hkw488ggssgcskk0ooc0ksk0` |
| Preview | `preview` | `https://preview.booking.devonwatkins.com` | `yscogs0wggcgco8g4wwk0o0g` |

**Branch workflow:** work on `preview` → test → merge `preview` → `master` → production.

**Webhook:** both services share the GitHub webhook at `http://178.156.247.239:8000/webhooks/source/github/events/manual` with secret `Red57Chair!01`. Both Coolify services must have that secret saved under Webhooks → GitHub Webhook Secret.

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

| Variable | Production value | Preview value |
|----------|-----------------|---------------|
| `SECRET_KEY` | *(random)* | *(different random)* |
| `GOOGLE_CLIENT_ID` | same | same |
| `GOOGLE_CLIENT_SECRET` | same | same |
| `OAUTH_REDIRECT_URI` | `https://booking.devonwatkins.com/admin/auth/callback` | `https://preview.booking.devonwatkins.com/admin/auth/callback` |
| `GOOGLE_REDIRECT_URI` | `https://booking.devonwatkins.com/admin/google/callback` | `https://preview.booking.devonwatkins.com/admin/google/callback` |
| `DATABASE_URL` | `sqlite:////data/booking.db` | `sqlite:////data/booking.db` *(separate volume)* |
| `ADMIN_EMAIL` | `devon.watkins@gmail.com` | same |
| `GOOGLE_MAPS_API_KEY` | *(set)* | same |

Both redirect URIs must also be registered in Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 client → Authorized redirect URIs.

---

## What Has Been Built (history)

- **Core app** — public booking UI, admin panel, Google Calendar integration, email via Resend
- **Issue #2** — 12-hour AM/PM time display on slot buttons
- **Issue #3** — Separate owner/guest calendar event titles per appointment type
- **Issue #4** — Multi-calendar conflict checking (webcal/ICS feeds + extra Google Calendars)
- **Issue #5** — Modern public booking UI redesign (Inter font, gradient header, step indicator)
- **Coolify migration** — Migrated from Fly.io to Hetzner + Coolify; HTTPS via Let's Encrypt (Traefik)
- **Issues #6 & #7** — Photo upload per appointment type, listing URL, rental requirements modal, calendar notification toggle (`owner_reminders_enabled`), editable email templates in admin (3 templates with fallback to defaults)
- **Drive time + calendar windows** — `DriveTimeCache` table, Google Maps Distance Matrix integration, calendar-window availability mode (restrict slots to specific calendar event windows)
- **Preview environment** — `preview` branch auto-deploys to `https://preview.booking.devonwatkins.com` with isolated DB; see Environments section above
- **Booking UX** — "Schedule Tour" green button replaces whole-card click; card list collapses and shows selected-type banner with full card content cloned into it; "← Change" to go back
- **Rental Application Link** — `rental_application_url` field on appointment types; "Rental Application" button on booking page opens URL in new tab

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
