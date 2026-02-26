from datetime import datetime, timedelta

import httpx

from app.config import get_settings

MAPS_DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"
CACHE_TTL_DAYS = 30


def get_drive_time(origin: str, destination: str, db) -> int:
    """Return drive time in minutes from origin to destination.

    Checks DriveTimeCache first. Calls Google Maps Distance Matrix API if
    the cache entry is missing or older than 30 days. Returns 0 if the API
    key is not configured or the request fails.
    """
    from app.models import DriveTimeCache

    settings = get_settings()
    if not settings.google_maps_api_key:
        return 0

    now = datetime.utcnow()
    cache_entry = (
        db.query(DriveTimeCache)
        .filter_by(origin_address=origin, destination_address=destination)
        .first()
    )

    if cache_entry and (now - cache_entry.cached_at) < timedelta(days=CACHE_TTL_DAYS):
        return cache_entry.drive_minutes

    # Call Google Maps Distance Matrix API
    try:
        resp = httpx.get(
            MAPS_DISTANCE_MATRIX_URL,
            params={
                "origins": origin,
                "destinations": destination,
                "mode": "driving",
                "key": settings.google_maps_api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        element = data["rows"][0]["elements"][0]
        if element["status"] != "OK":
            return 0
        duration_seconds = element["duration"]["value"]
        drive_minutes = (duration_seconds + 59) // 60  # round up to nearest minute
    except Exception:
        return 0

    # Upsert cache
    if cache_entry:
        cache_entry.drive_minutes = drive_minutes
        cache_entry.cached_at = now
    else:
        db.add(DriveTimeCache(
            origin_address=origin,
            destination_address=destination,
            drive_minutes=drive_minutes,
            cached_at=now,
        ))
    db.commit()
    return drive_minutes
