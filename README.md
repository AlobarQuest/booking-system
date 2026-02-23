# Booking Assistant

Personal appointment booking system with a public booking interface and admin panel. Integrates with Google Calendar and sends email confirmations via Resend.

## Features

- Public booking page with HTMX-powered slot picker
- Admin panel: appointment types, availability rules, booking management
- Google Calendar integration (conflict detection + event creation)
- Email notifications via Resend (confirmation to guest, alert to you)
- Single-password admin authentication

## Local Development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your values
uvicorn app.main:app --reload
```

Open http://localhost:8000 — first visit to `/admin` redirects to password setup.

## Google Calendar Setup

1. Go to https://console.cloud.google.com → New project
2. Enable the **Google Calendar API**
3. Create OAuth 2.0 credentials (Web application type)
   - Authorized redirect URI: `https://appointment.devonwatkins.com/admin/google/callback`
4. Download the JSON credentials file
5. Copy Client ID and Client Secret into your `.env`
6. In admin panel → Settings → Connect Google Calendar

## Resend Email Setup

1. Sign up at https://resend.com (free: 3,000 emails/month)
2. Add and verify `devonwatkins.com`
3. Create an API key and add to `.env` as `RESEND_API_KEY`
4. Set `FROM_EMAIL=noreply@devonwatkins.com`

## Fly.io Deployment

```bash
# Install Fly CLI: https://fly.io/docs/hands-on/install-flyctl/
fly auth login
fly launch              # creates app and prompts for config
fly volumes create booking_data --size 1
fly secrets set \
  DATABASE_URL=sqlite:////data/booking.db \
  SECRET_KEY=$(openssl rand -hex 32) \
  GOOGLE_CLIENT_ID=your-client-id \
  GOOGLE_CLIENT_SECRET=your-client-secret \
  GOOGLE_REDIRECT_URI=https://appointment.devonwatkins.com/admin/google/callback \
  RESEND_API_KEY=re_xxxx \
  FROM_EMAIL=noreply@devonwatkins.com
fly deploy
```

## Cloudflare DNS

In the Cloudflare dashboard for `devonwatkins.com`:
- Add a CNAME record: `appointment` → `booking-assistant.fly.dev` (Proxy ON)

## Running Tests

```bash
pytest tests/ -v
```

## First Run

After deploying:
1. Visit `https://appointment.devonwatkins.com/admin` → set your admin password
2. Settings → Connect Google Calendar
3. Create appointment types (e.g. "Property Showing", "Phone Call")
4. Set availability rules (e.g. Mon–Fri 9am–5pm)
5. Set your notification email
6. Share `https://appointment.devonwatkins.com/book` with clients
