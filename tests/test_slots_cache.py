"""Tests for the availability TTL cache (app/services/cache.py) and its
integration into the slot engine."""
from datetime import date as date_type, datetime
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.dependencies import set_setting
from app.models import AppointmentType
from app.services.cache import TTLCache, availability_cache
from app.services.slots import compute_slots_for_type


# ---------- TTLCache unit tests ----------

def test_get_or_fetch_caches_within_ttl():
    cache = TTLCache()
    fetch = MagicMock(return_value="value")
    assert cache.get_or_fetch("k", fetch, ttl_seconds=60) == "value"
    assert cache.get_or_fetch("k", fetch, ttl_seconds=60) == "value"
    fetch.assert_called_once()


def test_get_or_fetch_refetches_after_expiry():
    cache = TTLCache()
    fetch = MagicMock(side_effect=["first", "second"])
    fake_now = [1000.0]
    with patch("app.services.cache.time.monotonic", side_effect=lambda: fake_now[0]):
        assert cache.get_or_fetch("k", fetch, ttl_seconds=30) == "first"
        fake_now[0] += 31
        assert cache.get_or_fetch("k", fetch, ttl_seconds=30) == "second"
    assert fetch.call_count == 2


def test_zero_ttl_bypasses_cache():
    cache = TTLCache()
    fetch = MagicMock(side_effect=["first", "second"])
    assert cache.get_or_fetch("k", fetch, ttl_seconds=0) == "first"
    assert cache.get_or_fetch("k", fetch, ttl_seconds=0) == "second"
    assert fetch.call_count == 2


def test_clear_forces_refetch():
    cache = TTLCache()
    fetch = MagicMock(side_effect=["first", "second"])
    assert cache.get_or_fetch("k", fetch, ttl_seconds=60) == "first"
    cache.clear()
    assert cache.get_or_fetch("k", fetch, ttl_seconds=60) == "second"


def test_distinct_keys_fetch_separately():
    cache = TTLCache()
    fetch_a = MagicMock(return_value="a")
    fetch_b = MagicMock(return_value="b")
    assert cache.get_or_fetch(("k", 1), fetch_a, ttl_seconds=60) == "a"
    assert cache.get_or_fetch(("k", 2), fetch_b, ttl_seconds=60) == "b"
    fetch_a.assert_called_once()
    fetch_b.assert_called_once()


# ---------- Slot engine integration ----------

def _setup_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    appt = AppointmentType(
        name="Call", duration_minutes=30, buffer_before_minutes=0,
        buffer_after_minutes=0, calendar_id="primary", active=True,
        color="#fff", description="",
    )
    appt._custom_fields = "[]"
    db.add(appt)
    set_setting(db, "google_refresh_token", "fake-token")
    set_setting(db, "timezone", "UTC")
    db.commit()
    return db, appt


def _settings(ttl: int) -> Settings:
    return Settings(
        google_client_id="fake-id",
        google_client_secret="fake-secret",
        google_redirect_uri="http://localhost/callback",
        slots_cache_ttl_seconds=ttl,
    )


def test_compute_slots_reuses_cached_freebusy():
    db, appt = _setup_db()
    mock_cal = MagicMock()
    mock_cal.get_busy_intervals.return_value = []

    with patch("app.services.slots.get_settings", return_value=_settings(ttl=60)), \
         patch("app.services.slots.build_calendar_service", return_value=mock_cal):
        compute_slots_for_type(appt, date_type(2030, 9, 16), db)
        compute_slots_for_type(appt, date_type(2030, 9, 16), db)

    mock_cal.get_busy_intervals.assert_called_once()
    db.close()


def test_compute_slots_does_not_cache_across_dates():
    db, appt = _setup_db()
    mock_cal = MagicMock()
    mock_cal.get_busy_intervals.return_value = []

    with patch("app.services.slots.get_settings", return_value=_settings(ttl=60)), \
         patch("app.services.slots.build_calendar_service", return_value=mock_cal):
        compute_slots_for_type(appt, date_type(2030, 9, 16), db)
        compute_slots_for_type(appt, date_type(2030, 9, 17), db)

    assert mock_cal.get_busy_intervals.call_count == 2
    db.close()


def test_create_booking_invalidates_cache():
    from app.services.booking import create_booking

    db, appt = _setup_db()
    mock_cal = MagicMock()
    mock_cal.get_busy_intervals.return_value = []

    with patch("app.services.slots.get_settings", return_value=_settings(ttl=60)), \
         patch("app.services.slots.build_calendar_service", return_value=mock_cal):
        compute_slots_for_type(appt, date_type(2030, 9, 16), db)
        create_booking(
            db=db, appt_type=appt,
            start_dt=datetime(2030, 9, 16, 10, 0),
            end_dt=datetime(2030, 9, 16, 10, 30),
            guest_name="Alice", guest_email="alice@example.com",
            guest_phone="555-1234", notes="", custom_responses={},
        )
        compute_slots_for_type(appt, date_type(2030, 9, 16), db)

    assert mock_cal.get_busy_intervals.call_count == 2
    db.close()


def test_cache_disabled_when_ttl_zero():
    db, appt = _setup_db()
    mock_cal = MagicMock()
    mock_cal.get_busy_intervals.return_value = []

    with patch("app.services.slots.get_settings", return_value=_settings(ttl=0)), \
         patch("app.services.slots.build_calendar_service", return_value=mock_cal):
        compute_slots_for_type(appt, date_type(2030, 9, 16), db)
        compute_slots_for_type(appt, date_type(2030, 9, 16), db)

    assert mock_cal.get_busy_intervals.call_count == 2
    db.close()
