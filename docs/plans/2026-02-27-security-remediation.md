# Security Remediation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Address all Critical and Important security findings from the 2026-02-27 security audit, plus close the GitHub CodeQL false-positive alert.

**Architecture:** Layered fixes — infrastructure helpers first (CSRF tokens, rate limits, headers), then data escaping (email, URLs), then OAuth state hardening. CSRF is the biggest change; it threads through `dependencies.py`, Jinja2 globals, every POST form in templates, and every POST handler.

**Tech Stack:** FastAPI, Starlette SessionMiddleware, Jinja2, slowapi, Python `secrets`/`hmac`/`html` stdlib

---

## Finding Reference

| ID | Severity | Description |
|----|----------|-------------|
| CSRF-1 | Critical | No CSRF protection on any POST endpoint |
| CSRF-2 | Critical | Admin login has no rate limit |
| OAUTH-1 | Important | OAuth state parameter not validated |
| XSS-1 | Important | `listing_url`/`rental_application_url` accept `javascript:` URIs |
| EMAIL-2 | Important | Guest-supplied data inserted unescaped into HTML email body |
| XSS-2 | Important | `\|safe` filter on date in bookings template |
| MISC-1 | Important | No security response headers |
| CodeQL | Info | False positive in `tests/test_calendar.py:16` |

---

### Task 1: Fix CodeQL False Positive

**Files:**
- Modify: `tests/test_calendar.py:16`

**Step 1: Edit the assertion**

Change line 16 in `tests/test_calendar.py` from:
```python
assert url.startswith("https://accounts.google.com")
```
to:
```python
assert url == "https://accounts.google.com/o/oauth2/auth?..."
```

**Step 2: Run the test to confirm it still passes**

