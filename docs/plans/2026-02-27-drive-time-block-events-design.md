# Drive Time Block Events — Design

**Date:** 2026-02-27

## Problem

Drive time between appointments is already accounted for in slot availability (slots are hidden when there isn't enough gap to drive). However, the drive time windows are not visible on the owner's Google Calendar, making it hard to see at a glance why gaps exist or to spot cases where back-to-back appointments would require an infeasible drive.

## Solution

When a booking is confirmed, create up to two additional "BLOCK" calendar events on the owner's calendar to represent drive time — one for driving TO the new appointment, and one for driving FROM the new appointment to the next one.

## Conditions

Block events are only created when:
- `appt_type.requires_drive_time` is `True`
- `appt_type.location` is non-empty
- Google Calendar is connected (`google_refresh_token` and `google_client_id` are set)
- Computed drive time > 0

## Block Event Details

### Before block (driving TO the new appointment)

- Fetch events from `appt_type.calendar_id` in the 1-hour window before the appointment start
- Find the most recent event ending within that window
- Compute drive time from that event's location to `appt_type.location`
- Fall back to `home_address` setting if no preceding event has a location
- If drive time > 0, create:
  - **Title:** `BLOCK - Drive Time for {appt_type.name}`
  - **Start:** `appointment_start − drive_minutes` (UTC)
  - **End:** `appointment_start` (UTC)
  - **Calendar:** `appt_type.calendar_id`
  - Show as busy, no reminders, no attendees

### After block (driving FROM the new appointment to the next one)

- Fetch events from `appt_type.calendar_id` in the 1-hour window after the appointment end
- Find the earliest event starting within that window
- Compute drive time from `appt_type.location` to that event's location
- Skip if the next event has no location
- If drive time > 0, create:
  - **Title:** `BLOCK - Drive Time for {next_event_summary}`
  - **Start:** `appointment_end` (UTC)
  - **End:** `appointment_end + drive_minutes` (UTC)
  - **Calendar:** `appt_type.calendar_id`
  - Show as busy, no reminders, no attendees

## Implementation Location

New helper function `_create_drive_time_blocks()` in `app/routers/booking.py`, called inside `submit_booking()` immediately after the existing `cal.create_event()` call. Failures are non-fatal (wrapped in try/except, same pattern as existing event creation).

## CalendarService

Uses the existing `cal.create_event()` with:
- No `attendee_email`
- No `location`
- `show_as="busy"`
- `disable_reminders=True`

No changes needed to `CalendarService` itself.

## Error Handling

All block event creation is non-fatal. If the calendar fetch or event creation fails, the booking is still saved and the main appointment event is unaffected.
