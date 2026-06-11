from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./booking.db"
    secret_key: str = "change-me-in-production"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/admin/google/callback"
    google_maps_api_key: str = ""
    resend_api_key: str = ""
    alobar_id_client_id: str = ""
    alobar_id_client_secret: str = ""
    alobar_id_issuer: str = ""
    from_email: str = "noreply@example.com"
    timezone: str = "America/New_York"
    upload_dir: str = "/data/uploads"
    # TTL for cached Google freebusy / events / webcal lookups on the
    # /slots endpoints. 0 disables caching.
    slots_cache_ttl_seconds: int = 45

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