```bash
pytest tests/test_calendar.py::test_get_auth_url_returns_google_url -v
```
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_calendar.py
git commit -m "test: tighten OAuth URL assertion to fix CodeQL false positive"
```

---

### Task 2: Add Security Response Headers (MISC-1)

**Files:**
- Modify: `app/main.py`

**Step 1: Write a failing test**

Add to `tests/test_health.py`:
```python
def test_security_headers_present(client):
    resp = client.get("/health")
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_health.py::test_security_headers_present -v
```
Expected: FAIL — headers not present

**Step 3: Add middleware to `app/main.py`**

After the existing middleware line (line 30), add:
```python
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/test_health.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add app/main.py tests/test_health.py
git commit -m "security: add X-Frame-Options, X-Content-Type-Options, Referrer-Policy headers"
```

---

### Task 3: Rate-Limit Admin Login (CSRF-2)

**Files:**
- Modify: `app/routers/auth.py`

The `limiter` object already exists at `app/limiter.py` and is imported in `booking.py`. Auth router needs the same treatment.

**Step 1: Add rate limit import and decorator to auth.py**

In `app/routers/auth.py`, add the import after the existing imports:
```python
from app.limiter import limiter
```

Then add the decorator to `POST /admin/login` (currently line 19):
```python
@router.post("/admin/login")
@limiter.limit("5/minute")
def login(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
```

And to `POST /admin/setup` (currently line 51):
```python
@router.post("/admin/setup")
@limiter.limit("5/minute")
def setup(
    request: Request,
    ...
```

**Step 2: Run existing tests**

```bash
pytest tests/test_admin_auth.py -v
```
Expected: all PASS (rate limit is not triggered by a small number of test requests)

**Step 3: Commit**

```bash
git add app/routers/auth.py
git commit -m "security: rate-limit admin login and setup to 5 requests/minute"
```

---

### Task 4: Remove `|safe` from Bookings Date Cell (XSS-2)

**Files:**
- Modify: `app/templates/admin/bookings.html:15`

The `|safe` filter is used only to render a `<br>` between date and time. Replace it with two separate `<div>` elements.

**Step 1: Edit `app/templates/admin/bookings.html` line 15**

Change:
```html
<td style="white-space:nowrap;">{{ b.start_datetime.strftime("%b %-d, %Y<br>%-I:%M %p")|safe }}</td>
```
To:
```html
<td style="white-space:nowrap;">
  <div>{{ b.start_datetime.strftime("%b %-d, %Y") }}</div>
  <div>{{ b.start_datetime.strftime("%-I:%M %p") }}</div>
</td>
```
Make this change in **both** the "Upcoming" and "Past" tables in the template (the same pattern appears twice).

**Step 2: Run all tests**

```bash
pytest -v
```
Expected: all PASS

**Step 3: Commit**

```bash
git add app/templates/admin/bookings.html
git commit -m "security: remove |safe filter from date cell, use two div elements instead"
```

---

### Task 5: HTML-Escape Guest Data in Emails (EMAIL-2)

**Files:**
- Modify: `app/services/email.py`
- Modify: `tests/test_email.py`

**Step 1: Write failing tests**

Add to `tests/test_email.py`:
```python
def test_guest_confirmation_escapes_xss_in_custom_fields():
    from unittest.mock import patch
    from datetime import datetime
    from app.services.email import send_guest_confirmation
    sent = {}
    def fake_send(payload):
        sent["html"] = payload["html"]
    with patch("resend.Emails.send", side_effect=fake_send):
        send_guest_confirmation(
            api_key="x", from_email="f@x.com", guest_email="g@x.com",
            guest_name="Alice", appt_type_name="Tour",
            start_dt=datetime(2025, 3, 3, 10, 0),
            end_dt=datetime(2025, 3, 3, 11, 0),
            custom_responses={"Field": "<script>alert(1)</script>"},
            owner_name="Owner",
        )
    assert "<script>" not in sent["html"]
    assert "&lt;script&gt;" in sent["html"]


def test_admin_alert_escapes_xss_in_guest_data():
    from unittest.mock import patch
    from datetime import datetime
    from app.services.email import send_admin_alert
    sent = {}
    def fake_send(payload):
        sent["html"] = payload["html"]
    with patch("resend.Emails.send", side_effect=fake_send):
        send_admin_alert(
            api_key="x", from_email="f@x.com", notify_email="a@x.com",
            guest_name='<img src=x onerror=alert(1)>',
            guest_email="g@x.com", guest_phone="",
            appt_type_name="Tour",
            start_dt=datetime(2025, 3, 3, 10, 0),
            notes="<b>bad</b>",
            custom_responses={"Q": "<script>"},
        )
    assert "<img" not in sent["html"]
    assert "&lt;img" in sent["html"]
    assert "<b>bad</b>" not in sent["html"]
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_email.py::test_guest_confirmation_escapes_xss_in_custom_fields tests/test_email.py::test_admin_alert_escapes_xss_in_guest_data -v
```
Expected: FAIL

**Step 3: Fix `app/services/email.py`**

At the top of `email.py`, add to the imports:
```python
from html import escape
```

In `send_guest_confirmation`, change the `custom_html` construction (lines 50–53):
```python
custom_html = "".join(
    f"<p><strong>{escape(str(k))}:</strong> {escape(str(v))}</p>"
    for k, v in custom_responses.items() if v
)
```

In `send_admin_alert`, change the `custom_html` construction (lines 92–95):
```python
custom_html = "".join(
    f"<p><strong>{escape(str(k))}:</strong> {escape(str(v))}</p>"
    for k, v in custom_responses.items() if v
)
```

Also in `send_admin_alert`, the guest fields are inserted into the HTML template via `str.format()`. Since `str.format()` inserts values verbatim, we need to escape all guest-supplied strings **before** they reach the format call. Change the `html = (template or _ADMIN_ALERT_DEFAULT).format(...)` block (lines 97–105) to escape the guest values:

```python
    try:
        html = (template or _ADMIN_ALERT_DEFAULT).format(
            guest_name=escape(guest_name),
            guest_email=escape(guest_email),
            guest_phone=escape(guest_phone or "not provided"),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
            notes=escape(notes or "none"),
            custom_fields=custom_html,
        )
    except (KeyError, ValueError, IndexError):
        html = _ADMIN_ALERT_DEFAULT.format(
            guest_name=escape(guest_name),
            guest_email=escape(guest_email),
            guest_phone=escape(guest_phone or "not provided"),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
            notes=escape(notes or "none"),
            custom_fields=custom_html,
        )
```

Similarly, escape `guest_name` in `send_guest_confirmation`'s format call:
```python
    try:
        html = (template or _GUEST_CONFIRMATION_DEFAULT).format(
            guest_name=escape(guest_name),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
            owner_name=escape(owner_name),
            custom_fields=custom_html,
        )
    except (KeyError, ValueError, IndexError):
        html = _GUEST_CONFIRMATION_DEFAULT.format(
            guest_name=escape(guest_name),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
            owner_name=escape(owner_name),
            custom_fields=custom_html,
        )
```

And in `send_cancellation_notice`:
```python
    try:
        html = (template or _CANCELLATION_DEFAULT).format(
            guest_name=escape(guest_name),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
        )
    except (KeyError, ValueError, IndexError):
        html = _CANCELLATION_DEFAULT.format(
            guest_name=escape(guest_name),
            appt_type=escape(appt_type_name),
            date_time=_format_dt(start_dt),
        )
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_email.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add app/services/email.py tests/test_email.py
git commit -m "security: HTML-escape all guest-supplied data before inserting into email bodies"
```

---

### Task 6: Validate URL Schemes for listing_url and rental_application_url (XSS-1)

**Files:**
- Modify: `app/routers/admin.py`
- Modify: `tests/test_admin_appt_types.py`

**Step 1: Write failing tests**

Add to `tests/test_admin_appt_types.py`:
```python
def test_create_appt_type_rejects_javascript_listing_url(client, admin_session):
    resp = client.post("/admin/appointment-types", data={
        "name": "Test",
        "duration_minutes": 30,
        "listing_url": "javascript:alert(1)",
    }, follow_redirects=True)
    assert resp.status_code == 200
    # The listing_url should have been blanked out
    from app.database import SessionLocal
    from app.models import AppointmentType
    db = SessionLocal()
    t = db.query(AppointmentType).filter_by(name="Test").first()
    db.close()
    assert t.listing_url == ""


def test_create_appt_type_accepts_https_listing_url(client, admin_session):
    resp = client.post("/admin/appointment-types", data={
        "name": "Test2",
        "duration_minutes": 30,
        "listing_url": "https://example.com/listing",
    }, follow_redirects=True)
    assert resp.status_code == 200
    from app.database import SessionLocal
    from app.models import AppointmentType
    db = SessionLocal()
    t = db.query(AppointmentType).filter_by(name="Test2").first()
    db.close()
    assert t.listing_url == "https://example.com/listing"
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_admin_appt_types.py::test_create_appt_type_rejects_javascript_listing_url tests/test_admin_appt_types.py::test_create_appt_type_accepts_https_listing_url -v
```
Expected: FAIL

**Step 3: Add `_validate_url` helper and apply it in `app/routers/admin.py`**

Add this helper near the top of `admin.py` (after the imports, before the router declaration):
```python
def _validate_url(url: str) -> str:
    """Return the URL only if its scheme is http or https; blank it otherwise."""
    if not url:
        return ""
    from urllib.parse import urlparse
    scheme = urlparse(url).scheme.lower()
    return url if scheme in ("http", "https") else ""
```

In `create_appt_type` (around line 106), change:
```python
        listing_url=listing_url,
        rental_application_url=rental_application_url,
```
to:
```python
        listing_url=_validate_url(listing_url),
        rental_application_url=_validate_url(rental_application_url),
```

In `update_appt_type` (around lines 179–180), change:
```python
        t.listing_url = listing_url
        t.rental_application_url = rental_application_url
```
to:
```python
        t.listing_url = _validate_url(listing_url)
        t.rental_application_url = _validate_url(rental_application_url)
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_admin_appt_types.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add app/routers/admin.py tests/test_admin_appt_types.py
git commit -m "security: reject non-http/https schemes in listing_url and rental_application_url"
```

---

### Task 7: Validate OAuth State Parameter (OAUTH-1)

**Files:**
- Modify: `app/services/calendar.py`
- Modify: `app/routers/admin.py` (lines 500–528)
- Modify: `tests/test_calendar.py`

The `CalendarService.get_auth_url()` currently discards the `state` returned by the OAuth library. We need to return it so the caller can store it in the session.

**Step 1: Write failing test**

Add to `tests/test_calendar.py`:
```python
def test_get_auth_url_returns_state():
    """get_auth_url must return a (url, state) tuple so callers can store the state."""
    service = make_service()
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?...", "random-state")
    with patch.object(service, "_make_flow", return_value=mock_flow):
        url, state = service.get_auth_url()
    assert url.startswith("https://")
    assert state == "random-state"
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_calendar.py::test_get_auth_url_returns_state -v
```
Expected: FAIL — `get_auth_url` returns a string, not a tuple

**Step 3: Fix `CalendarService.get_auth_url()` in `app/services/calendar.py`**

Change lines 36–39:
```python
def get_auth_url(self) -> tuple[str, str]:
    """Return (auth_url, state) tuple. Caller must store state in session."""
    flow = self._make_flow()
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    return auth_url, state
```

**Step 4: Run the new test**

```bash
pytest tests/test_calendar.py::test_get_auth_url_returns_state -v
```
Expected: PASS

**Step 5: Check for any other callers of `get_auth_url()`**

```bash
grep -rn "get_auth_url" /home/devon/Projects/BookingAssistant/
```

Only `app/routers/admin.py:508` calls it. Update that caller.

**Step 6: Update `GET /admin/google/authorize` and `GET /admin/google/callback` in `app/routers/admin.py`**

Change `google_authorize` (lines 500–509):
```python
@router.get("/google/authorize")
def google_authorize(request: Request, _=AuthDep):
    settings = get_settings()
    cal = CalendarService(
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_redirect_uri,
    )
    url, state = cal.get_auth_url()
    request.session["oauth_state"] = state
    return RedirectResponse(url, status_code=302)
```

Change `google_callback` (lines 512–528):
```python
@router.get("/google/callback")
def google_callback(
    request: Request, code: str, db: Session = Depends(get_db), _=AuthDep
):
    received_state = request.query_params.get("state", "")
    expected_state = request.session.pop("oauth_state", "")
    if not expected_state or received_state != expected_state:
        _flash(request, "OAuth state mismatch — possible CSRF. Please try again.", "error")
        return RedirectResponse("/admin/settings", status_code=302)
    settings = get_settings()
    cal = CalendarService(
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_redirect_uri,
    )
    try:
        refresh_token = cal.exchange_code(code)
        set_setting(db, "google_refresh_token", refresh_token)
        _flash(request, "Google Calendar connected successfully.")
    except Exception as e:
        _flash(request, f"Google Calendar connection failed: {e}", "error")
    return RedirectResponse("/admin/settings", status_code=302)
```

**Step 7: Update the existing test for `get_auth_url` to unpack the tuple**

The existing `test_get_auth_url_returns_google_url` (line 10–16) calls `service.get_auth_url()` and assigns the result to `url`. Update it:
```python
def test_get_auth_url_returns_google_url():
    service = make_service()
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?...", "state")
    with patch.object(service, "_make_flow", return_value=mock_flow):
        url, state = service.get_auth_url()
    assert url == "https://accounts.google.com/o/oauth2/auth?..."
    assert state == "state"
```

**Step 8: Run all calendar tests**

```bash
pytest tests/test_calendar.py -v
```
Expected: all PASS

**Step 9: Run full test suite**

```bash
pytest -v
```
Expected: all PASS

**Step 10: Commit**

```bash
git add app/services/calendar.py app/routers/admin.py tests/test_calendar.py
git commit -m "security: validate OAuth state parameter in Google callback to prevent OAuth CSRF"
```

---

### Task 8: CSRF Infrastructure — Token Generation and Validation Dependency

**Files:**
- Modify: `app/dependencies.py`

This task adds the CSRF token helper and validation dependency. All POST handler changes come in Task 11.

**Step 1: Write failing tests for CSRF helpers**

Create `tests/test_csrf.py`:
```python
from unittest.mock import MagicMock


def _make_request(session=None):
    req = MagicMock()
    req.session = session or {}
    return req


def test_get_csrf_token_creates_token_on_first_call():
    from app.dependencies import get_csrf_token
    req = _make_request()
    token = get_csrf_token(req)
    assert token
    assert len(token) == 64  # 32 bytes hex
    assert req.session["csrf_token"] == token


def test_get_csrf_token_returns_same_token_on_second_call():
    from app.dependencies import get_csrf_token
    req = _make_request()
    t1 = get_csrf_token(req)
    t2 = get_csrf_token(req)
    assert t1 == t2


def test_validate_csrf_passes_with_valid_token():
    from app.dependencies import get_csrf_token, validate_csrf_token
    req = _make_request()
    token = get_csrf_token(req)
    # should not raise
    validate_csrf_token(req, token)


def test_validate_csrf_raises_403_with_wrong_token():
    from fastapi import HTTPException
    from app.dependencies import get_csrf_token, validate_csrf_token
    req = _make_request()
    get_csrf_token(req)
    try:
        validate_csrf_token(req, "wrong-token")
        assert False, "Should have raised HTTPException"
    except HTTPException as e:
        assert e.status_code == 403


def test_validate_csrf_raises_403_with_empty_token():
    from fastapi import HTTPException
    from app.dependencies import get_csrf_token, validate_csrf_token
    req = _make_request()
    get_csrf_token(req)
    try:
        validate_csrf_token(req, "")
        assert False, "Should have raised HTTPException"
    except HTTPException as e:
        assert e.status_code == 403
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_csrf.py -v
```
Expected: FAIL — `get_csrf_token` and `validate_csrf_token` don't exist yet

**Step 3: Add CSRF helpers to `app/dependencies.py`**

```python
import hmac
import secrets
from fastapi import HTTPException


def get_csrf_token(request: Request) -> str:
    """Return the CSRF token for this session, creating one if needed."""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)
    return request.session["csrf_token"]


def validate_csrf_token(request: Request, token: str) -> None:
    """Raise HTTP 403 if token does not match the session's CSRF token."""
    expected = request.session.get("csrf_token", "")
    if not token or not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="CSRF token invalid or missing.")
