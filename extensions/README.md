# Extensions

Custom features that don't need to live in the merge-sensitive core (`app.py`,
`static/app.js`, `static/index.html`) go here instead, one directory per
extension. This is what lets `dev` stay close to upstream/community while
still shipping features like Ithaca.

Backend host: [`src/extension_host.py`](../src/extension_host.py).
Frontend host: [`static/js/extensionHost.js`](../static/js/extensionHost.js) +
[`static/js/workspaceManager.js`](../static/js/workspaceManager.js).

## Discovery roots

Extensions are discovered from **two** places, merged together:

- **`extensions/`** (this directory, `<BASE_DIR>/extensions`) — in-repo, git-tracked,
  developer-authored extensions. Ithaca lives here. Present in a dev checkout;
  **excluded from packaged Docker/PyInstaller builds by default** — a fresh
  container or Windows install starts with zero extensions. Opt in with the
  `ODYSSEUS_BUNDLE_EXTENSIONS=true` build flag (`--build-arg` for Docker, an
  env var read by `Odysseus.spec` for the frozen build).
- **`<DATA_DIR>/extensions`** — admin-installed, via Settings → Extensions or
  `ODYSSEUS_EXTENSIONS_AUTOINSTALL` at boot (see below). Lives under `DATA_DIR`
  so it survives container recreates through the existing `data/` volume
  mount, and survives frozen-build app updates the same way `~/.odysseus/data`
  already does.

**On an id collision, the in-repo copy always wins** — installing something
with the same id as an in-repo extension is rejected outright, so bundling
Ithaca back in via `ODYSSEUS_BUNDLE_EXTENSIONS` is never ambiguous.

## Installing extensions

**Settings → Extensions** (admin-only): paste a git repository URL or a
direct `.zip` URL (e.g. a GitHub "Download ZIP" link) and click Install. The
extension's `id` is read from its own `extension.json` after downloading —
you don't specify it upfront. A GitHub-style zip with one nested top-level
folder is flattened automatically. Requires a restart to take effect (no hot
module reload); the UI says so.

**`ODYSSEUS_EXTENSIONS_AUTOINSTALL=id=url,id2=url2`** (boot-time env var):
install-if-missing at startup, before extensions are registered — the same
mechanism works for Docker, bare-metal, and the frozen launcher. Unlike the
Settings flow, the `id` here is a hint used to skip a network call on
subsequent boots once installed; the manifest's own `id` still wins if it
differs (a mismatch is logged loudly so you can fix the env var). A failed
entry (bad URL, unreachable host, git clone failure) is logged and skipped —
it never blocks the app from starting.

Both paths are git-clone (public repos only — `GIT_TERMINAL_PROMPT=0` makes
an auth-required repo fail fast instead of hanging, there is no
private-repo/credential support) or a size-capped `.zip` download
(`EXTENSION_INSTALL_MAX_BYTES` in `src/constants.py`, 50MB default), with a
zip-slip guard validating every archive member stays inside the extraction
directory before writing.

**Installing an extension runs its code with full server privileges the
moment it's enabled — there is no sandboxing or review.** Every install only
ever happens because an admin (or whoever controls the deploy's env vars)
explicitly pointed at that URL.

## Uninstalling extensions

**Settings → Extensions** → Uninstall (admin-only), or `DELETE
/api/extensions/<id>`. Only ever removes something under
`<DATA_DIR>/extensions` — an in-repo id (e.g. `ithaca`) simply isn't found
there, so in-repo extensions can never be removed this way (404). Takes
effect on next restart — the running process still has the old module
imported and its router mounted until then.

## DB models: stay in core, not extension-owned

There's no per-extension database/migration system, and none is planned as
part of this pass. An extension needing tables (RSS's `FeedGroup`/`Feed`/
`Article`/`FeedSyncAccount`) defines them in `core/database.py` on the shared
`Base`/engine, with migrations in the same file's migration runner, exactly
like any other `routes/*.py` module would — the extension's backend just
imports them (`from core.database import SessionLocal, Feed`). This means an
extension isn't fully "pluggable" in the DB sense: uninstalling one (e.g.
`rss` from `<DATA_DIR>/extensions`) leaves its tables/columns behind rather
than cleaning them up — an accepted gap, consistent with the no-hot-reload
posture below.

If an extension's backend router needs to be handed to other core code by
reference (RSS's is borrowed by `routes/codex_routes.py` for its
`/api/codex/feeds*` passthrough — see `_find_endpoint` there), retrieve the
already-registered instance via `extension_host.get_router(ext_id)` *after*
`register_all()` has run. Never call the extension's `setup()` a second time
to get "another" router — that double-registers every route on the app.

## Packaging an extension for re-hosting

Two ways to turn an extension's directory into a distributable zip, both
built on one shared `src.extension_host.build_package_zip(ext_id)`:

- **`GET /api/extensions/<id>/package.zip`** (admin-only) — a running
  instance packages any discovered extension (in-repo or installed) on the
  fly. This is the easy path for "host a location I can paste as a URL":
  download the zip, upload it wherever you already host static files (a
  GitHub release, a gist, your own web server), then paste that URL into
  another instance's Settings → Extensions → Install from URL (or
  `ODYSSEUS_EXTENSIONS_AUTOINSTALL`).
