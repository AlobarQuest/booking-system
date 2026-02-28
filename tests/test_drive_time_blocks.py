# tests/test_drive_time_blocks.py
"""Unit tests for _create_drive_time_blocks in app/routers/booking.py."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call


def _make_cal():
    """Return a mock CalendarService with a controllable create_event."""
    cal = MagicMock()
    cal.get_events_for_day.return_value = []
    cal.create_event.return_value = "block-evt-id"
    return cal


def _run(cal, nearby_events, drive_minutes, appt_name="Consultation",
         appt_location="456 Property Ln", home_address="123 Home St",
         start_utc=None, end_utc=None):
    """Helper: patch get_events_for_day and get_drive_time, then call _create_drive_time_blocks."""
    from app.routers.booking import _create_drive_time_blocks

    if start_utc is None:
        start_utc = datetime(2026, 3, 1, 15, 0)   # 3:00 PM UTC
    if end_utc is None:
        end_utc = start_utc + timedelta(minutes=30)

    cal.get_events_for_day.return_value = nearby_events

    db = MagicMock()
    with patch("app.routers.booking.get_drive_time", return_value=drive_minutes):
        _create_drive_time_blocks(
            cal=cal,
            refresh_token="tok",
            calendar_id="primary",
            appt_name=appt_name,
            appt_location=appt_location,
            start_utc=start_utc,
            end_utc=end_utc,
            home_address=home_address,
            db=db,
        )


def test_before_block_created_from_preceding_event():
    """Before block uses preceding event's location; title references the new appointment."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)
    preceding_end = start_utc - timedelta(minutes=30)

    preceding = {
        "start": preceding_end - timedelta(hours=1),
        "end": preceding_end,
        "summary": "Previous Showing",
        "location": "789 Other St",
    }
    _run(cal, nearby_events=[preceding], drive_minutes=20,
         start_utc=start_utc, end_utc=end_utc)

    cal.create_event.assert_any_call(
        refresh_token="tok",
        calendar_id="primary",
        summary="BLOCK - Drive Time for Consultation",
        description="",
        start=start_utc - timedelta(minutes=20),
        end=start_utc,
        show_as="busy",
        disable_reminders=True,
    )


def test_before_block_falls_back_to_home_address():
    """Before block uses home_address when no preceding event is found."""
    cal = _make_cal()
    _run(cal, nearby_events=[], drive_minutes=15)

    # create_event must have been called for the before block
    calls = cal.create_event.call_args_list
    before_calls = [c for c in calls if "Drive Time for Consultation" in c.kwargs.get("summary", "")]
    assert len(before_calls) == 1


def test_before_block_not_created_when_drive_time_zero():
    """No before block when drive time is 0 (same location or no API key)."""
    cal = _make_cal()
    _run(cal, nearby_events=[], drive_minutes=0)
    cal.create_event.assert_not_called()


def test_before_block_not_created_when_no_origin():
    """No before block when both preceding location and home_address are empty."""
    cal = _make_cal()
    _run(cal, nearby_events=[], drive_minutes=20, home_address="")
    cal.create_event.assert_not_called()


def test_after_block_created_from_following_event():
    """After block uses following event's location; title references the following event."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)
    following_start = end_utc + timedelta(minutes=20)

    following = {
        "start": following_start,
        "end": following_start + timedelta(hours=1),
        "summary": "Next Meeting",
        "location": "999 Far Away Rd",
    }
    _run(cal, nearby_events=[following], drive_minutes=25,
         start_utc=start_utc, end_utc=end_utc, home_address="")

    cal.create_event.assert_any_call(
        refresh_token="tok",
        calendar_id="primary",
        summary="BLOCK - Drive Time for Next Meeting",
        description="",
        start=end_utc,
        end=end_utc + timedelta(minutes=25),
        show_as="busy",
        disable_reminders=True,
    )


def test_after_block_not_created_when_following_has_no_location():
    """No after block when the following event has no location."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)

    following = {
        "start": end_utc + timedelta(minutes=10),
        "end": end_utc + timedelta(hours=1),
        "summary": "No Location Meeting",
        "location": "",
    }
    _run(cal, nearby_events=[following], drive_minutes=20,
         start_utc=start_utc, end_utc=end_utc, home_address="")

    cal.create_event.assert_not_called()


def test_both_blocks_created_when_both_neighbors_exist():
    """Both before and after blocks are created when adjacent events have locations."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)

    preceding = {
        "start": start_utc - timedelta(hours=1),
        "end": start_utc - timedelta(minutes=20),
        "summary": "Prior",
        "location": "111 Before St",
    }
    following = {
        "start": end_utc + timedelta(minutes=15),
        "end": end_utc + timedelta(hours=1),
        "summary": "After Meeting",
        "location": "222 After Ave",
    }
    _run(cal, nearby_events=[preceding, following], drive_minutes=10,
         start_utc=start_utc, end_utc=end_utc)

    assert cal.create_event.call_count == 2
    summaries = {c.kwargs["summary"] for c in cal.create_event.call_args_list}
    assert "BLOCK - Drive Time for Consultation" in summaries
    assert "BLOCK - Drive Time for After Meeting" in summaries


def test_calendar_fetch_failure_is_silent():
    """If get_events_for_day raises, no exception propagates and create_event is never called."""
    cal = _make_cal()
    cal.get_events_for_day.side_effect = Exception("API error")
    _run(cal, nearby_events=[], drive_minutes=20)
    cal.create_event.assert_not_called()


def test_fetches_events_in_plus_minus_one_hour_window():
    """get_events_for_day is called with the ±1-hour window around the appointment."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)
    _run(cal, nearby_events=[], drive_minutes=0, start_utc=start_utc, end_utc=end_utc)
    cal.get_events_for_day.assert_called_once_with(
        "tok",
        "primary",
        start_utc - timedelta(hours=1),
        end_utc + timedelta(hours=1),
    )


