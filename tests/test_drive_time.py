from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_get_drive_time_returns_zero_without_api_key():
    from app.services.drive_time import get_drive_time
    db = make_db()
    with patch("app.services.drive_time.get_settings") as mock_settings:
        mock_settings.return_value.google_maps_api_key = ""
        result = get_drive_time("123 Main St", "456 Oak Ave", db)
    assert result == 0


def test_get_drive_time_calls_maps_api_and_caches():
    from app.services.drive_time import get_drive_time
    db = make_db()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "rows": [{"elements": [{"status": "OK", "duration": {"value": 1500}}]}]
    }
    with patch("app.services.drive_time.get_settings") as mock_settings, \
         patch("app.services.drive_time.httpx.get", return_value=mock_response) as mock_get:
        mock_settings.return_value.google_maps_api_key = "fake-key"
        result = get_drive_time("123 Main St", "456 Oak Ave", db)
    assert result == 25  # 1500 seconds = 25 minutes (rounded up)
    mock_get.assert_called_once()


def test_get_drive_time_uses_cache_on_second_call():
    from app.services.drive_time import get_drive_time
    db = make_db()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "rows": [{"elements": [{"status": "OK", "duration": {"value": 1500}}]}]
    }
    with patch("app.services.drive_time.get_settings") as mock_settings, \
         patch("app.services.drive_time.httpx.get", return_value=mock_response) as mock_get:
        mock_settings.return_value.google_maps_api_key = "fake-key"
        get_drive_time("123 Main St", "456 Oak Ave", db)
        result = get_drive_time("123 Main St", "456 Oak Ave", db)
    assert result == 25
    assert mock_get.call_count == 1  # Only called once; second call used cache


def test_get_drive_time_returns_zero_on_maps_api_failure():
    from app.services.drive_time import get_drive_time
    db = make_db()
    with patch("app.services.drive_time.get_settings") as mock_settings, \
         patch("app.services.drive_time.httpx.get", side_effect=Exception("network error")):
        mock_settings.return_value.google_maps_api_key = "fake-key"
        result = get_drive_time("123 Main St", "456 Oak Ave", db)
    assert result == 0


def test_get_drive_time_refreshes_stale_cache():
    from app.services.drive_time import get_drive_time
    from app.models import DriveTimeCache
    db = make_db()
    # Insert a stale cache entry (31 days old)
    stale = DriveTimeCache(
        origin_address="123 Main St",
        destination_address="456 Oak Ave",
        drive_minutes=10,
        cached_at=datetime.utcnow() - timedelta(days=31),
    )
    db.add(stale)
    db.commit()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "rows": [{"elements": [{"status": "OK", "duration": {"value": 1800}}]}]
    }
    with patch("app.services.drive_time.get_settings") as mock_settings, \
         patch("app.services.drive_time.httpx.get", return_value=mock_response):
        mock_settings.return_value.google_maps_api_key = "fake-key"
        result = get_drive_time("123 Main St", "456 Oak Ave", db)
    assert result == 30  # Fresh from API, not the stale 10
