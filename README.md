# Booking Assistant

Personal appointment booking system with a public booking interface and admin panel. Integrates with Google Calendar and sends email confirmations via Resend.

**Live:** https://booking.devonwatkins.com

## Features

- Public booking page with HTMX-powered slot picker
- Admin panel: appointment types, availability rules, booking management
- Google Calendar integration (conflict detection + event creation)
- Webcal/ICS feed support for multi-calendar conflict checking
- Drive time buffers via Google Maps Distance Matrix API
- Drive time block events on owner's calendar (automatic "BLOCK" events before/after appointments)
- Calendar-window availability mode (restrict slots to specific calendar events)
- Email notifications via Resend (confirmation to guest, alert to owner)
- Editable email templates in admin panel
- Photo upload, listing URL, and rental requirements per appointment type
- Rental application link per appointment type
- Single-password admin authentication with CSRF protection on all forms
- Rate-limited admin login (5 requests/minute)
- Security headers (X-Frame-Options, X-Content-Type-Options, Referrer-Policy)

## Local Development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your values
uvicorn app.main:app --reload --port 8080
```

Open http://localhost:8080 — first visit to `/admin` redirects to password setup.

## Running Tests

```bash
pytest -v
```

## Deployment

Hosted on a Hetzner CX22 VPS using [Coolify](https://coolify.io) (self-hosted PaaS).

- Push to `master` → auto-deploys to production (`https://booking.devonwatkins.com`)
- Push to `preview` → auto-deploys to preview (`https://preview.booking.devonwatkins.com`)
- HTTPS via Let's Encrypt (Traefik)
- SQLite database persisted at `/data/booking.db` via Docker volume

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Random secret for session signing |
| `DATABASE_URL` | `sqlite:////data/booking.db` |
| `GOOGLE_CLIENT_ID` | Google OAuth2 client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth2 client secret |
| `GOOGLE_REDIRECT_URI` | `https://<host>/admin/google/callback` |
| `RESEND_API_KEY` | Resend API key for email |
| `FROM_EMAIL` | Sender address (e.g. `noreply@devonwatkins.com`) |
| `GOOGLE_MAPS_API_KEY` | *(optional)* For drive time buffer feature |
| `UPLOAD_DIR` | *(optional)* Path for uploaded photos (default: `uploads/`) |

## Google Calendar Setup

1. Go to https://console.cloud.google.com → New project
2. Enable the **Google Calendar API**
3. Create OAuth 2.0 credentials (Web application type)
   - Authorized redirect URI: `https://booking.devonwatkins.com/admin/google/callback`
4. Copy Client ID and Client Secret into your environment variables
5. In admin panel → Settings → Connect Google Calendar

## Resend Email Setup

1. Sign up at https://resend.com (free: 3,000 emails/month)
2. Add and verify your domain
3. Create an API key and set as `RESEND_API_KEY`
4. Set `FROM_EMAIL` to your verified sender address

## First Run

After deploying:
1. Visit `/admin` → set your admin password
2. Settings → Connect Google Calendar
3. Create appointment types (e.g. "Property Showing", "Phone Call")
4. Set availability rules (e.g. Mon–Fri 9am–5pm)
5. Set your notification email
6. Share `/book` with clients
