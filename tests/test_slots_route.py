from unittest.mock import patch
from datetime import datetime, timezone as dt_timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import AppointmentType, AvailabilityRule


def setup_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    appt = AppointmentType(
        name="Call",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        calendar_id="primary",
        active=True,
        color="#fff",
        description="",
    )
    appt._custom_fields = "[]"
    db.add(appt)
    # Monday (day_of_week=0) rule 9-11am
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
    return TestClient(app), appt_id


def test_slots_returns_html_for_valid_date():
    client, appt_id = setup_db()
    # 2025-03-03 is a Monday; mock utcnow to a time before the date so slots are not filtered
    with patch("app.routers.slots.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 3, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        mock_dt.combine = datetime.combine
        response = client.get(f"/slots?type_id={appt_id}&date=2025-03-03")
    assert response.status_code == 200
    assert "09:00" in response.text
    app.dependency_overrides.clear()


def test_slots_returns_no_slots_for_wrong_day():
    client, appt_id = setup_db()
    # 2025-03-04 is a Tuesday — no rules for Tuesday
    with patch("app.routers.slots.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 3, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        mock_dt.combine = datetime.combine
        response = client.get(f"/slots?type_id={appt_id}&date=2025-03-04")
    assert response.status_code == 200
    assert "No available" in response.text
    app.dependency_overrides.clear()


def test_slots_invalid_type_returns_error():
    client, _ = setup_db()
    response = client.get("/slots?type_id=9999&date=2025-03-03")
    assert response.status_code == 200
    assert "not found" in response.text.lower()
    app.dependency_overrides.clear()
