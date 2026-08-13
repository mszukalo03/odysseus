// static/js/workspaceManager.js
//
// Single host for "nav shell" features — Ithaca, RSS and the doc editor.
// Replaces the pattern each such feature used to hand-roll for itself:
// history.pushState/popstate, sidebar-collapse, title updates,
// active-nav-button toggling, and an Escape handler that has to sniff the DOM
// for other open surfaces.
//
// A feature registers a descriptor and never touches document.body, history,
// or sidebar classes directly — that's what makes a feature portable between
// a full-canvas page and a popup window.
//
//   Workspace.register({
//     id: 'ithaca',
//     route: '/ithaca',
//     title: 'Ithaca',
//     surface: 'both',            // 'workspace' | 'float' | 'both'
//     defaultDisplay: 'page',     // 'page' | 'popup' — used when settings
//                                 //   has no stored choice for this id
//     collapseSidebar: true,
//     mount(container) { ... },   // build DOM into container; called once
//     activate(ctx) {},           // shown; ctx.mode is 'page' | 'popup'
//     deactivate() {},            // hidden but not unmounted (stop timers)
//     unmount() {},                // pop-out/pop-in re-parents; true teardown is rare
//     onEscape() { return false; },  // true = handled, don't close the workspace
//   });
//
// ── Display modes ──────────────────────────────────────────────────────────
//
// Every registered feature can render two ways, and which one it uses is a
// user setting (`feature_display_modes` in src/settings.py, served by
// routes/display_routes.py, fetched once at boot by `loadDisplayModes()`):
//
//   'page'  — full-canvas surface in #workspace-host, with the feature's real
//             URL pushed onto history. Ithaca/RSS's shape today.
//   'popup' — a floating window. Generic features get this for free by
//             re-parenting their already-mounted DOM into a modalManager
//             window (popOut/popIn). A feature whose popup form is genuinely
//             a different surface — the doc editor's chat-adjacent split pane
//             is not the same DOM as a full-canvas page — opts out by
//             implementing `openPopup()`/`closePopup()` on its descriptor,
//             and the host delegates to those instead of re-parenting.
//
// Routes belong to page mode only. A popup floats *over* the chat screen,
// which is what '/' already means, so deep-linking a popup-mode feature opens
// the popup and normalizes the URL back to '/' rather than inventing a URL
// that wouldn't survive a reload.

import * as Modals from './modalManager.js';
import { registerMenuDismiss } from './escMenuStack.js';

const _registry = new Map();   // id -> descriptor
const _routes = new Map();     // route -> id
const _mounted = new Map();    // id -> container element
// Ordered page-mode surfaces currently showing, left-to-right: [] (nothing),
// [id] (today's single-workspace shape), or [idLeft, idRight] (split view).
// An ordered array rather than a left/right pair of named slots because the
// seam, the `data-pane` attribute, and "which id is the other one" are all
// naturally position-based — see static/js/workspaceSplit.js.
const _panes = [];
// Which pane owns the URL, the document title, and gets first refusal on
// Escape. Always one of _panes' entries, or null when _panes is empty.
// `active()` returns this — same external meaning `_activeId` used to have
// in the single-pane era, so callers outside this module don't change.
let _focusedId = null;
let _floatingIds = new Set();  // ids currently popped out to a modalManager window
let _host = null;
let _unregisterEscape = null;

function _isPane(id) {
  return _panes.includes(id);
}

// id -> 'page' | 'popup', from the settings API. Empty until loadDisplayModes()
// resolves; every read falls back to the descriptor's own default, so a failed
// or slow fetch just means "everyone uses their built-in default" rather than
// a broken nav.
const _displayModes = new Map();
// ids currently open as a descriptor-owned popup (`openPopup`), as opposed to
// _floatingIds which tracks host-owned re-parented ones. Kept apart because
// closing them takes different paths.
const _ownPopupIds = new Set();
// ids where close()/closePopup() is already mid-teardown for this id — guards
// notePopupOpen/noteClosed against reentering when a feature's own close path
// (e.g. document.js's closePanel()) fires from *inside* a deactivate()/
// closePopup() call the shell itself triggered. See notePopupOpen/noteClosed.
const _selfClosing = new Set();

const _VALID_MODES = new Set(['page', 'popup']);

/**
 * Resolve how `id` should render right now: the stored user choice, else the
 * descriptor's `defaultDisplay`, else 'page' — then clamped to what the
 * descriptor's `surface` actually supports, so a stale stored 'popup' for a
 * page-only feature can't wedge it shut.
 */
