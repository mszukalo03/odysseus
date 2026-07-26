"""Tests for src/tools/ithaca.py — the agent-facing get_ithaca_weather and
get_ithaca_software_updates tools, which wrap routes/ithaca_routes.py's
fetch/cache functions so the AI can answer questions using the same data as
the Ithaca hub's tiles."""

import pytest
from fastapi import HTTPException

import routes.ithaca_routes as ithaca_routes
from src.tools.ithaca import do_get_ithaca_weather, do_get_ithaca_software_updates


@pytest.fixture(autouse=True)
def _reset_caches():
    # Tool tests share process-level caches with the route tests; reset
    # before/after so a warm entry from one test doesn't leak into another.
    ithaca_routes._weather_cache.update({"key": None, "expires": 0.0, "data": None})
    ithaca_routes._digest_cache.update({"key": None, "expires": 0.0, "data": None})
    yield
    ithaca_routes._weather_cache.update({"key": None, "expires": 0.0, "data": None})
    ithaca_routes._digest_cache.update({"key": None, "expires": 0.0, "data": None})


FAKE_WEATHER = {
    "location": "London",
    "units": "metric",
    "current": {"temp": 21.4, "feels_like": 21.9, "humidity": 64, "wind_speed": 3.6, "description": "scattered clouds", "icon": "03d"},
    "hourly": [
        {"dt": 1000, "local_hour": 14, "temp": 20.0, "feels_like": 19.5, "humidity": 60, "pop": 45, "description": "light rain", "icon": "10d"},
        {"dt": 2000, "local_hour": 17, "temp": 18.0, "feels_like": 17.0, "humidity": 65, "pop": 0, "description": "clear sky", "icon": "01d"},
    ],
    "fetched_at": 123,
}

FAKE_DIGEST_WITH_UPDATES = {
    "filename": "2026-07-25-digest.md",
    "date": "2026-07-25",
    "sections": {"Software Updates": "..."},
    "software_updates": {
        "rows": [
            {"app": "Radarr", "hosted_on": "latitude", "deployed": "v6.3.1", "version": "v6.4.0",
             "update": "Yes", "update_available": True, "desc": "Fixed Trakt imports"},
            {"app": "Jellyfin", "hosted_on": "nuc", "deployed": "v11.9", "version": "—",
             "update": "No", "update_available": False, "desc": "RC only"},
        ],
        "note": "No Flatpak updates available.",
    },
    "fetched_at": 456,
}

FAKE_DIGEST_NO_UPDATES_SECTION = {
    "filename": "2026-07-25-digest.md",
    "date": "2026-07-25",
    "sections": {"Weather": "..."},
    "software_updates": None,
    "fetched_at": 456,
}


async def test_get_ithaca_weather_success(monkeypatch):
    async def fake_fetch():
        return FAKE_WEATHER
    monkeypatch.setattr(ithaca_routes, "_fetch_weather", fake_fetch)

    result = await do_get_ithaca_weather("{}")
    assert result["exit_code"] == 0
    assert "London" in result["response"]
    assert "scattered clouds" in result["response"]
    assert "21°C" in result["response"]
    assert "45% chance of rain" in result["response"]
    assert result["weather"] == FAKE_WEATHER


async def test_get_ithaca_weather_empty_content_defaults(monkeypatch):
    async def fake_fetch():
        return FAKE_WEATHER
    monkeypatch.setattr(ithaca_routes, "_fetch_weather", fake_fetch)

    result = await do_get_ithaca_weather("")
    assert result["exit_code"] == 0


async def test_get_ithaca_weather_not_configured(monkeypatch):
    async def fake_fetch():
        raise HTTPException(503, "OPENWEATHER_API_KEY is not configured")
    monkeypatch.setattr(ithaca_routes, "_fetch_weather", fake_fetch)

    result = await do_get_ithaca_weather("{}")
    assert result["exit_code"] == 1
    assert "OPENWEATHER_API_KEY" in result["error"]
    assert "response" not in result


