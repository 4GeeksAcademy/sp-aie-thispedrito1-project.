from __future__ import annotations

import time
from threading import Lock
from typing import Any, Dict, Tuple


class TTLCache:
    """In-memory cache with per-entry expiration.

    Route handlers in this API are sync `def`s, which FastAPI/Starlette runs
    in a threadpool — concurrent requests can hit the same cache instance
    from different threads, so mutations go through a lock.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + ttl_seconds, value)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Wipes every entry. Used by the test suite: this cache is a
        process-wide singleton, but each test resets its database directly
        (bypassing the write endpoints that normally trigger invalidate())."""
        with self._lock:
            self._store.clear()


# Shared across routers: cache keys are namespaced per endpoint (e.g.
# "inventory_products"), so one instance is simpler than one per module.
cache = TTLCache()