export function displayMode(id) {
  const desc = _registry.get(id);
  const surface = desc?.surface || 'both';
  if (surface === 'workspace') return 'page';
  if (surface === 'float') return 'popup';
  const stored = _displayModes.get(id);
  if (_VALID_MODES.has(stored)) return stored;
  const fallback = desc?.defaultDisplay;
  return _VALID_MODES.has(fallback) ? fallback : 'page';
}

/** Fetch the stored per-feature choices. Safe to call before any register(). */
export async function loadDisplayModes() {
  try {
    const resp = await fetch('/api/display-modes', { credentials: 'same-origin' });
    if (!resp.ok) return;
    const data = await resp.json();
    for (const [id, mode] of Object.entries(data.modes || {})) {
      if (_VALID_MODES.has(mode)) _displayModes.set(id, mode);
    }
  } catch (err) {
    console.error('Failed to load feature display modes:', err);
  }
}

/**
 * Change a feature's display mode and persist it. If the feature is open, it
 * is reopened in the new mode so the change is visible immediately instead of
 * on next launch.
 *
 * Persists first, applies locally only on success, and rejects on failure
 * (bad network, or a 403 from a non-admin in multi-user mode) — a caller like
 * a Settings UI control needs an honest failure to roll its own state back
 * to, not a local mode flip that quietly never made it to the server.
 */
