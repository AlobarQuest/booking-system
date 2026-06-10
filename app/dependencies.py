import hmac
import json
import secrets
from typing import NamedTuple

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
    raw = get_setting(db, "conflict_calendars", "[]")
    try:
        cals = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return cals if isinstance(cals, list) else []


def set_conflict_calendars(db: Session, cals: list[dict]) -> None:
    set_setting(db, "conflict_calendars", json.dumps(cals))


class EmailConfig(NamedTuple):
    enabled: bool
    api_key: str
    from_email: str

    @property
    def can_send(self) -> bool:
        return self.enabled and bool(self.api_key)


def get_email_config(db: Session, settings) -> EmailConfig:
    """Return notification settings, falling back to env-derived defaults."""
    return EmailConfig(
        enabled=get_setting(db, "notifications_enabled", "true") == "true",
        api_key=get_setting(db, "resend_api_key", settings.resend_api_key),
        from_email=get_setting(db, "from_email", settings.from_email),
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