- **`scripts/package_extension.py <id> [output_path]`** — the same builder,
  usable without a running instance (CI, or packaging before a manual
  upload).

The zip is **flat** — `extension.json` at the root, no wrapping top-level
folder — the opposite of a GitHub "Download ZIP", but `install_from_url()`'s
`_effective_root()` flattens either shape on the way in, so both round-trip
correctly. Excludes `__pycache__`, `.git`, `*.pyc`/`*.pyo`, and `.DS_Store`.

## Manifest (`extension.json`)

```json
{
  "id": "ithaca",
  "name": "Ithaca",
  "version": "1.0.0",
  "description": "Home hub dashboard",
  "enabled_by_default": true,
  "backend": { "module": "extensions.ithaca.backend", "setup": "setup" },
  "scopes": ["ithaca:read"],
  "nav": {
    "id": "ithaca",
    "label": "Ithaca",
    "route": "/ithaca",
    "section": "tools",
    "order": 20,
    "surface": "both",
    "admin_only": false
  },
  "frontend": { "entry": "static/index.js" }
}
```

- `backend.module` / `backend.setup`: a Python module with a zero-argument
  function (default name `setup`) returning a `fastapi.APIRouter`. Imported
  and `include_router`'d by `extension_host.register_all()` at startup.
  Extensions that need a manager instance already threaded through `app.py`
  (session_manager, task_scheduler, ...) should import it directly the same
  way most `routes/*.py` modules already do — there's no dependency-injection
  container to plug into.
- `scopes`: bearer-token API scopes this extension's routes require. Unioned
  into `routes/api_token_routes.py`'s allowed set automatically — don't edit
  that file to add a scope.
- `nav`: rendered by `extensionHost.js` by wiring click handlers onto
  `#tool-<nav.id>-btn` (the sidebar button). **The sidebar/rail markup itself
  is not generated dynamically yet** — see "Known gaps" below. `nav.id` only
  has to equal the sidebar button's id suffix — it does **not** have to match
  the extension's own `id` or its `nav.route`. RSS is the first case where
  they diverge: extension id `rss`, `nav.id: "rss"` (matching the pre-existing
  `#tool-rss-btn`), but `nav.route: "/feeds"` (preserving the old bookmarked
  URL). Get `nav.id` wrong and the sidebar button silently does nothing — no
  error, just a dead click.
- `frontend.entry`: an ES module served at `/ext/<id>/<path>`, dynamically
  `import()`'d. Default-exports a workspace descriptor (see below). Optional
  `frontend.css` field for a stylesheet, injected as a `<link>`.
- `settings_card`: not currently wired up (see "Known gaps"); omit it.

Enablement, checked in this order:
1. `ODYSSEUS_EXTENSIONS_DISABLED=id1,id2` / `ODYSSEUS_EXTENSIONS_ENABLED=id1,id2` env
2. `extensions.<id>.enabled` in the settings KV — toggle via
   `PUT /api/extensions/<id>` (admin-only; response has `reload_required: true`,
   there is no hot enable/disable)
3. manifest `enabled_by_default`

`GET /api/extensions` is the enabled-only, frontend-facing view (unauthenticated,
same boot-time snapshot the SPA shell reads). `GET /api/extensions/admin`
(admin-only) is the full management view — every discovered extension,
enabled or not, in-repo or installed, scanned fresh on each request so it
reflects installs/uninstalls done without a restart.

A malformed `extension.json`, or a backend module that throws on import/setup,
is logged and skipped — it never takes the app down.

## Workspace descriptor contract

A "workspace" is a full-canvas feature with a real URL (what Ithaca is now).
Register one with `static/js/workspaceManager.js`:

```js
export default {
  id: 'ithaca',
  route: '/ithaca',
  title: 'Ithaca',
  surface: 'both',          // 'workspace' | 'float' | 'both'
  collapseSidebar: true,
  mount(container) { /* build DOM into container, once */ },
  activate() { /* start timers/refresh */ },
  deactivate() { /* stop timers; called on close AND on popOut */ },
  onEscape() { return false; }, // true = you handled it, don't close
};
```

Rules that make a descriptor portable between a full workspace and a floating
window (`popOut`/`popIn`):
- Never touch `document.body`, `history`, or sidebar classes directly — the
  host owns navigation, history, title, active-nav-state, and Escape
  arbitration.
- `mount()` runs once; `activate`/`deactivate` run every time the surface
  becomes visible/hidden (including when popped in/out), so timers belong
  there, not in `mount()`.
