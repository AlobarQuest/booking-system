"""
Shared fixtures and helpers for E2E tests.

All E2E tests require live Google API credentials set as environment variables:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN, GOOGLE_MAPS_API_KEY

Tests auto-skip if any are missing.
"""
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import set_setting
from app.main import app
from app.models import AppointmentType, AvailabilityRule
from app.services.calendar import CalendarService

# ---------------------------------------------------------------------------
# Required env vars — all E2E tests skip if any are absent
# ---------------------------------------------------------------------------

REQUIRED_VARS = [
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "GOOGLE_MAPS_API_KEY",
]


def pytest_collection_modifyitems(items):
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    if missing:
        skip = pytest.mark.skip(reason=f"E2E env vars not set: {missing}")
        for item in items:
            if "e2e" in str(item.fspath):
                item.add_marker(skip)


# ---------------------------------------------------------------------------
# Address constants
# ---------------------------------------------------------------------------

ADDR_BUCKHEAD = "3500 Peachtree Rd NE, Atlanta, GA 30326"
ADDR_MIDTOWN = "1280 Peachtree St NE, Atlanta, GA 30309"
ADDR_DECATUR = "101 E Court Square, Decatur, GA 30030"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def future_monday() -> date:
    """Return a Monday at least 8 days from today.

    Ensures min_advance_hours=24 never filters out test slots.
    """
    today = date.today()
    days_ahead = 7 - today.weekday()  # days until next Monday
    if days_ahead <= 0:
        days_ahead += 7
    candidate = today + timedelta(days=days_ahead)
    # Ensure at least 8 days out
    if (candidate - today).days < 8:
        candidate += timedelta(weeks=1)
    return candidate


@contextmanager
def calendar_event(
    cal_service: CalendarService,
    refresh_token: str,
    summary: str,
    start_utc: datetime,
    end_utc: datetime,
    location: str = "",
    calendar_id: str = "primary",
):
    """Context manager that creates a calendar event and always deletes it.

    Yields the event ID. Deletes in finally even on test failure.
    """
    event_id = cal_service.create_event(
        refresh_token=refresh_token,
        calendar_id=calendar_id,
        summary=summary,
        description="E2E test event — will be auto-deleted",
        start=start_utc,
        end=end_utc,
        location=location,
        show_as="busy",
    )
    try:
        yield event_id
    finally:
        try:
            cal_service.delete_event(refresh_token, calendar_id, event_id)
        except Exception:
            pass  # best-effort cleanup


# ---------------------------------------------------------------------------
# DB seeding helpers
# ---------------------------------------------------------------------------


def seed_rule(db, day_of_week: int = 0, start: str = "09:00", end: str = "17:00"):
    """Seed a single availability rule. day_of_week=0 is Monday."""
    rule = AvailabilityRule(day_of_week=day_of_week, start_time=start, end_time=end, active=True)
    db.add(rule)
    db.commit()
    return rule


def seed_appt_type(db, **kwargs) -> AppointmentType:
    """Seed an AppointmentType with sensible defaults. Commits and returns the instance."""
    defaults = {
        "name": "Test Appointment",
        "duration_minutes": 60,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "calendar_id": "primary",
        "_custom_fields": "[]",
        "active": True,
    }
    defaults.update(kwargs)
    appt = AppointmentType(**defaults)
    db.add(appt)
    db.commit()
    db.refresh(appt)
    return appt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(name="e2e_db", scope="function")
def e2e_db_fixture():
    """In-memory SQLite session for E2E tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(name="e2e_client", scope="function")
def e2e_client_fixture(e2e_db):
    """TestClient with dependency override pointing to e2e_db."""

    def override_get_db():
        try:
            yield e2e_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(name="cal_service", scope="session")
def cal_service_fixture():
    """CalendarService instance using session-scoped env vars."""
    return CalendarService(
        client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        redirect_uri="http://localhost:8080/admin/google/callback",
    )


@pytest.fixture(name="refresh_token", scope="session")
def refresh_token_fixture():
    """Google refresh token from env."""
    return os.environ["GOOGLE_REFRESH_TOKEN"]
