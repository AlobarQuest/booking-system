# Admin Inspection Scheduling — Design Doc

**Date:** 2026-02-28

## Overview

Add an admin-only "Schedule Inspection" flow. While on the phone with a property owner or tenant, the admin enters their address and a date, sees available time slots (with drive time accounted for), picks a slot (or overrides to a specific time), optionally captures guest info, and confirms. A calendar event and drive time block events are created automatically. No email is sent to the guest.

---

## Data Model

### New column: `AppointmentType.admin_initiated` (bool, default False)

Marks an appointment type as admin-initiated. These types:
- Are hidden from the public booking page
- Always compute drive time (implicit — no checkbox needed)
- Never send guest email or guest calendar invite
- Never add calendar event notifications/reminders
- Support per-type availability windows (see below)
- Do not use: location, listing URL, photo, rental application URL, guest event title, calendar window settings, rental requirements, owner reminders

### New column: `Booking.location` (Text, default "")

Stores the per-booking inspection address for admin-initiated bookings. Regular bookings leave this blank.

### New column: `AvailabilityRule.appointment_type_id` (Integer, nullable FK → appointment_types.id)

- `NULL` = global rule (current behavior, applies to all types without type-specific rules)
- Set to a type ID = rule applies only to that appointment type

**Resolution logic:** if a type has any rules with its `appointment_type_id`, those rules are used exclusively when computing slots for that type. Global rules (`appointment_type_id IS NULL`) apply to all other types.

---

## Slot Computation Changes

### `_build_free_windows` (availability.py)

Currently filters rules by `active=True` only. Updated to:
1. Check if any rules exist for the given `appointment_type_id`.
2. If yes, use only those type-specific rules.
3. If no, fall back to global rules (`appointment_type_id IS NULL`).

### `/slots` endpoint (routers/slots.py)

Gains an optional `destination: str = ""` query parameter. When the appointment type is `admin_initiated` and `destination` is provided, it is used as the drive time destination instead of `appt_type.location`. For non-admin-initiated types, `destination` is ignored.

---

## Admin UI

### Appointment Type Form (admin/appointment_types.html)

New "Admin-initiated" checkbox. When checked, the form shows only relevant fields:

**Shown:** Name, Description, Duration, Buffer Before, Buffer After, Calendar ID, Color, Owner Event Title, Admin-initiated checkbox.

**Hidden:** Location, Listing URL, Photo, Rental Application URL, Guest Event Title, Calendar Window settings, Rental Requirements, Owner Reminders toggle.

An "Availability Windows" sub-section appears below the main form for admin-initiated types. Same day-of-week add/edit/delete interface as the global Availability admin page, but scoped to this type (rules stored with `appointment_type_id` set).

### New page: "Schedule Inspection" (admin/schedule_inspection.html)

New top-level nav item. Admin-only.

**Step 1 — Input form:**
- Appointment type dropdown (shows only active `admin_initiated` types)
- Inspection address (text input, required)
- Date picker (required)
- "Find Available Times" button → HTMX loads slot list

**Step 2 — Slot selection:**
- Available time slots rendered same as public booking slots_partial
- Below the slot list: "Use a specific time instead" — reveals a plain time input (HH:MM). Selecting a specific time bypasses all availability checks.

**Step 3 — Confirmation panel (shown after slot or manual time selected):**
- Optional: guest name, guest email, guest phone, notes
- "Confirm Booking" button → POST to new admin booking endpoint

**On success:** flash message, page resets to Step 1.

---

## Booking Creation

New admin route: `POST /admin/schedule-inspection`

Behavior mirrors the existing `submit_booking` flow in `routers/booking.py` with these differences:
- Stores the inspection address in `Booking.location`
- Does not send confirmation email
- Does not create a guest calendar event
- Creates the owner calendar event with no reminders/notifications
- Still calls `_create_drive_time_blocks()` using the inspection address as the destination

---

## Error Handling & Edge Cases

- **Drive time lookup fails:** non-fatal, window not trimmed, slot still shown (same as existing behavior).
- **No slots available:** "No available times" message shown; manual override is still accessible.
- **Address field blank:** client-side `required` validation prevents submission.
- **Calendar API failure:** error message shown, no booking record written.
- **Admin-initiated types on public page:** public `/book` route adds `admin_initiated=False` to its query filter.
- **Force-override time:** fully bypasses availability rules, busy checks, and advance notice. Drive time blocks are still created.
- **Multiple admin-initiated types:** all appear in the dropdown; admin selects the appropriate one.