export async function setDisplayMode(id, mode) {
  if (!_VALID_MODES.has(mode)) throw new Error(`Unknown display mode: ${mode}`);
  const resp = await fetch(`/api/display-modes/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ mode }),
  });
  if (!resp.ok) {
    let detail = '';
    try { detail = (await resp.json()).detail || ''; } catch (_) {}
    throw new Error(`Persisting display mode for "${id}" failed: HTTP ${resp.status}${detail ? ` — ${detail}` : ''}`);
  }
  const wasOpen = isOpen(id);
  if (wasOpen) closeAny(id);
  _displayModes.set(id, mode);
  if (wasOpen) open(id);
  return mode;
}

/** True if `id` is showing in any mode (page, host popup, or own popup). */
export function isOpen(id) {
  return _isPane(id) || _floatingIds.has(id) || _ownPopupIds.has(id);
}

function _ensureHost() {
  if (_host && document.body.contains(_host)) return _host;
  _host = document.getElementById('workspace-host');
  if (!_host) {
    _host = document.createElement('div');
    _host.id = 'workspace-host';
    _host.className = 'workspace-host hidden';
    document.body.appendChild(_host);
  }
  return _host;
}

// Reuses the shared "collapse sidebar to rail for a fullscreen route" contract
// that notes.js, emailLibrary.js, modalSnap.js and tileManager.js all already
// participate in (document.body.dataset.routeCollapsedSidebar +
// window._restoreSidebarIfRouteCollapsed, defined in app.js) — a workspace
// closing must restore the sidebar the same way those features do.
function _collapseSidebarToRail() {
  const sb = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  if (!sb || !rail) return;
  if (!sb.classList.contains('hidden')) {
    document.body.dataset.routeCollapsedSidebar = '1';
  }
  sb.classList.add('hidden');
  rail.classList.remove('rail-hidden');
  try { window.syncRailSide && window.syncRailSide(); } catch (_) {}
}

function _restoreSidebar() {
  try { window._restoreSidebarIfRouteCollapsed && window._restoreSidebarIfRouteCollapsed(); } catch (_) {}
}

function _setActiveNav(id, on) {
  document.getElementById(`rail-${id}`)?.classList.toggle('active', on);
  document.getElementById(`tool-${id}-btn`)?.classList.toggle('active', on);
}

/** Register a workspace descriptor. Idempotent — re-registering replaces it. */
export function register(descriptor) {
  if (!descriptor || !descriptor.id) throw new Error('Workspace.register requires an id');
  _registry.set(descriptor.id, descriptor);
  if (descriptor.route) _routes.set(descriptor.route, descriptor.id);
}

export function isRegistered(id) {
  return _registry.has(id);
}

/**
 * Every registered feature that has a real page/popup choice to make —
 * i.e. `surface: 'both'` (the only value `displayMode()` doesn't clamp to a
 * single fixed mode). Used by the Settings UI to build the per-feature
 * display list without hardcoding ids/labels there; a feature only needs a
 * `title` to show up correctly.
 */
export function list() {
  return [..._registry.values()]
    .filter((d) => (d.surface || 'both') === 'both')
    .map((d) => ({ id: d.id, title: d.title || d.id }));
}

export function active() {
  return _focusedId;
}

/** Ordered pane ids currently showing as page-mode surfaces (0, 1, or 2). */
export function panes() {
  return [..._panes];
}

/** True once a second pane is open alongside the focused one. */
export function isSplit() {
  return _panes.length > 1;
}

function _mount(desc) {
  if (_mounted.has(desc.id)) return _mounted.get(desc.id);
  const container = document.createElement('div');
  container.className = 'workspace-surface';
  container.dataset.workspaceId = desc.id;
  container.classList.add('hidden');
  _ensureHost().appendChild(container);
  _mounted.set(desc.id, container);
  try { desc.mount && desc.mount(container); } catch (err) {
    console.error(`Workspace "${desc.id}" mount() failed:`, err);
  }
  return container;
}

/**
 * Open a feature in whichever display mode it's configured for. This is the
 * single entry point every nav button and route resolution goes through —
 * callers ask for "open Ithaca", not "open Ithaca as a page".
 *
 * `mode` forces one rendering for this call only (it does not persist);
 * `fromRoute` marks a deep-link load (URL is already correct, don't push
 * history again).
 */
export function open(id, { fromRoute = false, replace = false, mode = null } = {}) {
  const desc = _registry.get(id);
  if (!desc) { console.warn(`Workspace "${id}" is not registered`); return; }
  const resolved = _VALID_MODES.has(mode) ? mode : displayMode(id);
  if (resolved === 'popup') {
    // A deep-link that resolves to popup has a URL that won't survive a
    // reload as a page — normalize it back to '/' (the chat screen the popup
    // floats over) so Back/refresh behave sensibly.
    if (fromRoute && desc.route && window.location.pathname === desc.route) {
      try { history.replaceState(null, '', '/'); } catch (_) {}
    }
    openPopup(id);
    return;
  }
  _openPage(id, { fromRoute, replace });
}

/**
 * Mount, show, and wire nav/route/escape for `id`'s full-canvas surface —
 * everything the page rendering does except call `activate()`. Split out of
 * `_openPage` so a feature's own opening code (not a nav click) can ask the
 * shell for its surface *before* it renders into it, without the shell
 * calling back into the feature's `activate()` while that same open is still
 * in progress (see `acquirePageSurface`). Callers are responsible for the
 * "already showing" guard — `_openPage` and `acquirePageSurface` each apply
 * it themselves, since only they know whether skipping means "no-op" or
 * "reuse the existing container".
 */
function _showPageSurface(id, { fromRoute = false, replace = false, split = false } = {}) {
  const desc = _registry.get(id);
  if (!desc) return null;
  // Coming from a popup form of the same feature — tear that down first so we
  // don't end up showing both at once.
  if (_ownPopupIds.has(id)) closePopup(id);
  if (_floatingIds.has(id)) popIn(id);

  // A plain (non-split) open replaces whatever pane(s) are showing — today's
  // single-workspace behavior. `split: true` (the split-view entry points,
  // added in a later commit) keeps the existing pane(s) and adds `id`
  // alongside — capped at two by whoever calls with `split: true`.
  if (!split) {
    for (const existing of [..._panes]) {
      if (existing !== id) close(existing, { silent: true });
    }
  }

  const container = _mount(desc);
  _floatingIds.delete(id);
  const host = _ensureHost();
  host.classList.remove('hidden');
  container.classList.remove('hidden');
  container.classList.add('workspace-surface-open');
  // Exposed so a feature can style or branch on its own rendering without
  // asking the host — `[data-display-mode="popup"] .foo { ... }`.
  container.dataset.displayMode = 'page';
  if (!_panes.includes(id)) _panes.push(id);
  if (desc.collapseSidebar) _collapseSidebarToRail();
  _setActiveNav(id, true);

  if (desc.route) {
    if (!fromRoute && window.location.pathname !== desc.route) {
      try {
        if (replace) history.replaceState({ workspaceId: id }, '', desc.route);
        else history.pushState({ workspaceId: id }, '', desc.route);
      } catch (_) {}
    }
    document.title = desc.title ? `${desc.title} — Odysseus` : document.title;
  }
  // The pane just shown becomes focused — owns the URL/title/Escape from
  // here, same as clicking a browser tab focuses it.
  _focusedId = id;
  _syncEscape();

  return container;
}

/** (Re)wire the single Escape-dismiss token to the currently focused pane. */
function _syncEscape() {
  if (_unregisterEscape) { _unregisterEscape(); _unregisterEscape = null; }
  if (!_focusedId) return;
  _unregisterEscape = registerMenuDismiss(() => close(_focusedId));
}

/** The full-canvas page rendering: show the surface, then activate the feature. */
function _openPage(id, opts = {}) {
  const desc = _registry.get(id);
  if (!desc) { console.warn(`Workspace "${id}" is not registered`); return; }
  if (_isPane(id) && !_floatingIds.has(id) && _focusedId === id) return;
  const container = _showPageSurface(id, opts);
  if (!container) return;
  try { desc.activate && desc.activate({ mode: 'page' }); } catch (err) {
    console.error(`Workspace "${id}" activate() failed:`, err);
  }
}

/**
 * Public: acquire `id`'s page surface directly — mount, show, and wire
 * nav/route/escape exactly as a normal page open would, but do NOT call the
 * descriptor's `activate()`. For a feature whose own open path builds its DOM
 * itself (document.js's `openPanel()` is the motivating case, via
 * `documentWorkspace.js`'s surface provider) and needs to know "render into a
 * page host, and which element" before that DOM exists — calling `open()`
 * instead would call back into the very code that's already running.
 *
 * Returns `null` when `id`'s resolved display mode isn't `'page'` and
 * `force` isn't set — so a caller can use the return value itself as the
 * page/popup branch. Idempotent: returns the existing container without
 * re-running mount/nav/route/escape wiring if `id` is already the active page.
 */
export function acquirePageSurface(id, { force = false, fromRoute = false, replace = false, split = false } = {}) {
  if (!_registry.has(id)) return null;
  if (!force && displayMode(id) !== 'page') return null;
  if (_isPane(id) && !_floatingIds.has(id)) return _mounted.get(id) || null;
  return _showPageSurface(id, { fromRoute, replace, split });
}

/**
 * The popup rendering. Two implementations behind one call:
 *
 *  - descriptor implements `openPopup()` → delegate to it. For a feature whose
 *    popup form is a genuinely different surface from its page form (the doc
 *    editor's split pane), re-parenting the page DOM would be wrong.
 *  - otherwise → mount the normal workspace DOM and re-parent it into a
 *    modalManager window via popOut(). Ithaca and RSS get this for free.
 */
export function openPopup(id) {
  const desc = _registry.get(id);
  if (!desc) { console.warn(`Workspace "${id}" is not registered`); return; }
  if (_floatingIds.has(id) || _ownPopupIds.has(id)) return;
  if (_isPane(id)) close(id, { silent: true });

  if (typeof desc.openPopup === 'function') {
    _ownPopupIds.add(id);
    _setActiveNav(id, true);
    try { desc.openPopup(); } catch (err) {
      _ownPopupIds.delete(id);
      _setActiveNav(id, false);
      console.error(`Workspace "${id}" openPopup() failed:`, err);
    }
    return;
  }
  popOut(id);
}

/** Reverse of openPopup, for either implementation. */
export function closePopup(id) {
  const desc = _registry.get(id);
  if (!desc) return;
  if (_ownPopupIds.has(id)) {
    _ownPopupIds.delete(id);
    _setActiveNav(id, false);
    // Same reentrancy wrap as close() — desc.closePopup() may call back into
    // noteClosed(id) (e.g. documentWorkspace's closePopup() calls
    // closePanel(), which fires the close notifier).
    _selfClosing.add(id);
    try {
      try { desc.closePopup && desc.closePopup(); } catch (err) {
        console.error(`Workspace "${id}" closePopup() failed:`, err);
      }
    } finally {
      _selfClosing.delete(id);
    }
    return;
  }
  if (_floatingIds.has(id)) popIn(id);
}

/** Close `id` however it happens to be open. */
export function closeAny(id) {
  if (_ownPopupIds.has(id) || _floatingIds.has(id)) { closePopup(id); return; }
  if (_isPane(id)) close(id);
}

/**
 * A descriptor-owned popup was opened by the feature itself (e.g.
 * document.js's openPanel() resolving to popup mode via its surface
 * provider), not through Workspace.openPopup(). Reconciles isOpen()/
 * toggle()/nav-active state for opens the shell didn't initiate. A no-op
 * when `openPopup()` already booked this id (the normal path, where the
 * shell adds to `_ownPopupIds` *before* calling the descriptor) or when `id`
 * is already the active page — a page isn't a popup regardless of who asks.
 */
export function notePopupOpen(id) {
  if (!_registry.has(id) || _ownPopupIds.has(id)) return;
  if (_isPane(id) && !_floatingIds.has(id)) return;
  _ownPopupIds.add(id);
  _setActiveNav(id, true);
}

/**
 * A feature tore its own surface down outside the shell's own close()/
 * closePopup() — e.g. document.js's closePanel() firing its injected close
 * notifier. Reconciles shell state (nav, URL, sidebar) without calling back
 * into the feature. Guarded by `_selfClosing` against the reentrant case
 * where close()/closePopup() is already mid-teardown for this same id and
 * will finish the job itself once its own deactivate()/closePopup() call
 * returns.
 */
export function noteClosed(id) {
  if (_selfClosing.has(id)) return;
  if (_ownPopupIds.delete(id)) { _setActiveNav(id, false); return; }
  if (_isPane(id)) close(id);
}

/**
 * `push`: use history.pushState instead of the default replaceState when
 * clearing the URL back to '/'. Used by the auto-close-on-other-tool-click
 * delegate below so the browser Back button still returns to the workspace
 * that got auto-closed (real "step back through pages" navigation) — an
 * explicit close via the workspace's own back/close button keeps the default
 * replaceState behavior (today's tested behavior, unchanged).
 */
export function close(id = _focusedId, { silent = false, fromHistory = false, push = false } = {}) {
  if (!id || !_isPane(id)) return;
  const desc = _registry.get(id);
  const container = _mounted.get(id);
  // Wrapped so a feature whose own close path (e.g. document.js's
  // closePanel()) calls back into `noteClosed(id)` from inside deactivate()
  // finds it a no-op instead of re-running this same close() reentrantly.
  _selfClosing.add(id);
  try {
    try { desc?.deactivate && desc.deactivate(); } catch (err) {
      console.error(`Workspace "${id}" deactivate() failed:`, err);
    }
  } finally {
    _selfClosing.delete(id);
  }
  container?.classList.add('hidden');
  container?.classList.remove('workspace-surface-open');
  const idx = _panes.indexOf(id);
  if (idx !== -1) _panes.splice(idx, 1);
  _setActiveNav(id, false);
  const wasFocused = _focusedId === id;

  if (_panes.length === 0) {
    // Last (or only) pane closing — same teardown as the single-workspace
    // era: hide the whole host, release the Escape token, go back to '/'.
    _ensureHost().classList.add('hidden');
    _focusedId = null;
    if (_unregisterEscape) { _unregisterEscape(); _unregisterEscape = null; }
    if (!silent && desc?.route && !fromHistory && window.location.pathname === desc.route) {
      try {
        if (push) history.pushState(null, '', '/');
        else history.replaceState(null, '', '/');
      } catch (_) {}
    }
    if (!silent) document.title = 'Odysseus';
    _restoreSidebar();
  } else if (wasFocused) {
    // A split partner survives — hand it the URL/title/Escape instead of
    // collapsing to '/'. Skip the URL/title write when this close came from
    // the browser's own popstate (fromHistory) — the address bar already
    // moved on its own, and rewriting it here would fight that navigation.
    _focusedId = _panes[0];
    const survivorDesc = _registry.get(_focusedId);
    if (!(silent || fromHistory) && survivorDesc?.route) {
      try { history.replaceState({ workspaceId: _focusedId }, '', survivorDesc.route); } catch (_) {}
      document.title = survivorDesc.title ? `${survivorDesc.title} — Odysseus` : document.title;
    }
    _syncEscape();
  }
  // sidebar collapse is booked once for the pair (see _collapseSidebarToRail)
  // and released only when the last pane closes, above — a survivor pane
  // still wants the rail collapsed, so no _restoreSidebar() call here.
}

/** Close every open pane — "return fully to chat". */
function _closeAllPanes(opts) {
  for (const id of [..._panes]) close(id, opts);
}

/** What a nav button does: open in the configured mode, or close if showing. */
export function toggle(id) {
  if (isOpen(id)) closeAny(id); else open(id);
}

/**
 * Re-parent a workspace's mounted DOM into a floating modalManager window.
 * Mounts on demand — popup can now be a feature's *initial* rendering, not
 * only something you pop out of an already-open page.
 */
export function popOut(id) {
  const desc = _registry.get(id);
  if (!desc || desc.surface === 'workspace') return;
  const container = _mount(desc);
  if (!container) return;
  const wasActive = _isPane(id);
  if (wasActive) close(id, { silent: true });

  container.classList.remove('hidden', 'workspace-surface-open');
  container.classList.add('modal', 'workspace-float');
  container.dataset.displayMode = 'popup';
  document.body.appendChild(container);
  _floatingIds.add(id);

  _setActiveNav(id, true);

  Modals.register(id, {
    label: desc.title || id,
    restoreFn: () => { container.classList.remove('hidden'); },
    closeFn: () => { popIn(id); },
  });
  try { desc.activate && desc.activate({ mode: 'popup' }); } catch (_) {}
}

/** Reverse of popOut — re-parent back into the workspace host. */
export function popIn(id) {
  const desc = _registry.get(id);
  const container = _mounted.get(id);
  if (!desc || !container || !_floatingIds.has(id)) return;
  Modals.unregister(id);
  container.classList.remove('modal', 'workspace-float');
  container.classList.add('hidden');
  _ensureHost().appendChild(container);
  _floatingIds.delete(id);
  _setActiveNav(id, false);
  try { desc.deactivate && desc.deactivate(); } catch (_) {}
}

/** Programmatic navigation — works the same as clicking a nav button. */
export function navigate(path) {
  const id = _routes.get(path);
  if (id) { open(id); return true; }
  return false;
}

// ── Single router: one popstate handler for every registered workspace ──
window.addEventListener('popstate', () => {
  const path = window.location.pathname;
  const id = _routes.get(path);
  if (id) {
    open(id, { fromRoute: true });
  } else if (_focusedId) {
    close(_focusedId, { fromHistory: true });
  }
});

// ── Escape: give the FOCUSED pane first refusal (e.g. a nested modal it
// owns), then close just that pane — unless something floating is still on
// top. Floating (unmigrated) features aren't workspace-aware yet, so this DOM
// check is a deliberate bridge until they migrate too (see
// extensions/README.md). The unfocused split partner (if any) is never asked
// and never closed by Escape — a single Escape closing two panes at once
// would be surprising, and the focused pane is by definition where the user
// is typing/looking.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !_focusedId) return;
  const desc = _registry.get(_focusedId);
  if (desc?.onEscape && desc.onEscape()) { e.stopPropagation(); return; }
  const floating = document.querySelector('.modal:not(.hidden):not(.workspace-float), .notes-pane');
  if (floating) return;
  close(_focusedId);
});

// Returning to the chat via the rail/sidebar chat affordances closes every
// open pane — these all land the user on the chat screen, which sits
// underneath every workspace. (Moved here from ithaca.js — it's about the
// chat pane a workspace overlays, not specific to any one feature.)
document.addEventListener('click', (e) => {
  if (!_panes.length) return;
  const backToChat = e.target.closest(
    '#rail-new-session, #rail-chats, #sidebar-new-chat-btn, #sidebar-brand-btn, #chats-section-title'
  );
  if (backToChat) _closeAllPanes();
}, true);

// Opening any other sidebar/rail tool closes every open pane — the same idea
// as "back to chat" above, generalized so every workspace gets it for free
// instead of hand-rolling it per feature (this used to be RSS-specific code
// in app.js). Skipped for a Ctrl/Cmd-click (that gesture means "open beside",
// not "switch tool" — see the split-entry commit) and for either pane's own
// nav button. `push: true` so the browser Back button still returns to the
// pane(s) that just got auto-closed, matching real multi-page navigation —
// see close()'s `push` option.
document.addEventListener('click', (e) => {
  if (!_panes.length) return;
  if (e.metaKey || (e.ctrlKey && !/Mac/i.test(navigator.platform || ''))) return;
  const control = e.target.closest('.section-header-flex, .list-item, .icon-rail-btn');
  if (!control) return;
  if (_panes.some((id) => control.id === `tool-${id}-btn` || control.id === `rail-${id}`)) return;
  if (control.closest('.workspace-surface, .workspace-host')) return;
  setTimeout(() => _closeAllPanes({ push: true }), 0);
}, true);

// Resolve a deep-link on initial load once the DOM (and registrants) are ready.
export function resolveInitialRoute() {
  const id = _routes.get(window.location.pathname);
  if (id) open(id, { fromRoute: true });
}

const Workspace = {
  register, isRegistered, list, open, close, toggle, popOut, popIn, navigate, active,
  resolveInitialRoute,
  // Display-mode surface (see the "Display modes" note at the top).
  openPopup, closePopup, closeAny, isOpen,
  displayMode, setDisplayMode, loadDisplayModes,
  acquirePageSurface, notePopupOpen, noteClosed,
  panes, isSplit,
};
export default Workspace;
