import hmac
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Setting


class AdminNotAuthenticated(Exception):
    pass


def require_admin(request: Request):
    if not request.session.get("admin_authenticated"):
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
