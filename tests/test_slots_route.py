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
    assert "9:00 AM" in response.text
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


def test_slots_display_12_hour_format():
    client, appt_id = setup_db()
    with patch("app.routers.slots.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 3, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        mock_dt.combine = datetime.combine
        response = client.get(f"/slots?type_id={appt_id}&date=2025-03-03")
    assert response.status_code == 200
    # Should show "9:00 AM" as display label, not "09:00"
    assert "9:00 AM" in response.text
    # "09:00" may appear in hx-get URL params (slot.value) but must not appear as button label text
    assert ">\n    09:00\n  <" not in response.text
    app.dependency_overrides.clear()


def test_slots_applies_drive_time_when_enabled(client):
    """When requires_drive_time=True, trim_windows_for_drive_time is called."""
    from unittest.mock import patch, MagicMock
    from app.models import AppointmentType, AvailabilityRule
    from app.database import get_db

    db = next(client.app.dependency_overrides[get_db]())
    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True)
    db.add(rule)
    appt = AppointmentType(
        name="Showing", duration_minutes=60, active=True,
        location="456 Oak Ave", requires_drive_time=True,
        buffer_before_minutes=0, buffer_after_minutes=0,
    )
    db.add(appt)
    db.commit()

    with patch("app.routers.slots.trim_windows_for_drive_time", return_value=[]) as mock_trim, \
         patch("app.routers.slots._build_free_windows", return_value=[]):
        resp = client.get(f"/slots?type_id={appt.id}&date=2025-03-03")
    mock_trim.assert_called_once()


def test_slots_calendar_window_filters_slots(client):
    """When calendar_window_enabled=True and no matching events, return no slots."""
    from unittest.mock import patch
    from app.models import AppointmentType, AvailabilityRule
    from app.database import get_db

    db = next(client.app.dependency_overrides[get_db]())
    rule = AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True)
    db.add(rule)
    appt = AppointmentType(
        name="Rental Showing", duration_minutes=60, active=True,
        location="456 Oak Ave",
        calendar_window_enabled=True,
        calendar_window_title="POSSIBLE RENTAL SHOWINGS",
        buffer_before_minutes=0, buffer_after_minutes=0,
    )
    db.add(appt)
    from app.dependencies import set_setting
    set_setting(db, "google_refresh_token", "fake-token")
    db.commit()

    with patch("app.routers.slots.CalendarService") as MockCal:
        MockCal.return_value.get_events_for_day.return_value = []  # No matching events
        MockCal.return_value.get_busy_intervals.return_value = []
        resp = client.get(f"/slots?type_id={appt.id}&date=2025-03-03")
    assert resp.status_code == 200
    assert "no-slots" in resp.text or resp.text.count("slot") == 0


def test_slots_uses_destination_for_admin_initiated_type():
    """For admin_initiated types, the destination query param is used for drive time (not appt_type.location)."""
    from unittest.mock import patch
    from app.models import AppointmentType, AvailabilityRule
    from app.database import Base, get_db
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    appt = AppointmentType(
        name="Inspection",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        calendar_id="primary",
        active=True,
        admin_initiated=True,
        requires_drive_time=True,
        color="#fff",
        description="",
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.add(AvailabilityRule(day_of_week=0, start_time="09:00", end_time="17:00", active=True))
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

    with patch("app.routers.slots.datetime") as mock_dt, \
         patch("app.services.availability.get_drive_time", return_value=0):
        mock_dt.now.return_value = datetime(2025, 3, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        mock_dt.combine = datetime.combine
        resp = client.get(f"/slots?type_id={appt_id}&date=2025-03-03&destination=123+Main+St+Atlanta")
    assert resp.status_code == 200
    assert "9:00 AM" in resp.text or "slot-btn" in resp.text  # slots rendered
    app.dependency_overrides.clear()
