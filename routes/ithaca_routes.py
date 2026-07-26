"""
ithaca_routes.py

Ithaca hub — backend for the dashboard/homepage tile screen.

The hub is the in-app frontend for the external n8n "Monday" digest workflow,
which PATCHes weather + software-update sections into a daily-digest markdown
file in an Obsidian vault (via the Obsidian Local REST API). Endpoints:

* GET  /api/ithaca/weather   — live OpenWeatherMap current conditions +
  3-hourly forecast, queried directly (NOT from the digest), TTL-cached.
* GET  /api/ithaca/digest    — latest daily-digest markdown from the Obsidian
  Local REST API, split into heading sections; the "Software Updates" table
  is parsed into structured rows and enriched with per-app links.
* GET  /api/ithaca/config    — per-app link config (git repo URL, ssh host,
  deploy path) used by the Software Updates tile. PUT is admin-only.
* POST /api/ithaca/ssh/open  — admin-only: ssh into an app's host and list
  its deploy path (the action behind the tile's "deployed on" link).

Also exposed to the AI agent as read-only tools (src/tools/ithaca.py):
get_ithaca_weather, get_ithaca_software_updates.

Config: OPENWEATHER_API_KEY, OPENWEATHER_LAT, OPENWEATHER_LON,
OPENWEATHER_UNITS, OBSIDIAN_API_URL, OBSIDIAN_API_TOKEN, OBSIDIAN_DIGEST_DIR
(see .env.example) — or the same keys settable in Settings > Integrations
("Ithaca Hub" card), which take priority over the env vars when non-empty
(see `_setting_or_env`).
"""

import asyncio
import json
import logging
import os
import re
import shlex
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.atomic_io import atomic_write_json
from core.constants import DATA_DIR
from core.middleware import require_admin

logger = logging.getLogger(__name__)

ITHACA_CONFIG_FILE = os.path.join(DATA_DIR, "ithaca.json")

# Bearer-token callers need this scope (see routes/api_token_routes.py).
# Cookie-session callers already passed AuthMiddleware, so they go through
# untouched — same split the feeds routes use.
ITHACA_READ_SCOPES = {"ithaca:read"}

WEATHER_CACHE_TTL = 10 * 60   # OpenWeatherMap free tier: no need to re-poll faster
DIGEST_CACHE_TTL = 5 * 60     # digest file changes weekly; keep tile loads instant
FORECAST_SLOTS = 9            # 9 × 3h ≈ next 27 hours

# Seed links for the projects tracked by the n8n workflow. data/ithaca.json
# overrides/extends these — the file wins per-app, defaults fill the gaps.
DEFAULT_APP_LINKS: Dict[str, Dict[str, str]] = {
    "Radarr":       {"repo_url": "https://github.com/Radarr/Radarr", "ssh_host": "", "path": ""},
    "Sonarr":       {"repo_url": "https://github.com/Sonarr/Sonarr", "ssh_host": "", "path": ""},
    "Prowlarr":     {"repo_url": "https://github.com/Prowlarr/Prowlarr", "ssh_host": "", "path": ""},
    "Transmission": {"repo_url": "https://github.com/transmission/transmission", "ssh_host": "", "path": ""},
    "Jellyfin":     {"repo_url": "https://github.com/jellyfin/jellyfin", "ssh_host": "", "path": ""},
    "Seerr":        {"repo_url": "https://github.com/seerr-team/seerr", "ssh_host": "", "path": ""},
    "Jellyseerr/Seerr": {"repo_url": "https://github.com/seerr-team/seerr", "ssh_host": "", "path": ""},
}

_DIGEST_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}.*\.md$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# ─── Auth helpers ───────────────────────────────────────────────────────────


def _require_read_access(request: Request) -> None:
    """Gate for `ody_` bearer tokens: require the ithaca:read scope. Browser
    cookie sessions (and no-auth/single-user mode) pass — AuthMiddleware
    already rejected unauthenticated non-exempt requests before this runs."""
    if getattr(request.state, "api_token", False):
        scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        if not scopes.intersection(ITHACA_READ_SCOPES):
            raise HTTPException(403, "API token missing required scope: ithaca:read")


