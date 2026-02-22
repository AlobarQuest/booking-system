# Personal Booking System — Design Document

**Date:** 2026-02-22
**Status:** Approved

---

## Overview

A personal appointment booking system that allows external users to schedule time via a public web interface, with an admin panel for managing availability, appointment types, and calendar integration.

---

## Stack

| Layer | Choice |
|-------|--------|
| Backend | Python 3.12 + FastAPI |
| Templating | Jinja2 |
| Dynamic UI | HTMX |
| Database | SQLite via SQLAlchemy 2.0 ORM |
| Calendar | Google Calendar API (google-api-python-client) |
| Email | Resend Python SDK |
| Config | pydantic-settings (env vars) |
| Auth | bcrypt password hash + Starlette signed session cookie |
| Hosting | Fly.io (Docker container, persistent volume) |
| DNS/CDN | Cloudflare (CNAME proxy, free SSL) |

---

## Project Structure

```
BookingAssistant/
├── app/
│   ├── main.py                # FastAPI app, router registration
│   ├── database.py            # SQLAlchemy engine + session
│   ├── models.py              # ORM models
│   ├── config.py              # Settings from env vars
│   ├── dependencies.py        # Shared FastAPI deps (db session, auth check)
│   ├── routers/
│   │   ├── booking.py         # Public booking pages
│   │   ├── admin.py           # Admin panel pages
│   │   ├── auth.py            # Login / logout
│   │   └── slots.py           # HTMX partials: slot calculation endpoint
│   ├── services/
│   │   ├── calendar.py        # Google Calendar API wrapper
│   │   ├── availability.py    # Slot calculation logic
│   │   ├── email.py           # Resend email sender + templates
│   │   └── booking.py         # Booking creation and cancellation
│   ├── templates/
│   │   ├── base.html
│   │   ├── booking/
│   │   │   ├── index.html          # Step 1: choose appointment type
│   │   │   ├── slots_partial.html  # HTMX partial: slot buttons
│   │   │   ├── form.html           # Step 2: contact info + custom fields
│   │   │   └── confirmation.html   # HTMX partial: booking confirmed
│   │   └── admin/
│   │       ├── login.html
│   │       ├── setup.html
│   │       ├── dashboard.html
│   │       ├── appointment_types.html
│   │       ├── availability.html
│   │       ├── bookings.html
│   │       └── settings.html
│   └── static/
│       ├── css/style.css
│       └── js/htmx.min.js
├── Dockerfile
├── fly.toml
├── .env.example
├── requirements.txt
└── README.md
```

---

## Data Models

### `appointment_types`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| name | TEXT | e.g. "Property Showing" |
| description | TEXT | |
| duration_minutes | INTEGER | |
| buffer_before_minutes | INTEGER | Default 0 |
| buffer_after_minutes | INTEGER | Default 0 |
| calendar_id | TEXT | Google Calendar ID to write events to |
| custom_fields | JSON | `[{"label": "...", "type": "text", "required": true}]` |
| active | BOOLEAN | Soft delete |
| color | TEXT | Hex color for admin UI |

### `availability_rules`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| day_of_week | INTEGER | 0=Monday … 6=Sunday |
| start_time | TIME | e.g. 09:00 |
| end_time | TIME | e.g. 17:00 |
| active | BOOLEAN | |

### `blocked_periods`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| start_datetime | DATETIME | |
| end_datetime | DATETIME | |
| reason | TEXT | Optional |

### `bookings`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| appointment_type_id | INTEGER FK | |
| start_datetime | DATETIME | |
| end_datetime | DATETIME | |
| guest_name | TEXT | |
| guest_email | TEXT | |
| guest_phone | TEXT | |
| notes | TEXT | |
| custom_field_responses | JSON | `{"Property Address": "123 Main St"}` |
| google_event_id | TEXT | For deletion on cancel |
| status | TEXT | `confirmed` or `cancelled` |
| created_at | DATETIME | |

### `settings` (key/value)
| Key | Description |
|-----|-------------|
| `admin_password_hash` | bcrypt hash |
| `notify_email` | Your alert address |
| `notifications_enabled` | `"true"` / `"false"` |
| `timezone` | `"America/New_York"` |
| `min_advance_hours` | e.g. `"24"` |
| `max_future_days` | e.g. `"30"` |
| `google_refresh_token` | Stored after OAuth |
| `owner_name` | Used in email footers |
| `from_email` | Sender address |

---

## Google Calendar Integration

### OAuth (one-time setup)
1. Admin visits `/admin/google/authorize` → redirected to Google consent screen
2. Google redirects to `/admin/google/callback` with auth code
3. App exchanges code for tokens, stores refresh token in `settings`
4. Subsequent API calls use the refresh token; the client library handles expiry automatically

### Scopes
```
https://www.googleapis.com/auth/calendar.events
https://www.googleapis.com/auth/calendar.freebusy
```

### Reading availability
- Use the freebusy API to query busy intervals across all relevant calendars for a given time range
- Returns only busy intervals (no event titles), preserving privacy
- Results combined with `availability_rules` and `blocked_periods` to compute open slots