async def test_get_ithaca_weather_network_error_is_caught(monkeypatch):
    # A raw network failure (timeout/DNS/connection-refused) reaching
    # OpenWeatherMap must produce a clean tool-error dict, not an unhandled
    # exception propagating out of the tool call.
    async def fake_fetch():
        raise ConnectionError("Cannot connect to host api.openweathermap.org")
    monkeypatch.setattr(ithaca_routes, "_fetch_weather", fake_fetch)

    result = await do_get_ithaca_weather("{}")
    assert result["exit_code"] == 1
    assert "api.openweathermap.org" in result["error"]


async def test_get_ithaca_software_updates_network_error_is_caught(monkeypatch):
    async def fake_fetch():
        raise ConnectionError("Cannot connect to Obsidian host")
    monkeypatch.setattr(ithaca_routes, "_fetch_digest", fake_fetch)

    result = await do_get_ithaca_software_updates("{}")
    assert result["exit_code"] == 1
    assert "Obsidian" in result["error"]


async def test_get_ithaca_weather_refresh_bypasses_cache(monkeypatch):
    calls = {"n": 0}

    async def fake_fetch():
        calls["n"] += 1
        return FAKE_WEATHER
    monkeypatch.setattr(ithaca_routes, "_fetch_weather", fake_fetch)

    await do_get_ithaca_weather("{}")
    await do_get_ithaca_weather("{}")
    assert calls["n"] == 1  # second call served from cache

    await do_get_ithaca_weather('{"refresh": true}')
    assert calls["n"] == 2  # refresh bypassed the cache


async def test_get_ithaca_software_updates_success(monkeypatch):
    async def fake_fetch():
        return FAKE_DIGEST_WITH_UPDATES
    monkeypatch.setattr(ithaca_routes, "_fetch_digest", fake_fetch)

    result = await do_get_ithaca_software_updates("{}")
    assert result["exit_code"] == 0
    assert "1 of 2 apps need an update" in result["response"]
    assert "Radarr" in result["response"] and "update available" in result["response"]
    assert "v6.3.1 → v6.4.0" in result["response"]
    assert "No Flatpak updates available." in result["response"]
    assert len(result["rows"]) == 2


async def test_get_ithaca_software_updates_no_section(monkeypatch):
    async def fake_fetch():
        return FAKE_DIGEST_NO_UPDATES_SECTION
    monkeypatch.setattr(ithaca_routes, "_fetch_digest", fake_fetch)

    result = await do_get_ithaca_software_updates("{}")
    assert result["exit_code"] == 0
    assert result["rows"] == []
    assert "No Software Updates section" in result["response"]


async def test_get_ithaca_software_updates_not_configured(monkeypatch):
    async def fake_fetch():
        raise HTTPException(503, "OBSIDIAN_API_TOKEN is not configured")
    monkeypatch.setattr(ithaca_routes, "_fetch_digest", fake_fetch)

    result = await do_get_ithaca_software_updates("{}")
    assert result["exit_code"] == 1
    assert "OBSIDIAN_API_TOKEN" in result["error"]


def test_tools_registered_in_dispatcher():
    from src.agent_tools import TOOL_TAGS
    assert "get_ithaca_weather" in TOOL_TAGS
    assert "get_ithaca_software_updates" in TOOL_TAGS


def test_tools_have_function_schemas():
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    names = [s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS]
    assert names.count("get_ithaca_weather") == 1
    assert names.count("get_ithaca_software_updates") == 1


async def test_execute_tool_block_dispatches_by_name(monkeypatch):
    async def fake_fetch():
        return FAKE_WEATHER
    monkeypatch.setattr(ithaca_routes, "_fetch_weather", fake_fetch)

    from src.agent_tools import ToolBlock, execute_tool_block
    desc, result = await execute_tool_block(ToolBlock("get_ithaca_weather", "{}"))
    assert desc == "get_ithaca_weather"
    assert result["exit_code"] == 0