def _reject_cross_site(request: Request) -> None:
    """Reject browser cross-site navigations to state-touching endpoints."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site request rejected")


# ─── Digest markdown parsing (pure functions — unit tested) ─────────────────


def parse_digest_sections(markdown: str) -> Dict[str, str]:
    """Split a digest markdown document into {heading text: body markdown}.

    Any heading level starts a new section (the n8n workflow patches under
    `Weather` / `Software Updates`, but other automations may add more at any
    level). Text before the first heading is keyed under "" when non-empty.
    A repeated heading name keeps the LAST occurrence — a re-patched section
    supersedes stale content above it.
    """
    sections: Dict[str, str] = {}
    current = ""
    buf: List[str] = []
    for line in markdown.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            body = "\n".join(buf).strip()
            if current or body:
                sections[current] = body
            current = m.group(2).strip()
            buf = []
        else:
            buf.append(line)
    body = "\n".join(buf).strip()
    if current or body:
        sections[current] = body
    return sections


def _clean_cell(cell: str) -> str:
    cell = cell.strip()
    if cell.startswith("**") and cell.endswith("**") and len(cell) > 4:
        cell = cell[2:-2].strip()
    return cell.replace("\\|", "|")


_HEADER_KEY_MAP = {
    "app": "app",
    "hosted on": "hosted_on",
    "currently deployed": "deployed",
    "update?": "update",
    "update": "update",
    "version no.": "version",
    "version": "version",
    "desc": "desc",
    "description": "desc",
}


def parse_updates_table(section_md: str) -> Dict[str, Any]:
    """Parse the workflow's Software Updates markdown table.

    Expected shape (produced by the workflow's "Code in JavaScript3" node):

        | **App** | **Hosted On** | **Currently Deployed** | **Update?** | **Version No.** | **Desc** |
        | --- | --- | ... |
        | **Radarr** | latitude | v6.3.1 | Yes | v6.4.0 | ... |

        <optional trailing note, e.g. "No Flatpak updates available.">

    Returns {"rows": [...], "note": str}. Unknown extra columns are kept
    under their slugified header name so a future automation change degrades
    gracefully instead of dropping data.
    """
    rows: List[Dict[str, Any]] = []
    notes: List[str] = []
    headers: List[str] = []
    for line in section_md.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("|"):
            notes.append(stripped)
            continue
        # Split on unescaped pipes only — the workflow escapes literal pipes
        # inside desc cells as "\|" (see Code in JavaScript3's replace).
        core = stripped[1:]
        if core.endswith("|") and not core.endswith("\\|"):
            core = core[:-1]
        cells = [_clean_cell(c) for c in re.split(r"(?<!\\)\|", core)]
        if not headers:
            headers = [
                _HEADER_KEY_MAP.get(c.lower(), re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_"))
                for c in cells
            ]
            continue
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue  # separator row
        row: Dict[str, Any] = {}
        for i, cell in enumerate(cells):
            key = headers[i] if i < len(headers) else f"col_{i}"
            row[key] = cell
        if not any(v for v in row.values()):
            continue
        row["update_available"] = str(row.get("update", "")).strip().lower() == "yes"
        rows.append(row)
    return {"rows": rows, "note": " ".join(notes).strip()}


# ─── Per-app link config ────────────────────────────────────────────────────


def load_app_links() -> Dict[str, Dict[str, str]]:
    """Merged per-app link map: data/ithaca.json entries over the defaults."""
    merged = {name: dict(info) for name, info in DEFAULT_APP_LINKS.items()}
    try:
        if os.path.exists(ITHACA_CONFIG_FILE):
            with open(ITHACA_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            apps = data.get("apps") if isinstance(data, dict) else None
            if isinstance(apps, dict):
                for name, info in apps.items():
                    if not isinstance(info, dict):
                        continue
                    entry = merged.setdefault(str(name), {"repo_url": "", "ssh_host": "", "path": ""})
                    for key in ("repo_url", "ssh_host", "path"):
                        if key in info:
                            entry[key] = str(info[key] or "")
    except Exception as exc:
        logger.warning("Failed to read %s: %s", ITHACA_CONFIG_FILE, exc)
    return merged


def _match_app_links(app_name: str, links: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Exact match first, then case-insensitive — AI-generated titles vary in
    case ("Jellyfin" vs "jellyfin") but are otherwise stable."""
    if app_name in links:
        return links[app_name]
    lowered = app_name.lower()
    for name, info in links.items():
        if name.lower() == lowered:
            return info
    return None


# ─── TTL caches (in-memory, per-process) ────────────────────────────────────

_weather_cache: Dict[str, Any] = {"key": None, "expires": 0.0, "data": None}
_digest_cache: Dict[str, Any] = {"key": None, "expires": 0.0, "data": None}
_weather_lock = asyncio.Lock()
_digest_lock = asyncio.Lock()


def _setting_or_env(setting_key: str, env_var: str, default: str = "") -> str:
    """Resolve a config value: a value saved in Settings > Integrations wins,
    falling back to the env var, then `default`. Mirrors
    services/search/providers.py's `_get_provider_key`/`_get_search_instance`
    pattern used for the other UI-configurable API keys."""
    try:
        from src.settings import get_setting
        val = (get_setting(setting_key) or "").strip()
        if val:
            return val
    except Exception:
        pass
    return (os.getenv(env_var) or default).strip()


def _weather_settings() -> Dict[str, str]:
    return {
        "api_key": _setting_or_env("openweather_api_key", "OPENWEATHER_API_KEY"),
        "lat": _setting_or_env("openweather_lat", "OPENWEATHER_LAT"),
        "lon": _setting_or_env("openweather_lon", "OPENWEATHER_LON"),
        "units": _setting_or_env("openweather_units", "OPENWEATHER_UNITS", "metric"),
    }


def _obsidian_settings() -> Dict[str, str]:
    return {
        "base": _setting_or_env("obsidian_api_url", "OBSIDIAN_API_URL").rstrip("/"),
        "token": _setting_or_env("obsidian_api_token", "OBSIDIAN_API_TOKEN"),
        "digest_dir": _setting_or_env("obsidian_digest_dir", "OBSIDIAN_DIGEST_DIR", "daily-digest").strip("/"),
    }


async def _fetch_weather() -> Dict[str, Any]:
    cfg = _weather_settings()
    api_key = cfg["api_key"]
    lat = cfg["lat"]
    lon = cfg["lon"]
    units = cfg["units"]
    if not api_key:
        raise HTTPException(503, "OPENWEATHER_API_KEY is not configured")
    if not lat or not lon:
        raise HTTPException(503, "OPENWEATHER_LAT / OPENWEATHER_LON are not configured")

    params = {"lat": lat, "lon": lon, "units": units, "appid": api_key}
    # Overridable for proxies/tests; production default is the real API.
    base = (os.getenv("OPENWEATHER_API_BASE") or "https://api.openweathermap.org").rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        current_resp, forecast_resp = await asyncio.gather(
            client.get(f"{base}/data/2.5/weather", params=params),
            client.get(f"{base}/data/2.5/forecast", params=params),
        )
    if current_resp.status_code == 401 or forecast_resp.status_code == 401:
        raise HTTPException(502, "OpenWeatherMap rejected the API key")
    if current_resp.status_code != 200 or forecast_resp.status_code != 200:
        raise HTTPException(
            502,
            f"OpenWeatherMap error (weather={current_resp.status_code}, "
            f"forecast={forecast_resp.status_code})",
        )
    cur = current_resp.json()
    fc = forecast_resp.json()

    def _cond(block: dict) -> Dict[str, Any]:
        w = (block.get("weather") or [{}])[0]
        return {"description": w.get("description", ""), "icon": w.get("icon", "")}

    tz_offset = int(fc.get("city", {}).get("timezone", cur.get("timezone", 0)) or 0)
    hourly = []
    for entry in (fc.get("list") or [])[:FORECAST_SLOTS]:
        hourly.append({
            "dt": entry.get("dt"),
            "local_hour": ((int(entry.get("dt", 0)) + tz_offset) // 3600) % 24,
            "temp": entry.get("main", {}).get("temp"),
            "feels_like": entry.get("main", {}).get("feels_like"),
            "humidity": entry.get("main", {}).get("humidity"),
            "pop": round(float(entry.get("pop") or 0) * 100),
            **_cond(entry),
        })
    return {
        "location": fc.get("city", {}).get("name") or cur.get("name") or "",
        "units": units,
        "current": {
            "temp": cur.get("main", {}).get("temp"),
            "feels_like": cur.get("main", {}).get("feels_like"),
            "humidity": cur.get("main", {}).get("humidity"),
            "wind_speed": cur.get("wind", {}).get("speed"),
            **_cond(cur),
        },
        "hourly": hourly,
        "fetched_at": int(time.time()),
    }


async def _fetch_digest() -> Dict[str, Any]:
    cfg = _obsidian_settings()
    if not cfg["base"]:
        raise HTTPException(503, "OBSIDIAN_API_URL is not configured")
    if not cfg["token"]:
        raise HTTPException(503, "OBSIDIAN_API_TOKEN is not configured")
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    dir_url = f"{cfg['base']}/vault/{quote(cfg['digest_dir'])}/"

    async with httpx.AsyncClient(timeout=10.0) as client:
        listing = await client.get(dir_url, headers={**headers, "Accept": "application/json"})
        if listing.status_code == 401:
            raise HTTPException(502, "Obsidian API rejected the token")
        if listing.status_code != 200:
            raise HTTPException(502, f"Obsidian API listing error ({listing.status_code})")
        try:
            files = listing.json().get("files") or []
        except ValueError:
            raise HTTPException(502, "Obsidian API returned a non-JSON directory listing")
        digests = sorted(
            (f for f in files if isinstance(f, str) and _DIGEST_NAME_RE.match(f)),
            reverse=True,  # date-prefixed names → lexicographic == chronological
        )
        if not digests:
            raise HTTPException(404, f"No digest files found in '{cfg['digest_dir']}/'")
        filename = digests[0]
        doc = await client.get(
            f"{cfg['base']}/vault/{quote(cfg['digest_dir'])}/{quote(filename)}",
            headers={**headers, "Accept": "text/markdown"},
        )
        if doc.status_code != 200:
            raise HTTPException(502, f"Obsidian API file error ({doc.status_code})")

    sections = parse_digest_sections(doc.text)
    updates_md = next(
        (body for name, body in sections.items() if name.lower() == "software updates"), None
    )
    software_updates = parse_updates_table(updates_md) if updates_md is not None else None
    if software_updates:
        links = load_app_links()
        for row in software_updates["rows"]:
            info = _match_app_links(str(row.get("app", "")), links) or {}
            # Table-provided values win — if the automation ever emits its own
            # Repo URL / host columns, the config only fills the gaps.
            row["repo_url"] = row.get("repo_url") or info.get("repo_url", "")
            row["ssh_host"] = row.get("ssh_host") or info.get("ssh_host", "")
            row["ssh_path"] = row.get("ssh_path") or info.get("path", "")
    return {
        "filename": filename,
        "date": filename[:10] if _DIGEST_NAME_RE.match(filename) else "",
        "sections": sections,
        "software_updates": software_updates,
        "fetched_at": int(time.time()),
    }


async def _cached(cache: Dict[str, Any], lock: asyncio.Lock, key: str, ttl: int,
                  fetch, refresh: bool) -> Dict[str, Any]:
    """Single-flight TTL cache: concurrent tile loads share one upstream call."""
    now = time.monotonic()
    if not refresh and cache["key"] == key and cache["expires"] > now and cache["data"]:
        return cache["data"]
    async with lock:
        now = time.monotonic()
        if not refresh and cache["key"] == key and cache["expires"] > now and cache["data"]:
            return cache["data"]
        data = await fetch()
        cache.update({"key": key, "expires": now + ttl, "data": data})
        return data


# ─── Routes ─────────────────────────────────────────────────────────────────


class AppLinkEntry(BaseModel):
    repo_url: str = ""
    ssh_host: str = ""
    path: str = ""


class IthacaConfigUpdate(BaseModel):
    apps: Dict[str, AppLinkEntry]


class SshOpenRequest(BaseModel):
    app: str


def setup_ithaca_routes() -> APIRouter:
    router = APIRouter(prefix="/api/ithaca", tags=["ithaca"])

    @router.get("/weather")
    async def get_weather(request: Request, refresh: bool = False):
        _require_read_access(request)
        wcfg = _weather_settings()
        key = "|".join((wcfg["lat"], wcfg["lon"], wcfg["units"]))
        try:
            return await _cached(_weather_cache, _weather_lock, key,
                                 WEATHER_CACHE_TTL, _fetch_weather, refresh)
        except HTTPException:
            raise
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"OpenWeatherMap unreachable: {exc.__class__.__name__}")

    @router.get("/digest")
    async def get_digest(request: Request, refresh: bool = False):
        _require_read_access(request)
        cfg = _obsidian_settings()
        key = f"{cfg['base']}|{cfg['digest_dir']}"
        try:
            return await _cached(_digest_cache, _digest_lock, key,
                                 DIGEST_CACHE_TTL, _fetch_digest, refresh)
        except HTTPException:
            raise
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Obsidian API unreachable: {exc.__class__.__name__}")

    @router.get("/config")
    async def get_config(request: Request):
        _require_read_access(request)
        return {"apps": load_app_links(), "config_file": ITHACA_CONFIG_FILE}

    @router.put("/config")
    async def put_config(data: IthacaConfigUpdate, request: Request):
        require_admin(request)
        _reject_cross_site(request)
        atomic_write_json(
            ITHACA_CONFIG_FILE,
            {"apps": {name: entry.model_dump() for name, entry in data.apps.items()}},
            indent=2,
        )
        # The digest cache embeds enriched link fields — drop it so the next
        # tile load reflects the new mapping without waiting out the TTL.
        _digest_cache.update({"key": None, "expires": 0.0, "data": None})
        return {"ok": True, "apps": load_app_links()}

    @router.post("/ssh/open")
    async def ssh_open(body: SshOpenRequest, request: Request):
        # SSH exec is remote code execution — admin-only, same bar as
        # /api/shell/exec. Host+path come from the server-side config, never
        # from the client payload.
        require_admin(request)
        _reject_cross_site(request)
        links = load_app_links()
        info = _match_app_links(body.app, links)
        if not info:
            raise HTTPException(404, f"No Ithaca config entry for app '{body.app}'")
        host = (info.get("ssh_host") or "").strip()
        if not host:
            raise HTTPException(
                400,
                f"No ssh_host configured for '{body.app}' — set it in data/ithaca.json "
                "or via PUT /api/ithaca/config",
            )
        path = (info.get("path") or "").strip()

        from routes.shell_routes import _ssh_base_argv  # shared argv hardening
        try:
            argv = _ssh_base_argv(host, None)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        # BatchMode: fail fast instead of hanging on a password prompt — the
        # ssh targets are key-authed aliases (same assumption n8n makes).
        argv[1:1] = ["-o", "BatchMode=yes"]
        if path and path != "~":
            remote_cmd = f"cd {shlex.quote(path)} && pwd && ls -la"
        else:
            remote_cmd = "cd && pwd && ls -la"
        argv.append(remote_cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        except FileNotFoundError:
            raise HTTPException(500, "ssh binary not found on the server")
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            raise HTTPException(504, f"ssh to '{host}' timed out")
        out = stdout.decode("utf-8", "replace").strip()
        err = stderr.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            return {"ok": False, "host": host, "path": path,
                    "error": err or f"ssh exited with code {proc.returncode}"}
        return {"ok": True, "host": host, "path": path, "output": out, "stderr": err}

    return router
