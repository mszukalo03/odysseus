"""Tests for extensions/ithaca/backend.py — digest markdown parsing, per-app
link config merging, and the bearer-token scope gate for the Ithaca hub."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import extensions.ithaca.backend as ithaca


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
    from routes.api_token_routes import _allowed_scopes
    assert "ithaca:read" in _allowed_scopes()


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


def test_obsidian_settings_reads_all_four_fields(monkeypatch):
    import src.settings as settings_mod
    saved = {"obsidian_vault_path": "/vault", "obsidian_api_url": "http://host:27123/",
             "obsidian_api_token": "T", "obsidian_digest_dir": "/digests/"}
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: saved.get(key, ""))
    cfg = ithaca._obsidian_settings()
    assert cfg == {"vault_path": "/vault", "base": "http://host:27123", "token": "T",
                   "digest_dir": "digests"}


def test_ithaca_settings_keys_registered_for_admin_settings_save():
    from src.settings import DEFAULT_SETTINGS
    for key in ("openweather_api_key", "openweather_lat", "openweather_lon", "openweather_units",
                "obsidian_vault_path", "obsidian_api_url", "obsidian_api_token", "obsidian_digest_dir"):
        assert key in DEFAULT_SETTINGS


def test_digest_cache_key_separates_vault_and_api_sources():
    # Switching source must not serve the other source's cached payload.
    vault = ithaca.digest_cache_key({"vault_path": "/vault", "base": "", "digest_dir": "d"})
    api = ithaca.digest_cache_key({"vault_path": "", "base": "http://h:27123", "digest_dir": "d"})
    assert vault != api


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
    assert not is_secret_key("obsidian_vault_path")


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

    router = ithaca.setup()
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
        "vault_path": "", "base": "http://100.96.143.85:27123", "token": "t",
        "digest_dir": "daily-digest",
    })

    router = ithaca.setup()
    endpoint = _get_endpoint(router, "/api/ithaca/digest")
    req = SimpleNamespace(state=SimpleNamespace(api_token=False), headers={})
    with pytest.raises(HTTPException) as exc:
        await endpoint(req)
    assert exc.value.status_code == 502
    assert "100.96.143.85:27123" in exc.value.detail
    assert "ConnectError" in exc.value.detail
    assert "Connection refused" in exc.value.detail
    # The failure mode that actually bit us: the REST API is hosted inside the
    # Obsidian desktop app, so a closed app looks like an unreachable host on
    # every address. Say so, and point at the vault-path alternative.
    assert "Obsidian is open" in exc.value.detail
    assert "OBSIDIAN_VAULT_PATH" in exc.value.detail


# ─── YAML frontmatter ───────────────────────────────────────────────────────


def test_strip_frontmatter_removes_obsidian_property_block():
    md = (
        "---\n"
        "title: Daily Digest - 2026-07-25\n"
        "tags:\n"
        "  - homelab\n"
        "---\n"
        "\n"
        "## Weather\n"
        "clear\n"
    )
    assert ithaca.strip_frontmatter(md).strip() == "## Weather\nclear"


def test_strip_frontmatter_leaves_plain_and_unterminated_documents():
    plain = "## Weather\nclear\n"
    assert ithaca.strip_frontmatter(plain) == plain
    # A lone "---" is a horizontal rule, not an unterminated property block —
    # dropping the rest of the file would silently blank every tile.
    unterminated = "---\ntitle: x\n## Weather\nclear\n"
    assert ithaca.strip_frontmatter(unterminated) == unterminated


def test_frontmatter_does_not_leak_into_preamble_section():
    md = "---\ntitle: x\ndate: 2026-07-25\n---\n## Weather\nclear\n"
    sections = ithaca.parse_digest_sections(md)
    assert "title: x" not in sections.get("", "")
    assert sections["Weather"] == "clear"


# ─── Secret redaction ───────────────────────────────────────────────────────


def test_redact_known_secret_values_anywhere_in_the_note():
    token = "38152caf60a9201deed1d7c221e8566f81e3fe736b6d79116a1001a2d5a2106a"
    md = f"## Notes\nBearer {token}\ncurl -H 'X-Key: {token}' localhost\n"
    out = ithaca.redact_secrets(md, (token,))
    assert token not in out
    assert "[redacted]" in out


def test_redact_bearer_and_authorization_without_knowing_the_value():
    # The credential of some *other* system pasted into the same note: no
    # configured value to match against, so the shape has to carry it.
    md = "## Notes\nBearer abcdef0123456789abcdef\nAuthorization: Basic dXNlcjpwYXNz\n"
    out = ithaca.redact_secrets(md)
    assert "abcdef0123456789abcdef" not in out
    assert "dXNlcjpwYXNz" not in out
    # The label survives so the reader can see something was there.
    assert "Bearer [redacted]" in out
    assert "Authorization: [redacted]" in out


def test_redact_leaves_release_note_prose_and_short_hashes_alone():
    # Regression guard: the patterns must not chew through the digest's actual
    # content. "Basic Auth" prose and a 40-char git SHA are not credentials.
    sha = "a" * 40
    md = (
        "| **Prowlarr** | | | Yes | v2.5.2 | fixes HTTP Basic Auth handling for "
        f"non-ASCII characters; see {sha} |\n"
    )
    out = ithaca.redact_secrets(md, ("",))  # blank configured secret must be ignored
    assert out == md


def test_redact_ignores_too_short_known_secrets():
    # A 3-char "secret" would otherwise redact fragments of every word.
    md = "Radarr release notes\n"
    assert ithaca.redact_secrets(md, ("arr",)) == md


async def test_digest_payload_never_carries_the_configured_token(tmp_path, monkeypatch):
    token = "b" * 64
    digest_dir = tmp_path / "daily-digest"
    digest_dir.mkdir()
    (digest_dir / "2026-07-25-digest.md").write_text(
        "## Software Updates\n"
        "| **App** | **Update?** |\n| --- | --- |\n| **Radarr** | Yes |\n\n"
        f"## Notes\nBearer {token}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ithaca, "_obsidian_settings", lambda: {
        "vault_path": str(tmp_path), "base": "", "token": token, "digest_dir": "daily-digest",
    })

    data = await ithaca._fetch_digest()
    assert token not in json.dumps(data)
    assert data["sections"]["Notes"] == "Bearer [redacted]"
    # ...and the rest of the digest still parses.
    assert [r["app"] for r in data["software_updates"]["rows"]] == ["Radarr"]


# ─── Reading the digest from the vault on disk ───────────────────────────────

REAL_DIGEST = """---
title: Daily Digest - 2026-07-25
date: 2026-07-25
type: daily-digest
tags:
  - homelab
  - digest
