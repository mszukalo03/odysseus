"""extension_host.py

Discovery + registration for extensions. An extension is a manifest
(`extension.json`) plus a backend module exposing `setup() -> APIRouter` and,
optionally, static assets served under `/ext/<id>/`.

This exists so custom features (Ithaca, and eventually RSS/doc-editor) live
outside the files that get merged from upstream/community forks — adding or
removing one should never touch app.py, index.html, or app.js.

Extensions are discovered from TWO roots:
  - EXTENSIONS_DIR       (<BASE_DIR>/extensions)   — in-repo, git-tracked, dev
    extensions (e.g. Ithaca). Present in a dev checkout; excluded from
    packaged Docker/PyInstaller builds by default (opt in with the
    ODYSSEUS_BUNDLE_EXTENSIONS build flag — see Dockerfile/Odysseus.spec).
  - INSTALLED_EXTENSIONS_DIR (<DATA_DIR>/extensions) — admin-installed via
    Settings > Extensions or ODYSSEUS_EXTENSIONS_AUTOINSTALL at boot. Lives
    under DATA_DIR so it survives container recreates via the existing data
    volume mount, and survives frozen-build app updates the same way
    ~/.odysseus/data already does.
On an id collision between the two, the in-repo copy always wins.

Enablement priority (highest first):
  1. ODYSSEUS_EXTENSIONS_DISABLED / ODYSSEUS_EXTENSIONS_ENABLED env (CSV of ids)
  2. `extensions.<id>.enabled` in the settings KV (src/settings.py)
  3. manifest `enabled_by_default`

Installing an extension from a URL runs arbitrary, untrusted Python with
full server privileges the moment it's enabled — there is no sandboxing or
review. Every install/uninstall entry point is admin-gated at the route
layer (routes/extension_routes.py) and logs a loud warning here too.
"""

import asyncio
import importlib
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter

from src.constants import BASE_DIR, DATA_DIR, EXTENSION_INSTALL_MAX_BYTES

logger = logging.getLogger(__name__)

EXTENSIONS_DIR = os.path.join(BASE_DIR, "extensions")
INSTALLED_EXTENSIONS_DIR = os.path.join(DATA_DIR, "extensions")
_INSTALL_TMP_DIR = os.path.join(DATA_DIR, "tmp", "extension-installs")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

GIT_CLONE_TIMEOUT_SECS = 60
INSTALL_HTTP_TIMEOUT_SECS = 60


@dataclass
class Extension:
    id: str
    manifest: Dict[str, Any]
    dir: str
    source: str = "in-repo"  # "in-repo" | "installed"

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


