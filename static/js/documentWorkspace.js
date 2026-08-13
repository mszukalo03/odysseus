// static/js/documentWorkspace.js
//
// Nav-shell adapter for the document editor. This is the whole of the doc
// editor's migration from "a thing static/app.js opens by hand" to "a feature
// the nav shell owns", and it deliberately holds no editor logic of its own —
// static/js/document.js still builds every pixel of the pane. See the
// migration ledger in extensions/README.md.
//
// The editor is the first feature whose two display modes are genuinely
// different surfaces rather than the same DOM in a different frame:
//
//   popup (default) — the historical split pane docked beside #chat-container
//                     with a drag divider, so you can chat while the AI
//                     streams into the doc. This is why the descriptor
//                     implements openPopup()/closePopup() instead of letting
//                     the host re-parent its page DOM: there is no page DOM to
//                     re-parent, the pane simply gets a different parent and
//                     skips the divider.
//   page            — the same pane parented into the full-canvas workspace
//                     surface, rendering like Ithaca and RSS.
//
// Both modes call the same document.js entry points. Which surface a given
// open lands on is decided by `_surface()` below — document.js's injected
// "surface provider", asked once at the top of every openPanel() call,
// regardless of whether the shell or the editor itself initiated that open.
// `setPageHost()` still exists as the lower-level switch `_surface()` and
// `activate()` both drive.

import Workspace from './workspaceManager.js';

const ID = 'doc-editor';

let _doc = null;         // the document.js module
let _sessions = null;    // the sessions.js module
let _onVisibility = null;
let _container = null;

/**
 * Open the editor's *content* — independent of which surface it renders on.
 *
 * Preserves the behavior the #overflow-doc-btn handler in app.js used to own:
 * a doc always belongs to a chat session, so a pending "New Chat" has to be
 * materialized before the editor can attach anything to it. Without a session
 * we still open an empty pane (`ensureDocPanel`) rather than doing nothing.
 */
async function _openContent() {
  if (!_doc) return;
  let sessionId = _sessions && _sessions.getCurrentSessionId && _sessions.getCurrentSessionId();
  if (!sessionId && _sessions && _sessions.hasPendingChat && _sessions.hasPendingChat()) {
    await _sessions.materializePendingSession();
    sessionId = _sessions.getCurrentSessionId();
  }
  if (sessionId) _doc.loadSessionDocs(sessionId);
  else _doc.ensureDocPanel();
}

/**
 * document.js's injected surface provider (see the "Surface provider" note
 * above openPanel() in document.js). Called at the top of EVERY openPanel()
 * — not just opens the shell itself initiated — which is what makes the
 * editor honor its page/popup setting from every entry point (boot restore,
 * /doc, AI streaming, email compose, the library, ...), not only the toolbar
 * button.
 *
 * Page mode: ask the shell for the page surface directly. `acquirePageSurface`
 * mounts/shows/wires nav+route+escape exactly like a normal open would, but
 * does NOT call this descriptor's own activate() — avoiding a call back into
 * the very openPanel() this function is being called from.
 * Popup mode: tell the shell a popup just opened (so isOpen()/toggle()/
 * nav-active state is correct even though the shell didn't initiate this
 * open) and return null so document.js builds the split pane.
 */
function _surface() {
  if (Workspace.displayMode(ID) === 'page') {
    const container = Workspace.acquirePageSurface(ID);
    if (container) { _container = container; return container; }
  }
  Workspace.notePopupOpen(ID);
  return null;
}

function _notify(visible) {
  try { _onVisibility && _onVisibility(visible); } catch (err) {
    console.error('doc-editor visibility callback failed:', err);
  }
}

const descriptor = {
  id: ID,
  route: '/editor',
  title: 'Editor',
  surface: 'both',
  // Popup is the historical shape and stays the out-of-the-box default: the
  // chat-beside-doc workflow is what the editor was built around, and silently
  // moving every existing user to full-canvas would be a regression, not an
  // upgrade. `feature_display_modes` in settings is how you opt into page mode.
  defaultDisplay: 'popup',
  collapseSidebar: true,

  // The pane is built by document.js on every open (openPanel constructs it
  // from scratch), so there is nothing to build once up front — mount only has
  // to remember which element page mode should render into.
  mount(container) {
    _container = container;
    container.classList.add('doc-editor-workspace');
  },

  activate({ mode } = {}) {
    if (mode === 'page') _doc?.setPageHost(_container);
    _openContent();
    _notify(true);
  },

  deactivate() {
    _doc?.closePanel();
    _doc?.setPageHost(null);
    _notify(false);
  },

  openPopup() {
    _doc?.setPageHost(null);   // popup mode is the chat-adjacent split pane
    _openContent();
    _notify(true);
  },

  closePopup() {
    _doc?.closePanel();
    _notify(false);
  },

  // document.js owns transient surfaces inside the pane (find bar, version
  // history, format menus, the AI-reply popover) and dismisses each on its own
  // Escape handler. Claim the key while one of the two plain show/hide panels
  // is open, so a single Escape doesn't both dismiss the sub-surface *and*
  // close the whole editor out from under the user.
  //
  // Belt-and-braces rather than the sole defence: in practice the editor's own
  // handlers stop propagation before the host's keydown listener runs. Note
  // this hook does NOT gate the host's other Escape path — the
  // escMenuStack entry registered in _openPage() closes the workspace without
  // consulting onEscape. That asymmetry predates this migration (RSS's
  // onEscape has it too) and is left alone here.
  onEscape() {
    const findBar = document.getElementById('doc-find-bar');
    if (findBar && findBar.style.display !== 'none') return true;
    const versions = document.getElementById('doc-version-panel');
    if (versions && !versions.classList.contains('hidden')) return true;
    return false;
  },
};

/**
 * Register the editor with the nav shell.
 *
 * @param {object} documentModule  static/js/document.js
 * @param {object} sessionModule   static/js/sessions.js
 * @param {(visible:boolean)=>void} onVisibilityChange — lets app.js keep its
 *        toolbar button state and persisted toggle state in sync without this
 *        module having to know about either.
 */
export function init({ documentModule, sessionModule, onVisibilityChange } = {}) {
  _doc = documentModule || null;
  _sessions = sessionModule || null;
  _onVisibility = onVisibilityChange || null;
  _doc?.setSurfaceProvider(_surface);
  // document.js's own close path (the pane's X/hide button, not
  // Workspace.close('doc-editor')) needs to tell the shell it happened.
  _doc?.setClosedNotifier(() => Workspace.noteClosed(ID));
  Workspace.register(descriptor);
}

/** What the editor's nav buttons do — open/close in the configured mode. */
export function toggle() {
  Workspace.toggle(ID);
}

export function isOpen() {
  return Workspace.isOpen(ID);
}

export default { init, toggle, isOpen, id: ID };
