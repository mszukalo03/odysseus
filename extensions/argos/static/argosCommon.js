// extensions/argos/static/argosCommon.js
//
// Small helpers shared by the Argos pairing screen — mirrors
// extensions/ithaca/static/ithacaCommon.js.

const API_BASE = window.location.origin;

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

/** fetch() against /api/argos/*, with same-origin credentials — this page
 * is admin-cookie authenticated, never bearer-token. */
export function api(path, opts = {}) {
  return fetch(`${API_BASE}/api/argos${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
}