def _discover_root(root: str, source: str) -> List[Extension]:
    """Scan <root>/*/extension.json. Malformed manifests are logged and
    skipped — one bad extension must never fail app startup."""
    extensions: List[Extension] = []
    if not os.path.isdir(root):
        return extensions
    for name in sorted(os.listdir(root)):
        ext_dir = os.path.join(root, name)
        manifest_path = os.path.join(ext_dir, "extension.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            ext_id = manifest["id"]
            if ext_id != name:
                logger.warning(
                    "%s/%s/extension.json id %r != directory name — using directory name",
                    root, name, ext_id,
                )
                ext_id = name
        except Exception as exc:
            logger.warning("Skipping %s/%s: invalid manifest (%s)", root, name, exc)
            continue
        extensions.append(Extension(id=ext_id, manifest=manifest, dir=ext_dir, source=source))
    return extensions


def discover() -> List[Extension]:
    """All discovered extensions from both roots, in-repo taking precedence
    on an id collision."""
    in_repo = _discover_root(EXTENSIONS_DIR, "in-repo")
    installed = _discover_root(INSTALLED_EXTENSIONS_DIR, "installed")
    in_repo_ids = {e.id for e in in_repo}
    extensions = list(in_repo)
    for ext in installed:
        if ext.id in in_repo_ids:
            logger.warning(
                "Installed extension %r shadowed by an in-repo extension of the "
                "same id — the in-repo copy wins; uninstall or rename the "
                "installed one to use it.",
                ext.id,
            )
            continue
        extensions.append(ext)
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


_registered_routers: Dict[str, APIRouter] = {}


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
                _registered_routers[ext.id] = router
            except Exception:
                logger.exception("Extension %r failed to register — skipping", ext.id)
                continue
        static_dir = ext.static_dir
        if static_dir:
            app.mount(f"/ext/{ext.id}", _extension_static(static_dir), name=f"ext-{ext.id}")
        registered.append(ext)
        logger.info("Extension %r registered", ext.id)
    return registered


def get_router(ext_id: str) -> Optional[APIRouter]:
    """The APIRouter instance register_all() built and mounted for ext_id,
    for code that needs the literal object (e.g. routes/codex_routes.py
    reaches into the feeds router's .routes to borrow endpoint functions).
    Only populated after register_all() has run; None if the extension is
    disabled, has no backend, or hasn't been registered yet."""
    return _registered_routers.get(ext_id)


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


def admin_payload() -> List[Dict[str, Any]]:
    """Full admin view of every discovered extension (enabled or not),
    scanned fresh so it reflects installs/uninstalls done without a restart.
    Used by GET /api/extensions/admin — distinct from manifest_payload(),
    which is the enabled-only, boot-time-snapshot view the frontend loader
    consumes."""
    out = []
    for ext in discover():
        out.append({
            "id": ext.id,
            "name": ext.manifest.get("name", ext.id),
            "version": ext.manifest.get("version", ""),
            "description": ext.manifest.get("description", ""),
            "source": ext.source,
            "enabled": is_enabled(ext),
            "scopes": ext.scopes,
        })
    return out


def all_scopes(registered: List[Extension]) -> List[str]:
    scopes: List[str] = []
    for ext in registered:
        scopes.extend(ext.scopes)
    return scopes


# ─── Install / uninstall ─────────────────────────────────────────────────


class ExtensionInstallError(Exception):
    """Raised for any install_from_url failure; message is safe to show an admin."""


def _validate_id(ext_id: str) -> None:
    if not ext_id or not _ID_RE.match(ext_id):
        raise ValueError(
            f"Invalid extension id {ext_id!r} — must match {_ID_RE.pattern}"
        )
    # Belt-and-suspenders against path traversal even though the regex above
    # already excludes '/', '.', and similar — an id is about to become a
    # directory name under a filesystem root we fully trust otherwise.
    if os.path.basename(ext_id) != ext_id:
        raise ValueError(f"Invalid extension id {ext_id!r}")


def _effective_root(staging_dir: str) -> str:
    """A GitHub-style 'Download ZIP' produces one top-level folder
    (repo-branch/) wrapping the real content; a git clone or a zip built
    with extension.json at its root does not. Detect the single-nested-dir
    case and flatten to it; otherwise treat staging_dir itself as the root."""
    entries = os.listdir(staging_dir)
    if len(entries) == 1:
        candidate = os.path.join(staging_dir, entries[0])
        if os.path.isdir(candidate):
            return candidate
    return staging_dir


def _safe_extract_zip(zip_path: str, dest_dir: str) -> None:
    """Extract dest_dir with a zip-slip guard — stdlib zipfile.extractall()
    does not validate that member paths stay inside dest_dir, so a malicious
    archive could write outside it (e.g. '../../etc/cron.d/x') otherwise."""
    dest_real = os.path.realpath(dest_dir)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = os.path.realpath(os.path.join(dest_dir, member.filename))
            if target != dest_real and not target.startswith(dest_real + os.sep):
                raise ExtensionInstallError(
                    f"Zip entry {member.filename!r} resolves outside the archive — refusing to extract"
                )
        zf.extractall(dest_dir)


async def _git_clone(url: str, dest_dir: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", url, dest_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=GIT_CLONE_TIMEOUT_SECS)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise ExtensionInstallError(f"git clone of {url!r} timed out after {GIT_CLONE_TIMEOUT_SECS}s")
    if proc.returncode != 0:
        raise ExtensionInstallError(
            f"git clone of {url!r} failed: {stderr.decode('utf-8', 'replace').strip()[:500]}"
        )


async def _download_zip(url: str, dest_zip_path: str) -> None:
    total = 0
    try:
        async with httpx.AsyncClient(timeout=INSTALL_HTTP_TIMEOUT_SECS, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise ExtensionInstallError(f"Download failed: HTTP {resp.status_code} for {url!r}")
                with open(dest_zip_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > EXTENSION_INSTALL_MAX_BYTES:
                            raise ExtensionInstallError(
                                f"Download exceeded the {EXTENSION_INSTALL_MAX_BYTES}-byte extension install limit"
                            )
                        f.write(chunk)
    except httpx.HTTPError as exc:
        raise ExtensionInstallError(f"Download of {url!r} failed: {exc}")


async def install_from_url(url: str, expected_id: Optional[str] = None, overwrite: bool = False) -> Extension:
    """Download `url` (a git repo, or a .zip URL) to a temp staging dir,
    read its extension.json, and move it into INSTALLED_EXTENSIONS_DIR under
    the id from the manifest. All-or-nothing — a failure at any step leaves
    no partial extension directory behind."""
    url = (url or "").strip()
    if not url:
        raise ExtensionInstallError("A URL is required")

    os.makedirs(_INSTALL_TMP_DIR, exist_ok=True)
    staging_dir = os.path.join(_INSTALL_TMP_DIR, f"stage-{uuid.uuid4().hex}")
    os.makedirs(staging_dir, exist_ok=False)
    try:
        if url.lower().endswith(".zip"):
            zip_path = os.path.join(_INSTALL_TMP_DIR, f"dl-{uuid.uuid4().hex}.zip")
            try:
                await _download_zip(url, zip_path)
                _safe_extract_zip(zip_path, staging_dir)
            finally:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
        else:
            # git clone needs an empty target dir it creates itself.
            os.rmdir(staging_dir)
            await _git_clone(url, staging_dir)

        effective_root = _effective_root(staging_dir)
        manifest_path = os.path.join(effective_root, "extension.json")
        if not os.path.isfile(manifest_path):
            raise ExtensionInstallError(
                "No extension.json found at the root of the downloaded content"
            )
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            ext_id = str(manifest["id"])
        except Exception as exc:
            raise ExtensionInstallError(f"Invalid extension.json: {exc}")

        _validate_id(ext_id)
        if expected_id and ext_id != expected_id:
            logger.warning(
                "Autoinstall entry %r downloaded a manifest whose id is %r — "
                "installing as %r. Update ODYSSEUS_EXTENSIONS_AUTOINSTALL to "
                "use the extension's real id to avoid this warning.",
                expected_id, ext_id, ext_id,
            )

        in_repo_ids = {e.id for e in _discover_root(EXTENSIONS_DIR, "in-repo")}
        if ext_id in in_repo_ids:
            raise ExtensionInstallError(
                f"'{ext_id}' is an in-repo extension and always takes precedence — "
                "installing over it would have no effect"
            )
        dest = os.path.join(INSTALLED_EXTENSIONS_DIR, ext_id)
        if os.path.isdir(dest):
            if not overwrite:
                raise ExtensionInstallError(
                    f"'{ext_id}' is already installed — pass overwrite to replace it"
                )
            shutil.rmtree(dest)

        os.makedirs(INSTALLED_EXTENSIONS_DIR, exist_ok=True)
        logger.warning(
            "Installing extension %r from %s — extensions run arbitrary Python "
            "with full server privileges; only install from sources you trust.",
            ext_id, url,
        )
        os.rename(effective_root, dest)
        return Extension(id=ext_id, manifest=manifest, dir=dest, source="installed")
    finally:
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)


def uninstall(ext_id: str) -> None:
    """Remove an installed extension. Only ever looks under
    INSTALLED_EXTENSIONS_DIR — an in-repo id (e.g. 'ithaca') simply isn't
    found there, so in-repo extensions can never be removed through this
    path; callers should treat a miss as 404."""
    _validate_id(ext_id)
    target = os.path.join(INSTALLED_EXTENSIONS_DIR, ext_id)
    target_real = os.path.realpath(target)
    installed_root_real = os.path.realpath(INSTALLED_EXTENSIONS_DIR)
    if target_real != installed_root_real and not target_real.startswith(installed_root_real + os.sep):
        raise ValueError("Refusing to uninstall outside the installed-extensions root")
    if not os.path.isdir(target):
        raise FileNotFoundError(f"Not an installed extension: {ext_id}")
    shutil.rmtree(target)
    try:
        from src.settings import load_settings, save_settings
        settings = load_settings()
        key = _setting_key(ext_id)
        if key in settings:
            del settings[key]
            save_settings(settings)
    except Exception:
        logger.exception("Failed to clear settings for uninstalled extension %r", ext_id)


# ─── Packaging (for re-hosting: an admin download, or scripts/package_extension.py) ───

_PACKAGE_EXCLUDE_DIRS = {"__pycache__", ".git"}
_PACKAGE_EXCLUDE_FILES = {".DS_Store"}
_PACKAGE_EXCLUDE_SUFFIXES = (".pyc", ".pyo")


def build_package_zip(ext_id: str) -> bytes:
    """Zip an extension's directory (in-repo or installed) for re-hosting —
    e.g. upload the result to a GitHub release, or serve it from another
    static host, then paste that URL into another instance's "Install from
    URL". Layout is flat (extension.json at the zip root, no wrapping
    top-level folder) — the opposite of a GitHub "Download ZIP", but
    install_from_url()'s _effective_root() already flattens either shape on
    the way in, so both round-trip correctly.

    Raises FileNotFoundError if ext_id isn't discovered (in-repo or
    installed), matching uninstall()'s error convention.
    """
    ext = next((e for e in discover() if e.id == ext_id), None)
    if ext is None:
        raise FileNotFoundError(f"Unknown extension: {ext_id}")
    root = Path(ext.dir)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            rel_parts = path.relative_to(root).parts
            if _PACKAGE_EXCLUDE_DIRS.intersection(rel_parts):
                continue
            if path.name in _PACKAGE_EXCLUDE_FILES or path.suffix in _PACKAGE_EXCLUDE_SUFFIXES:
                continue
            zf.write(path, path.relative_to(root))
    return buf.getvalue()


def autoinstall_from_env() -> None:
    """Read ODYSSEUS_EXTENSIONS_AUTOINSTALL=id=url,id2=url2 and install any
    id not already discovered. Called once at boot, before register_all().
    Never raises — a bad entry is logged and skipped so a broken/unreachable
    URL can never prevent the app from starting."""
    raw = os.getenv("ODYSSEUS_EXTENSIONS_AUTOINSTALL", "")
    pairs = [p.strip() for p in raw.split(",") if p.strip()]
    if not pairs:
        return
    known_ids = {e.id for e in discover()}
    for pair in pairs:
        if "=" not in pair:
            logger.warning(
                "Malformed ODYSSEUS_EXTENSIONS_AUTOINSTALL entry (expected id=url): %r", pair
            )
            continue
        ext_id, url = pair.split("=", 1)
        ext_id, url = ext_id.strip(), url.strip()
        if ext_id in known_ids:
            logger.info("Autoinstall: %r already present — skipping", ext_id)
            continue
        try:
            _validate_id(ext_id)
            asyncio.run(install_from_url(url, expected_id=ext_id))
        except Exception:
            logger.exception(
                "Autoinstall failed for %r (%s) — skipping, app boot continues", ext_id, url
            )
