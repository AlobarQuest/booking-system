# Design: Issues #6 & #7 — Appointment Type Features + Email Templates

## Issues
- **#6** Appointment type additional features (photo, listing link, rental requirements, notification toggle)
- **#7** Email templates editable via admin

---

## Section 1: Data Model

Four new columns on `appointment_types` (added via existing PRAGMA migration pattern in `init_db()`):

| Column | Type | Default | Purpose |
|---|---|---|---|
| `photo_filename` | TEXT | `''` | Stored filename of uploaded photo |
| `listing_url` | TEXT | `''` | Link to public property listing |
| `rental_requirements` | TEXT | `'[]'` | JSON array of requirement strings |
| `owner_reminders_enabled` | BOOLEAN | `0` | Whether owner's calendar event sends reminders |

`rental_requirements` uses a Python property with JSON getter/setter, same as `custom_fields`.

No new tables.

---

## Section 2: Photo Upload & Serving

- Photos stored at `/data/uploads/{type_id}_{uuid}{ext}` (same Docker volume as DB)
- Upload directory created at app startup if absent
- `GET /uploads/{filename}` route serves files via `FileResponse`
- Old photo deleted on replacement or appointment type deletion
- Admin form: `enctype="multipart/form-data"`, file input, thumbnail preview if photo exists, "Remove photo" button
- Upload handled within existing `POST /admin/appointment-types` and `POST /admin/appointment-types/{id}` form submissions
- Booking page: photo rendered at 170px wide inside type card if present

---

## Section 3: Rental Requirements

### Admin UI
- New "Rental Requirements" section on appointment type form
- ~10 predefined checkboxes (credit check, income 3x rent, valid government ID, background check, no smoking, no pets, renter's insurance, first/last/deposit, references required, minimum lease term)
- "Add custom" text input + button appends to the list
- Custom entries shown with a remove (×) button
- Saved as JSON array of strings in `rental_requirements`

### Public Booking Page
- "View Requirements" button on each type card (hidden if no requirements)
- Click opens a modal overlay with requirements as a bulleted list
- Requirements rendered server-side into `data-` attribute or inline `<template>` tag — no extra fetch needed
- Modal uses existing app CSS style (no JS framework)

---

## Section 4: Email Templates

### Storage
Three new `Settings` keys:

| Key | Template |
|---|---|
| `email_guest_confirmation` | Guest booking confirmation HTML |
| `email_guest_cancellation` | Guest cancellation notice HTML |
| `email_admin_alert` | Admin new-booking alert HTML |

Defaults are the current hardcoded HTML in `email.py`. Falls back to hardcoded default if key is absent or empty — no manual setup required on existing deployments.

### Rendering
`email.py` fetches template from DB at send time and calls `template.format(...)`.

### Available Variables

| Template | Variables |
|---|---|
| Guest confirmation | `{guest_name}`, `{appt_type}`, `{date_time}`, `{owner_name}` |
| Guest cancellation | `{guest_name}`, `{appt_type}`, `{date_time}` |
| Admin alert | `{guest_name}`, `{guest_email}`, `{guest_phone}`, `{appt_type}`, `{date_time}`, `{notes}` |

### Admin UI
- New "Email Templates" section on the Settings page
- One `<textarea>` per template with variable reference listed below it
- Single Save button per template (or one Save All)

---

## Section 5: Calendar Notification Settings

### Data
`owner_reminders_enabled` BOOLEAN on `AppointmentType` (default `False` = no reminders).

### Admin UI
- Checkbox on appointment type form: "Send calendar reminders on my event" (default unchecked)

### Implementation
- `CalendarService.create_event()` gets `disable_reminders: bool = False`
- When `True`: event body includes `"reminders": {"useDefault": False, "overrides": []}`
- When `False` (default): reminders key omitted — Google Calendar uses calendar default
- Booking service passes `disable_reminders=not appt_type.owner_reminders_enabled` for owner event only
- Guest event never sets `disable_reminders` — always gets calendar default reminders

---

## Files to Create/Modify

| File | Change |
|---|---|
| `app/models.py` | 4 new columns + `rental_requirements` property |
| `app/database.py` | 4 new PRAGMA migrations + upload dir creation |
| `app/routers/admin.py` | Form params for new fields, photo upload/delete logic, email template save |
| `app/routers/booking.py` | New `GET /uploads/{filename}` route |
| `app/services/email.py` | Fetch templates from DB, `format()` substitution, hardcoded fallbacks |
| `app/services/calendar.py` | `disable_reminders` param on `create_event()` |
| `app/services/booking.py` | Pass `disable_reminders` for owner event |
| `app/templates/admin/appointment_types.html` | Photo upload, listing URL, requirements, notification toggle |
| `app/templates/admin/settings.html` | Email templates section |
| `app/templates/booking/index.html` | Photo display, listing link, requirements button + modal |
