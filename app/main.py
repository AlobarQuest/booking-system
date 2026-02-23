from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.database import init_db
from app.routers import auth, admin, booking, slots

settings = get_settings()
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Booking Assistant", docs_url=None, redoc_url=None, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=28800)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

from app.dependencies import AdminNotAuthenticated

@app.exception_handler(AdminNotAuthenticated)
async def admin_not_authenticated_handler(request, exc):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/admin/login", status_code=302)

app.include_router(booking.router)
app.include_router(slots.router)
app.include_router(auth.router)
app.include_router(admin.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
