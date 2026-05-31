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

**Branch workflow:** create feature branch/worktree from `master` → implement → push branch to GitHub → open PR → CI must pass (`test` check) → merge via GitHub UI → Coolify auto-deploys from `master` → reset `preview` to `master` (`git push origin master:preview --force`).

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
| `app/dependencies.py` | `get_setting()`, `set_setting()`, `require_admin()`, `get_csrf_token()`, `validate_csrf_token()`, `require_csrf()` |
| `app/routers/slots.py` | GET /slots — computes available time slots |
| `app/routers/booking.py` | GET/POST /book — public booking flow |
| `app/routers/admin.py` | All /admin/* routes |
| `app/services/availability.py` | `compute_slots()`, `_build_free_windows()`, `intersect_windows()`, `trim_windows_for_drive_time()`, `filter_by_advance_notice()` |
| `app/services/calendar.py` | `CalendarService` — Google OAuth PKCE flow, Google Calendar API wrapper, event normalization helpers, `fetch_webcal_busy()` |
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

Google requires an exact host/path match for OAuth redirect URIs. `https://preview.booking.devonwatkins.com/admin/google/callback` must be registered exactly for preview Google reconnect to work.

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
- **Security remediation** — CSRF tokens on all POST forms (`require_csrf` dependency + session-backed `_csrf` hidden field); rate-limited admin login/setup (5/min); security response headers (X-Frame-Options, X-Content-Type-Options, Referrer-Policy); HTML-escaped guest data in emails; `javascript:` / non-HTTP URL scheme rejection on listing/rental URLs; OAuth state parameter validation
- **Alobar ID SSO (May 2026)** — Admin auth migrated from bcrypt password (stored in Settings DB) to Alobar ID OIDC via authlib. Login: `GET /login` → Authentik → `GET /auth/callback` sets `session["user_sub"]`. Logout: `GET /admin/logout` → Authentik end-session. Authentik app slug: `booking-assistant`. OIDC env vars: `ALOBAR_ID_CLIENT_ID`, `ALOBAR_ID_CLIENT_SECRET`, `ALOBAR_ID_ISSUER` (set in Coolify + BWS Shared Infrastructure project).
- **Mobile booking UI** — responsive card layout: photo stacks above text on mobile (`flex-direction: column-reverse`); reduced header padding; full-width date picker on mobile
- **Drive time block events** — when a booking is confirmed for an appointment type with `requires_drive_time=True`, creates "BLOCK - Drive Time for …" calendar events on the owner's calendar: one before the appointment (from preceding event location or `home_address` setting) and one after (to the next event's location), each within a ±1-hour window; implemented in `_create_drive_time_blocks()` in `app/routers/booking.py`
- **Google Calendar reliability fixes (March 12, 2026)** — reconnect now persists the OAuth PKCE code verifier across `/admin/google/authorize` → `/admin/google/callback`; calendar-window mode now fails closed when enabled but no matching events exist, supports matching all-day events, and normalizes offset-aware `events.list()` timestamps to UTC before local conversion so local booking windows do not shift earlier or later

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
