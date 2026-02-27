import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.database import init_db
from app.dependencies import AdminNotAuthenticated
from app.limiter import limiter
from app.routers import auth, admin, booking, slots

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    os.makedirs(get_settings().upload_dir, exist_ok=True)
    yield


app = FastAPI(title="Booking Assistant", docs_url=None, redoc_url=None, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=28800)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(booking.router)
app.include_router(slots.router)
app.include_router(auth.router)
app.include_router(admin.router)


@app.exception_handler(AdminNotAuthenticated)
async def admin_not_authenticated_handler(request, exc):
    return RedirectResponse(url="/admin/login", status_code=302)


@app.get("/health")
def health_check():
    return {"status": "ok"}
