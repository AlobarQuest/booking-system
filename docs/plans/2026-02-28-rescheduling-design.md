# Rescheduling Design

**Goal:** Allow guests to reschedule their own appointment via a secure email link, and allow the admin to reschedule any upcoming appointment from the admin panel.

**Architecture:** A UUID token stored on each booking authenticates guest reschedule requests without login. Admin reschedules use existing session auth. Both paths share the same reschedule operation: create new calendar event first, then delete old one, then update the booking record and send a new confirmation email.

**Tech Stack:** FastAPI, SQLAlchemy, Jinja2, HTMX (slot loading reused from existing booking flow), Google Calendar API, Resend email.

---

## Section 1: Schema & Token

One new column on `Booking`:

- `reschedule_token: str` — UUID4, generated at booking creation, unique, indexed.

Migration via the existing PRAGMA loop in `app/database.py:init_db()`. Existing rows with a NULL/empty token get a UUID generated during migration.

The guest confirmation email gets one line added:

> "Need to reschedule? [Click here to pick a new time →](https://booking.devonwatkins.com/reschedule/{token})"

---

## Section 2: Guest Reschedule Page

**`GET /reschedule/{token}`** — public, no login required.

- Looks up booking by token. Returns 404 if not found or cancelled.
- If the appointment start is within `min_advance_hours` of now, shows a "too close to reschedule" message instead of the picker.
- Otherwise: renders a page showing the current booking summary (type name, current date/time) + the same date picker and HTMX slot loader as the public booking page. Appointment type is known from the booking — no type selection shown.
- Reuses existing `GET /slots?type_id=...&date=...` endpoint unchanged.

**`POST /reschedule/{token}`** — public, validates token.

Operation order (guards booking integrity):
1. Re-validate token and advance-notice cutoff.
2. Create new Google Calendar event at the new time. If this fails, return an error — booking is unchanged.
3. Delete old Google Calendar event (non-fatal if it fails — new event already exists).
4. Update `booking.start_datetime`, `booking.end_datetime`, `booking.google_event_id`.
5. Send new guest confirmation email via `send_guest_confirmation`.
6. Redirect to a success page.

---

## Section 3: Admin Reschedule

**`GET /admin/bookings/{id}/reschedule`** — admin auth required.

- Same slot-picker UI as the guest page (shared template or near-identical).
- No token needed — admin session provides auth.

**`POST /admin/bookings/{id}/reschedule`** — admin auth required.

Same operation as guest reschedule (create new event → delete old → update booking → send guest confirmation email). On completion: flash success message, redirect to `/admin/bookings`.

Admin bookings table: "Reschedule" button added next to "Cancel" for each upcoming booking.

---

## Security

- Token is UUID4 (122 bits entropy) — unguessable by brute force.
- Token is permanent for the life of the booking (no expiry needed; the appointment date itself is the natural deadline).
- Guest cannot reschedule within `min_advance_hours` of the appointment (same constraint as new bookings).
- Admin bypass: admin can reschedule any upcoming booking regardless of advance notice (they may need to handle last-minute changes).
- CSRF token required on all POST forms.

---

## New `CalendarService.delete_event()`

A new method on `CalendarService` that calls the Google Calendar Events: delete API. Failure is caught and treated as non-fatal after a new event has already been created.

---

## Email

- Existing `send_guest_confirmation` reused for the post-reschedule confirmation (same template, new date/time).
- Confirmation email template updated to include the reschedule link: `https://booking.devonwatkins.com/reschedule/{token}`.
- The editable email template in admin settings will include `{reschedule_url}` as a new placeholder.