```

Note: `Request` is already imported in `dependencies.py`. Add `import hmac` and `import secrets` to the imports at the top.

**Step 4: Run CSRF tests**

```bash
pytest tests/test_csrf.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add app/dependencies.py tests/test_csrf.py
git commit -m "security: add CSRF token generation and validation helpers to dependencies"
```

---

### Task 9: CSRF — Register Jinja2 Global on All Template Engines

**Files:**
- Modify: `app/routers/auth.py`
- Modify: `app/routers/admin.py`
- Modify: `app/routers/booking.py`

Each router has its own `Jinja2Templates` instance. We need to register a `csrf_token` global on each so templates can call `{{ csrf_token(request) }}`.

**Step 1: Add the global to `app/routers/auth.py`**

After the line `templates = Jinja2Templates(directory="app/templates")`, add:
```python
from app.dependencies import get_csrf_token as _get_csrf_token
templates.env.globals["csrf_token"] = _get_csrf_token
```

**Step 2: Add the global to `app/routers/admin.py`**

After the existing `templates.env.filters["enumerate"] = enumerate` line (line 19), add:
```python
from app.dependencies import get_csrf_token as _get_csrf_token
templates.env.globals["csrf_token"] = _get_csrf_token
```

**Step 3: Add the global to `app/routers/booking.py`**

After the line `templates = Jinja2Templates(directory="app/templates")`, add:
```python
from app.dependencies import get_csrf_token as _get_csrf_token
templates.env.globals["csrf_token"] = _get_csrf_token
```

**Step 4: Run all tests to verify nothing is broken**

```bash
pytest -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add app/routers/auth.py app/routers/admin.py app/routers/booking.py
git commit -m "security: register csrf_token Jinja2 global on all template engines"
```

---

### Task 10: CSRF — Add Hidden Token Field to All POST Forms in Templates

**Files:**
- Modify: `app/templates/admin/login.html`
- Modify: `app/templates/admin/setup.html`
- Modify: `app/templates/admin/availability.html`
- Modify: `app/templates/admin/settings.html`
- Modify: `app/templates/admin/appointment_types.html`
- Modify: `app/templates/admin/bookings.html`
- Modify: `app/templates/booking/form_partial.html`

The pattern for every `<form method="post" ...>` is to add this hidden input as the **first child**:
```html
<input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
```

**Step 1: Edit `app/templates/admin/login.html`**

Inside `<form method="post" action="/admin/login" ...>`, add the hidden input as the first child:
```html
<form method="post" action="/admin/login" style="margin-top:1.5rem;">
  <input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
  <label>Password
    ...