- See `extensions/ithaca/static/index.js` for a complete worked example
  (converted from the old hand-rolled `static/js/ithaca.js`, which did its own
  `pushState`/`popstate`/Escape/sidebar-collapse — all now the host's job).

## Converting an existing `Modals.register` popup to a workspace

1. Find the feature's `Modals.register(id, {...})` call (`static/js/modalManager.js:1146`).
2. Split its "build the DOM" code into a `mount(container)` that appends into
   the given container instead of wiring up its own draggable/floating window
   chrome.
3. Move anything that starts a timer/poll into `activate()`, and the
   corresponding teardown into `deactivate()`.
4. Register the descriptor with `Workspace.register(...)` instead of
   `Modals.register(...)`.
5. Give it a manifest `nav.route` if it should be deep-linkable.
6. Set `surface: 'both'` if you still want a "pop out to floating window"
   affordance — `Workspace.popOut(id)` re-parents the mounted DOM into a
   `modalManager` window using the existing drag/resize/dock machinery, no
   changes needed there.

## Migration ledger

| Feature | Status | Notes |
|---|---|---|
| Ithaca | ✅ extension + workspace | First mover; backend at `extensions/ithaca/backend.py`, frontend at `extensions/ithaca/static/index.js`. Stays in-repo for development but is excluded from packaged Docker/PyInstaller builds by default — see "Discovery roots" above. |
| RSS reader | ✅ extension + workspace | Backend at `extensions/rss/backend.py` (`setup_feed_routes`), services at `extensions/rss/services/`, frontend at `extensions/rss/static/index.js`. `routes/codex_routes.py` still borrows its router (for the `/api/codex/feeds*` passthrough) via `extension_host.get_router("rss")`, retrieved after `register_all()` runs — see "DB models" below for why the models didn't move too. |
| Doc editor | Not migrated | `static/js/document.js`'s `doc-panel` registration is already a clean `Modals.register` shape — probably the easiest next conversion. |

## Known gaps (deliberate, not oversights)

- **Nav markup isn't generated dynamically.** `extensionHost.js` only wires a
  click handler onto an existing `#tool-<id>-btn` / `#rail-<id>` pair; it
  doesn't inject the button HTML itself. Ithaca's buttons are still static
  markup in `static/index.html`. Building real dynamic nav injection (icons,
  ordering, mobile rail rules) is follow-up work, worth doing once a second
  extension actually needs it.
- **Per-extension settings cards aren't manifest-driven yet.** Ithaca's
  settings card is still static markup in `static/index.html` +
  `static/js/settings.js` (`initIthacaSettings`), because `settings.js`'s
  `initAll()` runs synchronously and an async-fetched card can lose that
  race. The `settings_card` manifest field and `extensionHost.js`'s injector
  exist but aren't used by Ithaca — a future extension needing a settings
  card should either accept that race (rare fields, low stakes) or fix the
  timing first. (This is unrelated to the Settings → Extensions *management*
  tab itself, which is a normal lazy-loaded admin tab — `admin.js`'s
  `initAll()`/`refreshAll()` pattern, same as `users`/`tools`/`system` — and
  doesn't have this race.)
- **Per-route favicon/title** (the inline bootstrap script at the top of
  `static/index.html`) is not manifest-driven — it runs before any JS module
  loads, so it can't consult `/api/extensions`. A new extension wanting a
  custom favicon still adds one entry there.
- **The scope-picker UI label/icon** in `static/js/settings.js` (~line 5421)
  is still a static list — cosmetic metadata, not wired to `scopes` in the
  manifest.
- **`src/tools/ithaca.py`** (the `get_home_weather`/`get_homelab_updates`
  agent tools) stays in core, not in `extensions/ithaca/` — it's wired into
  the agent tool registry (`src/agent_tools/__init__.py`,
  `src/tool_execution.py`, `src/tool_index.py`, `src/tool_schemas.py`) which
  has no extension hook of its own yet. Moving it means either building one
  or accepting the current fan-out for agent tools specifically.
- **No hot-reload.** Install, uninstall, and enable/disable all require a
  full process restart to take effect — FastAPI has no way to un-mount a
  router, and Python doesn't cleanly un-import a module. `reload_required:
  true` in every mutating API response, and the Settings UI copy, are honest
  about this; there's no in-app restart button.
- **No private-repo support.** Git installs set `GIT_TERMINAL_PROMPT=0` so an
  auth-required clone fails fast instead of hanging — there's no credential
  storage or injection. Point at a public repo, or a `.zip` URL that doesn't
  require auth.
- **No locking across concurrent installs.** Two admins installing at the
  same moment (or the same id via both Settings and
  `ODYSSEUS_EXTENSIONS_AUTOINSTALL` at once) isn't guarded against — a
  reasonable gap given this is a single-operator admin action, not a
  multi-tenant concern.

## Every workspace auto-closes when another tool opens

`workspaceManager.js` closes the active workspace whenever a click lands on
`.section-header-flex, .list-item, .icon-rail-btn` elsewhere in the sidebar
or rail (excluding the workspace's own nav button and anything inside its own
mounted DOM). This was originally RSS-specific logic in `static/app.js`,
generalized here so it applies to every workspace (Ithaca included) instead
of being hand-rolled per feature. The auto-close uses `close(id, { push:
true })` — a `pushState` to `/`, not the default `replaceState` — so the
browser Back button still returns to whatever got auto-closed, matching real
multi-page navigation instead of skipping past it.
