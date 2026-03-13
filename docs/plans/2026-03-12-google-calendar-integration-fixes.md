# Google Calendar Integration Fixes

**Date:** 2026-03-12
**Status:** Deployed to production

---

## Overview

Three Google Calendar issues were fixed on March 12, 2026:

1. Google reconnect could fail with `invalid_grant: Missing code verifier`.
2. Calendar-window mode could incorrectly fall back to normal availability or ignore matching all-day events.
3. Timed Google events returned with explicit UTC offsets could be shifted to the wrong local hour during slot calculation.

These fixes were deployed as:

- `c988a53` — persist Google OAuth PKCE verifier
- `1677841` — tighten calendar-window availability
- `ea88d4d` — normalize Google event offsets to UTC

---

## Fix 1: OAuth Reconnect

**Symptom:** Re-authorizing Google from `/admin/settings` failed with `Google Calendar connection failed: (invalid_grant) Missing code verifier`.

**Root cause:** The OAuth flow generated a PKCE code verifier during `/admin/google/authorize`, but the callback did not persist and reuse that verifier during token exchange.

**Fix:** Store both `oauth_state` and `oauth_code_verifier` in session during `/admin/google/authorize`, then pass the stored verifier into `exchange_code()` during `/admin/google/callback`.

**Operational note:** A saved refresh token is not a live health check by itself. If Google conflicts stop affecting slots, re-authorize Google from the admin settings page.

---

## Fix 2: Calendar-Window Behavior

**Symptom:** Appointment types using `Only allow bookings during specific Google calendar events` could still show ordinary availability when no matching event existed, or fail to treat matching all-day events as booking windows.

**Fixes:**

- Calendar-window mode now fails closed. If the feature is enabled and no matching title is found for the day, the day returns no slots.
- Matching all-day Google events are treated as valid booking windows for the requested date.
- Matching is still exact-title and case-insensitive on the configured Google calendar.

**Expected behavior:** A matching window event becomes allowed time. A non-matching day becomes unavailable.

---

## Fix 3: Timezone Normalization

**Symptom:** A Google event such as `2026-03-17T13:00:00-04:00` to `2026-03-17T17:00:00-04:00` could appear as a `9:00 AM` to `1:00 PM` window in `America/New_York` instead of `1:00 PM` to `5:00 PM`.

**Root cause:** The app parsed Google `events.list()` timestamps and then stripped timezone information before converting them, effectively treating offset-aware local times as naive UTC.

**Fix:** Convert offset-aware Google event timestamps to UTC first, then drop timezone info for the app's naive-UTC internal representation.

**Expected behavior:** Timed window events stay aligned to the correct local hour, regardless of whether Google returns `Z` or an explicit offset such as `-04:00`.

---

## Verification Checklist

- Re-authorize Google successfully from `/admin/settings`.
- Confirm normal availability hides slots that overlap existing busy events.
- Confirm calendar-window appointment types show slots only inside matching title windows.
- Confirm days without a matching calendar window event return zero slots.
- Confirm timed afternoon windows stay in the afternoon after timezone conversion.
