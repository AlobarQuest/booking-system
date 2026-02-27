from unittest.mock import MagicMock, patch
from app.services.calendar import CalendarService


def make_cal():
    return CalendarService("cid", "csec", "http://redirect")


def _inserted_event(mock_service):
    """Return the event body dict passed to events().insert().

    mock_service is the object returned by _build_service (i.e. the service instance),
    so we access .events().insert() directly without .return_value.
    """
    return mock_service.events.return_value.insert.call_args[1]["body"]


def test_create_event_no_reminders_key_by_default():
    """When disable_reminders=False (default), 'reminders' key must NOT appear in the event body."""
    with patch.object(CalendarService, "_build_service") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.events.return_value.insert.return_value.execute.return_value = {"id": "evt1"}
        from datetime import datetime
        cal = make_cal()
        cal.create_event(
            refresh_token="tok",
            calendar_id="primary",
            summary="Test",
            description="",
            start=datetime(2026, 3, 1, 10, 0),
            end=datetime(2026, 3, 1, 11, 0),
        )
    event_body = _inserted_event(mock_service)
    assert "reminders" not in event_body


def test_create_event_disable_reminders_sets_empty_overrides():
    """When disable_reminders=True, event body must include reminders with useDefault=False and empty overrides."""
    with patch.object(CalendarService, "_build_service") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.events.return_value.insert.return_value.execute.return_value = {"id": "evt2"}
        from datetime import datetime
        cal = make_cal()
        cal.create_event(
            refresh_token="tok",
            calendar_id="primary",
            summary="Test",
            description="",
            start=datetime(2026, 3, 1, 10, 0),
            end=datetime(2026, 3, 1, 11, 0),
            disable_reminders=True,
        )
    event_body = _inserted_event(mock_service)
    assert event_body.get("reminders") == {"useDefault": False, "overrides": []}


def test_submit_booking_passes_disable_reminders_when_owner_reminders_disabled():
    """When owner_reminders_enabled=False (default), create_event is called with disable_reminders=True."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from app.database import Base, get_db
    from app.main import app
    from app.models import AppointmentType
    from app.config import Settings
    from app.dependencies import set_setting

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    appt = AppointmentType(
        name="Consultation",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        calendar_id="primary",
        active=True,
        color="#3b82f6",
        description="",
        owner_reminders_enabled=False,
    )
    appt._custom_fields = "[]"
    db.add(appt)
    set_setting(db, "timezone", "America/New_York")
    set_setting(db, "google_refresh_token", "fake-refresh-token")
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

    mock_settings = Settings(
        google_client_id="fake-client-id",
        google_client_secret="fake-secret",
        google_redirect_uri="http://localhost/callback",
    )

    client = TestClient(app)
    with patch("app.routers.booking.get_settings", return_value=mock_settings), \
         patch("app.services.calendar.CalendarService.create_event", return_value="evt-id") as mock_create:
        response = client.post("/book", data={
            "type_id": str(appt_id),
            "start_datetime": "2026-03-01T10:00:00",
            "guest_name": "Alice",
            "guest_email": "alice@example.com",
        })
    assert response.status_code == 200
    assert mock_create.called
    assert mock_create.call_args.kwargs.get("disable_reminders") is True

    app.dependency_overrides.clear()


def test_submit_booking_does_not_disable_reminders_when_owner_reminders_enabled():
    """When owner_reminders_enabled=True, create_event is called with disable_reminders=False."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from app.database import Base, get_db
    from app.main import app
    from app.models import AppointmentType
    from app.config import Settings
    from app.dependencies import set_setting

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    appt = AppointmentType(
        name="Consultation",
        duration_minutes=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        calendar_id="primary",
        active=True,
        color="#3b82f6",
        description="",
        owner_reminders_enabled=True,
    )
    appt._custom_fields = "[]"
    db.add(appt)
    set_setting(db, "timezone", "America/New_York")
    set_setting(db, "google_refresh_token", "fake-refresh-token")
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

    mock_settings = Settings(
        google_client_id="fake-client-id",
        google_client_secret="fake-secret",
        google_redirect_uri="http://localhost/callback",
    )

    client = TestClient(app)
    with patch("app.routers.booking.get_settings", return_value=mock_settings), \
         patch("app.services.calendar.CalendarService.create_event", return_value="evt-id") as mock_create:
        response = client.post("/book", data={
            "type_id": str(appt_id),
            "start_datetime": "2026-03-01T10:00:00",
            "guest_name": "Bob",
            "guest_email": "bob@example.com",
        })
    assert response.status_code == 200
    assert mock_create.called
    assert mock_create.call_args.kwargs.get("disable_reminders") is False

    app.dependency_overrides.clear()
