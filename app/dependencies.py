from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Setting


def require_admin(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("admin_authenticated"):
        from fastapi.responses import RedirectResponse
        raise HTTPException(status_code=302, headers={"Location": "/admin/login"})
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
