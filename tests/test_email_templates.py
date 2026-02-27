# tests/test_email_templates.py
import pytest
from unittest.mock import patch
from datetime import datetime
from app.services.email import (
    send_guest_confirmation,
    send_admin_alert,
    send_cancellation_notice,
    _GUEST_CONFIRMATION_DEFAULT,
    _ADMIN_ALERT_DEFAULT,
    _CANCELLATION_DEFAULT,
)


def test_send_guest_confirmation_uses_custom_template():
    custom_tpl = "<p>Hi {guest_name}, your {appt_type} on {date_time} is set. — {owner_name}</p>"
    captured = {}
    with patch("resend.Emails.send", side_effect=lambda x: captured.update(x)):
        send_guest_confirmation(
            api_key="key",
            from_email="from@test.com",
            guest_email="guest@test.com",
            guest_name="Alice",
            appt_type_name="Rental Showing",
            start_dt=datetime(2026, 3, 1, 10, 0),
            end_dt=datetime(2026, 3, 1, 10, 30),
            custom_responses={},
            owner_name="Devon",
            template=custom_tpl,
        )
    assert "Alice" in captured["html"]
    assert "Rental Showing" in captured["html"]
    assert "Devon" in captured["html"]


def test_send_guest_confirmation_falls_back_to_default():
    captured = {}
    with patch("resend.Emails.send", side_effect=lambda x: captured.update(x)):
        send_guest_confirmation(
            api_key="key",
            from_email="from@test.com",
            guest_email="guest@test.com",
            guest_name="Bob",
            appt_type_name="Showing",
            start_dt=datetime(2026, 3, 1, 10, 0),
            end_dt=datetime(2026, 3, 1, 10, 30),
            custom_responses={},
            owner_name="Devon",
            template="",
        )
    assert "Bob" in captured["html"]
    assert "confirmed" in captured["html"].lower()


def test_send_admin_alert_uses_custom_template():
    custom_tpl = "<p>New: {guest_name} ({guest_email}) for {appt_type} on {date_time}</p>"
    captured = {}
    with patch("resend.Emails.send", side_effect=lambda x: captured.update(x)):
        send_admin_alert(
            api_key="key",
            from_email="from@test.com",
            notify_email="admin@test.com",
            guest_name="Carol",
            guest_email="carol@test.com",
            guest_phone="",
            appt_type_name="Showing",
            start_dt=datetime(2026, 3, 1, 10, 0),
            notes="",
            custom_responses={},
            template=custom_tpl,
        )
    assert "Carol" in captured["html"]
    assert "carol@test.com" in captured["html"]


def test_send_cancellation_uses_custom_template():
    custom_tpl = "<p>Cancelled: {guest_name} — {appt_type} — {date_time}</p>"
    captured = {}
    with patch("resend.Emails.send", side_effect=lambda x: captured.update(x)):
        send_cancellation_notice(
            api_key="key",
            from_email="from@test.com",
            guest_email="guest@test.com",
            guest_name="Dave",
            appt_type_name="Showing",
            start_dt=datetime(2026, 3, 1, 10, 0),
            template=custom_tpl,
        )
    assert "Dave" in captured["html"]
    assert "Cancelled" in captured["html"]


def test_guest_confirmation_falls_back_to_default_on_bad_placeholder():
    """Custom template with unknown placeholder falls back to default, not raise."""
    from unittest.mock import MagicMock
    with patch("resend.Emails.send") as mock_send:
        send_guest_confirmation(
            api_key="test",
            from_email="from@test.com",
            guest_email="guest@test.com",
            guest_name="Alice",
            appt_type_name="Showing",
            start_dt=datetime(2026, 3, 1, 10, 0),
            end_dt=datetime(2026, 3, 1, 11, 0),
            custom_responses={},
            owner_name="Bob",
            template="Hello {unknown_placeholder}!",
        )
    assert mock_send.called
    html = mock_send.call_args[0][0]["html"]
    assert "confirmed" in html.lower()


def test_admin_alert_falls_back_to_default_on_bad_placeholder():
    with patch("resend.Emails.send") as mock_send:
        send_admin_alert(
            api_key="test",
            from_email="from@test.com",
            notify_email="admin@test.com",
            guest_name="Alice",
            guest_email="guest@test.com",
            guest_phone="",
            appt_type_name="Showing",
            start_dt=datetime(2026, 3, 1, 10, 0),
            notes="",
            custom_responses={},
            template="Bad {bogus} template",
        )
    assert mock_send.called
    html = mock_send.call_args[0][0]["html"]
    assert "new booking" in html.lower()


def test_cancellation_falls_back_to_default_on_bad_placeholder():
    with patch("resend.Emails.send") as mock_send:
        send_cancellation_notice(
            api_key="test",
            from_email="from@test.com",
            guest_email="guest@test.com",
            guest_name="Alice",
            appt_type_name="Showing",
            start_dt=datetime(2026, 3, 1, 10, 0),
            template="Bad {nope} template",
        )
    assert mock_send.called
    html = mock_send.call_args[0][0]["html"]
    assert "cancelled" in html.lower()