### Writing events
On confirmed booking, create a Calendar event on the appointment type's `calendar_id`:
- Title: `[Type Name] — [Guest Name]`
- Description: all guest details + notes + custom field responses
- Attendee: guest email (optional Google invite)
- Duration: appointment start → end (buffers built into the slot calculation, not separate events)

### Cancellation
Admin cancels booking → delete Google event by `google_event_id` → update booking status to `cancelled`.

### Error handling
If Calendar API fails during booking, the booking is still saved to the DB. The admin panel flags bookings with no `google_event_id` for manual follow-up.

---

## Public Booking Flow

Single page at `/book`, three steps driven by HTMX (no full page reloads).

**Step 1 — Select appointment type**
- Cards for each active appointment type
- Selecting one reveals a date input constrained to `[today + min_advance_hours, today + max_future_days]`

**Step 2 — Pick a slot**
- Date selection triggers HTMX GET to `/slots?type_id=X&date=YYYY-MM-DD`
- Server computes available slots:
  1. Find `availability_rules` for that day of week
  2. Subtract `blocked_periods` overlapping that day
  3. Subtract busy intervals from Google Calendar freebusy
  4. Subtract slots that fall within `min_advance_hours` of now
  5. Split remaining windows into `duration + buffer` chunks
- Returns HTML partial of slot buttons (or "no availability" message)
- Clicking a slot sets a 30-second soft-lock (in-memory dict) and reveals the contact form

**Step 3 — Contact form + submission**
- Fields: Name (required), Email (required), Phone, Notes
- Plus any `custom_fields` from the appointment type
- Rate limit: 10 submissions per IP per hour
- On submit:
  1. Check soft-lock still valid (slot not taken by concurrent booking)
  2. Create `booking` record (status: confirmed)
  3. Create Google Calendar event
  4. Send confirmation email to guest
  5. Send alert email to admin
  6. Return confirmation HTML partial

**Race condition handling:**
If two users submit for the same slot simultaneously, the second receives an HTMX-swapped "slot no longer available" message and the slot picker refreshes.

---

## Admin Panel

All routes under `/admin/*` protected by session cookie dependency.

### Authentication
- Single password stored as bcrypt hash in `settings`
- Starlette session middleware with signed cookie (8-hour expiry)
- First-run redirect to `/admin/setup` if no password is set

### Pages

| Route | Purpose |
|-------|---------|
| `/admin/` | Dashboard: upcoming bookings count, next 5 appointments |
| `/admin/appointment-types` | List, create, edit, soft-delete appointment types |
| `/admin/availability` | Weekly rules grid, add/remove blocked periods, advance/window settings |
| `/admin/bookings` | Upcoming + past bookings table, cancel action |
| `/admin/settings` | Notification email, toggle, timezone, Google auth status, change password |
| `/admin/google/authorize` | Initiates Google OAuth |
| `/admin/google/callback` | Receives OAuth code, stores token |

---

## Email Notifications

Sent via Resend SDK. Three email types:

### Guest confirmation (on booking)
- **Subject:** `Your [Type] is confirmed — [Date] at [Time]`
- **Body:** Appointment details, all custom field responses, contact/cancellation instructions

### Admin alert (on booking)
- **Subject:** `New booking: [Guest Name] — [Type] on [Date]`
- **Body:** All guest info, notes, custom field responses, link to admin bookings page

### Guest cancellation (on admin cancel)
- **Subject:** `Your [Type] on [Date] has been cancelled`
- **Body:** Date/time of cancelled appointment, prompt to reschedule

Email templates are HTML strings in `app/services/email.py`. Owner name and from-address come from `settings`.

---

## Deployment

### Fly.io
- Dockerfile: Python 3.12 slim, `uvicorn app.main:app --host 0.0.0.0 --port 8080`
- `fly.toml`: 1 shared CPU, 256MB RAM, persistent volume mounted at `/data`
- SQLite file: `/data/booking.db`
- DB initialized on startup via `Base.metadata.create_all()`
- Health check: `GET /health` → 200

### Cloudflare
- CNAME record: `book.yourdomain.com` → `<appname>.fly.dev` (proxied)
- Free SSL termination, DDoS protection, CDN caching of static assets

### Environment Variables
```
DATABASE_URL=sqlite:////data/booking.db
SECRET_KEY=<random 32-char string>
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://book.yourdomain.com/admin/google/callback
RESEND_API_KEY=...
FROM_EMAIL=noreply@yourdomain.com
```

### Deploy commands
```bash
fly auth login
fly launch          # first-time: creates app + volume
fly secrets set KEY=value ...
fly deploy          # all subsequent deploys
```

---

## Development Phases

| Phase | Scope |
|-------|-------|
| 1 | Project scaffold, DB models, config, health check |
| 2 | Google Calendar OAuth + freebusy integration |
| 3 | Availability calculation logic + slot endpoint |
| 4 | Public booking flow (HTMX steps 1–3) |
| 5 | Admin panel: auth, appointment types, availability, bookings, settings |
| 6 | Email notifications (Resend) |
| 7 | Fly.io Dockerfile + fly.toml, deployment validation |
| 8 | Polish: mobile responsiveness, error states, rate limiting, timezone handling |

---

## Out of Scope

- Multi-user / team support
- Payment processing
- SMS notifications
- Recurring appointments
- Waiting lists
- Calendar widget embedding
