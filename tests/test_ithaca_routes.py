"""Tests for routes/ithaca_routes.py — digest markdown parsing, per-app link
config merging, and the bearer-token scope gate for the Ithaca hub."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.ithaca_routes as ithaca


# ─── parse_digest_sections ──────────────────────────────────────────────────


def test_sections_split_by_heading_any_level():
    md = (
        "# GitHub Weekly News Report\n"
        "Generated: 2026-07-20\n"
        "## Weather\n"
        "**Morning**: clear sky\n"
        "\n"
        "## Software Updates\n"
        "| a | b |\n"
        "### Nested\n"
        "nested body\n"
    )
    sections = ithaca.parse_digest_sections(md)
    assert list(sections.keys()) == [
        "GitHub Weekly News Report", "Weather", "Software Updates", "Nested",
    ]
    assert sections["Weather"] == "**Morning**: clear sky"
    assert sections["Nested"] == "nested body"


def test_sections_preamble_and_duplicate_headings():
    md = "intro text\n## Weather\nold\n## Weather\nnew\n"
    sections = ithaca.parse_digest_sections(md)
    assert sections[""] == "intro text"
    # a re-patched section supersedes stale content above it
    assert sections["Weather"] == "new"


def test_sections_empty_document():
    assert ithaca.parse_digest_sections("") == {}


# ─── parse_updates_table ────────────────────────────────────────────────────

WORKFLOW_TABLE = (
    "| **App** | **Hosted On** | **Currently Deployed** | **Update?** | **Version No.** | **Desc** |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
    "| **Radarr** | latitude | v6.3.1 | Yes | v6.3.1 → v6.4.0 | Fixed Trakt imports; caching |\n"
    "| **Jellyfin** | nuc | 11.9 | No | — | RC only \\| informational |\n"
    "\n"
    "No Flatpak updates available.\n"
)


def test_updates_table_parses_workflow_shape():
    result = ithaca.parse_updates_table(WORKFLOW_TABLE)
    rows = result["rows"]
    assert len(rows) == 2
    assert rows[0]["app"] == "Radarr"
    assert rows[0]["hosted_on"] == "latitude"
    assert rows[0]["deployed"] == "v6.3.1"
    assert rows[0]["update_available"] is True
    assert rows[1]["app"] == "Jellyfin"
    assert rows[1]["update_available"] is False
    # escaped pipes inside a cell survive
    assert rows[1]["desc"] == "RC only | informational"
    assert result["note"] == "No Flatpak updates available."


def test_updates_table_keeps_unknown_columns():
    md = (
        "| **App** | **Repo URL** |\n"
        "| --- | --- |\n"
        "| **Radarr** | https://github.com/Radarr/Radarr |\n"
    )
    rows = ithaca.parse_updates_table(md)["rows"]
    assert rows[0]["repo_url"] == "https://github.com/Radarr/Radarr"


def test_updates_table_empty_section():
    result = ithaca.parse_updates_table("")
    assert result == {"rows": [], "note": ""}


# ─── per-app link config ────────────────────────────────────────────────────


def test_app_links_defaults_present():
    links = ithaca.load_app_links()
    assert links["Radarr"]["repo_url"] == "https://github.com/Radarr/Radarr"
    assert links["Jellyseerr/Seerr"]["repo_url"] == "https://github.com/seerr-team/seerr"


def test_app_links_file_overrides_and_extends(tmp_path, monkeypatch):
    cfg = tmp_path / "ithaca.json"
    cfg.write_text(json.dumps({
        "apps": {
            "Radarr": {"ssh_host": "latitude", "path": "/opt/radarr"},
            "MyApp": {"repo_url": "https://git.example.com/me/myapp"},
        }
    }), encoding="utf-8")
    monkeypatch.setattr(ithaca, "ITHACA_CONFIG_FILE", str(cfg))
    links = ithaca.load_app_links()
    # file wins per-field, defaults fill what the file omits
    assert links["Radarr"]["ssh_host"] == "latitude"
    assert links["Radarr"]["path"] == "/opt/radarr"
    assert links["Radarr"]["repo_url"] == "https://github.com/Radarr/Radarr"
    assert links["MyApp"]["repo_url"] == "https://git.example.com/me/myapp"


def test_app_links_corrupt_file_falls_back_to_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "ithaca.json"
    cfg.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(ithaca, "ITHACA_CONFIG_FILE", str(cfg))
    links = ithaca.load_app_links()
    assert links["Radarr"]["repo_url"] == "https://github.com/Radarr/Radarr"


def test_match_app_links_case_insensitive():
    links = {"Radarr": {"repo_url": "x"}}
    assert ithaca._match_app_links("radarr", links) == {"repo_url": "x"}
    assert ithaca._match_app_links("Sonarr", links) is None


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
    from routes.api_token_routes import ALLOWED_SCOPES
    assert "ithaca:read" in ALLOWED_SCOPES


# ─── Settings > Integrations override env vars ──────────────────────────────


def test_setting_or_env_prefers_saved_setting(monkeypatch):
    import src.settings as settings_mod
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: "from-settings")
    monkeypatch.setenv("OPENWEATHER_API_KEY", "from-env")
    assert ithaca._setting_or_env("openweather_api_key", "OPENWEATHER_API_KEY") == "from-settings"


def test_setting_or_env_falls_back_to_env_when_setting_blank(monkeypatch):
    import src.settings as settings_mod
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: "")
    monkeypatch.setenv("OPENWEATHER_API_KEY", "from-env")
    assert ithaca._setting_or_env("openweather_api_key", "OPENWEATHER_API_KEY") == "from-env"


def test_setting_or_env_uses_default_when_both_unset(monkeypatch):
    import src.settings as settings_mod
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: "")
    monkeypatch.delenv("OPENWEATHER_UNITS", raising=False)
    assert ithaca._setting_or_env("openweather_units", "OPENWEATHER_UNITS", "metric") == "metric"


def test_weather_settings_reads_all_four_fields(monkeypatch):
    import src.settings as settings_mod
    saved = {"openweather_api_key": "K", "openweather_lat": "1.1", "openweather_lon": "2.2", "openweather_units": "imperial"}
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: saved.get(key, ""))
    cfg = ithaca._weather_settings()
    assert cfg == {"api_key": "K", "lat": "1.1", "lon": "2.2", "units": "imperial"}


def test_obsidian_settings_reads_all_three_fields(monkeypatch):
    import src.settings as settings_mod
    saved = {"obsidian_api_url": "http://host:27123/", "obsidian_api_token": "T", "obsidian_digest_dir": "/digests/"}
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: saved.get(key, ""))
    cfg = ithaca._obsidian_settings()
    assert cfg == {"base": "http://host:27123", "token": "T", "digest_dir": "digests"}


def test_ithaca_settings_keys_registered_for_admin_settings_save():
    from src.settings import DEFAULT_SETTINGS
    for key in ("openweather_api_key", "openweather_lat", "openweather_lon", "openweather_units",
                "obsidian_api_url", "obsidian_api_token", "obsidian_digest_dir"):
        assert key in DEFAULT_SETTINGS


def test_ithaca_secret_keys_are_masked_by_settings_scrub():
    from src.settings_scrub import is_secret_key
    assert is_secret_key("openweather_api_key")
    assert is_secret_key("obsidian_api_token")
    # Non-secret config fields must NOT be masked, or the settings UI can't
    # display/edit them for non-admin GET callers.
    assert not is_secret_key("openweather_lat")
    assert not is_secret_key("openweather_lon")
    assert not is_secret_key("openweather_units")
    assert not is_secret_key("obsidian_api_url")
    assert not is_secret_key("obsidian_digest_dir")


# ─── HTTP route error diagnostics ───────────────────────────────────────────


def _get_endpoint(router, path):
    return next(r.endpoint for r in router.routes if r.path == path)


async def test_weather_route_reports_real_network_error(monkeypatch):
    # A raw connection failure must surface the underlying reason (host,
    # exception message), not just the exception class name — this is what
    # the user actually reads when the tile fails to load.
    import httpx as httpx_mod

    async def fake_fetch():
        raise httpx_mod.ConnectError("[Errno 111] Connection refused")
    monkeypatch.setattr(ithaca, "_fetch_weather", fake_fetch)

    router = ithaca.setup_ithaca_routes()
    endpoint = _get_endpoint(router, "/api/ithaca/weather")
    req = SimpleNamespace(state=SimpleNamespace(api_token=False), headers={})
    with pytest.raises(HTTPException) as exc:
        await endpoint(req)
    assert exc.value.status_code == 502
    assert "ConnectError" in exc.value.detail
    assert "Connection refused" in exc.value.detail


async def test_digest_route_reports_real_network_error_and_host(monkeypatch):
    import httpx as httpx_mod

    async def fake_fetch():
        raise httpx_mod.ConnectError("[Errno 111] Connection refused")
    monkeypatch.setattr(ithaca, "_fetch_digest", fake_fetch)
    monkeypatch.setattr(ithaca, "_obsidian_settings", lambda: {
        "base": "http://100.96.143.85:27123", "token": "t", "digest_dir": "daily-digest",
    })

    router = ithaca.setup_ithaca_routes()
    endpoint = _get_endpoint(router, "/api/ithaca/digest")
    req = SimpleNamespace(state=SimpleNamespace(api_token=False), headers={})
    with pytest.raises(HTTPException) as exc:
        await endpoint(req)
    assert exc.value.status_code == 502
    assert "100.96.143.85:27123" in exc.value.detail
    assert "ConnectError" in exc.value.detail
    assert "Connection refused" in exc.value.detail