---

## Weather
**Evening**: overcast clouds, 23–26°C, 64% humidity

## Software Updates
| **App** | **Hosted On** | **Currently Deployed** | **Update?** | **Version No.** | **Desc** |
| --- | --- | --- | --- | --- | --- |
| **Radarr** | radarr.host.example.com | v6.4.x | Yes | Stable v6.4 Release | Trakt pagination fixes; improved cover caching |
| **Prowlarr** |  |  | Yes | v2.5.2 Stable Release | Fixes missing download client categories |
| **Jellyfin** |  |  | No | v12.0-rc3 Preview Release | Third release candidate; MP4 subtitle probe fixes |

No Flatpak updates available.

## Notes
"""


def _write_vault(tmp_path, files):
    digest_dir = tmp_path / "daily-digest"
    digest_dir.mkdir()
    for name, body in files.items():
        (digest_dir / name).write_text(body, encoding="utf-8")
    return {"vault_path": str(tmp_path), "base": "", "token": "", "digest_dir": "daily-digest"}


def test_read_digest_from_disk_picks_newest_by_date_prefix(tmp_path):
    cfg = _write_vault(tmp_path, {
        "2026-07-18-digest.md": "## Software Updates\nold\n",
        "2026-07-25-digest.md": REAL_DIGEST,
        "template.md": "not a digest\n",
        "README.txt": "ignored\n",
    })
    filename, text = ithaca._read_digest_from_disk(cfg)
    assert filename == "2026-07-25-digest.md"
    assert "Radarr" in text


def test_read_digest_from_disk_missing_dir_names_the_path(tmp_path):
    cfg = {"vault_path": str(tmp_path), "base": "", "token": "", "digest_dir": "nope"}
    with pytest.raises(HTTPException) as exc:
        ithaca._read_digest_from_disk(cfg)
    assert exc.value.status_code == 503
    assert "nope" in exc.value.detail


def test_read_digest_from_disk_empty_dir_is_404(tmp_path):
    cfg = _write_vault(tmp_path, {})
    with pytest.raises(HTTPException) as exc:
        ithaca._read_digest_from_disk(cfg)
    assert exc.value.status_code == 404


def test_digest_dir_cannot_escape_the_vault_root(tmp_path):
    (tmp_path / "vault").mkdir()
    (tmp_path / "secrets").mkdir()
    cfg = {"vault_path": str(tmp_path / "vault"), "base": "", "token": "",
           "digest_dir": "../secrets"}
    with pytest.raises(HTTPException) as exc:
        ithaca._digest_dir_on_disk(cfg)
    assert exc.value.status_code == 400


async def test_fetch_digest_prefers_the_vault_and_parses_the_updates_table(tmp_path, monkeypatch):
    cfg = _write_vault(tmp_path, {"2026-07-25-digest.md": REAL_DIGEST})
    # An API base is configured too — the vault must still win, so the tile
    # never depends on the Obsidian desktop app being open.
    cfg["base"] = "http://127.0.0.1:1/unreachable"
    monkeypatch.setattr(ithaca, "_obsidian_settings", lambda: cfg)

    data = await ithaca._fetch_digest()
    assert data["source"] == "vault"
    assert data["filename"] == "2026-07-25-digest.md"
    assert data["date"] == "2026-07-25"

    su = data["software_updates"]
    assert [r["app"] for r in su["rows"]] == ["Radarr", "Prowlarr", "Jellyfin"]
    assert [r["update_available"] for r in su["rows"]] == [True, True, False]
    assert su["note"] == "No Flatpak updates available."
    # Per-app links still get merged in for the vault source.
    assert su["rows"][0]["repo_url"] == "https://github.com/Radarr/Radarr"
    # Frontmatter must not surface as a section.
    assert "title: Daily Digest" not in data["sections"].get("", "")


async def test_fetch_digest_with_no_source_configured_explains_both_options(monkeypatch):
    monkeypatch.setattr(ithaca, "_obsidian_settings", lambda: {
        "vault_path": "", "base": "", "token": "", "digest_dir": "daily-digest",
    })
    with pytest.raises(HTTPException) as exc:
        await ithaca._fetch_digest()
    assert exc.value.status_code == 503
    assert "OBSIDIAN_VAULT_PATH" in exc.value.detail
    assert "OBSIDIAN_API_URL" in exc.value.detail
