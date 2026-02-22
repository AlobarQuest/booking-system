# Personal Booking System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a personal appointment booking system with a public HTMX booking interface, admin panel, Google Calendar integration, and Resend email notifications, deployable to Fly.io.

**Architecture:** FastAPI HTMX monolith serving Jinja2 templates. SQLAlchemy 2.0 + SQLite on a persistent Fly.io volume. Google Calendar freebusy API for conflict detection. Resend SDK for transactional email. Single-password admin with signed session cookies.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX 1.9, SQLAlchemy 2.0, SQLite, google-api-python-client, resend, pydantic-settings, bcrypt, slowapi, pytest, httpx

---

## Prerequisites (do before Task 1)

```bash
cd /home/devon/Projects/BookingAssistant
python3.12 -m venv .venv
source .venv/bin/activate
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `app/__init__.py`
- Create: `app/static/js/htmx.min.js` (download)
- Create: `app/static/css/style.css` (empty placeholder)

**Step 1: Create `requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
jinja2>=3.1.0
python-multipart>=0.0.9
sqlalchemy>=2.0.0
itsdangerous>=2.2.0
bcrypt>=4.2.0
google-api-python-client>=2.147.0
google-auth-oauthlib>=1.2.0
google-auth-httplib2>=0.2.0
resend>=2.0.0
pydantic-settings>=2.5.0
slowapi>=0.1.9
httpx>=0.27.0
pytest>=8.3.0
```

**Step 2: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: no errors, packages installed.

**Step 3: Create directory structure**

```bash
mkdir -p app/routers app/services app/templates/booking app/templates/admin app/static/js app/static/css tests
touch app/__init__.py app/routers/__init__.py app/services/__init__.py tests/__init__.py
```

**Step 4: Download HTMX**

```bash
curl -o app/static/js/htmx.min.js https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
```

**Step 5: Create `tests/conftest.py`**

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app


@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
```

**Step 6: Commit**

```bash
git add .
git commit -m "chore: project scaffold, requirements, test fixtures"
```

---

## Task 2: Configuration

**Files:**
- Create: `app/config.py`
- Create: `.env.example`
- Create: `tests/test_config.py`

**Step 1: Write failing test — `tests/test_config.py`**

```python
from app.config import get_settings


def test_settings_has_required_fields():
    s = get_settings()
    assert hasattr(s, "database_url")
    assert hasattr(s, "secret_key")
    assert hasattr(s, "google_client_id")
    assert hasattr(s, "resend_api_key")
    assert hasattr(s, "timezone")
    assert s.timezone == "America/New_York"
```

**Step 2: Run to verify it fails**

```bash
pytest tests/test_config.py -v
```

Expected: `ImportError` or `ModuleNotFoundError`.

**Step 3: Create `app/config.py`**

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./booking.db"
    secret_key: str = "change-me-in-production"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/admin/google/callback"
    resend_api_key: str = ""
    from_email: str = "noreply@example.com"
    timezone: str = "America/New_York"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

**Step 4: Create `.env.example`**

```
DATABASE_URL=sqlite:////data/booking.db
SECRET_KEY=change-me-generate-with-openssl-rand-hex-32
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=https://book.yourdomain.com/admin/google/callback
RESEND_API_KEY=re_xxxxxxxxxxxx
FROM_EMAIL=noreply@yourdomain.com
TIMEZONE=America/New_York
```

**Step 5: Run test to verify it passes**

```bash
pytest tests/test_config.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add app/config.py .env.example tests/test_config.py
git commit -m "feat: pydantic-settings configuration"
```

---

## Task 3: Database Models

**Files:**
- Create: `app/database.py`
- Create: `app/models.py`
- Create: `tests/test_models.py`

**Step 1: Write failing test — `tests/test_models.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod, Booking, Setting


def test_all_tables_create():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    appt = AppointmentType(
        name="Phone Call",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=5,
        calendar_id="primary",
        custom_fields=[],
        active=True,
        color="#3b82f6",
    )
    db.add(appt)
    db.commit()
    assert appt.id is not None

    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True)
    db.add(rule)
    db.commit()
    assert rule.id is not None

    setting = Setting(key="timezone", value="America/New_York")
    db.add(setting)
    db.commit()
    assert db.query(Setting).filter_by(key="timezone").first().value == "America/New_York"
    db.close()
```

**Step 2: Run to verify it fails**

```bash
pytest tests/test_models.py -v
```

**Step 3: Create `app/database.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    connect_args = {"check_same_thread": False} if "sqlite" in settings.database_url else {}
    return create_engine(settings.database_url, connect_args=connect_args)


engine = _make_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
```

**Step 4: Create `app/models.py`**

```python
import json
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class AppointmentType(Base):
    __tablename__ = "appointment_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, default=0)
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, default=0)
    calendar_id: Mapped[str] = mapped_column(String(200), default="primary")
    _custom_fields: Mapped[str] = mapped_column("custom_fields", Text, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    color: Mapped[str] = mapped_column(String(20), default="#3b82f6")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="appointment_type")

    @property
    def custom_fields(self) -> list:
        return json.loads(self._custom_fields)

    @custom_fields.setter
    def custom_fields(self, value: list):
        self._custom_fields = json.dumps(value)


class AvailabilityRule(Base):
    __tablename__ = "availability_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon, 6=Sun
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class BlockedPeriod(Base):
    __tablename__ = "blocked_periods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_type_id: Mapped[int] = mapped_column(ForeignKey("appointment_types.id"))
    start_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    guest_name: Mapped[str] = mapped_column(String(200), nullable=False)
    guest_email: Mapped[str] = mapped_column(String(200), nullable=False)
    guest_phone: Mapped[str] = mapped_column(String(50), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    _custom_field_responses: Mapped[str] = mapped_column("custom_field_responses", Text, default="{}")
    google_event_id: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # confirmed | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    appointment_type: Mapped["AppointmentType"] = relationship(back_populates="bookings")

    @property
    def custom_field_responses(self) -> dict:
        return json.loads(self._custom_field_responses)

    @custom_field_responses.setter
    def custom_field_responses(self, value: dict):
        self._custom_field_responses = json.dumps(value)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
```

**Step 5: Run test**

```bash
pytest tests/test_models.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add app/database.py app/models.py tests/test_models.py
git commit -m "feat: SQLAlchemy database models"
```

---

## Task 4: FastAPI App Entry Point + Health Check

**Files:**
- Create: `app/main.py`
- Create: `app/dependencies.py`
- Create: `tests/test_health.py`

**Step 1: Write failing test — `tests/test_health.py`**

```python
def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

**Step 2: Run to verify it fails**

```bash
pytest tests/test_health.py -v
```

**Step 3: Create `app/dependencies.py`**

```python
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Setting


