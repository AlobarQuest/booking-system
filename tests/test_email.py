from unittest.mock import patch, MagicMock
from datetime import datetime
from app.services.email import (
    send_guest_confirmation,
    send_admin_alert,
    send_cancellation_notice,
)


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
        assert "Jane" in args["html"]


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
            notes="test note",
            custom_responses={"Property": "123 Main St"},
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0][0]
        assert args["to"] == ["me@example.com"]
        assert "Jane" in args["subject"]
        assert "123 Main St" in args["html"]


def test_guest_confirmation_escapes_xss_in_custom_fields():
    from unittest.mock import patch
    from datetime import datetime
    from app.services.email import send_guest_confirmation
    sent = {}
    def fake_send(payload):
        sent["html"] = payload["html"]
    with patch("resend.Emails.send", side_effect=fake_send):
        send_guest_confirmation(
            api_key="x", from_email="f@x.com", guest_email="g@x.com",
            guest_name="Alice", appt_type_name="Tour",
            start_dt=datetime(2025, 3, 3, 10, 0),
            end_dt=datetime(2025, 3, 3, 11, 0),
            custom_responses={"Field": "<script>alert(1)</script>"},
            owner_name="Owner",
        )
    assert "<script>" not in sent["html"]
    assert "&lt;script&gt;" in sent["html"]


def test_admin_alert_escapes_xss_in_guest_data():
    from unittest.mock import patch
    from datetime import datetime
    from app.services.email import send_admin_alert
    sent = {}
    def fake_send(payload):
        sent["html"] = payload["html"]
    with patch("resend.Emails.send", side_effect=fake_send):
        send_admin_alert(
            api_key="x", from_email="f@x.com", notify_email="a@x.com",
            guest_name='<img src=x onerror=alert(1)>',
            guest_email="g@x.com", guest_phone="",
            appt_type_name="Tour",
            start_dt=datetime(2025, 3, 3, 10, 0),
            notes="<b>bad</b>",
            custom_responses={"Q": "<script>"},
        )
    assert "<img" not in sent["html"]
    assert "&lt;img" in sent["html"]
    assert "<b>bad</b>" not in sent["html"]


def test_send_cancellation_notice_calls_resend():
    with patch("resend.Emails.send") as mock_send:
        mock_send.return_value = {"id": "test-id"}
        send_cancellation_notice(
            api_key="re_test",
            from_email="no@example.com",
            guest_email="jane@example.com",
            guest_name="Jane",
            appt_type_name="Phone Call",
            start_dt=datetime(2025, 3, 3, 9, 0),
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0][0]
        assert args["to"] == ["jane@example.com"]
        assert "cancelled" in args["subject"].lower()
