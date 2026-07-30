// static/js/workspaceManager.js
//
// Single host for "workspace" features — full-canvas screens with a real URL
// (Ithaca today; RSS/doc-editor are migration candidates — see
// extensions/README.md). Replaces the pattern each such feature used to
// hand-roll for itself: history.pushState/popstate, sidebar-collapse,
// title updates, active-nav-button toggling, and an Escape handler that has
// to sniff the DOM for other open surfaces.
//
// A feature registers a descriptor and never touches document.body, history,
// or sidebar classes directly — that's what makes a feature portable between
// a full workspace and a floating window (see popOut/popIn), and what will
// let RSS/doc-editor migrate off the popup system one at a time later.
//
//   Workspace.register({
//     id: 'ithaca',
//     route: '/ithaca',
//     title: 'Ithaca',
//     surface: 'both',            // 'workspace' | 'float' | 'both'
//     collapseSidebar: true,
//     mount(container) { ... },   // build DOM into container; called once
//     activate() {},              // shown (start timers/refresh)
//     deactivate() {},            // hidden but not unmounted (stop timers)
//     unmount() {},                // pop-out/pop-in re-parents; true teardown is rare
//     onEscape() { return false; },  // true = handled, don't close the workspace
//   });

import * as Modals from './modalManager.js';
import { registerMenuDismiss } from './escMenuStack.js';

const _registry = new Map();   // id -> descriptor
const _routes = new Map();     // route -> id
const _mounted = new Map();    // id -> container element
let _activeId = null;
let _floatingIds = new Set();  // ids currently popped out to a modalManager window
let _host = null;
let _unregisterEscape = null;

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

export function active() {
  return _activeId;
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
 * Open a workspace as the full-canvas surface. `fromRoute` marks a deep-link
 * load (URL is already correct, don't push history again).
 */
export function open(id, { fromRoute = false, replace = false } = {}) {
  const desc = _registry.get(id);
  if (!desc) { console.warn(`Workspace "${id}" is not registered`); return; }
  if (_activeId === id && !_floatingIds.has(id)) return;

  if (_activeId && _activeId !== id) close(_activeId, { silent: true });

  const container = _mount(desc);
  _floatingIds.delete(id);
  const host = _ensureHost();
  host.classList.remove('hidden');
  container.classList.remove('hidden');
  container.classList.add('workspace-surface-open');
  _activeId = id;
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

  if (_unregisterEscape) _unregisterEscape();
  _unregisterEscape = registerMenuDismiss(() => close(id));

  try { desc.activate && desc.activate(); } catch (err) {
    console.error(`Workspace "${id}" activate() failed:`, err);
  }
}

export function close(id = _activeId, { silent = false, fromHistory = false } = {}) {
  if (!id || _activeId !== id) return;
  const desc = _registry.get(id);
  const container = _mounted.get(id);
  try { desc?.deactivate && desc.deactivate(); } catch (err) {
    console.error(`Workspace "${id}" deactivate() failed:`, err);
  }
  container?.classList.add('hidden');
  container?.classList.remove('workspace-surface-open');
  _ensureHost().classList.add('hidden');
  _setActiveNav(id, false);
  _activeId = null;
  if (_unregisterEscape) { _unregisterEscape(); _unregisterEscape = null; }

  if (!silent && desc?.route && !fromHistory && window.location.pathname === desc.route) {
    try { history.replaceState(null, '', '/'); } catch (_) {}
  }
  if (!silent) document.title = 'Odysseus';
  _restoreSidebar();
}

export function toggle(id) {
  if (_activeId === id) close(id); else open(id);
}

/** Re-parent a workspace's mounted DOM into a floating modalManager window. */
export function popOut(id) {
  const desc = _registry.get(id);
  const container = _mounted.get(id);
  if (!desc || !container || desc.surface === 'workspace') return;
  const wasActive = _activeId === id;
  if (wasActive) close(id, { silent: true });

  container.classList.remove('hidden', 'workspace-surface-open');
  container.classList.add('modal', 'workspace-float');
  document.body.appendChild(container);
  _floatingIds.add(id);

  Modals.register(id, {
    label: desc.title || id,
    restoreFn: () => { container.classList.remove('hidden'); },
    closeFn: () => { popIn(id); },
  });
  try { desc.activate && desc.activate(); } catch (_) {}
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
  } else if (_activeId) {
    close(_activeId, { fromHistory: true });
  }
});

// ── Escape: give the active workspace first refusal (e.g. a nested modal it
// owns), then close it — unless something floating is still on top. Floating
// (unmigrated) features aren't workspace-aware yet, so this DOM check is a
// deliberate bridge until they migrate too (see extensions/README.md).
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !_activeId) return;
  const desc = _registry.get(_activeId);
  if (desc?.onEscape && desc.onEscape()) { e.stopPropagation(); return; }
  const floating = document.querySelector('.modal:not(.hidden):not(.workspace-float), .rss-pane-backdrop, .notes-pane');
  if (floating) return;
  close(_activeId);
});

// Returning to the chat via the rail/sidebar chat affordances closes whatever
// workspace is open — these all land the user on the chat screen, which sits
// underneath every workspace. (Moved here from ithaca.js — it's about the
// chat pane a workspace overlays, not specific to any one feature.)
document.addEventListener('click', (e) => {
  if (!_activeId) return;
  const backToChat = e.target.closest(
    '#rail-new-session, #rail-chats, #sidebar-new-chat-btn, #sidebar-brand-btn, #chats-section-title'
  );
  if (backToChat) close(_activeId);
}, true);

// Resolve a deep-link on initial load once the DOM (and registrants) are ready.
export function resolveInitialRoute() {
  const id = _routes.get(window.location.pathname);
  if (id) open(id, { fromRoute: true });
}

const Workspace = { register, isRegistered, open, close, toggle, popOut, popIn, navigate, active, resolveInitialRoute };
export default Workspace;