```

**Step 2: Edit `app/templates/admin/setup.html`**

Inside `<form method="post" action="/admin/setup" ...>`, add:
```html
<form method="post" action="/admin/setup" style="margin-top:1.5rem;">
  <input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
  ...
```

**Step 3: Edit `app/templates/admin/availability.html`**

There are 5 `<form method="post"` elements. Add the hidden input as first child in each:
1. `<form method="post" action="/admin/availability/rules/{{ rule.id }}/delete">` (line ~18)
2. `<form method="post" action="/admin/availability/rules">` (line ~32)
3. `<form method="post" action="/admin/availability/blocks/{{ b.id }}/delete">` (line ~57)
4. `<form method="post" action="/admin/availability/blocks">` (line ~71)
5. `<form method="post" action="/admin/availability/settings">` (line ~81)

**Step 4: Edit `app/templates/admin/settings.html`**

There are 5 `<form method="post"` elements:
1. `<form method="post" action="/admin/settings">` (general settings)
2. `<form method="post" action="/admin/settings/password">`
3. `<form method="post" action="/admin/settings/conflict-calendars/{{ i }}/delete">` (in loop)
4. `<form method="post" action="/admin/settings/conflict-calendars">` (add calendar)
5. `<form method="post" action="/admin/settings/email-templates">`

**Step 5: Edit `app/templates/admin/appointment_types.html`**

There are 2+ `<form method="post"` elements:
1. `<form method="post" action="/admin/appointment-types/{{ t.id }}/toggle">` (in loop)
2. `<form method="post" action="...">` (create/edit form — line ~40)

**Step 6: Edit `app/templates/admin/bookings.html`**

Cancel button form (in loop):
```html
<form method="post" action="/admin/bookings/{{ b.id }}/cancel" ...>
  <input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
  ...
