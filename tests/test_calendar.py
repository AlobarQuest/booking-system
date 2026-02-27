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
    assert url == "https://accounts.google.com/o/oauth2/auth?..."


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


def test_fetch_webcal_busy_parses_ics():
    from unittest.mock import patch, MagicMock
    from datetime import datetime, timezone
    from app.services.calendar import fetch_webcal_busy

    # Minimal valid ICS with one event: 2025-03-03 09:00-09:30 UTC
    ics_content = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20250303T090000Z
DTEND:20250303T093000Z
SUMMARY:Test Event
END:VEVENT
END:VCALENDAR"""

    mock_response = MagicMock()
    mock_response.content = ics_content
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.calendar.httpx.get", return_value=mock_response):
        start = datetime(2025, 3, 3, 0, 0, 0)
        end = datetime(2025, 3, 4, 0, 0, 0)
        intervals = fetch_webcal_busy("webcal://example.com/cal", start, end)

    assert len(intervals) == 1
    assert intervals[0][0] == datetime(2025, 3, 3, 9, 0, 0)
    assert intervals[0][1] == datetime(2025, 3, 3, 9, 30, 0)


def test_fetch_webcal_busy_excludes_out_of_range():
    from unittest.mock import patch, MagicMock
    from datetime import datetime
    from app.services.calendar import fetch_webcal_busy

    ics_content = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20250310T090000Z
DTEND:20250310T093000Z
SUMMARY:Different Day
END:VEVENT
END:VCALENDAR"""

    mock_response = MagicMock()
    mock_response.content = ics_content
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.calendar.httpx.get", return_value=mock_response):
        start = datetime(2025, 3, 3, 0, 0, 0)
        end = datetime(2025, 3, 4, 0, 0, 0)
        intervals = fetch_webcal_busy("webcal://example.com/cal", start, end)

    assert intervals == []


def test_get_events_for_day_returns_event_list():
    service = make_service()
    mock_api_result = {
        "items": [
            {
                "summary": "Doctor Appointment",
                "location": "123 Medical Dr",
                "start": {"dateTime": "2025-03-03T10:00:00Z"},
                "end": {"dateTime": "2025-03-03T11:00:00Z"},
            },
            {
                "summary": "All Day Event",
                "start": {"date": "2025-03-03"},  # all-day — no dateTime
                "end": {"date": "2025-03-04"},
            },
        ]
    }
    with patch.object(service, "_build_service") as mock_build:
        mock_svc = MagicMock()
        mock_svc.events().list().execute.return_value = mock_api_result
        mock_build.return_value = mock_svc
        events = service.get_events_for_day(
            "fake-token", "primary",
            datetime(2025, 3, 3, 0, 0), datetime(2025, 3, 4, 0, 0)
        )
    assert len(events) == 1  # all-day event is skipped
    assert events[0]["summary"] == "Doctor Appointment"
    assert events[0]["location"] == "123 Medical Dr"
    assert events[0]["start"] == datetime(2025, 3, 3, 10, 0)
    assert events[0]["end"] == datetime(2025, 3, 3, 11, 0)


def test_get_events_for_day_missing_location_returns_empty_string():
    service = make_service()
    mock_api_result = {
        "items": [
            {
                "summary": "Meeting",
                "start": {"dateTime": "2025-03-03T14:00:00Z"},
                "end": {"dateTime": "2025-03-03T15:00:00Z"},
            }
        ]
    }
    with patch.object(service, "_build_service") as mock_build:
        mock_svc = MagicMock()
        mock_svc.events().list().execute.return_value = mock_api_result
        mock_build.return_value = mock_svc
        events = service.get_events_for_day(
            "fake-token", "primary",
            datetime(2025, 3, 3, 0, 0), datetime(2025, 3, 4, 0, 0)
        )
    assert events[0]["location"] == ""


def test_fetch_webcal_events_returns_location():
    """fetch_webcal_events must return the location field from ICS events."""
    from unittest.mock import patch, MagicMock
    from datetime import datetime
    from app.services.calendar import fetch_webcal_events

    ics_content = b"""BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nDTSTART:20260228T190000Z\r\nDTEND:20260228T200000Z\r\nSUMMARY:Showing\r\nLOCATION:2260 Harvest Ridge\\, Buford GA 30519\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""

    mock_response = MagicMock()
    mock_response.content = ics_content
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.calendar.httpx.get", return_value=mock_response):
        start = datetime(2026, 2, 28, 0, 0, 0)
        end = datetime(2026, 3, 1, 0, 0, 0)
        events = fetch_webcal_events("webcal://example.com/cal", start, end)

    assert len(events) == 1
    assert events[0]["location"] == "2260 Harvest Ridge, Buford GA 30519"
    assert events[0]["summary"] == "Showing"
    assert events[0]["start"] == datetime(2026, 2, 28, 19, 0, 0)


def test_fetch_webcal_busy_recurring_event():
    """Recurring events (RRULE) must be included, not silently skipped."""
    from unittest.mock import patch, MagicMock
    from datetime import datetime
    from app.services.calendar import fetch_webcal_busy

    # Weekly recurring event: every Monday 10:00-11:00 UTC starting 2025-03-03
    ics_content = b"""BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nDTSTART:20250303T100000Z\r\nDTEND:20250303T110000Z\r\nRRULE:FREQ=WEEKLY;BYDAY=MO\r\nSUMMARY:Weekly Meeting\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""

    mock_response = MagicMock()
    mock_response.content = ics_content
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.calendar.httpx.get", return_value=mock_response):
        # Query 2025-03-03 (Monday) — should include the recurring event occurrence
        start = datetime(2025, 3, 3, 0, 0, 0)
        end = datetime(2025, 3, 4, 0, 0, 0)
        intervals = fetch_webcal_busy("webcal://example.com/cal", start, end)

    assert len(intervals) == 1, f"Expected 1 interval, got {len(intervals)}"
    assert intervals[0][0] == datetime(2025, 3, 3, 10, 0, 0)
    assert intervals[0][1] == datetime(2025, 3, 3, 11, 0, 0)


def test_fetch_webcal_busy_all_day_event():
    from unittest.mock import patch, MagicMock
    from datetime import datetime
    from app.services.calendar import fetch_webcal_busy

    # All-day event on 2025-03-03: DTSTART:20250303, DTEND:20250304 (exclusive per RFC 5545)
    ics_content = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART;VALUE=DATE:20250303
DTEND;VALUE=DATE:20250304
SUMMARY:All Day Event
END:VEVENT
END:VCALENDAR"""

    mock_response = MagicMock()
    mock_response.content = ics_content
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.calendar.httpx.get", return_value=mock_response):
        # Query window: 2025-03-03 (should include the all-day event)
        start = datetime(2025, 3, 3, 0, 0, 0)
        end = datetime(2025, 3, 4, 0, 0, 0)
        intervals = fetch_webcal_busy("webcal://example.com/cal", start, end)

    assert len(intervals) == 1
    # ev_start: DTSTART:20250303 -> datetime(2025, 3, 3, 0, 0, 0)
    assert intervals[0][0] == datetime(2025, 3, 3, 0, 0, 0)
    # ev_end: DTEND:20250304 (exclusive) -> datetime(2025, 3, 4, 0, 0, 0)
    assert intervals[0][1] == datetime(2025, 3, 4, 0, 0, 0)
