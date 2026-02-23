from unittest.mock import patch, MagicMock
from datetime import datetime
from app.services.calendar import CalendarService


def make_service():
    return CalendarService(client_id="fake-id", client_secret="fake-secret", redirect_uri="http://localhost/cb")


def test_get_auth_url_returns_google_url():
    service = make_service()
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?...", "state")
    with patch.object(service, "_make_flow", return_value=mock_flow):
        url = service.get_auth_url()
    assert url.startswith("https://accounts.google.com")


def test_is_authorized_false_without_token():
    assert make_service().is_authorized("") is False


def test_is_authorized_true_with_token():
    assert make_service().is_authorized("some-token") is True


def test_get_busy_intervals_parses_response():
    service = make_service()
    mock_result = {
        "calendars": {
            "primary": {
                "busy": [
                    {"start": "2025-03-03T12:00:00Z", "end": "2025-03-03T13:00:00Z"}
                ]
            }
        }
    }
    with patch.object(service, "_build_service") as mock_build:
        mock_svc = MagicMock()
        mock_svc.freebusy().query().execute.return_value = mock_result
        mock_build.return_value = mock_svc
        intervals = service.get_busy_intervals(
            "fake-token", ["primary"],
            datetime(2025, 3, 3, 0, 0), datetime(2025, 3, 4, 0, 0)
        )
    assert len(intervals) == 1
    assert intervals[0][0] == datetime(2025, 3, 3, 12, 0)
    assert intervals[0][1] == datetime(2025, 3, 3, 13, 0)
