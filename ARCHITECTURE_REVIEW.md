# Architecture Review — BookingAssistant

*Review date: June 2026. Written as a deep-dive onboarding review: architecture reconstruction, data-flow analysis, problem inventory, and the refactoring strategy applied in the accompanying change set.*

---

## 1. System overview

A single-tenant appointment booking system. One owner, many guests. FastAPI monolith, server-rendered Jinja2 templates with HTMX for partial updates, SQLite for persistence, Google Calendar as the availability/event backend, Resend for transactional email, Google Maps Distance Matrix for drive-time buffers, Alobar ID (Authentik OIDC) for admin auth.

```
                        ┌──────────────────────────────────────────────┐
                        │                  FastAPI app                 │
  Guest (HTMX) ───────▶ │  routers/booking  routers/slots  (public)    │
  Owner (browser) ────▶ │  routers/admin    routers/auth   (admin)     │
                        │        │                                     │
                        │        ▼                                     │
                        │  services/  slots · scheduling · booking ·   │
                        │             availability · calendar ·        │
                        │             drive_time · email · timeutils   │
                        │        │                                     │
                        │        ▼                                     │
                        │  models + database (SQLite, manual schema)   │
                        └───────┬──────────┬──────────┬────────────────┘
                                ▼          ▼          ▼
                        Google Calendar  Resend   Google Maps
                        (freebusy/events) (email)  (Distance Matrix)
```

### Layers (post-refactor)

| Layer | Modules | Responsibility |
|---|---|---|
| HTTP | `app/routers/*` | Parse/validate requests, call services, render templates. No business logic. |
| Domain services | `app/services/slots.py`, `scheduling.py`, `booking.py`, `availability.py` | Slot computation, booking lifecycle (create/cancel/reschedule), drive-time blocks. |
| Integration services | `app/services/calendar.py`, `drive_time.py`, `email.py` | Wrap Google Calendar, Maps, Resend. Normalize external data to naive-UTC dicts/tuples. |
| Cross-cutting | `app/dependencies.py`, `services/timeutils.py`, `templating.py`, `limiter.py`, `config.py` | Settings access, CSRF, auth guard, timezone conversion, shared Jinja env, rate limiting. |
| Persistence | `app/models.py`, `app/database.py` | SQLAlchemy models, engine, manual migrations in `init_db()`. |

### Datetime convention (load-bearing, previously implicit)

The whole system runs on **naive datetimes in two frames**:

- **Naive local** (owner's configured timezone, a DB setting): bookings, availability rules, blocked periods, everything shown to users.
- **Naive UTC**: everything crossing the Google Calendar / webcal boundary.

`services/timeutils.py` is now the single conversion point (`utc_to_local`, `local_to_utc`, `local_day_bounds_utc`, `get_timezone`). Before the refactor this conversion idiom (`.replace(tzinfo=...).astimezone(...).replace(tzinfo=None)`) was hand-rolled in 12+ places across three routers — the highest-risk duplication in the codebase, because a one-sided mistake silently shifts every slot by the UTC offset (a bug of exactly this shape was fixed in March 2026 per the project history).

---

## 2. Complete data flows

### Public booking (the critical path)

1. `GET /` → active, non-admin appointment types → booking page.
2. Guest picks type + date → HTMX `GET /slots?type_id&date` → `services.slots.compute_slots_for_type()`:
   - Load availability rules (type-specific rules override global ones), blocked periods, settings.
   - Compute the local day's UTC bounds; fetch Google freebusy for the type's calendar plus configured conflict calendars; fetch webcal/ICS feeds; all converted to naive local.
   - *Calendar-window mode*: only events exactly matching a configured title open windows; everything else on that calendar is busy; fails closed if no match.
   - Subtract blocked periods and busy intervals from rule windows; intersect with calendar windows; trim window starts by drive time from the preceding event's location (Maps API, 30-day DB cache).
   - Split windows into 15-minute-aligned start times; apply advance-notice cutoff.
   - *Group showings* (`max_concurrent > 1`): re-inject already-booked start times (Google freebusy merges adjacent events, so they can't be un-blocked interval-wise), then filter by overlap count.
3. Guest submits → `POST /book` (rate-limited, CSRF-checked): re-validate, count overlapping confirmed bookings against `max_concurrent` (race-condition guard), insert `Booking` with a UUID reschedule token.
4. Post-commit, best-effort side effects: owner calendar event → drive-time BLOCK events before/after (IDs stored on the booking for later cleanup) → guest confirmation + admin alert emails (templates editable in admin, fall back to trusted defaults on bad placeholders).

### Reschedule / cancel

Token-addressed (`/reschedule/{token}`, `/cancel/{token}`), no login. `services.scheduling.perform_reschedule()` ordering guards integrity: create new calendar event (fatal on failure — booking unchanged) → delete old event (non-fatal) → update DB → email (non-fatal). Cancel: delete event + drive-time blocks (non-fatal) → email → mark cancelled. Admin reschedule/cancel reuse the same service functions; admin inspection booking uses the narrower `compute_inspection_slots()` (own calendar only, ad-hoc destination for drive time).

### Auth

Admin: OIDC via Authentik (`/login` → callback sets `session["user_sub"]`); `require_admin` raises → exception handler redirects to `/login`. Guests: unauthenticated; capability URLs via reschedule tokens.

---

## 3. Problem inventory

### 3.1 Bad architecture decisions

| # | Finding | Severity | Status |
|---|---|---|---|
| A1 | **Business logic lived in routers and was imported across routers as private functions.** `app.routers.admin` imported `_compute_slots_for_type` from `app.routers.slots` and `_delete_drive_time_events`/`_perform_reschedule` from `app.routers.booking`. Routers were the de-facto domain layer; "private" helpers were public API. | High | **Fixed** — extracted to `services/slots.py` and `services/scheduling.py`; routers are thin HTTP adapters. |
| A2 | **~25 bare `except Exception: pass` blocks and zero logging in the entire application.** "Non-fatal" is the right *policy* for calendar/email side effects, but with no logs, a dead refresh token or Resend outage is indistinguishable from success. The booking confirms; the owner's calendar silently stops updating. | High | **Fixed** — every swallow site now logs a warning with traceback; failure policy unchanged. |
| A3 | **Settings split across three stores with per-key queries.** Env vars (`config.py`), DB `Setting` key-value rows, and some keys (`resend_api_key`, `from_email`, `timezone`) in *both* with fallback. A booking request issues ~10 separate `Setting` queries. | Medium | Partially addressed (`get_email_config`, `get_conflict_calendars`, `get_timezone` consolidate the multi-key reads). Roadmap: one `load_settings(db)` read per request. |
| A4 | **Manual schema migrations** via PRAGMA loops in `init_db()`. Works, but column definitions are duplicated between `models.py` and `database.py`, and there's no down-migration or history. | Medium | Accepted for now (house pattern, documented in CLAUDE.md). Roadmap: Alembic. |
| A5 | **Test suite mocks router-module internals** (`patch("app.routers.slots.datetime")`, `_build_free_windows`, …) rather than integration boundaries, so any module reorganization breaks tests that still pass functionally. | Medium | Patch targets updated to the new layout; structural coupling remains. Roadmap: fake `CalendarService` fixture + clock injection. |
| A6 | Per-router `Jinja2Templates` instances with divergent filter/global registration (admin had `enumerate`, booking had `csrf_token`, slots had neither). | Low | **Fixed** — single shared env in `app/templating.py`. |

### 3.2 Duplicate logic (found → resolved)

| Duplication | Was | Now |
|---|---|---|
| Admin `inspection_slots` re-implemented ~80 lines of the slot engine (day bounds, freebusy, localization, drive-time trim, advance notice) | 2 copies | `compute_inspection_slots()` built from the same building blocks; the *intentional* differences (no conflict calendars/webcal/window/group capacity) are now documented in its docstring instead of being implicit in a fork |
| Naive UTC↔local conversion idiom | 12+ inline copies | `services/timeutils.py` |
| `CalendarService(settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri)` 5-line construction | 8 copies | `build_calendar_service(settings)` |
| Email template render + fallback try/except with identical kwargs | 3×2 copies | `_render()` + `_send()` in `services/email.py` |
| `conflict_calendars` JSON parse with error swallow | 3 copies | `get_conflict_calendars()` / `set_conflict_calendars()` |
| Notification config triple-read (`notifications_enabled`, `resend_api_key`, `from_email`) | 4 copies | `get_email_config()` → `EmailConfig` named tuple |
| Booking cancel calendar cleanup (event + drive-time blocks) in public and admin routes | 2 copies | `delete_booking_calendar_events()` |
| Token-error page rendering, booking-by-token lookup, reschedule form re-render contexts in `routers/booking.py` | 4–5 copies | `_token_error()`, `_booking_by_token()`, `_reschedule_form()` |
| Photo save/delete file handling in create + update appointment type | 2 copies | `_save_photo()` / `_delete_photo()` |

### 3.3 Performance bottlenecks

| # | Finding | Status |
|---|---|---|
| P1 | **Webcal feeds fetched synchronously on every `/slots` request, per feed, with no caching** (10 s timeout each). Google freebusy + events calls likewise sequential. A guest clicking through dates pays the full network cost every click. This is the dominant latency term. | **Fixed** (follow-up PR) — `services/cache.py` TTL cache (default 45 s, `SLOTS_CACHE_TTL_SECONDS`, 0 disables) over freebusy/events/webcal lookups, cleared on every booking mutation. |
| P2 | No indexes on `bookings` despite every hot query filtering on `status` + `start_datetime` (+ `appointment_type_id` for conflict/capacity checks). Full table scans on the booking race-condition guard. | **Fixed** — composite indexes `(status, start_datetime)` and `(appointment_type_id, status, start_datetime)`, created idempotently for existing DBs. |
| P3 | `bookings_page` does an O(n²) pairwise overlap scan to badge group showings. Bounded today (upcoming + 50 past) — fine at this scale; a sort-and-sweep per type is the fix if listings grow. | Accepted (documented). |
| P4 | Slot computation loads *all* `BlockedPeriod` rows and all active rules per request rather than date-filtered. Negligible now; trivially filterable later. | Accepted. |

### 3.4 Scalability risks

- **S1 — SQLite + single process** is the architecture's stated scope (single owner, Coolify volume). The real risk isn't throughput, it's the **booking race**: the `max_concurrent` overlap check and the insert are not atomic. Two simultaneous guests can both pass the count. SQLite's write serialization makes the window tiny, but moving to Postgres/multi-worker without adding a transactional guard (`SELECT … FOR UPDATE` or a unique constraint on `(appointment_type_id, start_datetime)` for `max_concurrent=1` types) would widen it.
- **S2 — Blocking I/O in request handlers.** Acceptable under FastAPI's threadpool for sync routes at this traffic; the latency cost (P1) bites before the concurrency cost does.
- **S3 — Filesystem session/upload coupling**: uploads on a container volume, sessions in signed cookies — both fine single-node, both assumptions to revisit if ever multi-node.

### 3.5 Maintainability issues

- **M1 — Silent failure** (A2): fixed; this was the most dangerous one operationally.
- **M2 — 950-line admin router** mixing nine concerns. Reduced and de-duplicated in place; splitting into `admin/` sub-routers is the next step if it keeps growing.
- **M3 — Stringly-typed booleans** from HTML forms (`requires_drive_time == "true"`), JSON-in-TEXT columns with property wrappers (`custom_fields`, `rental_requirements`). Idiomatic enough for the stack; documented so it isn't "fixed" into a behavior change casually.
- **M4 — README drift**: README still describes "single-password admin authentication" although auth is OIDC since May 2026.

---

## 4. What this change set did (behavior-preserving)

1. **Service-layer extraction** — `services/slots.py` (slot engine: `compute_slots_for_type`, `compute_inspection_slots`), `services/scheduling.py` (`perform_reschedule`, `create_drive_time_blocks`, `delete_drive_time_events`, `delete_booking_calendar_events`). Routers now contain HTTP concerns only; no router imports another router.
2. **Single-point utilities** — `services/timeutils.py` (timezone frames), `build_calendar_service()` (client construction), `app/templating.py` (shared Jinja env), `get_conflict_calendars` / `get_email_config` (settings access).
3. **Observability** — module loggers everywhere side effects can fail; every previously-silent `except` now records a warning with traceback. Failure *policy* (non-fatal best-effort) is unchanged.
4. **Email service dedup** — one render-with-fallback helper, one send helper.
5. **Indexes** — composite booking indexes in both the ORM metadata (fresh DBs) and `init_db()` (existing DBs), following the project's idempotent-migration pattern.
6. **Tests** — all patch targets updated to the new module layout; assertions untouched. Suite: 163 passed, 17 skipped — identical to the pre-refactor baseline.

Deliberately **not** done, to honor "do not change functionality": busy-interval caching (changes freshness semantics), async I/O conversion, transactional booking guard (changes conflict behavior under race), Alembic migration (deployment-affecting), README fixes beyond scope, and the O(n²) admin badge scan (bounded, correct).

## 5. Refactoring roadmap (recommended order)

1. ~~**Short-TTL cache for busy intervals + webcal feeds** (P1)~~ — done in the follow-up PR (`services/cache.py`).
2. **Transactional booking guard** (S1) — wrap overlap-check + insert in `BEGIN IMMEDIATE` (SQLite) so the race window closes; prerequisite for any move off SQLite.
3. **Single settings load per request** (A3) — one query, typed accessor object; removes ~10 queries/request and the env/DB fallback ambiguity.
4. **Test seams** (A5) — inject a clock and a calendar gateway fixture instead of `patch(...datetime)`; makes future moves free.
5. **Alembic** (A4) — retire the PRAGMA loops; the current pattern is one `ALTER`-typo away from a broken prod boot.
6. **Split `routers/admin.py`** (M2) into `admin/{appointment_types,availability,bookings,settings,google,inspection}.py` when it next grows.