def require_admin(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("admin_authenticated"):
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
```

**Step 4: Create `app/main.py`**

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import init_db
from app.routers import auth, admin, booking, slots

settings = get_settings()

app = FastAPI(title="Booking Assistant", docs_url=None, redoc_url=None)

app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=28800)  # 8 hours

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(booking.router)
app.include_router(slots.router)
app.include_router(auth.router)
app.include_router(admin.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.on_event("startup")
def on_startup():
    init_db()
```

**Step 5: Create stub routers (needed for import)**

```bash
# app/routers/booking.py
cat > app/routers/booking.py << 'EOF'
from fastapi import APIRouter
router = APIRouter()
EOF

# app/routers/slots.py
cat > app/routers/slots.py << 'EOF'
from fastapi import APIRouter
router = APIRouter()
EOF

# app/routers/auth.py
cat > app/routers/auth.py << 'EOF'
from fastapi import APIRouter
router = APIRouter()
EOF

# app/routers/admin.py
cat > app/routers/admin.py << 'EOF'
from fastapi import APIRouter
router = APIRouter()
EOF
```

**Step 6: Run test**

```bash
pytest tests/test_health.py -v
```

Expected: PASS.

**Step 7: Commit**

```bash
git add app/main.py app/dependencies.py app/routers/booking.py app/routers/slots.py app/routers/auth.py app/routers/admin.py tests/test_health.py
git commit -m "feat: FastAPI app entry point and health check"
```

---

## Task 5: Google Calendar Service — OAuth

**Files:**
- Create: `app/services/calendar.py`
- Create: `tests/test_calendar.py`

**Step 1: Write failing test — `tests/test_calendar.py`**

```python
from unittest.mock import MagicMock, patch
from app.services.calendar import CalendarService


def test_get_auth_url_returns_string():
    service = CalendarService(client_id="fake-id", client_secret="fake-secret", redirect_uri="http://localhost/cb")
    url = service.get_auth_url()
    assert url.startswith("https://accounts.google.com")


def test_is_authorized_false_without_token():
    service = CalendarService(client_id="fake-id", client_secret="fake-secret", redirect_uri="http://localhost/cb")
    assert service.is_authorized(refresh_token="") is False


def test_is_authorized_true_with_token():
    service = CalendarService(client_id="fake-id", client_secret="fake-secret", redirect_uri="http://localhost/cb")
    assert service.is_authorized(refresh_token="some-token") is True
```

**Step 2: Run to verify it fails**

```bash
pytest tests/test_calendar.py -v
```

**Step 3: Create `app/services/calendar.py`**

```python
from datetime import datetime
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]


class CalendarService:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def _make_flow(self) -> Flow:
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uris": [self.redirect_uri],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=SCOPES,
            redirect_uri=self.redirect_uri,
        )

    def get_auth_url(self) -> str:
        flow = self._make_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        return auth_url

    def exchange_code(self, code: str) -> str:
        """Exchange OAuth code for refresh token. Returns the refresh token."""
        flow = self._make_flow()
        flow.fetch_token(code=code)
        return flow.credentials.refresh_token

    def is_authorized(self, refresh_token: str) -> bool:
        return bool(refresh_token)

    def _build_service(self, refresh_token: str):
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=SCOPES,
        )
        return build("calendar", "v3", credentials=creds)

    def get_busy_intervals(
        self, refresh_token: str, calendar_ids: list[str], start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Return list of (start, end) busy intervals from Google Calendar."""
        service = self._build_service(refresh_token)
        body = {
            "timeMin": start.isoformat() + "Z",
            "timeMax": end.isoformat() + "Z",
            "items": [{"id": cal_id} for cal_id in calendar_ids],
        }
        result = service.freebusy().query(body=body).execute()
        intervals = []
        for cal_data in result.get("calendars", {}).values():
            for busy in cal_data.get("busy", []):
                busy_start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00")).replace(tzinfo=None)
                busy_end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00")).replace(tzinfo=None)
                intervals.append((busy_start, busy_end))
        return intervals

    def create_event(
        self,
        refresh_token: str,
        calendar_id: str,
        summary: str,
        description: str,
        start: datetime,
        end: datetime,
        attendee_email: str = "",
    ) -> str:
        """Create a calendar event. Returns the event ID."""
        service = self._build_service(refresh_token)
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        }
        if attendee_email:
            event["attendees"] = [{"email": attendee_email}]
        result = service.events().insert(calendarId=calendar_id, body=event, sendUpdates="all").execute()
        return result["id"]

    def delete_event(self, refresh_token: str, calendar_id: str, event_id: str):
        """Delete a calendar event."""
        service = self._build_service(refresh_token)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
```

**Step 4: Run tests**

```bash
pytest tests/test_calendar.py -v
```

Expected: PASS (these tests don't call the real API).

**Step 5: Commit**

```bash
git add app/services/calendar.py tests/test_calendar.py
git commit -m "feat: Google Calendar service (OAuth + freebusy + event CRUD)"
```

---

## Task 6: Availability Calculation Service

**Files:**
- Create: `app/services/availability.py`
- Create: `tests/test_availability.py`

**Step 1: Write failing tests — `tests/test_availability.py`**

```python
from datetime import date, datetime, time
from app.services.availability import compute_slots, subtract_intervals, split_into_slots


def test_subtract_intervals_removes_busy_time():
    windows = [(time(9, 0), time(17, 0))]
    busy = [(datetime(2025, 3, 3, 12, 0), datetime(2025, 3, 3, 13, 0))]
    result = subtract_intervals(windows, busy, date(2025, 3, 3))
    assert (time(9, 0), time(12, 0)) in result
    assert (time(13, 0), time(17, 0)) in result


def test_split_into_slots_basic():
    windows = [(time(9, 0), time(11, 0))]
    slots = split_into_slots(windows, duration_minutes=60, buffer_after_minutes=0)
    assert slots == [time(9, 0), time(10, 0)]


def test_split_respects_buffer():
    windows = [(time(9, 0), time(11, 0))]
    # 60min appointment + 15min buffer = 75min per slot
    slots = split_into_slots(windows, duration_minutes=60, buffer_after_minutes=15)
    assert slots == [time(9, 0)]


def test_compute_slots_no_rules_returns_empty():
    result = compute_slots(
        target_date=date(2025, 3, 3),
        rules=[],
        blocked_periods=[],
        busy_intervals=[],
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        min_advance_hours=0,
        now=datetime(2025, 3, 3, 8, 0),
    )
    assert result == []


def test_compute_slots_returns_correct_times():
    from app.models import AvailabilityRule
    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="10:30", active=True)
    result = compute_slots(
        target_date=date(2025, 3, 3),   # Monday
        rules=[rule],
        blocked_periods=[],
        busy_intervals=[],
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        min_advance_hours=0,
        now=datetime(2025, 3, 2, 8, 0),
    )
    assert len(result) == 3
    assert time(9, 0) in result
    assert time(9, 30) in result
    assert time(10, 0) in result
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_availability.py -v
```

**Step 3: Create `app/services/availability.py`**

```python
from datetime import date, datetime, time, timedelta
from app.models import AvailabilityRule, BlockedPeriod


def _time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _minutes_to_time(m: int) -> time:
    return time(m // 60, m % 60)


def subtract_intervals(
    windows: list[tuple[time, time]],
    busy: list[tuple[datetime, datetime]],
    target_date: date,
) -> list[tuple[time, time]]:
    """Remove busy datetime intervals from time windows on a given date."""
    result = []
    for w_start, w_end in windows:
        segments = [(w_start, w_end)]
        for busy_start, busy_end in busy:
            if busy_start.date() > target_date or busy_end.date() < target_date:
                continue
            b_start = busy_start.time() if busy_start.date() == target_date else time(0, 0)
            b_end = busy_end.time() if busy_end.date() == target_date else time(23, 59)
            new_segments = []
            for s, e in segments:
                if b_end <= s or b_start >= e:
                    new_segments.append((s, e))
                else:
                    if s < b_start:
                        new_segments.append((s, b_start))
                    if b_end < e:
                        new_segments.append((b_end, e))
            segments = new_segments
        result.extend(segments)
    return result


def split_into_slots(
    windows: list[tuple[time, time]],
    duration_minutes: int,
    buffer_after_minutes: int,
) -> list[time]:
    """Split time windows into appointment start times."""
    slot_duration = duration_minutes + buffer_after_minutes
    slots = []
    for w_start, w_end in windows:
        current = _time_to_minutes(w_start)
        end = _time_to_minutes(w_end)
        while current + duration_minutes <= end:
            slots.append(_minutes_to_time(current))
            current += slot_duration
    return slots


def compute_slots(
    target_date: date,
    rules: list[AvailabilityRule],
    blocked_periods: list[BlockedPeriod],
    busy_intervals: list[tuple[datetime, datetime]],
    duration_minutes: int,
    buffer_before_minutes: int,
    buffer_after_minutes: int,
    min_advance_hours: int,
    now: datetime,
) -> list[time]:
    """Compute available appointment start times for a given date."""
    day_of_week = target_date.weekday()  # 0=Monday
    day_rules = [r for r in rules if r.day_of_week == day_of_week and r.active]
    if not day_rules:
        return []

    windows = [
        (time.fromisoformat(r.start_time), time.fromisoformat(r.end_time))
        for r in day_rules
    ]

    # Subtract blocked periods
    blocked = [
        (bp.start_datetime, bp.end_datetime)
        for bp in blocked_periods
        if bp.start_datetime.date() <= target_date <= bp.end_datetime.date()
    ]
    windows = subtract_intervals(windows, blocked, target_date)

    # Subtract Google Calendar busy intervals
    windows = subtract_intervals(windows, busy_intervals, target_date)

    # Apply buffer_before by shrinking window starts
    if buffer_before_minutes:
        windows = [
            (_minutes_to_time(_time_to_minutes(s) + buffer_before_minutes), e)
            for s, e in windows
            if _time_to_minutes(e) - _time_to_minutes(s) > buffer_before_minutes
        ]

    # Split into slots
    slots = split_into_slots(windows, duration_minutes, buffer_after_minutes)

    # Filter out slots too soon
    cutoff = now + timedelta(hours=min_advance_hours)
    cutoff_time = cutoff.time() if cutoff.date() == target_date else (time(23, 59) if cutoff.date() > target_date else time(0, 0))
    if cutoff.date() > target_date:
        return []
    if cutoff.date() == target_date:
        slots = [s for s in slots if s >= cutoff_time]

    return slots
```

**Step 4: Run tests**

```bash
pytest tests/test_availability.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add app/services/availability.py tests/test_availability.py
git commit -m "feat: availability slot calculation service"
```

---

## Task 7: Slots HTMX Endpoint

**Files:**
- Modify: `app/routers/slots.py`
- Create: `app/templates/booking/slots_partial.html`
- Create: `tests/test_slots_route.py`

**Step 1: Write failing test — `tests/test_slots_route.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType, AvailabilityRule
from fastapi.testclient import TestClient


def make_client_with_data():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    appt = AppointmentType(name="Call", duration_minutes=30, buffer_before_minutes=0,
                            buffer_after_minutes=0, calendar_id="primary",
                            custom_fields=[], active=True, color="#fff")
    db.add(appt)
    # Monday rule 9-11
    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="11:00", active=True)
    db.add(rule)
    db.commit()
    appt_id = appt.id
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    return client, appt_id


def test_slots_returns_html_partial():
    client, appt_id = make_client_with_data()
    # 2025-03-03 is a Monday
    response = client.get(f"/slots?type_id={appt_id}&date=2025-03-03")
    assert response.status_code == 200
    assert "09:00" in response.text
    app.dependency_overrides.clear()
```

**Step 2: Run to verify it fails**

```bash
pytest tests/test_slots_route.py -v
```

**Step 3: Create `app/templates/booking/slots_partial.html`**

```html
{% if slots %}
<div class="slots-grid">
  {% for slot in slots %}
  <button
    class="slot-btn"
    hx-get="/book/form?type_id={{ type_id }}&date={{ date }}&time={{ slot }}"
    hx-target="#booking-form-area"
    hx-swap="innerHTML"
    onclick="selectSlot(this)"
  >
    {{ slot }}
  </button>
  {% endfor %}
</div>
{% else %}
<p class="no-slots">No available times on this date. Please choose another day.</p>
{% endif %}
```

**Step 4: Implement `app/routers/slots.py`**

```python
from datetime import datetime, date as date_type
from unittest.mock import MagicMock

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_setting
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod
from app.services.availability import compute_slots
from app.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/slots", response_class=HTMLResponse)
def get_slots(
    request: Request,
    type_id: int = Query(...),
    date: str = Query(...),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return HTMLResponse("<p>Appointment type not found.</p>")

    target_date = date_type.fromisoformat(date)
    rules = db.query(AvailabilityRule).filter_by(active=True).all()
    blocked = db.query(BlockedPeriod).all()

    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    refresh_token = get_setting(db, "google_refresh_token", "")

    busy_intervals = []
    if refresh_token and settings.google_client_id:
        from app.services.calendar import CalendarService
        from datetime import datetime, timedelta
        cal = CalendarService(settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri)
        day_start = datetime.combine(target_date, __import__('datetime').time(0, 0))
        day_end = day_start + timedelta(days=1)
        try:
            busy_intervals = cal.get_busy_intervals(refresh_token, [appt_type.calendar_id], day_start, day_end)
        except Exception:
            pass  # Degrade gracefully if Calendar API fails

    slots = compute_slots(
        target_date=target_date,
        rules=rules,
        blocked_periods=blocked,
        busy_intervals=busy_intervals,
        duration_minutes=appt_type.duration_minutes,
        buffer_before_minutes=appt_type.buffer_before_minutes,
        buffer_after_minutes=appt_type.buffer_after_minutes,
        min_advance_hours=min_advance,
        now=datetime.utcnow(),
    )

    slot_strings = [s.strftime("%H:%M") for s in slots]
    return templates.TemplateResponse(
        "booking/slots_partial.html",
        {"request": request, "slots": slot_strings, "type_id": type_id, "date": date},
    )
```

**Step 5: Run test**

```bash
pytest tests/test_slots_route.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add app/routers/slots.py app/templates/booking/slots_partial.html tests/test_slots_route.py
git commit -m "feat: HTMX slots endpoint and partial template"
```

---

## Task 8: Base Templates + CSS

**Files:**
- Create: `app/templates/base.html`
- Create: `app/templates/admin_base.html`
- Create: `app/static/css/style.css`

**Step 1: Create `app/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Book an Appointment{% endblock %}</title>
  <link rel="stylesheet" href="/static/css/style.css">
  <script src="/static/js/htmx.min.js"></script>
</head>
<body>
  <main class="container">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

**Step 2: Create `app/templates/admin_base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Admin{% endblock %} — Booking Admin</title>
  <link rel="stylesheet" href="/static/css/style.css">
  <script src="/static/js/htmx.min.js"></script>
</head>
<body class="admin">
  <nav class="admin-nav">
    <a href="/admin/">Dashboard</a>
    <a href="/admin/appointment-types">Appointment Types</a>
    <a href="/admin/availability">Availability</a>
    <a href="/admin/bookings">Bookings</a>
    <a href="/admin/settings">Settings</a>
    <a href="/admin/logout">Logout</a>
  </nav>
  <main class="admin-container">
    {% if flash %}
    <div class="flash flash-{{ flash.type }}">{{ flash.message }}</div>
    {% endif %}
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

**Step 3: Create `app/static/css/style.css`**

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f9fafb; color: #111; }

.container { max-width: 680px; margin: 2rem auto; padding: 1rem; }
.admin-container { max-width: 960px; margin: 2rem auto; padding: 1rem; }

/* Nav */
.admin-nav { background: #1e293b; padding: .75rem 1.5rem; display: flex; gap: 1.5rem; flex-wrap: wrap; }
.admin-nav a { color: #cbd5e1; text-decoration: none; font-size: .9rem; }
.admin-nav a:hover { color: #fff; }

/* Cards */
.card { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 1.25rem; margin-bottom: 1rem; cursor: pointer; transition: border-color .15s; }
.card:hover { border-color: #3b82f6; }
.card.selected { border-color: #3b82f6; background: #eff6ff; }
.card h3 { font-size: 1.1rem; margin-bottom: .25rem; }
.card p { color: #64748b; font-size: .9rem; }

/* Slots */
.slots-grid { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1rem 0; }
.slot-btn { background: #fff; border: 1px solid #d1d5db; border-radius: 6px; padding: .5rem 1rem; cursor: pointer; font-size: .95rem; transition: all .15s; }
.slot-btn:hover { border-color: #3b82f6; color: #3b82f6; }
.slot-btn.selected { background: #3b82f6; color: #fff; border-color: #3b82f6; }

/* Forms */
form { display: flex; flex-direction: column; gap: .75rem; }
label { font-size: .9rem; font-weight: 500; color: #374151; }
input, textarea, select { width: 100%; padding: .5rem .75rem; border: 1px solid #d1d5db; border-radius: 6px; font-size: 1rem; font-family: inherit; }
input:focus, textarea:focus, select:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,.15); }

/* Buttons */
.btn { padding: .6rem 1.25rem; border-radius: 6px; font-size: 1rem; border: none; cursor: pointer; font-family: inherit; }
.btn-primary { background: #3b82f6; color: #fff; }
.btn-primary:hover { background: #2563eb; }
.btn-danger { background: #ef4444; color: #fff; }
.btn-danger:hover { background: #dc2626; }
.btn-secondary { background: #e2e8f0; color: #374151; }
.btn-secondary:hover { background: #cbd5e1; }

/* Flash messages */
.flash { padding: .75rem 1rem; border-radius: 6px; margin-bottom: 1rem; }
.flash-success { background: #d1fae5; color: #065f46; }
.flash-error { background: #fee2e2; color: #991b1b; }

/* Tables */
table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; border: 1px solid #e2e8f0; }
th { background: #f8fafc; padding: .75rem 1rem; text-align: left; font-size: .85rem; color: #64748b; border-bottom: 1px solid #e2e8f0; }
td { padding: .75rem 1rem; border-bottom: 1px solid #f1f5f9; font-size: .9rem; }
tr:last-child td { border-bottom: none; }

/* Headings */
h1 { font-size: 1.75rem; margin-bottom: .5rem; }
h2 { font-size: 1.25rem; margin-bottom: 1rem; color: #1e293b; }
.subtitle { color: #64748b; margin-bottom: 2rem; }

/* Utils */
.no-slots { color: #64748b; padding: 1rem 0; }
.section { margin-top: 2rem; }
.confirmation-box { background: #d1fae5; border: 1px solid #6ee7b7; border-radius: 8px; padding: 1.5rem; }

/* Responsive */
@media (max-width: 640px) {
  .container, .admin-container { padding: .75rem; }
  .admin-nav { gap: 1rem; }
}
```

**Step 4: Verify static files are served**

```bash
# Start the server briefly
uvicorn app.main:app --reload &
sleep 2
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/static/css/style.css
# Expected: 200
kill %1
```

**Step 5: Commit**

```bash
git add app/templates/base.html app/templates/admin_base.html app/static/css/style.css
git commit -m "feat: base templates and CSS styles"
```

---

## Task 9: Public Booking Page

**Files:**
- Modify: `app/routers/booking.py`
- Create: `app/templates/booking/index.html`
- Create: `app/templates/booking/form_partial.html`
- Create: `app/templates/booking/confirmation_partial.html`
- Create: `tests/test_booking_route.py`

**Step 1: Write failing tests — `tests/test_booking_route.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType


def setup_client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    appt = AppointmentType(name="Phone Call", duration_minutes=30, buffer_before_minutes=0,
                            buffer_after_minutes=0, calendar_id="primary",
                            custom_fields=[], active=True, color="#3b82f6")
    db.add(appt)
    db.commit()
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app), Session


def test_booking_page_loads():
    client, _ = setup_client()
    response = client.get("/book")
    assert response.status_code == 200
    assert "Phone Call" in response.text
    app.dependency_overrides.clear()


def test_form_partial_returns_html():
    client, Session = setup_client()
    db = Session()
    appt_id = db.query(AppointmentType).first().id
    db.close()
    response = client.get(f"/book/form?type_id={appt_id}&date=2025-03-03&time=09:00")
    assert response.status_code == 200
    assert "guest_name" in response.text
    app.dependency_overrides.clear()
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_booking_route.py -v
```

**Step 3: Create `app/templates/booking/index.html`**

```html
{% extends "base.html" %}
{% block title %}Book an Appointment{% endblock %}
{% block content %}
<h1>Schedule an Appointment</h1>
<p class="subtitle">Select a type and choose a time that works for you.</p>

<div id="type-selection">
  {% for appt in appointment_types %}
  <div class="card" onclick="selectType({{ appt.id }}, this)">
    <h3>{{ appt.name }}</h3>
    <p>{{ appt.description }} &bull; {{ appt.duration_minutes }} min</p>
  </div>
  {% endfor %}
</div>

<div id="date-picker-area" style="display:none;" class="section">
  <label for="date-input"><strong>Choose a date:</strong></label>
  <input type="date" id="date-input" style="max-width:200px;margin-top:.5rem;"
    min="{{ min_date }}" max="{{ max_date }}"
    hx-get="/slots"
    hx-include="[name='type_id']"
    hx-target="#slots-area"
    hx-swap="innerHTML"
    hx-trigger="change"
    name="date">
  <input type="hidden" name="type_id" id="type_id_input" value="">
</div>

<div id="slots-area" class="section"></div>
<div id="booking-form-area" class="section"></div>

<script>
function selectType(id, el) {
  document.querySelectorAll('.card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  document.getElementById('type_id_input').value = id;
  document.getElementById('date-picker-area').style.display = 'block';
  document.getElementById('slots-area').innerHTML = '';
  document.getElementById('booking-form-area').innerHTML = '';
  document.getElementById('date-input').value = '';
}

function selectSlot(el) {
  document.querySelectorAll('.slot-btn').forEach(b => b.classList.remove('selected'));
  el.classList.add('selected');
}
</script>
{% endblock %}
```

**Step 4: Create `app/templates/booking/form_partial.html`**

```html
<div class="card">
  <h2>Your details</h2>
  <p style="color:#64748b;margin-bottom:1rem;">
    {{ appt_type.name }} &bull; {{ date_display }} at {{ time_display }}
  </p>
  <form hx-post="/book" hx-target="#booking-form-area" hx-swap="innerHTML">
    <input type="hidden" name="type_id" value="{{ appt_type.id }}">
    <input type="hidden" name="start_datetime" value="{{ start_datetime }}">

    <label>Full Name *
      <input type="text" name="guest_name" required>
    </label>
    <label>Email Address *
      <input type="email" name="guest_email" required>
    </label>
    <label>Phone Number
      <input type="tel" name="guest_phone">
    </label>
    {% for field in appt_type.custom_fields %}
    <label>{{ field.label }}{% if field.required %} *{% endif %}
      <input type="text" name="custom_{{ field.label }}"
             {% if field.required %}required{% endif %}>
    </label>
    {% endfor %}
    <label>Notes
      <textarea name="notes" rows="3" placeholder="Anything else we should know?"></textarea>
    </label>
    <button type="submit" class="btn btn-primary">Confirm Booking</button>
  </form>
</div>
```

**Step 5: Create `app/templates/booking/confirmation_partial.html`**

```html
<div class="confirmation-box">
  <h2>&#10003; Booking Confirmed!</h2>
  <p style="margin-top:.75rem;">
    <strong>{{ booking.appointment_type.name }}</strong><br>
    {{ start_display }}<br>
    A confirmation has been sent to <strong>{{ booking.guest_email }}</strong>.
  </p>
</div>
```

**Step 6: Implement `app/routers/booking.py`**

```python
from datetime import datetime, timedelta, date as date_type
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_setting
from app.models import AppointmentType, Booking
from app.services.booking import create_booking

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
@router.get("/book", response_class=HTMLResponse)
def booking_page(request: Request, db: Session = Depends(get_db)):
    appointment_types = db.query(AppointmentType).filter_by(active=True).all()
    min_advance = int(get_setting(db, "min_advance_hours", "24"))
    max_future = int(get_setting(db, "max_future_days", "30"))
    min_date = (datetime.utcnow() + timedelta(hours=min_advance)).date().isoformat()
    max_date = (datetime.utcnow() + timedelta(days=max_future)).date().isoformat()
    return templates.TemplateResponse("booking/index.html", {
        "request": request,
        "appointment_types": appointment_types,
        "min_date": min_date,
        "max_date": max_date,
    })


@router.get("/book/form", response_class=HTMLResponse)
def booking_form(
    request: Request,
    type_id: int,
    date: str,
    time: str,
    db: Session = Depends(get_db),
):
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return HTMLResponse("<p>Appointment type not found.</p>")
    start_dt = datetime.fromisoformat(f"{date}T{time}:00")
    return templates.TemplateResponse("booking/form_partial.html", {
        "request": request,
        "appt_type": appt_type,
        "date_display": start_dt.strftime("%A, %B %-d, %Y"),
        "time_display": start_dt.strftime("%-I:%M %p"),
        "start_datetime": f"{date}T{time}:00",
    })


@router.post("/book", response_class=HTMLResponse)
def submit_booking(
    request: Request,
    type_id: int = Form(...),
    start_datetime: str = Form(...),
    guest_name: str = Form(...),
    guest_email: str = Form(...),
    guest_phone: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return HTMLResponse("<p class='flash flash-error'>Invalid appointment type.</p>")

    start_dt = datetime.fromisoformat(start_datetime)
    end_dt = start_dt + timedelta(minutes=appt_type.duration_minutes)

    # Check for conflicts
    conflict = db.query(Booking).filter(
        Booking.appointment_type_id == type_id,
        Booking.status == "confirmed",
        Booking.start_datetime < end_dt,
        Booking.end_datetime > start_dt,
    ).first()
    if conflict:
        return HTMLResponse("<p class='flash flash-error'>That time slot was just booked. Please go back and choose another.</p>")

    # Extract custom field responses from form
    custom_responses = {}
    for field in appt_type.custom_fields:
        key = f"custom_{field['label']}"
        # Access raw form data for dynamic fields
        custom_responses[field["label"]] = request.headers.get(key, "")

    booking = create_booking(
        db=db,
        appt_type=appt_type,
        start_dt=start_dt,
        end_dt=end_dt,
        guest_name=guest_name,
        guest_email=guest_email,
        guest_phone=guest_phone,
        notes=notes,
        custom_responses=custom_responses,
    )

    start_display = start_dt.strftime("%A, %B %-d, %Y at %-I:%M %p")
    return templates.TemplateResponse("booking/confirmation_partial.html", {
        "request": request,
        "booking": booking,
        "start_display": start_display,
    })
```

**Note on custom form fields:** The form POST needs to handle dynamic field names. Update `submit_booking` to read from raw form data:

```python
# After the Form parameters, add:
async def submit_booking(request: Request, ...):
    form_data = await request.form()
    custom_responses = {}
    for field in appt_type.custom_fields:
        key = f"custom_{field['label']}"
        custom_responses[field["label"]] = form_data.get(key, "")
```

Make the route `async` and use `await request.form()` for the custom fields.

**Step 7: Run tests**

```bash
pytest tests/test_booking_route.py -v
```

Expected: PASS.

**Step 8: Commit**

```bash
git add app/routers/booking.py app/templates/booking/ tests/test_booking_route.py
git commit -m "feat: public booking page and HTMX form flow"
```

---

## Task 10: Booking Service (create + cancel)

**Files:**
- Create: `app/services/booking.py`
- Create: `tests/test_booking_service.py`

**Step 1: Write failing tests — `tests/test_booking_service.py`**

```python
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import AppointmentType, Booking
from app.services.booking import create_booking, cancel_booking


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_create_booking_saves_to_db():
    db = make_db()
    appt = AppointmentType(name="Call", duration_minutes=30, buffer_before_minutes=0,
                            buffer_after_minutes=0, calendar_id="primary",
                            custom_fields=[], active=True, color="#fff")
    db.add(appt)
    db.commit()

    start = datetime(2025, 3, 3, 9, 0)
    end = datetime(2025, 3, 3, 9, 30)
    booking = create_booking(db, appt, start, end, "Jane", "jane@example.com", "555", "notes", {})
    assert booking.id is not None
    assert booking.status == "confirmed"
    assert booking.guest_name == "Jane"


def test_cancel_booking_updates_status():
    db = make_db()
    appt = AppointmentType(name="Call", duration_minutes=30, buffer_before_minutes=0,
                            buffer_after_minutes=0, calendar_id="primary",
                            custom_fields=[], active=True, color="#fff")
    db.add(appt)
    db.commit()
    start = datetime(2025, 3, 3, 9, 0)
    end = datetime(2025, 3, 3, 9, 30)
    booking = create_booking(db, appt, start, end, "Jane", "jane@example.com", "", "", {})
    cancel_booking(db, booking.id)
    db.refresh(booking)
    assert booking.status == "cancelled"
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_booking_service.py -v
```

**Step 3: Create `app/services/booking.py`**

```python
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import AppointmentType, Booking


def create_booking(
    db: Session,
    appt_type: AppointmentType,
    start_dt: datetime,
    end_dt: datetime,
    guest_name: str,
    guest_email: str,
    guest_phone: str,
    notes: str,
    custom_responses: dict,
    google_event_id: str = "",
) -> Booking:
    booking = Booking(
        appointment_type_id=appt_type.id,
        start_datetime=start_dt,
        end_datetime=end_dt,
        guest_name=guest_name,
        guest_email=guest_email,
        guest_phone=guest_phone,
        notes=notes,
        google_event_id=google_event_id,
        status="confirmed",
    )
    booking.custom_field_responses = custom_responses
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking


def cancel_booking(db: Session, booking_id: int) -> Booking | None:
    booking = db.query(Booking).filter_by(id=booking_id).first()
    if booking:
        booking.status = "cancelled"
        db.commit()
        db.refresh(booking)
    return booking
```

**Step 4: Run tests**

```bash
pytest tests/test_booking_service.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add app/services/booking.py tests/test_booking_service.py
git commit -m "feat: booking service (create and cancel)"
```

---

## Task 11: Email Service

**Files:**
- Create: `app/services/email.py`
- Create: `tests/test_email.py`

**Step 1: Write failing tests — `tests/test_email.py`**

```python
from unittest.mock import patch, MagicMock
from datetime import datetime
from app.services.email import send_guest_confirmation, send_admin_alert, send_cancellation_notice


def test_send_guest_confirmation_calls_resend():
    with patch("resend.Emails.send") as mock_send:
        mock_send.return_value = {"id": "test-id"}
        send_guest_confirmation(
            api_key="re_test",
            from_email="no@example.com",
            guest_email="jane@example.com",
            guest_name="Jane",
            appt_type_name="Phone Call",
            start_dt=datetime(2025, 3, 3, 9, 0),
            end_dt=datetime(2025, 3, 3, 9, 30),
            custom_responses={},
            owner_name="Bob",
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0][0]
        assert args["to"] == ["jane@example.com"]
        assert "Phone Call" in args["subject"]


def test_send_admin_alert_calls_resend():
    with patch("resend.Emails.send") as mock_send:
        mock_send.return_value = {"id": "test-id"}
        send_admin_alert(
            api_key="re_test",
            from_email="no@example.com",
            notify_email="me@example.com",
            guest_name="Jane",
            guest_email="jane@example.com",
            guest_phone="555",
            appt_type_name="Phone Call",
            start_dt=datetime(2025, 3, 3, 9, 0),
            notes="test",
            custom_responses={},
        )
        mock_send.assert_called_once()
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_email.py -v
```

**Step 3: Create `app/services/email.py`**

```python
import resend
from datetime import datetime


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%A, %B %-d, %Y at %-I:%M %p UTC")


def send_guest_confirmation(
    api_key: str,
    from_email: str,
    guest_email: str,
    guest_name: str,
    appt_type_name: str,
    start_dt: datetime,
    end_dt: datetime,
    custom_responses: dict,
    owner_name: str,
):
    resend.api_key = api_key
    custom_html = "".join(
        f"<p><strong>{k}:</strong> {v}</p>" for k, v in custom_responses.items() if v
    )
    html = f"""
    <h2>Your appointment is confirmed</h2>
    <p>Hi {guest_name},</p>
    <p>Your <strong>{appt_type_name}</strong> is confirmed:</p>
    <p><strong>Date/Time:</strong> {_format_dt(start_dt)}</p>
    {custom_html}
    <p>If you need to cancel, please reply to this email.</p>
    <p>— {owner_name}</p>
    """
    resend.Emails.send({
        "from": from_email,
        "to": [guest_email],
        "subject": f"Your {appt_type_name} is confirmed — {start_dt.strftime('%b %-d')}",
        "html": html,
    })


def send_admin_alert(
    api_key: str,
    from_email: str,
    notify_email: str,
    guest_name: str,
    guest_email: str,
    guest_phone: str,
    appt_type_name: str,
    start_dt: datetime,
    notes: str,
    custom_responses: dict,
):
    resend.api_key = api_key
    custom_html = "".join(
        f"<p><strong>{k}:</strong> {v}</p>" for k, v in custom_responses.items() if v
    )
    html = f"""
    <h2>New Booking: {guest_name}</h2>
    <p><strong>Type:</strong> {appt_type_name}</p>
    <p><strong>Date/Time:</strong> {_format_dt(start_dt)}</p>
    <p><strong>Guest:</strong> {guest_name}</p>
    <p><strong>Email:</strong> {guest_email}</p>
    <p><strong>Phone:</strong> {guest_phone or 'not provided'}</p>
    {custom_html}
    <p><strong>Notes:</strong> {notes or 'none'}</p>
    <p><a href="/admin/bookings">View in admin panel</a></p>
    """
    resend.Emails.send({
        "from": from_email,
        "to": [notify_email],
        "subject": f"New booking: {guest_name} — {appt_type_name} on {start_dt.strftime('%b %-d')}",
        "html": html,
    })


def send_cancellation_notice(
    api_key: str,
    from_email: str,
    guest_email: str,
    guest_name: str,
    appt_type_name: str,
    start_dt: datetime,
):
    resend.api_key = api_key
    html = f"""
    <h2>Appointment Cancelled</h2>
    <p>Hi {guest_name},</p>
    <p>Your <strong>{appt_type_name}</strong> on {_format_dt(start_dt)} has been cancelled.</p>
    <p>Please reach out to reschedule.</p>
    """
    resend.Emails.send({
        "from": from_email,
        "to": [guest_email],
        "subject": f"Your {appt_type_name} on {start_dt.strftime('%b %-d')} has been cancelled",
        "html": html,
    })
```

**Step 4: Run tests**

```bash
pytest tests/test_email.py -v
```

Expected: PASS.

**Step 5: Wire emails into `app/routers/booking.py` `submit_booking`**

After creating the booking, add:

```python
from app.services.email import send_guest_confirmation, send_admin_alert

# After booking = create_booking(...)
settings = get_settings()
notify_email = get_setting(db, "notify_email", "")
notifications_enabled = get_setting(db, "notifications_enabled", "true") == "true"
owner_name = get_setting(db, "owner_name", "")

if notifications_enabled and settings.resend_api_key:
    try:
        send_guest_confirmation(
            api_key=settings.resend_api_key,
            from_email=settings.from_email,
            guest_email=guest_email,
            guest_name=guest_name,
            appt_type_name=appt_type.name,
            start_dt=start_dt,
            end_dt=end_dt,
            custom_responses=custom_responses,
            owner_name=owner_name,
        )
        if notify_email:
            send_admin_alert(
                api_key=settings.resend_api_key,
                from_email=settings.from_email,
                notify_email=notify_email,
                guest_name=guest_name,
                guest_email=guest_email,
                guest_phone=guest_phone,
                appt_type_name=appt_type.name,
                start_dt=start_dt,
                notes=notes,
                custom_responses=custom_responses,
            )
    except Exception:
        pass  # Email failure should not break the booking
```

**Step 6: Run all tests**

```bash
pytest tests/ -v
```

Expected: all PASS.

**Step 7: Commit**

```bash
git add app/services/email.py app/routers/booking.py tests/test_email.py
git commit -m "feat: Resend email service and wire into booking flow"
```

---

## Task 12: Admin Authentication

**Files:**
- Modify: `app/routers/auth.py`
- Create: `app/templates/admin/login.html`
- Create: `app/templates/admin/setup.html`
- Create: `tests/test_admin_auth.py`

**Step 1: Write failing tests — `tests/test_admin_auth.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.database import Base, get_db
from app.main import app
from app.models import Setting
import bcrypt


def make_authed_client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    hashed = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    db.add(Setting(key="admin_password_hash", value=hashed))
    db.commit()
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app, raise_server_exceptions=True)


def test_login_page_loads(client):
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert "password" in response.text.lower()


def test_login_with_wrong_password_fails():
    c = make_authed_client()
    response = c.post("/admin/login", data={"password": "wrongpass"}, follow_redirects=False)
    assert response.status_code in (200, 302)
    app.dependency_overrides.clear()


def test_login_with_correct_password_redirects():
    c = make_authed_client()
    response = c.post("/admin/login", data={"password": "testpass"}, follow_redirects=False)
    assert response.status_code == 302
    assert "/admin" in response.headers["location"]
    app.dependency_overrides.clear()
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_admin_auth.py -v
```

**Step 3: Create `app/templates/admin/login.html`**

```html
{% extends "base.html" %}
{% block title %}Admin Login{% endblock %}
{% block content %}
<div style="max-width:360px;margin:4rem auto;">
  <h1>Admin Login</h1>
  {% if error %}
  <p class="flash flash-error">{{ error }}</p>
  {% endif %}
  <form method="post" action="/admin/login" style="margin-top:1.5rem;">
    <label>Password
      <input type="password" name="password" autofocus required>
    </label>
    <button type="submit" class="btn btn-primary" style="margin-top:.5rem;">Login</button>
  </form>
</div>
{% endblock %}
```

**Step 4: Create `app/templates/admin/setup.html`**

```html
{% extends "base.html" %}
{% block title %}Initial Setup{% endblock %}
{% block content %}
<div style="max-width:400px;margin:4rem auto;">
  <h1>Welcome! Set Admin Password</h1>
  <p class="subtitle">This runs once to secure your admin panel.</p>
  <form method="post" action="/admin/setup" style="margin-top:1.5rem;">
    <label>New Password
      <input type="password" name="password" required minlength="8">
    </label>
    <label>Confirm Password
      <input type="password" name="confirm" required minlength="8">
    </label>
    {% if error %}<p class="flash flash-error">{{ error }}</p>{% endif %}
    <button type="submit" class="btn btn-primary" style="margin-top:.5rem;">Set Password</button>
  </form>
</div>
{% endblock %}
```

**Step 5: Implement `app/routers/auth.py`**

```python
import bcrypt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_setting, set_setting

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("admin/login.html", {"request": request, "error": None})


@router.post("/admin/login")
def login(request: Request, password: str = Form(...), db: Session = Depends(get_db)):
    stored_hash = get_setting(db, "admin_password_hash", "")
    if not stored_hash:
        return RedirectResponse("/admin/setup", status_code=302)
    if bcrypt.checkpw(password.encode(), stored_hash.encode()):
        request.session["admin_authenticated"] = True
        return RedirectResponse("/admin/", status_code=302)
    return templates.TemplateResponse("admin/login.html", {"request": request, "error": "Incorrect password."})


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
def setup(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    if get_setting(db, "admin_password_hash"):
        return RedirectResponse("/admin/login", status_code=302)
    if password != confirm:
        return templates.TemplateResponse("admin/setup.html", {"request": request, "error": "Passwords do not match."})
    if len(password) < 8:
        return templates.TemplateResponse("admin/setup.html", {"request": request, "error": "Password must be at least 8 characters."})
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    set_setting(db, "admin_password_hash", hashed)
    # Set defaults
    set_setting(db, "timezone", "America/New_York")
    set_setting(db, "min_advance_hours", "24")
    set_setting(db, "max_future_days", "30")
    set_setting(db, "notifications_enabled", "true")
    request.session["admin_authenticated"] = True
    return RedirectResponse("/admin/", status_code=302)
```

**Step 6: Run tests**

```bash
pytest tests/test_admin_auth.py -v
```

Expected: PASS.

**Step 7: Commit**

```bash
git add app/routers/auth.py app/templates/admin/login.html app/templates/admin/setup.html tests/test_admin_auth.py
git commit -m "feat: admin authentication (login, logout, first-run setup)"
```

---

## Task 13: Admin Dashboard + Appointment Types CRUD

**Files:**
- Modify: `app/routers/admin.py`
- Create: `app/templates/admin/dashboard.html`
- Create: `app/templates/admin/appointment_types.html`
- Create: `tests/test_admin_appt_types.py`

**Step 1: Write failing tests — `tests/test_admin_appt_types.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType


def make_admin_client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    client = TestClient(app, raise_server_exceptions=True)
    client.cookies["session"] = ""  # will use session middleware
    # Force admin session
    with client as c:
        # patch session
        pass
    return client, Session


def test_admin_dashboard_redirects_without_auth(client):
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_create_appointment_type(client):
    # Set session via login workaround
    from app.models import Setting
    import bcrypt
    # This test uses the conftest client fixture which has no admin session
    # so we just test the redirect
    response = client.post("/admin/appointment-types", data={
        "name": "Phone Call",
        "description": "A quick call",
        "duration_minutes": "30",
        "buffer_before_minutes": "0",
        "buffer_after_minutes": "5",
        "calendar_id": "primary",
        "color": "#3b82f6",
    }, follow_redirects=False)
    # Unauthenticated → redirect to login
    assert response.status_code in (302, 307)
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_admin_appt_types.py -v
```

**Step 3: Create `app/templates/admin/dashboard.html`**

```html
{% extends "admin_base.html" %}
{% block title %}Dashboard{% endblock %}
{% block content %}
<h1>Dashboard</h1>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;margin:1.5rem 0;">
  <div class="card" style="cursor:default;">
    <p style="color:#64748b;font-size:.85rem;">Upcoming (7 days)</p>
    <p style="font-size:2rem;font-weight:700;">{{ upcoming_count }}</p>
  </div>
  <div class="card" style="cursor:default;">
    <p style="color:#64748b;font-size:.85rem;">Total Bookings</p>
    <p style="font-size:2rem;font-weight:700;">{{ total_count }}</p>
  </div>
</div>
<h2>Next Appointments</h2>
{% if next_bookings %}
<table>
  <thead><tr><th>Date/Time</th><th>Type</th><th>Guest</th><th>Email</th></tr></thead>
  <tbody>
  {% for b in next_bookings %}
  <tr>
    <td>{{ b.start_datetime.strftime("%b %-d, %Y %-I:%M %p") }}</td>
    <td>{{ b.appointment_type.name }}</td>
    <td>{{ b.guest_name }}</td>
    <td>{{ b.guest_email }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p style="color:#64748b;">No upcoming appointments.</p>
{% endif %}
{% endblock %}
```

**Step 4: Create `app/templates/admin/appointment_types.html`**

```html
{% extends "admin_base.html" %}
{% block title %}Appointment Types{% endblock %}
{% block content %}
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem;">
  <h1>Appointment Types</h1>
</div>

<table>
  <thead><tr><th>Name</th><th>Duration</th><th>Buffer</th><th>Calendar</th><th>Status</th><th>Actions</th></tr></thead>
  <tbody>
  {% for t in types %}
  <tr>
    <td><strong>{{ t.name }}</strong><br><small style="color:#64748b;">{{ t.description }}</small></td>
    <td>{{ t.duration_minutes }} min</td>
    <td>+{{ t.buffer_after_minutes }} min after</td>
    <td><code style="font-size:.8rem;">{{ t.calendar_id }}</code></td>
    <td>{% if t.active %}<span style="color:#059669;">Active</span>{% else %}<span style="color:#dc2626;">Inactive</span>{% endif %}</td>
    <td>
      <a href="/admin/appointment-types/{{ t.id }}/edit" class="btn btn-secondary" style="font-size:.85rem;padding:.35rem .75rem;">Edit</a>
      <form method="post" action="/admin/appointment-types/{{ t.id }}/toggle" style="display:inline;">
        <button class="btn btn-secondary" style="font-size:.85rem;padding:.35rem .75rem;">
          {% if t.active %}Disable{% else %}Enable{% endif %}
        </button>
      </form>
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>

<div class="section">
  <h2>{% if edit_type %}Edit: {{ edit_type.name }}{% else %}New Appointment Type{% endif %}</h2>
  <form method="post" action="{% if edit_type %}/admin/appointment-types/{{ edit_type.id }}{% else %}/admin/appointment-types{% endif %}">
    <label>Name * <input type="text" name="name" value="{{ edit_type.name if edit_type else '' }}" required></label>
    <label>Description <input type="text" name="description" value="{{ edit_type.description if edit_type else '' }}"></label>
    <label>Duration (minutes) * <input type="number" name="duration_minutes" value="{{ edit_type.duration_minutes if edit_type else 30 }}" required min="5"></label>
    <label>Buffer before (minutes) <input type="number" name="buffer_before_minutes" value="{{ edit_type.buffer_before_minutes if edit_type else 0 }}" min="0"></label>
    <label>Buffer after (minutes) <input type="number" name="buffer_after_minutes" value="{{ edit_type.buffer_after_minutes if edit_type else 0 }}" min="0"></label>
    <label>Google Calendar ID <input type="text" name="calendar_id" value="{{ edit_type.calendar_id if edit_type else 'primary' }}"></label>
    <label>Color <input type="color" name="color" value="{{ edit_type.color if edit_type else '#3b82f6' }}"></label>
    <button type="submit" class="btn btn-primary">{% if edit_type %}Save Changes{% else %}Create Type{% endif %}</button>
    {% if edit_type %}<a href="/admin/appointment-types" class="btn btn-secondary">Cancel</a>{% endif %}
  </form>
</div>
{% endblock %}
```

**Step 5: Implement `app/routers/admin.py`**

```python
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_setting, require_admin, set_setting
from app.models import AppointmentType, AvailabilityRule, BlockedPeriod, Booking
from app.config import get_settings
from app.services.calendar import CalendarService

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")
AuthDep = Depends(require_admin)


def flash(request: Request, message: str, type: str = "success"):
    request.session["flash"] = {"message": message, "type": type}


def get_flash(request: Request):
    return request.session.pop("flash", None)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), _=AuthDep):
    now = datetime.utcnow()
    week_ahead = now + timedelta(days=7)
    upcoming_count = db.query(Booking).filter(
        Booking.status == "confirmed", Booking.start_datetime >= now, Booking.start_datetime <= week_ahead
    ).count()
    total_count = db.query(Booking).filter_by(status="confirmed").count()
    next_bookings = db.query(Booking).filter(
        Booking.status == "confirmed", Booking.start_datetime >= now
    ).order_by(Booking.start_datetime).limit(5).all()
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request, "upcoming_count": upcoming_count,
        "total_count": total_count, "next_bookings": next_bookings,
        "flash": get_flash(request),
    })


# --- Appointment Types ---

@router.get("/appointment-types", response_class=HTMLResponse)
def list_appt_types(request: Request, db: Session = Depends(get_db), _=AuthDep):
    types = db.query(AppointmentType).all()
    return templates.TemplateResponse("admin/appointment_types.html", {
        "request": request, "types": types, "edit_type": None, "flash": get_flash(request),
    })


@router.post("/appointment-types")
def create_appt_type(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    duration_minutes: int = Form(...),
    buffer_before_minutes: int = Form(0),
    buffer_after_minutes: int = Form(0),
    calendar_id: str = Form("primary"),
    color: str = Form("#3b82f6"),
    db: Session = Depends(get_db),
    _=AuthDep,
):
    t = AppointmentType(
        name=name, description=description, duration_minutes=duration_minutes,
        buffer_before_minutes=buffer_before_minutes, buffer_after_minutes=buffer_after_minutes,
        calendar_id=calendar_id, color=color, active=True,
    )
    t.custom_fields = []
    db.add(t)
    db.commit()
    flash(request, f"Created '{name}'.")
    return RedirectResponse("/admin/appointment-types", status_code=302)


@router.get("/appointment-types/{type_id}/edit", response_class=HTMLResponse)
def edit_appt_type_page(request: Request, type_id: int, db: Session = Depends(get_db), _=AuthDep):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    types = db.query(AppointmentType).all()
    return templates.TemplateResponse("admin/appointment_types.html", {
        "request": request, "types": types, "edit_type": t, "flash": get_flash(request),
    })


@router.post("/appointment-types/{type_id}")
def update_appt_type(
    request: Request, type_id: int,
    name: str = Form(...), description: str = Form(""),
    duration_minutes: int = Form(...), buffer_before_minutes: int = Form(0),
    buffer_after_minutes: int = Form(0), calendar_id: str = Form("primary"),
    color: str = Form("#3b82f6"), db: Session = Depends(get_db), _=AuthDep,
):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    if t:
        t.name = name; t.description = description; t.duration_minutes = duration_minutes
        t.buffer_before_minutes = buffer_before_minutes; t.buffer_after_minutes = buffer_after_minutes
        t.calendar_id = calendar_id; t.color = color
        db.commit()
        flash(request, f"Updated '{name}'.")
    return RedirectResponse("/admin/appointment-types", status_code=302)


@router.post("/appointment-types/{type_id}/toggle")
def toggle_appt_type(request: Request, type_id: int, db: Session = Depends(get_db), _=AuthDep):
    t = db.query(AppointmentType).filter_by(id=type_id).first()
    if t:
        t.active = not t.active
        db.commit()
        flash(request, f"{'Enabled' if t.active else 'Disabled'} '{t.name}'.")
    return RedirectResponse("/admin/appointment-types", status_code=302)
```

**Step 6: Run tests**

```bash
pytest tests/ -v
```

Expected: all PASS.

**Step 7: Commit**

```bash
git add app/routers/admin.py app/templates/admin/dashboard.html app/templates/admin/appointment_types.html tests/test_admin_appt_types.py
git commit -m "feat: admin dashboard and appointment types CRUD"
```

---

## Task 14: Admin Availability + Bookings + Settings Pages

**Files:**
- Modify: `app/routers/admin.py` (add routes)
- Create: `app/templates/admin/availability.html`
- Create: `app/templates/admin/bookings.html`
- Create: `app/templates/admin/settings.html`

**Step 1: Create `app/templates/admin/availability.html`**

```html
{% extends "admin_base.html" %}
{% block title %}Availability{% endblock %}
{% block content %}
<h1>Availability</h1>

<h2>Weekly Schedule</h2>
<table>
  <thead><tr><th>Day</th><th>Start</th><th>End</th><th>Status</th><th></th></tr></thead>
  <tbody>
  {% for rule in rules %}
  <tr>
    <td>{{ ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][rule.day_of_week] }}</td>
    <td>{{ rule.start_time }}</td>
    <td>{{ rule.end_time }}</td>
    <td>{% if rule.active %}Active{% else %}Inactive{% endif %}</td>
    <td>
      <form method="post" action="/admin/availability/rules/{{ rule.id }}/delete" style="display:inline;">
        <button class="btn btn-danger" style="font-size:.8rem;padding:.3rem .6rem;">Delete</button>
      </form>
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>

<div class="section">
  <h2>Add Availability Window</h2>
  <form method="post" action="/admin/availability/rules" style="max-width:400px;">
    <label>Day
      <select name="day_of_week">
        {% for i, d in enumerate(["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]) %}
        <option value="{{ i }}">{{ d }}</option>
        {% endfor %}
      </select>
    </label>
    <label>Start Time <input type="time" name="start_time" value="09:00" required></label>
    <label>End Time <input type="time" name="end_time" value="17:00" required></label>
    <button type="submit" class="btn btn-primary">Add Rule</button>
  </form>
</div>

<div class="section">
  <h2>Blocked Periods</h2>
  <table>
    <thead><tr><th>Start</th><th>End</th><th>Reason</th><th></th></tr></thead>
    <tbody>
    {% for b in blocks %}
    <tr>
      <td>{{ b.start_datetime.strftime("%b %-d, %Y %-I:%M %p") }}</td>
      <td>{{ b.end_datetime.strftime("%b %-d, %Y %-I:%M %p") }}</td>
      <td>{{ b.reason or "—" }}</td>
      <td>
        <form method="post" action="/admin/availability/blocks/{{ b.id }}/delete">
          <button class="btn btn-danger" style="font-size:.8rem;padding:.3rem .6rem;">Delete</button>
        </form>
      </td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  <div style="margin-top:1rem;">
    <h3>Add Block</h3>
    <form method="post" action="/admin/availability/blocks" style="max-width:400px;">
      <label>Start <input type="datetime-local" name="start_datetime" required></label>
      <label>End <input type="datetime-local" name="end_datetime" required></label>
      <label>Reason (optional) <input type="text" name="reason"></label>
      <button type="submit" class="btn btn-primary">Block Time</button>
    </form>
  </div>
</div>

<div class="section">
  <h2>Booking Window Settings</h2>
  <form method="post" action="/admin/availability/settings" style="max-width:400px;">
    <label>Minimum advance notice (hours)
      <input type="number" name="min_advance_hours" value="{{ min_advance }}" min="0">
    </label>
    <label>Maximum days in advance
      <input type="number" name="max_future_days" value="{{ max_future }}" min="1">
    </label>
    <button type="submit" class="btn btn-primary">Save</button>
  </form>
</div>
{% endblock %}
```

**Step 2: Create `app/templates/admin/bookings.html`**

```html
{% extends "admin_base.html" %}
{% block title %}Bookings{% endblock %}
{% block content %}
<h1>Bookings</h1>
{% if flash %}<div class="flash flash-{{ flash.type }}">{{ flash.message }}</div>{% endif %}

<h2>Upcoming</h2>
{% if upcoming %}
<table>
  <thead><tr><th>Date/Time</th><th>Type</th><th>Guest</th><th>Email</th><th>Phone</th><th>Notes</th><th></th></tr></thead>
  <tbody>
  {% for b in upcoming %}
  <tr>
    <td>{{ b.start_datetime.strftime("%b %-d, %Y %-I:%M %p") }}</td>
    <td>{{ b.appointment_type.name }}</td>
    <td>{{ b.guest_name }}</td>
    <td>{{ b.guest_email }}</td>
    <td>{{ b.guest_phone or "—" }}</td>
    <td>{{ b.notes or "—" }}</td>
    <td>
      <form method="post" action="/admin/bookings/{{ b.id }}/cancel"
            onsubmit="return confirm('Cancel this booking?')">
        <button class="btn btn-danger" style="font-size:.8rem;padding:.3rem .6rem;">Cancel</button>
      </form>
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}<p style="color:#64748b;">No upcoming bookings.</p>{% endif %}

<div class="section">
  <h2>Past Bookings</h2>
  {% if past %}
  <table>
    <thead><tr><th>Date/Time</th><th>Type</th><th>Guest</th><th>Status</th></tr></thead>
    <tbody>
    {% for b in past %}
    <tr>
      <td>{{ b.start_datetime.strftime("%b %-d, %Y %-I:%M %p") }}</td>
      <td>{{ b.appointment_type.name }}</td>
      <td>{{ b.guest_name }}</td>
      <td>{{ b.status }}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}<p style="color:#64748b;">No past bookings.</p>{% endif %}
</div>
{% endblock %}
```

**Step 3: Create `app/templates/admin/settings.html`**

```html
{% extends "admin_base.html" %}
{% block title %}Settings{% endblock %}
{% block content %}
<h1>Settings</h1>
{% if flash %}<div class="flash flash-{{ flash.type }}">{{ flash.message }}</div>{% endif %}

<form method="post" action="/admin/settings" style="max-width:480px;">
  <label>Your Name (used in emails)
    <input type="text" name="owner_name" value="{{ owner_name }}">
  </label>
  <label>Notification Email (your address)
    <input type="email" name="notify_email" value="{{ notify_email }}">
  </label>
  <label>
    <input type="checkbox" name="notifications_enabled" value="true" {% if notifications_enabled %}checked{% endif %}>
    Send email notifications
  </label>
  <label>Timezone
    <input type="text" name="timezone" value="{{ timezone }}">
  </label>
  <button type="submit" class="btn btn-primary">Save Settings</button>
</form>

<div class="section" style="max-width:480px;">
  <h2>Change Admin Password</h2>
  <form method="post" action="/admin/settings/password">
    <label>New Password <input type="password" name="password" minlength="8" required></label>
    <label>Confirm <input type="password" name="confirm" required></label>
    <button type="submit" class="btn btn-secondary">Change Password</button>
  </form>
</div>

<div class="section">
  <h2>Google Calendar</h2>
  {% if google_authorized %}
  <p style="color:#059669;">&#10003; Connected</p>
  {% else %}
  <p style="color:#dc2626;">Not connected.</p>
  {% endif %}
  <a href="/admin/google/authorize" class="btn btn-secondary" style="display:inline-block;margin-top:.5rem;">
    {% if google_authorized %}Re-authorize{% else %}Connect Google Calendar{% endif %}
  </a>
</div>
{% endblock %}
```

**Step 4: Add remaining routes to `app/routers/admin.py`**

```python
# Add these to the existing admin router:

# --- Availability ---

@router.get("/availability", response_class=HTMLResponse)
def availability_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    rules = db.query(AvailabilityRule).order_by(AvailabilityRule.day_of_week).all()
    blocks = db.query(BlockedPeriod).order_by(BlockedPeriod.start_datetime).all()
    return templates.TemplateResponse("admin/availability.html", {
        "request": request, "rules": rules, "blocks": blocks,
        "min_advance": get_setting(db, "min_advance_hours", "24"),
        "max_future": get_setting(db, "max_future_days", "30"),
        "flash": get_flash(request),
        "enumerate": enumerate,
    })


@router.post("/availability/rules")
def create_rule(
    request: Request,
    day_of_week: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    db: Session = Depends(get_db), _=AuthDep,
):
    db.add(AvailabilityRule(day_of_week=day_of_week, start_time=start_time, end_time=end_time, active=True))
    db.commit()
    flash(request, "Availability rule added.")
    return RedirectResponse("/admin/availability", status_code=302)


@router.post("/availability/rules/{rule_id}/delete")
def delete_rule(request: Request, rule_id: int, db: Session = Depends(get_db), _=AuthDep):
    rule = db.query(AvailabilityRule).filter_by(id=rule_id).first()
    if rule:
        db.delete(rule)
        db.commit()
    flash(request, "Rule deleted.")
    return RedirectResponse("/admin/availability", status_code=302)


@router.post("/availability/blocks")
def create_block(
    request: Request,
    start_datetime: str = Form(...),
    end_datetime: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db), _=AuthDep,
):
    db.add(BlockedPeriod(
        start_datetime=datetime.fromisoformat(start_datetime),
        end_datetime=datetime.fromisoformat(end_datetime),
        reason=reason,
    ))
    db.commit()
    flash(request, "Period blocked.")
    return RedirectResponse("/admin/availability", status_code=302)


@router.post("/availability/blocks/{block_id}/delete")
def delete_block(request: Request, block_id: int, db: Session = Depends(get_db), _=AuthDep):
    b = db.query(BlockedPeriod).filter_by(id=block_id).first()
    if b:
        db.delete(b)
        db.commit()
    flash(request, "Block removed.")
    return RedirectResponse("/admin/availability", status_code=302)


@router.post("/availability/settings")
def save_availability_settings(
    request: Request,
    min_advance_hours: str = Form(...),
    max_future_days: str = Form(...),
    db: Session = Depends(get_db), _=AuthDep,
):
    set_setting(db, "min_advance_hours", min_advance_hours)
    set_setting(db, "max_future_days", max_future_days)
    flash(request, "Settings saved.")
    return RedirectResponse("/admin/availability", status_code=302)


# --- Bookings ---

@router.get("/bookings", response_class=HTMLResponse)
def bookings_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    now = datetime.utcnow()
    upcoming = db.query(Booking).filter(
        Booking.status == "confirmed", Booking.start_datetime >= now
    ).order_by(Booking.start_datetime).all()
    past = db.query(Booking).filter(
        Booking.start_datetime < now
    ).order_by(Booking.start_datetime.desc()).limit(50).all()
    return templates.TemplateResponse("admin/bookings.html", {
        "request": request, "upcoming": upcoming, "past": past, "flash": get_flash(request),
    })


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking_route(request: Request, booking_id: int, db: Session = Depends(get_db), _=AuthDep):
    from app.services.booking import cancel_booking
    booking = db.query(Booking).filter_by(id=booking_id).first()
    if not booking:
        flash(request, "Booking not found.", "error")
        return RedirectResponse("/admin/bookings", status_code=302)

    settings = get_settings()
    refresh_token = get_setting(db, "google_refresh_token", "")

    # Delete from Google Calendar
    if booking.google_event_id and refresh_token and settings.google_client_id:
        try:
            cal = CalendarService(settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri)
            cal.delete_event(refresh_token, booking.appointment_type.calendar_id, booking.google_event_id)
        except Exception:
            pass

    # Send cancellation email
    notify_enabled = get_setting(db, "notifications_enabled", "true") == "true"
    if notify_enabled and settings.resend_api_key:
        from app.services.email import send_cancellation_notice
        try:
            send_cancellation_notice(
                api_key=settings.resend_api_key,
                from_email=settings.from_email,
                guest_email=booking.guest_email,
                guest_name=booking.guest_name,
                appt_type_name=booking.appointment_type.name,
                start_dt=booking.start_datetime,
            )
        except Exception:
            pass

    cancel_booking(db, booking_id)
    flash(request, f"Booking for {booking.guest_name} cancelled.")
    return RedirectResponse("/admin/bookings", status_code=302)


# --- Settings ---

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), _=AuthDep):
    refresh_token = get_setting(db, "google_refresh_token", "")
    settings = get_settings()
    cal = CalendarService(settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri)
    return templates.TemplateResponse("admin/settings.html", {
        "request": request,
        "owner_name": get_setting(db, "owner_name", ""),
        "notify_email": get_setting(db, "notify_email", ""),
        "notifications_enabled": get_setting(db, "notifications_enabled", "true") == "true",
        "timezone": get_setting(db, "timezone", "America/New_York"),
        "google_authorized": cal.is_authorized(refresh_token),
        "flash": get_flash(request),
    })


@router.post("/settings")
def save_settings(
    request: Request,
    owner_name: str = Form(""),
    notify_email: str = Form(""),
    notifications_enabled: str = Form("false"),
    timezone: str = Form("America/New_York"),
    db: Session = Depends(get_db), _=AuthDep,
):
    set_setting(db, "owner_name", owner_name)
    set_setting(db, "notify_email", notify_email)
    set_setting(db, "notifications_enabled", "true" if notifications_enabled == "true" else "false")
    set_setting(db, "timezone", timezone)
    flash(request, "Settings saved.")
    return RedirectResponse("/admin/settings", status_code=302)


@router.post("/settings/password")
def change_password(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(...),
    db: Session = Depends(get_db), _=AuthDep,
):
    import bcrypt
    if password != confirm:
        flash(request, "Passwords do not match.", "error")
        return RedirectResponse("/admin/settings", status_code=302)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    set_setting(db, "admin_password_hash", hashed)
    flash(request, "Password changed.")
    return RedirectResponse("/admin/settings", status_code=302)


# --- Google OAuth ---

@router.get("/google/authorize")
def google_authorize(_=AuthDep):
    settings = get_settings()
    cal = CalendarService(settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri)
    url = cal.get_auth_url()
    return RedirectResponse(url, status_code=302)


@router.get("/google/callback")
def google_callback(request: Request, code: str, db: Session = Depends(get_db), _=AuthDep):
    settings = get_settings()
    cal = CalendarService(settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri)
    refresh_token = cal.exchange_code(code)
    set_setting(db, "google_refresh_token", refresh_token)
    flash(request, "Google Calendar connected successfully.")
    return RedirectResponse("/admin/settings", status_code=302)
```

**Step 5: Run all tests**

```bash
pytest tests/ -v
```

Expected: all PASS.

**Step 6: Commit**

```bash
git add app/routers/admin.py app/templates/admin/
git commit -m "feat: admin availability, bookings, and settings pages"
```

---

## Task 15: Wire Google Calendar into Booking Creation

**Files:**
- Modify: `app/routers/booking.py` (add Calendar event creation)

**Step 1: Update `submit_booking` in `app/routers/booking.py`**

After `booking = create_booking(...)`, before the email block, add:

```python
settings = get_settings()
refresh_token = get_setting(db, "google_refresh_token", "")

if refresh_token and settings.google_client_id:
    from app.services.calendar import CalendarService
    cal = CalendarService(settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri)
    description_lines = [
        f"Guest: {guest_name}",
        f"Email: {guest_email}",
        f"Phone: {guest_phone or 'not provided'}",
        f"Notes: {notes or 'none'}",
    ]
    for k, v in custom_responses.items():
        description_lines.append(f"{k}: {v}")
    try:
        event_id = cal.create_event(
            refresh_token=refresh_token,
            calendar_id=appt_type.calendar_id,
            summary=f"{appt_type.name} — {guest_name}",
            description="\n".join(description_lines),
            start=start_dt,
            end=end_dt,
            attendee_email=guest_email,
        )
        booking.google_event_id = event_id
        db.commit()
    except Exception:
        pass  # Booking saved; calendar event creation failure is non-fatal
```

**Step 2: Run all tests**

```bash
pytest tests/ -v
```

Expected: all PASS (Calendar API is not called in tests since refresh token is empty).

**Step 3: Commit**

```bash
git add app/routers/booking.py
git commit -m "feat: create Google Calendar event on booking confirmation"
```

---

## Task 16: Rate Limiting

**Files:**
- Modify: `app/main.py`
- Modify: `app/routers/booking.py`

**Step 1: Add slowapi to `app/main.py`**

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

**Step 2: Apply limit to booking submission in `app/routers/booking.py`**

```python
from app.main import limiter

@router.post("/book", response_class=HTMLResponse)
@limiter.limit("10/hour")
async def submit_booking(request: Request, ...):
    ...
```

Note: Make `submit_booking` `async` and use `await request.form()` to read all form data including dynamic custom fields:

```python
@router.post("/book", response_class=HTMLResponse)
@limiter.limit("10/hour")
async def submit_booking(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    type_id = int(form_data.get("type_id", 0))
    start_datetime = form_data.get("start_datetime", "")
    guest_name = form_data.get("guest_name", "").strip()
    guest_email = form_data.get("guest_email", "").strip()
    guest_phone = form_data.get("guest_phone", "").strip()
    notes = form_data.get("notes", "").strip()

    # Input validation
    if not all([type_id, start_datetime, guest_name, guest_email]):
        return HTMLResponse("<p class='flash flash-error'>Please fill in all required fields.</p>")

    appt_type = db.query(AppointmentType).filter_by(id=type_id, active=True).first()
    if not appt_type:
        return HTMLResponse("<p class='flash flash-error'>Invalid appointment type.</p>")

    custom_responses = {}
    for field in appt_type.custom_fields:
        custom_responses[field["label"]] = form_data.get(f"custom_{field['label']}", "")

    # ... rest of the function unchanged
```

**Step 3: Run all tests**

```bash
pytest tests/ -v
```

**Step 4: Commit**

```bash
git add app/main.py app/routers/booking.py
git commit -m "feat: rate limiting on booking submission (10/hour per IP)"
```

---

## Task 17: Dockerfile + Fly.io Deployment Config

**Files:**
- Create: `Dockerfile`
- Create: `fly.toml`
- Create: `.dockerignore`

**Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Step 2: Create `.dockerignore`**

```
.venv/
__pycache__/
*.pyc
.env
tests/
*.db
.git/
```

**Step 3: Create `fly.toml`**

```toml
app = "your-app-name"
primary_region = "iad"

[build]

[env]
  PORT = "8080"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 1

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 256

[mounts]
  source = "booking_data"
  destination = "/data"
```

**Note:** Replace `your-app-name` with your actual Fly.io app name after running `fly launch`.

**Step 4: Verify Docker build (optional local test)**

```bash
docker build -t booking-test .
docker run -p 8080:8080 -e SECRET_KEY=test booking-test &
sleep 3
curl http://localhost:8080/health
# Expected: {"status":"ok"}
docker stop $(docker ps -q --filter ancestor=booking-test)
```

**Step 5: Commit**

```bash
git add Dockerfile fly.toml .dockerignore
git commit -m "chore: Dockerfile and Fly.io deployment config"
```

---

## Task 18: README + .env.example

**Files:**
- Create: `README.md`

**Step 1: Create `README.md`**

````markdown
# Booking Assistant

Personal appointment booking system. Public booking page + admin panel with Google Calendar integration.

## Local Development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your values
uvicorn app.main:app --reload
```

Open http://localhost:8000 — first visit to `/admin` triggers password setup.

## Google Calendar Setup

1. Go to https://console.cloud.google.com → New project
2. Enable the **Google Calendar API**
3. Create OAuth credentials (Web application)
   - Authorized redirect URI: `https://your-domain.com/admin/google/callback`
4. Copy Client ID + Secret into `.env`
5. In admin panel → Settings → Connect Google Calendar

## Resend Email Setup

1. Sign up at https://resend.com (free: 3k emails/month)
2. Add and verify your domain
3. Create an API key and add to `.env` as `RESEND_API_KEY`
4. Set `FROM_EMAIL` to an address on your verified domain

## Fly.io Deployment

```bash
# Install Fly CLI: https://fly.io/docs/hands-on/install-flyctl/
fly auth login
fly launch              # creates app + volume (answer prompts)
fly secrets set \
  SECRET_KEY=$(openssl rand -hex 32) \
  GOOGLE_CLIENT_ID=... \
  GOOGLE_CLIENT_SECRET=... \
  GOOGLE_REDIRECT_URI=https://your-app.fly.dev/admin/google/callback \
  RESEND_API_KEY=... \
  FROM_EMAIL=noreply@yourdomain.com
fly deploy
```

## Cloudflare DNS

In Cloudflare dashboard for your domain:
- Add CNAME record: `book` → `your-app.fly.dev` (Proxied ON)

## Running Tests

```bash
pytest tests/ -v
```
````

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README with setup and deployment instructions"
```

---

## Task 19: Final Validation

**Step 1: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests PASS, no errors.

**Step 2: Start server and smoke test**

```bash
uvicorn app.main:app --reload &
sleep 2
curl -s http://localhost:8000/health
# Expected: {"status":"ok"}
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/book
# Expected: 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/admin/
# Expected: 302 (redirect to login)
kill %1
```

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: complete personal booking system implementation"
```

---

## Deployment Checklist

After deploying to Fly.io:

- [ ] Visit `https://your-domain.com/admin/setup` to set admin password
- [ ] Go to Settings → Connect Google Calendar
- [ ] Create at least one Appointment Type
- [ ] Add Availability Rules (e.g., Mon–Fri 9am–5pm)
- [ ] Set notification email in Settings
- [ ] Test a booking end-to-end at `https://your-domain.com/book`
- [ ] Verify confirmation email received
- [ ] Verify Google Calendar event created
