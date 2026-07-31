// static/js/extensionHost.js
//
// Frontend half of the extension system (see src/extension_host.py and
// extensions/README.md). Fetches the enabled-extension manifest, loads each
// extension's CSS/JS, and registers its workspace descriptor with
// workspaceManager. One extension throwing must never break the rest of the
// app — every step is wrapped so a bad extension just logs and is skipped.
//
// Nav wiring: an extension's rail/sidebar buttons are expected at the
// conventional ids `rail-<nav.id>` / `tool-<nav.id>-btn` (matching every
// built-in tool button). Ithaca's buttons already exist as static markup in
// index.html; this only wires their click handlers. Extensions with no
// existing markup are a known follow-up — see extensions/README.md.

import Workspace from './workspaceManager.js';

async function _loadCss(href) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = href;
  document.head.appendChild(link);
}

function _wireNav(nav) {
  if (!nav) return;
  // Only the sidebar button gets a direct listener — every rail button in
  // this app already delegate-clicks its paired sidebar button (see
  // `_railToolMap` in app.js), so attaching here too would double-toggle.
  const sidebarBtn = document.getElementById(`tool-${nav.id}-btn`);
  sidebarBtn?.addEventListener('click', () => Workspace.toggle(nav.id));
}

async function _appendSettingsCard(card) {
  if (!card || !card.url) return;
  try {
    const resp = await fetch(card.url, { credentials: 'same-origin' });
    if (!resp.ok) return;
    const html = await resp.text();
    const container = document.getElementById('settings-extension-cards');
    if (!container) return;
    const wrap = document.createElement('div');
    if (card.admin_only) wrap.classList.add('admin-only');
    wrap.innerHTML = html;
    container.appendChild(wrap);
  } catch (err) {
    console.error('Extension settings card failed to load:', card.url, err);
  }
}

async function _loadExtension(manifest) {
  if (manifest.css) await _loadCss(manifest.css);
  if (manifest.entry) {
    try {
      const mod = await import(manifest.entry);
      const descriptor = mod.default || mod;
      if (descriptor && descriptor.id) {
        Workspace.register(descriptor);
      }
    } catch (err) {
      console.error(`Extension "${manifest.id}" failed to load its entry module:`, err);
    }
  }
  _wireNav(manifest.nav);
  await _appendSettingsCard(manifest.settings_card);
}

export async function init() {
  let manifests = [];
  try {
    const resp = await fetch('/api/extensions', { credentials: 'same-origin' });
    if (resp.ok) manifests = await resp.json();
  } catch (err) {
    console.error('Failed to fetch /api/extensions:', err);
    return;
  }
  await Promise.all(manifests.map((m) => _loadExtension(m).catch((err) => {
    console.error(`Extension "${m.id}" failed to initialize:`, err);
  })));
  Workspace.resolveInitialRoute();
}

export default { init };