def test_after_block_not_created_when_drive_time_zero():
    """No after block when drive time is 0, even if the following event has a location."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)

    following = {
        "start": end_utc + timedelta(minutes=10),
        "end": end_utc + timedelta(hours=1),
        "summary": "Next Meeting",
        "location": "999 Far Away Rd",
    }
    _run(cal, nearby_events=[following], drive_minutes=0,
         start_utc=start_utc, end_utc=end_utc, home_address="")
    cal.create_event.assert_not_called()


def test_before_block_uses_most_recent_preceding_event():
    """When multiple events precede the appointment, the one with the latest end time is used."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)

    earlier = {
        "start": start_utc - timedelta(hours=1),
        "end": start_utc - timedelta(minutes=45),
        "summary": "Earlier Event",
        "location": "111 Wrong St",
    }
    later = {
        "start": start_utc - timedelta(minutes=50),
        "end": start_utc - timedelta(minutes=20),
        "summary": "Later Event",
        "location": "222 Right Ave",
    }
    _run(cal, nearby_events=[earlier, later], drive_minutes=10,
         start_utc=start_utc, end_utc=end_utc, home_address="")

    # The before block should be calculated FROM "222 Right Ave" (later event), not "111 Wrong St"
    # We verify by checking the before block was created (drive_mins=10 > 0)
    before_calls = [c for c in cal.create_event.call_args_list
                    if "Drive Time for Consultation" in c.kwargs.get("summary", "")]
    assert len(before_calls) == 1
    # The block ends at start_utc and starts 10 min before
    assert before_calls[0].kwargs["end"] == start_utc
    assert before_calls[0].kwargs["start"] == start_utc - timedelta(minutes=10)


def test_after_block_uses_earliest_following_event():
    """When multiple events follow the appointment, the one with the earliest start time is used."""
    cal = _make_cal()
    start_utc = datetime(2026, 3, 1, 15, 0)
    end_utc = start_utc + timedelta(minutes=30)

    earlier_following = {
        "start": end_utc + timedelta(minutes=10),
        "end": end_utc + timedelta(minutes=70),
        "summary": "Earlier Next",
        "location": "333 Right Rd",
    }
    later_following = {
        "start": end_utc + timedelta(minutes=40),
        "end": end_utc + timedelta(hours=2),
        "summary": "Later Next",
        "location": "444 Wrong Rd",
    }
    _run(cal, nearby_events=[earlier_following, later_following], drive_minutes=15,
         start_utc=start_utc, end_utc=end_utc, home_address="")

    # The after block should reference "Earlier Next" (earliest start), not "Later Next"
    after_calls = [c for c in cal.create_event.call_args_list
                   if "Drive Time for Earlier Next" in c.kwargs.get("summary", "")]
    assert len(after_calls) == 1
    assert after_calls[0].kwargs["start"] == end_utc
    assert after_calls[0].kwargs["end"] == end_utc + timedelta(minutes=15)


def test_submit_booking_calls_drive_time_blocks_when_requires_drive_time():
    """submit_booking calls _create_drive_time_blocks when requires_drive_time is True."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from app.database import Base, get_db
    from app.main import app
    from app.models import AppointmentType
    from app.dependencies import require_csrf

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    appt = AppointmentType(
        name="Property Showing",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        calendar_id="primary",
        active=True,
        color="#3b82f6",
        description="",
        requires_drive_time=True,
        location="456 Property Ln",
    )
    appt._custom_fields = "[]"
    db.add(appt)
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
    app.dependency_overrides[require_csrf] = lambda: None

    with patch("app.routers.booking._create_drive_time_blocks") as mock_blocks:
        with patch("app.routers.booking.get_settings") as mock_settings:
            from app.config import Settings
            mock_settings.return_value = Settings(
                google_client_id="fake-id",
                google_client_secret="fake-secret",
                google_redirect_uri="http://localhost/callback",
            )
            with patch("app.services.calendar.CalendarService.create_event", return_value="evt-1"):
                from app.dependencies import set_setting
                s = Session()
                set_setting(s, "google_refresh_token", "fake-token")
                set_setting(s, "timezone", "America/New_York")
                s.close()

                client = TestClient(app)
                response = client.post("/book", data={
                    "type_id": str(appt_id),
                    "start_datetime": "2026-03-01T10:00:00",
                    "guest_name": "Alice",
                    "guest_email": "alice@example.com",
                })

    assert response.status_code == 200
    assert mock_blocks.called

    app.dependency_overrides.clear()
