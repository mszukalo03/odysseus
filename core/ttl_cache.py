"""
core/ttl_cache.py

Generic async single-flight TTL cache. "Fetch this, but share one in-flight
request across concurrent callers, and cache the result for N seconds" was
independently reimplemented in src/task_scheduler.py, extensions/ithaca/
weather.py, and extensions/ithaca/tiles.py before being centralized here.

Concurrent callers for a key not yet cached share ONE fetch() call (via
asyncio.Future) rather than serializing on a lock — the lock is only held
briefly to check/register a pending future, so an unrelated key's fetch is
never blocked behind another key's in-flight fetch. Exceptions propagate to
every waiter and do not poison the cache.

Each subsystem should instantiate its own `TTLCache()` rather than sharing
one global instance, so unrelated domains (scheduled-task fetches, weather,
tile queries, ...) never share key space.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Hashable, Optional, Tuple


class TTLCache:
    def __init__(self) -> None:
        self._entries: Dict[Hashable, Tuple[float, Any]] = {}
        self._pending: Dict[Hashable, "asyncio.Future[Any]"] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: Hashable, ttl: float, fetch: Callable[[], Awaitable[Any]]) -> Any:
        """Return the cached value for `key` if still fresh, else await
        `fetch()` (single-flight) and cache the result for `ttl` seconds."""
        now = time.monotonic()
        async with self._lock:
            entry = self._entries.get(key)
            if entry and entry[0] > now:
                return entry[1]
            fut = self._pending.get(key)
            if fut is not None:
                pending = fut
                owner = False
            else:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._pending[key] = fut
                pending = fut
                owner = True
        if not owner:
            return await pending
        try:
            val = await fetch()
            async with self._lock:
                self._entries[key] = (time.monotonic() + ttl, val)
                self._pending.pop(key, None)
            pending.set_result(val)
            return val
        except Exception as e:
            async with self._lock:
                self._pending.pop(key, None)
            pending.set_exception(e)
            # Mark the exception "retrieved" on the owner's own future: if no
            # other caller ever awaits `pending` (the common case — most
            # calls have no concurrent waiter), asyncio logs "exception was
            # never retrieved" to stderr on GC. .exception() doesn't consume
            # it — a real waiter awaiting `pending` still raises normally.
            pending.exception()
            raise

    def invalidate(self, key: Optional[Hashable] = None) -> None:
        """Drop one cached entry, or every entry if `key` is None. Does not
        cancel an in-flight fetch — that result still lands once it
        completes and simply gets overwritten by any newer one."""
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)
