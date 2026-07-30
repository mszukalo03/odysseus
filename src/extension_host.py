"""extension_host.py

Discovery + registration for `extensions/<id>/`. An extension is a manifest
(`extension.json`) plus a backend module exposing `setup() -> APIRouter` and,
optionally, static assets served under `/ext/<id>/`.

This exists so custom features (Ithaca, and eventually RSS/doc-editor) live
outside the files that get merged from upstream/community forks — adding or
removing one should never touch app.py, index.html, or app.js.

Enablement priority (highest first):
  1. ODYSSEUS_EXTENSIONS_DISABLED / ODYSSEUS_EXTENSIONS_ENABLED env (CSV of ids)
  2. `extensions.<id>.enabled` in the settings KV (src/settings.py)
  3. manifest `enabled_by_default`
"""

import importlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from src.constants import BASE_DIR

logger = logging.getLogger(__name__)

EXTENSIONS_DIR = os.path.join(BASE_DIR, "extensions")


@dataclass
class Extension:
    id: str
    manifest: Dict[str, Any]
    dir: str

    @property
    def backend_module(self) -> Optional[str]:
        return (self.manifest.get("backend") or {}).get("module")

    @property
    def backend_setup_fn(self) -> str:
        return (self.manifest.get("backend") or {}).get("setup", "setup")

    @property
    def scopes(self) -> List[str]:
        return list(self.manifest.get("scopes") or [])

    @property
    def static_dir(self) -> Optional[str]:
        if not (self.manifest.get("frontend") or {}).get("entry"):
            return None
        d = os.path.join(self.dir, "static")
        return d if os.path.isdir(d) else None


def discover() -> List[Extension]:
    """Scan extensions/*/extension.json. Malformed manifests are logged and
    skipped — one bad extension must never fail app startup."""
    extensions: List[Extension] = []
    if not os.path.isdir(EXTENSIONS_DIR):
        return extensions
    for name in sorted(os.listdir(EXTENSIONS_DIR)):
        ext_dir = os.path.join(EXTENSIONS_DIR, name)
        manifest_path = os.path.join(ext_dir, "extension.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            ext_id = manifest["id"]
            if ext_id != name:
                logger.warning(
                    "extensions/%s/extension.json id %r != directory name — using directory name",
                    name, ext_id,
                )
                ext_id = name
        except Exception as exc:
            logger.warning("Skipping extensions/%s: invalid manifest (%s)", name, exc)
            continue
        extensions.append(Extension(id=ext_id, manifest=manifest, dir=ext_dir))
    extensions.sort(key=lambda e: (int((e.manifest.get("nav") or {}).get("order", 100)), e.id))
    return extensions


def _setting_key(ext_id: str) -> str:
    return f"extensions.{ext_id}.enabled"


def is_enabled(ext: Extension) -> bool:
    disabled = {s.strip() for s in (os.getenv("ODYSSEUS_EXTENSIONS_DISABLED") or "").split(",") if s.strip()}
    if ext.id in disabled:
        return False
    enabled_env = {s.strip() for s in (os.getenv("ODYSSEUS_EXTENSIONS_ENABLED") or "").split(",") if s.strip()}
    if ext.id in enabled_env:
        return True
    try:
        from src.settings import is_setting_overridden, get_setting
        if is_setting_overridden(_setting_key(ext.id)):
            return bool(get_setting(_setting_key(ext.id)))
    except Exception:
        pass
    return bool(ext.manifest.get("enabled_by_default", True))


def set_enabled(ext_id: str, enabled: bool) -> None:
    from src.settings import load_settings, save_settings
    settings = load_settings()
    settings[_setting_key(ext_id)] = bool(enabled)
    save_settings(settings)


def register_all(app) -> List[Extension]:
    """Import + include_router() every enabled extension's backend. Returns
    the extensions that were actually registered (enabled + import succeeded)."""
    registered: List[Extension] = []
    for ext in discover():
        if not is_enabled(ext):
            logger.info("Extension %r disabled — skipping", ext.id)
            continue
        module_name = ext.backend_module
        if module_name:
            try:
                module = importlib.import_module(module_name)
                setup_fn = getattr(module, ext.backend_setup_fn)
                router: APIRouter = setup_fn()
                app.include_router(router)
            except Exception:
                logger.exception("Extension %r failed to register — skipping", ext.id)
                continue
        static_dir = ext.static_dir
        if static_dir:
            app.mount(f"/ext/{ext.id}", _extension_static(static_dir), name=f"ext-{ext.id}")
        registered.append(ext)
        logger.info("Extension %r registered", ext.id)
    return registered


def _extension_static(directory: str):
    from fastapi.staticfiles import StaticFiles
    return StaticFiles(directory=directory)


def nav_routes(registered: List[Extension]) -> List[str]:
    """Page routes (e.g. '/ithaca') that should serve the SPA shell."""
    return [
        nav["route"]
        for ext in registered
        if (nav := ext.manifest.get("nav")) and nav.get("route")
    ]


def manifest_payload(registered: List[Extension]) -> List[Dict[str, Any]]:
    """JSON the frontend loader consumes: nav + frontend entry + settings card,
    enabled extensions only."""
    out = []
    for ext in registered:
        entry = (ext.manifest.get("frontend") or {}).get("entry")
        payload: Dict[str, Any] = {
            "id": ext.id,
            "name": ext.manifest.get("name", ext.id),
            "nav": ext.manifest.get("nav"),
        }
        if entry:
            payload["entry"] = f"/ext/{ext.id}/{entry.split('static/', 1)[-1]}"
        css = (ext.manifest.get("frontend") or {}).get("css")
        if css:
            payload["css"] = f"/ext/{ext.id}/{css.split('static/', 1)[-1]}"
        settings_card = ext.manifest.get("settings_card")
        if settings_card and settings_card.get("template"):
            tpl = settings_card["template"].split("static/", 1)[-1]
            payload["settings_card"] = {
                "url": f"/ext/{ext.id}/{tpl}",
                "admin_only": bool(settings_card.get("admin_only", True)),
            }
        out.append(payload)
    return out


def all_scopes(registered: List[Extension]) -> List[str]:
    scopes: List[str] = []
    for ext in registered:
        scopes.extend(ext.scopes)
    return scopes
