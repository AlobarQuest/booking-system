"""A dead Google refresh token silently disables conflict checking on /slots.
The owner must be emailed once per failure episode (edge-triggered), re-armed
when Google Calendar is reconnected."""
from datetime import date as date_type
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.dependencies import get_setting, set_setting
from app.models import AppointmentType
from app.services.slots import compute_slots_for_type


def _setup_db(with_email_config=True):
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
    if with_email_config:
        set_setting(db, "resend_api_key", "re_fake")
        set_setting(db, "notify_email", "owner@example.com")
        set_setting(db, "from_email", "bookings@example.com")
    db.commit()
    return db, appt


def _settings() -> Settings:
    return Settings(
        google_client_id="fake-id",
        google_client_secret="fake-secret",
        google_redirect_uri="https://booking.example.com/admin/google/callback",
        slots_cache_ttl_seconds=0,
    )


def _compute_with_dead_token(db, appt, target, mock_alert_target):
    mock_cal = MagicMock()
    mock_cal.get_busy_intervals.side_effect = RefreshError("invalid_grant: Bad Request")
    with patch("app.services.slots.get_settings", return_value=_settings()), \
         patch("app.services.slots.build_calendar_service", return_value=mock_cal), \
         patch(mock_alert_target) as mock_alert:
        compute_slots_for_type(appt, target, db)
        return mock_alert


ALERT_TARGET = "app.services.slots.email.send_google_token_alert"


def test_refresh_error_alerts_admin_once_per_episode():
    db, appt = _setup_db()
    mock_cal = MagicMock()
    mock_cal.get_busy_intervals.side_effect = RefreshError("invalid_grant: Bad Request")
    with patch("app.services.slots.get_settings", return_value=_settings()), \
         patch("app.services.slots.build_calendar_service", return_value=mock_cal), \
         patch(ALERT_TARGET) as mock_alert:
        compute_slots_for_type(appt, date_type(2030, 9, 16), db)
        compute_slots_for_type(appt, date_type(2030, 9, 17), db)

    mock_alert.assert_called_once()
    kwargs = mock_alert.call_args.kwargs
    assert kwargs["notify_email"] == "owner@example.com"
    assert kwargs["settings_url"] == "https://booking.example.com/admin/settings"
    assert get_setting(db, "google_token_alert_sent", "") != ""
    db.close()


def test_generic_google_failure_does_not_alert():
    db, appt = _setup_db()
    mock_cal = MagicMock()
    mock_cal.get_busy_intervals.side_effect = ConnectionError("network down")
    with patch("app.services.slots.get_settings", return_value=_settings()), \
         patch("app.services.slots.build_calendar_service", return_value=mock_cal), \
         patch(ALERT_TARGET) as mock_alert:
        compute_slots_for_type(appt, date_type(2030, 9, 16), db)

    mock_alert.assert_not_called()
    assert get_setting(db, "google_token_alert_sent", "") == ""
    db.close()


def test_no_resend_config_skips_alert_without_arming_flag():
    """Without email config nothing can be sent — the flag must stay clear so
    the alert still fires once Resend is configured."""
    db, appt = _setup_db(with_email_config=False)
    mock_cal = MagicMock()
    mock_cal.get_busy_intervals.side_effect = RefreshError("invalid_grant")
    with patch("app.services.slots.get_settings", return_value=_settings()), \
         patch("app.services.slots.build_calendar_service", return_value=mock_cal), \
         patch(ALERT_TARGET) as mock_alert:
        compute_slots_for_type(appt, date_type(2030, 9, 16), db)

    mock_alert.assert_not_called()
    assert get_setting(db, "google_token_alert_sent", "") == ""
    db.close()


def test_failed_alert_send_does_not_arm_flag():
    """If the alert email itself fails, the flag must stay clear for a retry —
    and the failure must not break slot computation."""
    db, appt = _setup_db()
    mock_cal = MagicMock()
    mock_cal.get_busy_intervals.side_effect = RefreshError("invalid_grant")
    with patch("app.services.slots.get_settings", return_value=_settings()), \
         patch("app.services.slots.build_calendar_service", return_value=mock_cal), \
         patch(ALERT_TARGET, side_effect=RuntimeError("resend 500")):
        slots = compute_slots_for_type(appt, date_type(2030, 9, 16), db)

    assert isinstance(slots, list)
    assert get_setting(db, "google_token_alert_sent", "") == ""
    db.close()


def test_reconnect_rearms_alert():
    """A successful Google reconnect clears the sent-flag so the next failure
    episode alerts again."""
    import unittest.mock
    from app.routers.admin import google_callback

    request = SimpleNamespace(
        session={"oauth_state": "state-123", "oauth_code_verifier": "v"},
        query_params={"state": "state-123"},
    )
    db = unittest.mock.MagicMock()
    with unittest.mock.patch("app.routers.admin.get_settings"), \
         unittest.mock.patch("app.routers.admin.build_calendar_service") as MockCal, \
         unittest.mock.patch("app.routers.admin.set_setting") as mock_set_setting:
        MockCal.return_value.exchange_code.return_value = "new-token"
        google_callback(request, code="auth-code", db=db, _=True)

    mock_set_setting.assert_any_call(db, "google_refresh_token", "new-token")
    mock_set_setting.assert_any_call(db, "google_token_alert_sent", "")
    db.close()
