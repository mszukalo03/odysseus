// extensions/ithaca/static/ithacaCommon.js
//
// Small helpers shared across the Ithaca frontend modules
// (index.js, tileBuilder.js, tileRenderer.js) — centralized here instead of
// each module keeping its own identical copy.

const API_BASE = window.location.origin;

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

/** fetch() against /api/ithaca/*, with the JSON headers + same-origin
 * credentials every call site needs. */
export function api(path, opts = {}) {
  return fetch(`${API_BASE}/api/ithaca${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
}
