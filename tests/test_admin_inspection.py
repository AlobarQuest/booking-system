import os
import pytest
from unittest.mock import patch
from datetime import datetime, timezone as dt_timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.dependencies import require_admin, require_csrf
from app.models import AppointmentType, AvailabilityRule


@pytest.fixture
def insp_client(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    os.makedirs(tmp_path / "uploads", exist_ok=True)
    from app.config import get_settings
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_admin] = lambda: True
    app.dependency_overrides[require_csrf] = lambda: None

    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        yield c, TestSession

    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_schedule_inspection_page_loads(insp_client):
    client, _ = insp_client
    resp = client.get("/admin/schedule-inspection")
    assert resp.status_code == 200
    assert "Schedule Inspection" in resp.text


def test_inspection_slots_returns_html(insp_client):
    """GET /admin/inspection-slots returns slot HTML for a valid admin-initiated type and date."""
    client, SessionFactory = insp_client
    db = SessionFactory()
    t = AppointmentType(
        name="Inspection",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        active=True,
        admin_initiated=True,
        requires_drive_time=True,
        color="#fff",
        calendar_id="primary",
        description="",
        owner_event_title="Inspection",
    )
    t._custom_fields = "[]"
    db.add(t)
    # Monday availability rule
    db.add(AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True))
    db.commit()
    type_id = t.id
    db.close()

    with patch("app.routers.admin.datetime") as mock_dt, \
         patch("app.services.availability.get_drive_time", return_value=0):
        mock_dt.now.return_value = datetime(2025, 3, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        mock_dt.combine = datetime.combine
        mock_dt.fromisoformat = datetime.fromisoformat
        resp = client.get(
            f"/admin/inspection-slots?type_id={type_id}&date=2025-03-03&destination=123+Main+St"
        )
    assert resp.status_code == 200
