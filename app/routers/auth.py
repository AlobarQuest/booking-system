import bcrypt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_setting, set_setting, require_csrf
from app.limiter import limiter

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
from app.dependencies import get_csrf_token as _get_csrf_token
templates.env.globals["csrf_token"] = _get_csrf_token


@router.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("admin/login.html", {"request": request, "error": None})


@router.post("/admin/login")
@limiter.limit("5/minute")
def login(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
    _csrf_ok: None = Depends(require_csrf),
):
    stored_hash = get_setting(db, "admin_password_hash", "")
    if not stored_hash:
        return RedirectResponse("/admin/setup", status_code=302)
    if bcrypt.checkpw(password.encode(), stored_hash.encode()):
        request.session["admin_authenticated"] = True
        return RedirectResponse("/admin/", status_code=302)
    return templates.TemplateResponse(
        "admin/login.html",
        {"request": request, "error": "Incorrect password."},
        status_code=401,
    )


@router.get("/admin/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=302)


@router.get("/admin/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db)):
    if get_setting(db, "admin_password_hash"):
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse("admin/setup.html", {"request": request, "error": None})


@router.post("/admin/setup")
@limiter.limit("5/minute")
def setup(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(...),
    db: Session = Depends(get_db),
    _csrf_ok: None = Depends(require_csrf),
):
    if get_setting(db, "admin_password_hash"):
        return RedirectResponse("/admin/login", status_code=302)
    if password != confirm:
        return templates.TemplateResponse(
            "admin/setup.html",
            {"request": request, "error": "Passwords do not match."},
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            "admin/setup.html",
            {"request": request, "error": "Password must be at least 8 characters."},
        )
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    set_setting(db, "admin_password_hash", hashed)
    set_setting(db, "timezone", "America/New_York")
    set_setting(db, "min_advance_hours", "24")
    set_setting(db, "max_future_days", "30")
    set_setting(db, "notifications_enabled", "true")
    request.session["admin_authenticated"] = True
    return RedirectResponse("/admin/", status_code=302)
