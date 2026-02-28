import json
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, init_db
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


def test_appointment_type_has_drive_time_fields():
    from app.models import AppointmentType
    t = AppointmentType()
    assert hasattr(t, "requires_drive_time")
    assert hasattr(t, "calendar_window_enabled")
    assert hasattr(t, "calendar_window_title")
    assert hasattr(t, "calendar_window_calendar_id")


def test_drive_time_cache_model_exists():
    from app.models import DriveTimeCache
    entry = DriveTimeCache()
    assert hasattr(entry, "origin_address")
    assert hasattr(entry, "destination_address")
    assert hasattr(entry, "drive_minutes")
    assert hasattr(entry, "cached_at")


def test_appointment_type_has_title_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    appt = AppointmentType(
        name="Showing",
        duration_minutes=30,
        owner_event_title="Rental Showing — 123 Main St",
        guest_event_title="Your Home Tour",
        active=True,
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.commit()
    assert appt.owner_event_title == "Rental Showing — 123 Main St"
    assert appt.guest_event_title == "Your Home Tour"
    db.close()


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    from app.config import get_settings
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def test_appointment_type_new_columns(db_engine):
    cols = {c["name"] for c in inspect(db_engine).get_columns("appointment_types")}
    assert "photo_filename" in cols
    assert "listing_url" in cols
    assert "rental_requirements" in cols
    assert "owner_reminders_enabled" in cols


def test_rental_requirements_property():
    t = AppointmentType(name="Test", duration_minutes=30)
    assert t.rental_requirements == []
    t.rental_requirements = ["No pets", "Income 3x rent"]
    assert json.loads(t._rental_requirements) == ["No pets", "Income 3x rent"]
    assert t.rental_requirements == ["No pets", "Income 3x rent"]


def test_rental_requirements_defaults_empty():
    t = AppointmentType(name="Test", duration_minutes=30)
    assert t.rental_requirements == []
    assert (t.photo_filename or "") == ""
    assert (t.listing_url or "") == ""
    assert not t.owner_reminders_enabled


def test_appointment_type_has_admin_initiated():
    t = AppointmentType()
    assert hasattr(t, "admin_initiated")
    assert t.admin_initiated is False or t.admin_initiated == 0 or t.admin_initiated is None


def test_booking_has_location():
    b = Booking()
    assert hasattr(b, "location")


def test_availability_rule_has_appointment_type_id():
    r = AvailabilityRule()
    assert hasattr(r, "appointment_type_id")
    assert r.appointment_type_id is None
