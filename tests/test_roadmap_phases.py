"""Tests for the booking-capacity guard (try_create_booking) and the
single-query settings store (load_db_settings)."""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.dependencies import load_db_settings, set_setting
from app.models import AppointmentType
from app.services.booking import try_create_booking


def _setup_db(max_concurrent: int = 1):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    appt = AppointmentType(
        name="Tour", duration_minutes=30, buffer_before_minutes=0,
        buffer_after_minutes=0, calendar_id="primary", active=True,
        color="#fff", description="", max_concurrent=max_concurrent,
    )
    appt._custom_fields = "[]"
    db.add(appt)
    db.commit()
    return db, appt


def _book(db, appt, name="Guest"):
    return try_create_booking(
        db=db, appt_type=appt,
        start_dt=datetime(2030, 9, 16, 10, 0),
        end_dt=datetime(2030, 9, 16, 10, 30),
        guest_name=name, guest_email=f"{name.lower()}@example.com",
        guest_phone="555-1234", notes="", custom_responses={},
    )


# ---------- try_create_booking ----------

def test_try_create_booking_succeeds_on_free_slot():
    db, appt = _setup_db()
    booking = _book(db, appt)
    assert booking is not None
    assert booking.status == "confirmed"
    db.close()


def test_try_create_booking_rejects_full_slot():
    db, appt = _setup_db(max_concurrent=1)
    assert _book(db, appt, "First") is not None
    assert _book(db, appt, "Second") is None
    db.close()


def test_try_create_booking_respects_max_concurrent():
    db, appt = _setup_db(max_concurrent=2)
    assert _book(db, appt, "First") is not None
    assert _book(db, appt, "Second") is not None
    assert _book(db, appt, "Third") is None
    db.close()


def test_cancelled_bookings_do_not_count_toward_capacity():
    from app.services.booking import cancel_booking

    db, appt = _setup_db(max_concurrent=1)
    first = _book(db, appt, "First")
    cancel_booking(db, first.id)
    assert _book(db, appt, "Second") is not None
    db.close()


# ---------- load_db_settings ----------

def test_load_db_settings_defaults():
    db, _ = _setup_db()
    env = Settings(resend_api_key="env-key", from_email="env@example.com")
    dbs = load_db_settings(db, env)
    assert dbs.timezone_name == "America/New_York"
    assert dbs.min_advance_hours == 24
    assert dbs.max_future_days == 30
    assert dbs.notifications_enabled is True
    # Env fallback applies when the DB has no value
    assert dbs.resend_api_key == "env-key"
    assert dbs.from_email == "env@example.com"
    assert dbs.conflict_calendars == []
    assert dbs.google_refresh_token == ""
    db.close()


def test_load_db_settings_db_values_win_over_env():
    db, _ = _setup_db()
    set_setting(db, "timezone", "UTC")
    set_setting(db, "min_advance_hours", "2")
    set_setting(db, "resend_api_key", "db-key")
    set_setting(db, "notifications_enabled", "false")
    set_setting(db, "conflict_calendars", '[{"type": "google", "id": "x@y.z", "name": "X"}]')
    env = Settings(resend_api_key="env-key")
    dbs = load_db_settings(db, env)
    assert dbs.timezone_name == "UTC"
    assert str(dbs.tzinfo) == "UTC"
    assert dbs.min_advance_hours == 2
    assert dbs.resend_api_key == "db-key"
    assert dbs.notifications_enabled is False
    assert dbs.can_send_email is False
    assert dbs.conflict_calendars == [{"type": "google", "id": "x@y.z", "name": "X"}]
    db.close()


def test_load_db_settings_tolerates_bad_conflict_calendars_json():
    db, _ = _setup_db()
    set_setting(db, "conflict_calendars", "not-json{")
    dbs = load_db_settings(db, Settings())
    assert dbs.conflict_calendars == []
    db.close()