```
The same pattern appears in both the Upcoming and Past booking tables.

**Step 7: Edit `app/templates/booking/form_partial.html`**

```html
<form hx-post="/book" hx-target="#booking-form-area" hx-swap="innerHTML">
  <input type="hidden" name="_csrf" value="{{ csrf_token(request) }}">
  <input type="hidden" name="type_id" value="{{ appt_type.id }}">
  ...
```

**Step 8: Run all tests**

```bash
pytest -v
```
Expected: all PASS (tests don't exercise CSRF validation yet — that's Task 11)

**Step 9: Commit**

```bash
git add app/templates/
git commit -m "security: add CSRF hidden token field to all POST forms"
```

---

### Task 11: CSRF — Validate Token in All POST Handlers (CSRF-1)

**Files:**
- Modify: `app/routers/auth.py`
- Modify: `app/routers/admin.py`
- Modify: `app/routers/booking.py`

**Step 1: Add `require_csrf` dependency to `app/dependencies.py`**

First, add this dependency function to `app/dependencies.py` after `validate_csrf_token`:
```python
from fastapi import Form as _Form

async def require_csrf(request: Request, _csrf: str = _Form("")) -> None:
    """FastAPI dependency. Validates the _csrf form field against the session token."""
    validate_csrf_token(request, _csrf)
