"""Tests for core/ttl_cache.py — the shared single-flight TTL cache used by
src/task_scheduler.py, extensions/ithaca/weather.py, and
extensions/ithaca/tiles.py."""

import asyncio

import pytest

from core.ttl_cache import TTLCache


async def test_returns_fresh_value_without_refetching():
    cache = TTLCache()
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return "value"

    assert await cache.get("k", 60, fetch) == "value"
    assert await cache.get("k", 60, fetch) == "value"
    assert calls["n"] == 1


async def test_expired_entry_is_refetched():
    cache = TTLCache()
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return calls["n"]

    assert await cache.get("k", 0.01, fetch) == 1
    await asyncio.sleep(0.03)
    assert await cache.get("k", 0.01, fetch) == 2


async def test_different_keys_do_not_collide():
    cache = TTLCache()

    async def fetch_a():
        return "a"

    async def fetch_b():
        return "b"

    assert await cache.get("a", 60, fetch_a) == "a"
    assert await cache.get("b", 60, fetch_b) == "b"


async def test_concurrent_callers_share_one_fetch():
    cache = TTLCache()
    calls = {"n": 0}
    started = asyncio.Event()

    async def fetch():
        calls["n"] += 1
        started.set()
        await asyncio.sleep(0.02)
        return "value"

    results = await asyncio.gather(*[cache.get("k", 60, fetch) for _ in range(5)])
    assert results == ["value"] * 5
    assert calls["n"] == 1  # single-flight: only one real fetch for 5 concurrent callers


async def test_exception_propagates_to_every_waiter_and_does_not_poison_cache():
    cache = TTLCache()
    attempt = {"n": 0}

    async def flaky_fetch():
        attempt["n"] += 1
        if attempt["n"] == 1:
            raise RuntimeError("boom")
        return "recovered"

    with pytest.raises(RuntimeError, match="boom"):
        await cache.get("k", 60, flaky_fetch)
    # A failed fetch must not leave a poisoned/pending entry behind.
    assert await cache.get("k", 60, flaky_fetch) == "recovered"


async def test_concurrent_callers_all_see_the_exception():
    cache = TTLCache()

    async def failing_fetch():
        await asyncio.sleep(0.01)
        raise ValueError("nope")

    results = await asyncio.gather(
        *[cache.get("k", 60, failing_fetch) for _ in range(3)],
        return_exceptions=True,
    )
    assert all(isinstance(r, ValueError) for r in results)


def test_invalidate_one_key():
    cache = TTLCache()
    cache._entries["k1"] = (1e18, "v1")
    cache._entries["k2"] = (1e18, "v2")
    cache.invalidate("k1")
    assert "k1" not in cache._entries
    assert "k2" in cache._entries


def test_invalidate_all():
    cache = TTLCache()
    cache._entries["k1"] = (1e18, "v1")
    cache._entries["k2"] = (1e18, "v2")
    cache.invalidate()
    assert cache._entries == {}


async def test_invalidate_forces_refetch_even_if_still_fresh():
    cache = TTLCache()
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return calls["n"]

    assert await cache.get("k", 999, fetch) == 1
    cache.invalidate("k")
    assert await cache.get("k", 999, fetch) == 2
