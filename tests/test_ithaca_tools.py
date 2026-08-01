"""Tests for src/tools/ithaca.py — the agent-facing get_home_weather and
query_ithaca_tile tools. get_home_weather wraps extensions/ithaca/weather.py
so the AI sees the same cached data as the Weather tile. query_ithaca_tile
wraps extensions/ithaca/tiles.py so the AI can list/run any user-defined
tile — it never accepts arbitrary SQL, only a tile id to run."""

import pytest
from fastapi import HTTPException

import extensions.ithaca.weather as weather
from src.tools.ithaca import do_get_home_weather, do_query_ithaca_tile


@pytest.fixture(autouse=True)
def _reset_weather_cache():
    weather._cache.invalidate()
    yield
    weather._cache.invalidate()


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


async def test_get_home_weather_success(monkeypatch):
    async def fake_fetch():
        return FAKE_WEATHER
    monkeypatch.setattr(weather, "_fetch_weather", fake_fetch)

    result = await do_get_home_weather("{}")
    assert result["exit_code"] == 0
    assert "London" in result["response"]
    assert "scattered clouds" in result["response"]
    assert "21°C" in result["response"]
    assert "45% chance of rain" in result["response"]
    assert result["weather"] == FAKE_WEATHER


async def test_get_home_weather_empty_content_defaults(monkeypatch):
    async def fake_fetch():
        return FAKE_WEATHER
    monkeypatch.setattr(weather, "_fetch_weather", fake_fetch)

    result = await do_get_home_weather("")
    assert result["exit_code"] == 0


async def test_get_home_weather_not_configured(monkeypatch):
    async def fake_fetch():
        raise HTTPException(503, "OPENWEATHER_API_KEY is not configured")
    monkeypatch.setattr(weather, "_fetch_weather", fake_fetch)

    result = await do_get_home_weather("{}")
    assert result["exit_code"] == 1
    assert "OPENWEATHER_API_KEY" in result["error"]
    assert "response" not in result


async def test_get_home_weather_network_error_is_caught(monkeypatch):
    # A raw network failure (timeout/DNS/connection-refused) reaching
    # OpenWeatherMap must produce a clean tool-error dict, not an unhandled
    # exception propagating out of the tool call.
    async def fake_fetch():
        raise ConnectionError("Cannot connect to host api.openweathermap.org")
    monkeypatch.setattr(weather, "_fetch_weather", fake_fetch)

    result = await do_get_home_weather("{}")
    assert result["exit_code"] == 1
    assert "api.openweathermap.org" in result["error"]


async def test_get_home_weather_refresh_bypasses_cache(monkeypatch):
    calls = {"n": 0}

    async def fake_fetch():
        calls["n"] += 1
        return FAKE_WEATHER
    monkeypatch.setattr(weather, "_fetch_weather", fake_fetch)

    await do_get_home_weather("{}")
    await do_get_home_weather("{}")
    assert calls["n"] == 1  # second call served from cache

    await do_get_home_weather('{"refresh": true}')
    assert calls["n"] == 2  # refresh bypassed the cache


# ─── query_ithaca_tile ──────────────────────────────────────────────────────


async def test_query_ithaca_tile_lists_when_no_tile_id(monkeypatch):
    import extensions.ithaca.tiles as tiles_mod
    monkeypatch.setattr(tiles_mod, "list_tile_configs", lambda: [
        {"id": "flagged_apps", "title": "Flagged Apps", "notes": "review queue"},
    ])

    result = await do_query_ithaca_tile("{}")
    assert result["exit_code"] == 0
    assert "flagged_apps" in result["response"]
    assert "Flagged Apps" in result["response"]
    assert result["tiles"][0]["id"] == "flagged_apps"


async def test_query_ithaca_tile_lists_empty_state(monkeypatch):
    import extensions.ithaca.tiles as tiles_mod
    monkeypatch.setattr(tiles_mod, "list_tile_configs", lambda: [])

    result = await do_query_ithaca_tile("{}")
    assert result["exit_code"] == 0
    assert result["tiles"] == []
    assert "No custom Ithaca tiles" in result["response"]


async def test_query_ithaca_tile_runs_table_tile(monkeypatch):
    import extensions.ithaca.tiles as tiles_mod

    async def fake_run_tile(tile_id, force=False):
        return {"type": "table", "columns": ["app_name", "host"], "rows": [
            {"app_name": "Radarr", "host": "latitude"},
        ]}
    monkeypatch.setattr(tiles_mod, "run_tile", fake_run_tile)

    result = await do_query_ithaca_tile('{"tile_id": "flagged_apps"}')
    assert result["exit_code"] == 0
    assert "Radarr" in result["response"]
    assert "latitude" in result["response"]


async def test_query_ithaca_tile_runs_chart_tile(monkeypatch):
    import extensions.ithaca.tiles as tiles_mod

    async def fake_run_tile(tile_id, force=False):
        return {"type": "bar", "points": [{"label": "auto_update", "value": 2.0}]}
    monkeypatch.setattr(tiles_mod, "run_tile", fake_run_tile)

    result = await do_query_ithaca_tile('{"tile_id": "breakdown"}')
    assert result["exit_code"] == 0
    assert "auto_update" in result["response"]
    assert "2.0" in result["response"]


async def test_query_ithaca_tile_unknown_id(monkeypatch):
    import extensions.ithaca.tiles as tiles_mod

    async def fake_run_tile(tile_id, force=False):
        raise ValueError(f"No tile config with id '{tile_id}'")
    monkeypatch.setattr(tiles_mod, "run_tile", fake_run_tile)

    result = await do_query_ithaca_tile('{"tile_id": "nope"}')
    assert result["exit_code"] == 1
    assert "nope" in result["error"]


async def test_query_ithaca_tile_query_failure(monkeypatch):
    import extensions.ithaca.tiles as tiles_mod
    from core.external_db import ExternalDbError

    async def fake_run_tile(tile_id, force=False):
        raise ExternalDbError("connection refused")
    monkeypatch.setattr(tiles_mod, "run_tile", fake_run_tile)

    result = await do_query_ithaca_tile('{"tile_id": "flagged_apps"}')
    assert result["exit_code"] == 1
    assert "connection refused" in result["error"]


def test_tools_registered_in_dispatcher():
    from src.agent_tools import TOOL_TAGS
    assert "get_home_weather" in TOOL_TAGS
    assert "query_ithaca_tile" in TOOL_TAGS


def test_tools_have_function_schemas():
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    names = [s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS]
    assert names.count("get_home_weather") == 1
    assert names.count("query_ithaca_tile") == 1


async def test_execute_tool_block_dispatches_by_name(monkeypatch):
    async def fake_fetch():
        return FAKE_WEATHER
    monkeypatch.setattr(weather, "_fetch_weather", fake_fetch)

    from src.agent_tools import ToolBlock, execute_tool_block
    desc, result = await execute_tool_block(ToolBlock("get_home_weather", "{}"))
    assert desc == "get_home_weather"
    assert result["exit_code"] == 0