```

Note: `_Form` is aliased to avoid shadowing. This dependency reads the `_csrf` form field automatically.

**Step 2: Write integration tests for CSRF enforcement**

Add to `tests/test_admin_auth.py`:
```python
def test_login_post_rejects_missing_csrf(client):
    resp = client.post("/admin/login", data={"password": "anything"})
    assert resp.status_code == 403


def test_login_post_accepts_valid_csrf(client):
    # GET the page first to establish session and get CSRF token
    get_resp = client.get("/admin/login")
    assert get_resp.status_code == 200
    # Extract CSRF token from the response HTML
    import re
    match = re.search(r'name="_csrf" value="([^"]+)"', get_resp.text)
    assert match, "CSRF token not found in login form"
    csrf_token = match.group(1)
    resp = client.post("/admin/login", data={"password": "wrongpassword", "_csrf": csrf_token})
    # Should reach the handler (password wrong, but not 403)
    assert resp.status_code in (200, 401)
```

**Step 3: Run tests to verify they fail**

```bash
pytest tests/test_admin_auth.py::test_login_post_rejects_missing_csrf tests/test_admin_auth.py::test_login_post_accepts_valid_csrf -v
```
Expected: `test_login_post_rejects_missing_csrf` FAIL (currently returns 200/302 not 403), `test_login_post_accepts_valid_csrf` FAIL (no token in form yet — already added in Task 10, but validation not wired yet)

**Step 4: Add CSRF validation to `app/routers/auth.py`**

Add `require_csrf` to the imports from `app.dependencies`:
```python
from app.dependencies import get_setting, set_setting, require_csrf
```

Add `_csrf_ok: None = Depends(require_csrf)` to both POST handlers:
```python
@router.post("/admin/login")
@limiter.limit("5/minute")
def login(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
    _csrf_ok: None = Depends(require_csrf),
):
```

```python
@router.post("/admin/setup")
@limiter.limit("5/minute")
def setup(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(...),
    db: Session = Depends(get_db),
    _csrf_ok: None = Depends(require_csrf),
):
```

**Step 5: Run CSRF tests for auth**

```bash
pytest tests/test_admin_auth.py -v
```
Expected: all PASS

**Step 6: Add CSRF validation to all 14 POST handlers in `app/routers/admin.py`**

Add `require_csrf` to the import from `app.dependencies`:
```python
from app.dependencies import get_setting, require_admin, set_setting, require_csrf
```

Then add `_csrf_ok: None = Depends(require_csrf)` to each POST handler signature. The handlers are at these lines:
- Line 69: `create_appt_type`
- Line 144: `update_appt_type`
- Line 209: `toggle_appt_type`
- Line 235: `create_rule`
- Line 250: `delete_rule`
- Line 260: `create_block`
- Line 279: `delete_block`
- Line 291: `update_availability_settings`
- Line 328: `cancel_booking_route`
- Line 405: `update_settings`
- Line 425: `change_password`
- Line 442: `add_conflict_calendar`
- Line 465: `delete_conflict_calendar`
- Line 482: `update_email_templates`

For each, add `_csrf_ok: None = Depends(require_csrf)` before `db: Session = Depends(get_db)` or after the last param. Example for `create_rule`:
```python
@router.post("/availability/rules")
def create_rule(
    request: Request,
    day_of_week: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    db: Session = Depends(get_db),
    _=AuthDep,
    _csrf_ok: None = Depends(require_csrf),
):
```

**Step 7: Add CSRF validation to `POST /book` in `app/routers/booking.py`**

`submit_booking` reads the form manually. Add `require_csrf` as a dependency:

Add import:
```python
from app.dependencies import get_setting, require_csrf
```

Add to function signature:
```python
@router.post("/book", response_class=HTMLResponse)
@limiter.limit("10/hour")
async def submit_booking(
    request: Request,
    db: Session = Depends(get_db),
    _csrf_ok: None = Depends(require_csrf),
):
```

FastAPI will resolve `require_csrf` before entering the handler body. Since `require_csrf` calls `await request.form()` and Starlette caches the form body, the subsequent `form_data = await request.form()` in the handler body will still work.

**Step 8: Run all tests**

```bash
pytest -v
```
Expected: all PASS

If any existing test posts to a form endpoint and gets a 403, it means that test needs to include a valid CSRF token. To do this in tests: make a GET request to the relevant page first, extract the token from the form HTML, then include it in the POST. The `conftest.py` may need a helper. See next step.

**Step 9: If any admin tests fail with 403, add a CSRF helper to `tests/conftest.py`**

Check the conftest for an `admin_session` fixture. The test client in FastAPI's `TestClient` persists cookies across requests, so session state is preserved. A helper that does a GET to extract the CSRF token can be added:

```python
def get_csrf(client, path: str) -> str:
    """GET a page and extract the CSRF token from a hidden input."""
    import re
    resp = client.get(path)
    match = re.search(r'name="_csrf" value="([^"]+)"', resp.text)
    return match.group(1) if match else ""
