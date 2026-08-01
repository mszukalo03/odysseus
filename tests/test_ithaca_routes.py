"""Tests for extensions/ithaca/backend.py (route wiring + bearer-token scope
gate) and extensions/ithaca/weather.py (the one persistent built-in tile).

The Software-Updates/Obsidian-digest tile and its SSH-open action were
removed — Weather is now the only built-in tile, and everything else is a
user-defined tile (extensions/ithaca/tiles.py, covered in test_tiles.py-style
suites for that module)."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import extensions.ithaca.backend as ithaca
import extensions.ithaca.weather as weather


# ─── bearer-token scope gate ────────────────────────────────────────────────


def _fake_request(api_token=False, scopes=None):
    state = SimpleNamespace(api_token=api_token, api_token_scopes=scopes or [])
    return SimpleNamespace(state=state)


def test_scope_gate_cookie_session_passes():
    ithaca._require_read_access(_fake_request(api_token=False))


def test_scope_gate_token_with_scope_passes():
    ithaca._require_read_access(_fake_request(api_token=True, scopes=["ithaca:read"]))


def test_scope_gate_token_without_scope_rejected():
    with pytest.raises(HTTPException) as exc:
        ithaca._require_read_access(_fake_request(api_token=True, scopes=["chat"]))
    assert exc.value.status_code == 403


def test_ithaca_scope_registered_for_tokens():
    from routes.api_token_routes import _allowed_scopes
    assert "ithaca:read" in _allowed_scopes()


# ─── Settings > Integrations override env vars (extensions/ithaca/weather.py) ─


def test_setting_or_env_prefers_saved_setting(monkeypatch):
    import src.settings as settings_mod
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: "from-settings")
    monkeypatch.setenv("OPENWEATHER_API_KEY", "from-env")
    assert weather._setting_or_env("openweather_api_key", "OPENWEATHER_API_KEY") == "from-settings"


def test_setting_or_env_falls_back_to_env_when_setting_blank(monkeypatch):
    import src.settings as settings_mod
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: "")
    monkeypatch.setenv("OPENWEATHER_API_KEY", "from-env")
    assert weather._setting_or_env("openweather_api_key", "OPENWEATHER_API_KEY") == "from-env"


def test_setting_or_env_uses_default_when_both_unset(monkeypatch):
    import src.settings as settings_mod
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: "")
    monkeypatch.delenv("OPENWEATHER_UNITS", raising=False)
    assert weather._setting_or_env("openweather_units", "OPENWEATHER_UNITS", "metric") == "metric"


def test_weather_settings_reads_all_four_fields(monkeypatch):
    import src.settings as settings_mod
    saved = {"openweather_api_key": "K", "openweather_lat": "1.1", "openweather_lon": "2.2", "openweather_units": "imperial"}
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: saved.get(key, ""))
    cfg = weather.weather_settings()
    assert cfg == {"api_key": "K", "lat": "1.1", "lon": "2.2", "units": "imperial"}


def test_ithaca_settings_keys_registered_for_admin_settings_save():
    from src.settings import DEFAULT_SETTINGS
    for key in ("openweather_api_key", "openweather_lat", "openweather_lon", "openweather_units"):
        assert key in DEFAULT_SETTINGS


def test_ithaca_secret_keys_are_masked_by_settings_scrub():
    from src.settings_scrub import is_secret_key
    assert is_secret_key("openweather_api_key")
    # Non-secret config fields must NOT be masked, or the settings UI can't
    # display/edit them for non-admin GET callers.
    assert not is_secret_key("openweather_lat")
    assert not is_secret_key("openweather_lon")
    assert not is_secret_key("openweather_units")


# ─── HTTP route error diagnostics ───────────────────────────────────────────


def _get_endpoint(router, path):
    return next(r.endpoint for r in router.routes if r.path == path)


async def test_weather_route_reports_real_network_error(monkeypatch):
    # A raw connection failure must surface the underlying reason (host,
    # exception message), not just the exception class name — this is what
    # the user actually reads when the tile fails to load.
    import httpx as httpx_mod

    async def fake_get_weather(refresh=False):
        raise httpx_mod.ConnectError("[Errno 111] Connection refused")
    monkeypatch.setattr(ithaca._weather, "get_weather", fake_get_weather)

    router = ithaca.setup()
    endpoint = _get_endpoint(router, "/api/ithaca/weather")
    req = SimpleNamespace(state=SimpleNamespace(api_token=False), headers={})
    with pytest.raises(HTTPException) as exc:
        await endpoint(req)
    assert exc.value.status_code == 502
    assert "ConnectError" in exc.value.detail
    assert "Connection refused" in exc.value.detail


async def test_get_weather_caches_and_refresh_bypasses(monkeypatch):
    weather._cache.invalidate()
    import src.settings as settings_mod
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: {
        "openweather_lat": "1.1", "openweather_lon": "2.2",
    }.get(key, ""))

    calls = {"n": 0}

    async def fake_fetch():
        calls["n"] += 1
        return {"location": "X"}
    monkeypatch.setattr(weather, "_fetch_weather", fake_fetch)

    await weather.get_weather()
    await weather.get_weather()
    assert calls["n"] == 1  # second call served from cache

    await weather.get_weather(refresh=True)
    assert calls["n"] == 2  # refresh bypassed the cache
    weather._cache.invalidate()
