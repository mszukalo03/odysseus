"""
extensions/ithaca/tile_packaging.py

Export/import for portable tile configs — the analogue of
src/extension_host.py's build_package_zip()/install_from_url() for a much
smaller artifact. A tile config is already a small, flat JSON document
(extensions/ithaca/tile_schema.py), so a package is just that document plus
a non-secret `connection_hint` describing what kind of connection it
expects — no zip, no credentials, ever.

Export: `data_source.connection_ref` in the exported package is the LOCAL
connection's id, which is meaningless on another instance and MUST NOT be
trusted there. `install_tile_package` requires the importer to explicitly
choose one of their own local connections to bind to — never auto-matched
by name/id, since a same-named-but-different connection on the target
instance would otherwise silently run the tile's query against the wrong
database. Each action button's `endpoint_ref` (a local WebhookTarget id) is
stripped the same way and requires its own explicit binding on import — an
imported tile must never silently point at whatever webhook happens to
share an id on the new instance.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from core.external_db import get_connection, ExternalDbError
from core.webhook_action import get_target, WebhookActionError

from extensions.ithaca.tile_schema import TileConfig
from extensions.ithaca.tiles import get_tile_config, save_tile_config

PACKAGE_SCHEMA_VERSION = 1

_ID_RE = re.compile(r"^[a-z0-9_\-]+$")


class TilePackagingError(Exception):
    """User-facing error for tile export/import failures."""


def build_tile_package(tile_id: str) -> dict[str, Any]:
    """Return an exportable package for a saved tile — the config itself,
    plus a non-secret hint about the connection it expects, so the
    importing admin knows what to bind it to."""
    data = get_tile_config(tile_id)
    if data is None:
        raise TilePackagingError(f"No tile config with id '{tile_id}'")
    cfg = TileConfig.model_validate(data)

    # Fall back to the tile's own authoring hint (data_source.type) if the
    # connection has since been deleted — better than always assuming
    # postgres for a tile that may have been authored against another
    # dialect.
    hint: dict[str, Any] = {"kind": cfg.data_source.type, "label": cfg.data_source.connection_ref}
    if cfg.data_source.type == "http":
        # An http tile's connection_ref names a WebhookTarget, not an
        # ExternalDbConnection — same "weaker hint if since deleted" fallback.
        try:
            target = get_target(cfg.data_source.connection_ref)
            hint["label"] = target.label
        except WebhookActionError:
            pass
    else:
        try:
            conn = get_connection(cfg.data_source.connection_ref)
            hint["kind"] = conn.kind
            hint["label"] = conn.label
            hint["database"] = conn.database
        except ExternalDbError:
            # Connection may have since been deleted — the package is still
            # exportable, just with a weaker hint (only the stale local id/label).
            pass

    package = cfg.model_dump()
    # The exported connection_ref is a LOCAL id on this instance and carries
    # no meaning elsewhere — replaced with a placeholder so it's obvious an
    # import must supply its own binding, and to avoid a stale id looking
    # like it "worked" if it happens to collide on the target instance.
    package["data_source"]["connection_ref"] = ""
    package["package_schema_version"] = PACKAGE_SCHEMA_VERSION
    package["connection_hint"] = hint

    action_hints = []
    for action, raw_action in zip(cfg.actions, package.get("actions") or []):
        target_hint = {"action_id": action.id, "label": action.label}
        try:
            target = get_target(action.endpoint_ref)
            target_hint["endpoint_label"] = target.label
        except WebhookActionError:
            pass  # target may have since been deleted — export anyway, weaker hint
        action_hints.append(target_hint)
        raw_action["endpoint_ref"] = ""
    if action_hints:
        package["action_hints"] = action_hints

    return package


def install_tile_package(
    package: dict[str, Any], connection_binding: str, action_bindings: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Validate an imported package and save it bound to LOCAL connection/
    webhook-target ids the importing admin explicitly chose. Returns the
    saved tile config."""
    if not isinstance(package, dict):
        raise TilePackagingError("Package is not a JSON object")
    if package.get("package_schema_version") != PACKAGE_SCHEMA_VERSION:
        raise TilePackagingError(
            f"Unsupported package schema version: {package.get('package_schema_version')!r}"
        )
    connection_binding = str(connection_binding or "").strip()
    if not connection_binding:
        raise TilePackagingError("connection_binding is required — pick a local connection for this tile")
    data_source_type = (package.get("data_source") or {}).get("type")
    if data_source_type == "http":
        try:
            get_target(connection_binding)  # 404s here if the id doesn't exist locally
        except WebhookActionError as exc:
            raise TilePackagingError(str(exc)) from exc
    else:
        try:
            get_connection(connection_binding)  # 404s here if the id doesn't exist locally
        except ExternalDbError as exc:
            raise TilePackagingError(str(exc)) from exc

    tile = {k: v for k, v in package.items() if k not in ("package_schema_version", "connection_hint", "action_hints")}
    data_source = dict(tile.get("data_source") or {})
    data_source["connection_ref"] = connection_binding
    tile["data_source"] = data_source

    actions = tile.get("actions") or []
    if actions:
        action_bindings = action_bindings or {}
        missing = [a.get("id") for a in actions if not action_bindings.get(a.get("id"))]
        if missing:
            raise TilePackagingError(
                f"This tile has action button(s) that need a local webhook endpoint bound: {', '.join(missing)}"
            )
        bound_actions = []
        for a in actions:
            endpoint_id = action_bindings[a["id"]]
            try:
                get_target(endpoint_id)
            except WebhookActionError as exc:
                raise TilePackagingError(str(exc)) from exc
            bound_actions.append({**a, "endpoint_ref": endpoint_id})
        tile["actions"] = bound_actions

    tile_id = str(tile.get("id") or "").strip()
    if not tile_id or not _ID_RE.match(tile_id):
        raise TilePackagingError("Package has an invalid or missing tile id")
    if get_tile_config(tile_id) is not None:
        raise TilePackagingError(
            f"A tile with id '{tile_id}' already exists on this instance — rename it in the package before importing"
        )

    try:
        return save_tile_config(tile)
    except Exception as exc:
        raise TilePackagingError(f"Imported tile config failed validation: {exc}") from exc
