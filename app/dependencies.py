import hmac
import json
import secrets
from dataclasses import dataclass
from functools import cached_property
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Setting


class AdminNotAuthenticated(Exception):
    pass


def require_admin(request: Request):
    if not request.session.get("user_sub"):
        raise AdminNotAuthenticated()
    return True


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter_by(key=key).first()
    return row.value if row else default


def set_setting(db: Session, key: str, value: str):
    row = db.query(Setting).filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


def get_conflict_calendars(db: Session) -> list[dict]:
    """Return the configured extra conflict calendars ([{type, id, name}, ...])."""
    return _parse_conflict_calendars(get_setting(db, "conflict_calendars", "[]"))


def set_conflict_calendars(db: Session, cals: list[dict]) -> None:
    set_setting(db, "conflict_calendars", json.dumps(cals))


@dataclass(frozen=True)
class DbSettings:
    """Typed snapshot of the DB-backed settings, loaded with one query.

    Hot paths previously issued ~10 individual Setting queries per request;
    load_db_settings() replaces them. Where a key exists in both the DB and
    the environment (resend_api_key, from_email), the DB value wins and the
    environment is the fallback — same precedence as the old per-key reads.
    """
    timezone_name: str
    min_advance_hours: int
    max_future_days: int
    google_refresh_token: str
    home_address: str
    owner_name: str
    notify_email: str
    contact_phone: str
    notifications_enabled: bool
    resend_api_key: str
    from_email: str
    conflict_calendars: list
    email_guest_confirmation: str
    email_admin_alert: str
    email_guest_cancellation: str

    @cached_property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def can_send_email(self) -> bool:
        return self.notifications_enabled and bool(self.resend_api_key)


def _parse_conflict_calendars(raw: str) -> list:
    try:
        cals = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return cals if isinstance(cals, list) else []


def load_db_settings(db: Session, env) -> DbSettings:
    """Load all DB settings in a single query. env: the app Settings object."""
    values = {row.key: row.value for row in db.query(Setting).all()}

    def get(key: str, default: str = "") -> str:
        return values.get(key, default)

    return DbSettings(
        timezone_name=get("timezone", "America/New_York"),
        min_advance_hours=int(get("min_advance_hours", "24")),
        max_future_days=int(get("max_future_days", "30")),
        google_refresh_token=get("google_refresh_token"),
        home_address=get("home_address"),
        owner_name=get("owner_name"),
        notify_email=get("notify_email"),
        contact_phone=get("contact_phone"),
        notifications_enabled=get("notifications_enabled", "true") == "true",
        resend_api_key=get("resend_api_key", env.resend_api_key),
        from_email=get("from_email", env.from_email),
        conflict_calendars=_parse_conflict_calendars(get("conflict_calendars", "[]")),
        email_guest_confirmation=get("email_guest_confirmation"),
        email_admin_alert=get("email_admin_alert"),
        email_guest_cancellation=get("email_guest_cancellation"),
    )


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


async def require_csrf(request: Request) -> None:
    """FastAPI dependency. Validates the _csrf form field against the session token."""
    form_data = await request.form()
    token = str(form_data.get("_csrf", ""))
    validate_csrf_token(request, token)
