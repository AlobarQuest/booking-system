from app.config import get_settings


def test_google_maps_api_key_defaults_empty():
    import os
    os.environ.pop("GOOGLE_MAPS_API_KEY", None)
    from app.config import Settings
    s = Settings()
    assert s.google_maps_api_key == ""


def test_settings_has_required_fields():
    s = get_settings()
    assert hasattr(s, "database_url")
    assert hasattr(s, "secret_key")
    assert hasattr(s, "google_client_id")
    assert hasattr(s, "resend_api_key")
    assert hasattr(s, "timezone")
    assert s.timezone == "America/New_York"
