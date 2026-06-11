"""Settings page must report Google Calendar status from token *validity*,
not mere token presence — a revoked refresh token previously showed Connected."""
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import require_admin, set_setting
from app.main import app
from app.services.calendar import CalendarService

BROKEN_MARKER = "Connection broken"
CONNECTED_MARKER = "Connected"


@pytest.fixture(name="status_client")
def status_client_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    os.makedirs(tmp_path / "uploads", exist_ok=True)
    from app.config import get_settings
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_admin] = lambda: True

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, TestSession

    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _store_refresh_token(TestSession, value="some-stored-token"):
    db = TestSession()
    set_setting(db, "google_refresh_token", value)
    db.close()


def test_no_token_shows_not_connected(status_client):
    client, _ = status_client
    resp = client.get("/admin/settings")
    assert resp.status_code == 200
    assert "Not connected" in resp.text
    assert BROKEN_MARKER not in resp.text


def test_rejected_token_shows_broken_not_connected(status_client, monkeypatch):
    client, TestSession = status_client
    _store_refresh_token(TestSession)
    monkeypatch.setattr(CalendarService, "verify_refresh_token", lambda self, token: False)

    resp = client.get("/admin/settings")
    assert resp.status_code == 200
    assert BROKEN_MARKER in resp.text
    assert "&#10003; Connected" not in resp.text


def test_valid_token_shows_connected(status_client, monkeypatch):
    client, TestSession = status_client
    _store_refresh_token(TestSession)
    monkeypatch.setattr(CalendarService, "verify_refresh_token", lambda self, token: True)

    resp = client.get("/admin/settings")
    assert resp.status_code == 200
    assert CONNECTED_MARKER in resp.text
    assert BROKEN_MARKER not in resp.text


def test_unverifiable_token_still_shows_connected(status_client, monkeypatch):
    """Network hiccups (verify returns None) must not flip the status to broken."""
    client, TestSession = status_client
    _store_refresh_token(TestSession)
    monkeypatch.setattr(CalendarService, "verify_refresh_token", lambda self, token: None)

    resp = client.get("/admin/settings")
    assert resp.status_code == 200
    assert CONNECTED_MARKER in resp.text
    assert BROKEN_MARKER not in resp.text


def test_verify_refresh_token_maps_google_responses(monkeypatch):
    """Unit check of the mapping: refresh ok -> True, RefreshError -> False,
    transport failure -> None."""
    from google.auth.exceptions import RefreshError, TransportError

    svc = CalendarService("cid", "csecret", "https://example.com/cb")

    def _ok(self, request):
        return None

    def _rejected(self, request):
        raise RefreshError("invalid_grant: Bad Request")

    def _network_down(self, request):
        raise TransportError("connection refused")

    from google.oauth2.credentials import Credentials

    monkeypatch.setattr(Credentials, "refresh", _ok)
    assert svc.verify_refresh_token("tok") is True

    monkeypatch.setattr(Credentials, "refresh", _rejected)
    assert svc.verify_refresh_token("tok") is False

    monkeypatch.setattr(Credentials, "refresh", _network_down)
    assert svc.verify_refresh_token("tok") is None
