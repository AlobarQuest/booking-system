import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.routers.admin import google_authorize, google_callback


def test_unauthenticated_admin_redirects_to_login(client):
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_login_initiates_oidc_redirect(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.headers = {"location": "https://id.alobar.net/application/o/authorize/?foo=1"}
    mock_resp.body = b""
    mock_resp.background = None

    with patch("app.routers.auth.oauth.authentik.authorize_redirect", new_callable=AsyncMock) as mock_redir:
        mock_redir.return_value = mock_resp
        response = client.get("/login", follow_redirects=False)

    mock_redir.assert_called_once()
    redirect_uri_arg = mock_redir.call_args[0][1]
    assert "/auth/callback" in redirect_uri_arg


def test_logout_redirects_to_authentik_end_session(client):
    with patch("app.routers.auth._settings") as mock_settings:
        mock_settings.alobar_id_issuer = "https://id.alobar.net/application/o/booking-assistant/"
        response = client.get("/admin/logout", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert "id.alobar.net" in location
    assert "end-session" in location


def test_google_authorize_stores_state_and_code_verifier_in_session():
    request = SimpleNamespace(session={})
    with unittest.mock.patch("app.routers.admin.get_settings") as mock_settings, \
         unittest.mock.patch("app.routers.admin.build_calendar_service") as MockCalendarService:
        mock_settings.return_value.google_client_id = "cid"
        mock_settings.return_value.google_client_secret = "secret"
        mock_settings.return_value.google_redirect_uri = "https://example.com/callback"
        MockCalendarService.return_value.get_auth_url.return_value = (
            "https://accounts.google.com/o/oauth2/auth?x=1",
            "state-123",
            "verifier-123",
        )
        response = google_authorize(request, _=True)
    assert request.session["oauth_state"] == "state-123"
    assert request.session["oauth_code_verifier"] == "verifier-123"
    assert response.status_code == 302
    assert response.headers["location"] == "https://accounts.google.com/o/oauth2/auth?x=1"


def test_google_callback_passes_stored_code_verifier_to_exchange():
    request = SimpleNamespace(
        session={"oauth_state": "state-123", "oauth_code_verifier": "verifier-123"},
        query_params={"state": "state-123"},
    )
    db = unittest.mock.MagicMock()
    with unittest.mock.patch("app.routers.admin.get_settings") as mock_settings, \
         unittest.mock.patch("app.routers.admin.build_calendar_service") as MockCalendarService, \
         unittest.mock.patch("app.routers.admin.set_setting") as mock_set_setting:
        mock_settings.return_value.google_client_id = "cid"
        mock_settings.return_value.google_client_secret = "secret"
        mock_settings.return_value.google_redirect_uri = "https://example.com/callback"
        MockCalendarService.return_value.exchange_code.return_value = "refresh-token"
        response = google_callback(request, code="auth-code", db=db, _=True)
    MockCalendarService.return_value.exchange_code.assert_called_once_with(
        "auth-code",
        code_verifier="verifier-123",
    )
    mock_set_setting.assert_called_once_with(db, "google_refresh_token", "refresh-token")
    assert "oauth_state" not in request.session
    assert "oauth_code_verifier" not in request.session
    assert response.status_code == 302
    assert response.headers["location"] == "/admin/settings"
