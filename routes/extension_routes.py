"""routes/extension_routes.py — /api/extensions* endpoints.

GET /api/extensions is the frontend-facing, enabled-only, boot-time snapshot
(unauthenticated, same as any other static manifest the SPA shell reads).
Everything else here is admin-only: listing every discovered extension
(enabled or not, in-repo or installed), enabling/disabling one, and
installing/uninstalling from a URL. See src/extension_host.py for the
underlying discovery/install logic and extensions/README.md for the
security posture (installing runs arbitrary Python with full server
privileges — there is no sandboxing or review).
"""

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from src import extension_host
from src.extension_host import Extension


def setup_extension_routes(registered_extensions: List[Extension]) -> APIRouter:
    router = APIRouter(prefix="/api/extensions", tags=["extensions"])

    @router.get("")
    async def list_extensions():
        return extension_host.manifest_payload(registered_extensions)

    @router.get("/admin")
    async def list_all_extensions(request: Request):
        require_admin(request)
        return extension_host.admin_payload()

    @router.put("/{ext_id}")
    async def set_extension_enabled(ext_id: str, body: Dict[str, Any], request: Request):
        require_admin(request)
        known_ids = {e.id for e in extension_host.discover()}
        if ext_id not in known_ids:
            raise HTTPException(404, f"Unknown extension: {ext_id}")
        enabled = bool(body.get("enabled"))
        extension_host.set_enabled(ext_id, enabled)
        return {"ok": True, "id": ext_id, "enabled": enabled, "reload_required": True}

    @router.post("/install")
    async def install_extension(body: Dict[str, Any], request: Request):
        require_admin(request)
        url = str(body.get("url") or "").strip()
        overwrite = bool(body.get("overwrite", False))
        if not url:
            raise HTTPException(400, "A URL is required")
        try:
            ext = await extension_host.install_from_url(url, overwrite=overwrite)
        except extension_host.ExtensionInstallError as exc:
            msg = str(exc)
            if "already installed" in msg:
                raise HTTPException(409, msg)
            if "in-repo extension" in msg or "Invalid extension id" in msg:
                raise HTTPException(400, msg)
            if "No extension.json" in msg or "Invalid extension.json" in msg:
                raise HTTPException(422, msg)
            raise HTTPException(502, msg)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {
            "ok": True,
            "id": ext.id,
            "name": ext.manifest.get("name", ext.id),
            "source": ext.source,
            "reload_required": True,
        }

    @router.delete("/{ext_id}")
    async def uninstall_extension(ext_id: str, request: Request):
        require_admin(request)
        try:
            extension_host.uninstall(ext_id)
        except FileNotFoundError:
            raise HTTPException(404, f"Not an installed extension: {ext_id}")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "id": ext_id, "reload_required": True}

    return router
