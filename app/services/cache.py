"""Process-local TTL cache for external availability data.

Every /slots request hits Google freebusy, events.list and webcal feeds
live, so a guest clicking through dates re-pays the full network cost on
each click. Entries are cached for a short TTL
(settings.slots_cache_ttl_seconds; 0 disables caching) and the whole cache
is cleared whenever a booking mutates, so availability is never staler
than the TTL and is usually fresher.

Single-process by design — matches the app's one-container deployment.
"""
import threading
import time
from typing import Any, Callable, Hashable


class TTLCache:
    def __init__(self, max_entries: int = 512):
        self._lock = threading.Lock()
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._max_entries = max_entries

    def get_or_fetch(self, key: Hashable, fetch: Callable[[], Any], ttl_seconds: float) -> Any:
        """Return the cached value for key, calling fetch() on miss/expiry.

        ttl_seconds <= 0 bypasses the cache entirely (no read, no store).
        Concurrent misses on the same key may fetch more than once; the
        fetch deliberately runs outside the lock because it is a slow
        network call.
        """
        if ttl_seconds <= 0:
            return fetch()
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is not None and entry[0] > now:
                return entry[1]
        value = fetch()
        with self._lock:
            if len(self._data) >= self._max_entries:
                self._evict_expired(now)
                if len(self._data) >= self._max_entries:
                    self._data.clear()
            self._data[key] = (now + ttl_seconds, value)
        return value

    def _evict_expired(self, now: float) -> None:
        for key in [k for k, (expires, _) in self._data.items() if expires <= now]:
            del self._data[key]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# Shared instance for slot-availability lookups. Cleared on every booking
# mutation (create / cancel / reschedule / drive-time block changes).
availability_cache = TTLCache()
