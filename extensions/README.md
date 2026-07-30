# Extensions

Custom features that don't need to live in the merge-sensitive core (`app.py`,
`static/app.js`, `static/index.html`) go here instead, one directory per
extension. This is what lets `dev` stay close to upstream/community while
still shipping features like Ithaca.

Backend host: [`src/extension_host.py`](../src/extension_host.py).
Frontend host: [`static/js/extensionHost.js`](../static/js/extensionHost.js) +
[`static/js/workspaceManager.js`](../static/js/workspaceManager.js).

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
  is not generated dynamically yet** — see "Known gaps" below.
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
| Ithaca | ✅ extension + workspace | First mover; backend at `extensions/ithaca/backend.py`, frontend at `extensions/ithaca/static/index.js` |
| RSS reader | Not migrated | `static/js/feedReader.js` (1357 lines) has a draggable window, 3 nested ad-hoc modals, and its backend router is borrowed by `routes/codex_routes.py` (`app.py` passes `feed_router=feed_router`) — that coupling needs resolving before the router can move into an extension. Its DB models (`FeedGroup`/`Feed`/`Article`/`FeedSyncAccount` in `core/database.py`) and migrations would move with it. |
| Doc editor | Not migrated | `static/js/document.js`'s `doc-panel` registration is already a clean `Modals.register` shape — probably the easiest second conversion once RSS or another feature proves the recipe out. |

## Known gaps (deliberate, not oversights)

- **Nav markup isn't generated dynamically.** `extensionHost.js` only wires a
  click handler onto an existing `#tool-<id>-btn` / `#rail-<id>` pair; it
  doesn't inject the button HTML itself. Ithaca's buttons are still static
  markup in `static/index.html`. Building real dynamic nav injection (icons,
  ordering, mobile rail rules) is follow-up work, worth doing once a second
  extension actually needs it.
- **Settings cards aren't manifest-driven yet.** Ithaca's settings card is
  still static markup in `static/index.html` + `static/js/settings.js`
  (`initIthacaSettings`), because `settings.js`'s `initAll()` runs
  synchronously and an async-fetched card can lose that race. The
  `settings_card` manifest field and `extensionHost.js`'s injector exist but
  aren't used by Ithaca — a future extension needing a settings card should
  either accept that race (rare fields, low stakes) or fix the timing first.
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