```

Any test that posts to an admin endpoint should call `get_csrf(client, "/admin/...")` and include `"_csrf": token` in the form data.

**Step 10: Run full test suite to confirm all green**

```bash
pytest -v
```
Expected: all PASS

**Step 11: Commit**

```bash
git add app/dependencies.py app/routers/auth.py app/routers/admin.py app/routers/booking.py tests/test_admin_auth.py tests/test_csrf.py tests/conftest.py
git commit -m "security: enforce CSRF token validation on all POST endpoints"
```

---

## Summary

| Task | Finding(s) | Effort |
|------|-----------|--------|
| 1 | CodeQL false positive | Minimal |
| 2 | MISC-1 security headers | Small |
| 3 | CSRF-2 rate limit login | Small |
| 4 | XSS-2 `\|safe` in template | Small |
| 5 | EMAIL-2 HTML escape emails | Small |
| 6 | XSS-1 URL scheme validation | Small |
| 7 | OAUTH-1 OAuth state validation | Medium |
| 8 | CSRF-1 token infrastructure | Small |
| 9 | CSRF-1 Jinja2 globals | Small |
| 10 | CSRF-1 form hidden fields | Medium |
| 11 | CSRF-1 POST handler validation | Medium |

All tasks follow TDD. Each is independently committable. Tasks 8–11 together close CSRF-1.

Run `pytest -v` after each task. All tests should pass before moving to the next task.
