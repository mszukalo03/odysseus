// extensions/argos/static/index.js — Argos pairing screen.
//
// This page's only job is minting a scoped token and handing the user
// install instructions for the browser extension (extensions/argos/browser/,
// downloadable as a zip via GET /api/argos/browser-extension.zip). All the
// actual Q&A happens in the extension's side panel, driven by the extension
// itself against /api/argos/context + /api/chat_stream — this workspace
// never talks to those routes.
//
// Registered with workspaceManager.js the same way every other extension
// workspace is (see extensions/ithaca/static/index.js for the fuller
// reference implementation this mirrors).

import Workspace from '/static/js/workspaceManager.js';
import { esc, api } from './argosCommon.js';

const ARGOS_ICON =
  '<path d="M12 3 3 8v8l9 5 9-5V8z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/>';

function _el(id) { return document.getElementById(id); }

async function _mintToken() {
  const btn = _el('argos-mint-btn');
  const resultEl = _el('argos-mint-result');
  btn.disabled = true;
  btn.textContent = 'Minting…';
  try {
    const resp = await api('/pair', { method: 'POST' });
    if (!resp.ok) {
      const detail = (await resp.json().catch(() => ({}))).detail || `HTTP ${resp.status}`;
      resultEl.innerHTML = `<div class="argos-error">${esc(detail)}</div>`;
      return;
    }
    const data = await resp.json();
    resultEl.innerHTML = `
      <div class="argos-token-box">
        <p>Paste this into the extension's Options page. It is shown once —
        if you lose it, mint a new one (the old one stays valid until you
        revoke it in Settings &rarr; API Tokens).</p>
        <div class="argos-token-row">
          <code id="argos-token-value">${esc(data.token)}</code>
          <button class="doc-action-icon-btn" id="argos-copy-token-btn" title="Copy token">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          </button>
        </div>
        <p class="argos-hint">Server URL: <code>${esc(window.location.origin)}</code></p>
      </div>`;
    _el('argos-copy-token-btn')?.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(data.token);
        const copyBtn = _el('argos-copy-token-btn');
        const original = copyBtn.title;
        copyBtn.title = 'Copied';
        setTimeout(() => { copyBtn.title = original; }, 1500);
      } catch (err) {
        // Clipboard API can fail without a secure context; the token is
        // still selectable text, so this is a soft failure.
      }
    });
  } catch (err) {
    resultEl.innerHTML = `<div class="argos-error">${esc(String(err.message || err))}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Mint pairing token';
  }
}

function mount(container) {
  const screen = document.createElement('div');
  screen.className = 'argos-screen argos-screen-open';
  screen.id = 'argos-screen';
  screen.innerHTML = `
    <div class="argos-header" data-ws-popup-header>
      <button class="argos-back-btn" data-action="close" title="Back to chat">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
        <span>Chat</span>
      </button>
      <h2 class="argos-title">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ARGOS_ICON}</svg>
        Argos
      </h2>
      <span class="argos-header-sub">browser companion</span>
      <span style="flex:1"></span>
      <button class="doc-action-icon-btn" data-ws-split title="Split screen with another feature">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="12" y1="4" x2="12" y2="20"/></svg>
      </button>
    </div>
    <div class="argos-body">
      <p class="argos-intro">Argos is a Chrome/Brave extension that answers
      questions about the page you're on, using this Odysseus instance. Pair
      it once, then load it as an unpacked extension.</p>

      <div class="argos-card" id="argos-admin-card">
        <h3>1. Install the extension</h3>
        <p>Download the extension source, then in Chrome or Brave open
        <code>chrome://extensions</code> (or <code>brave://extensions</code>),
        turn on Developer mode, and choose "Load unpacked" on the extracted
        folder.</p>
        <a class="argos-download-btn" href="/api/argos/browser-extension.zip">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Download argos-browser-extension.zip
        </a>
      </div>

      <div class="argos-card">
        <h3>2. Pair it</h3>
        <p>Mint a token scoped for the extension, then paste it — along with
        this server's URL — into the extension's Options page.</p>
        <button class="argos-primary-btn" id="argos-mint-btn">Mint pairing token</button>
        <div id="argos-mint-result"></div>
      </div>

      <p class="argos-hint">Revoke access any time from
      Settings &rarr; Integrations &rarr; API Tokens. Page content is
      untrusted by design — Argos answers questions with agent tools turned
      off, so a page can't trick it into taking actions.</p>
    </div>
  `;
  container.appendChild(screen);

  const adminCard = screen.querySelector('#argos-admin-card');
  const mintCard = screen.querySelector('#argos-mint-btn')?.closest('.argos-card');
  if (!window._isAdmin) {
    adminCard?.remove();
    if (mintCard) {
      mintCard.innerHTML = '<h3>Pairing</h3><p>Ask an admin to mint you a pairing token and share the install package.</p>';
    }
    return;
  }
  screen.querySelector('#argos-mint-btn')?.addEventListener('click', _mintToken);
}

function activate() {}
function deactivate() {}

const argosWorkspace = {
  id: 'argos',
  route: '/argos',
  title: 'Argos',
  surface: 'both',
  defaultDisplay: 'page',
  collapseSidebar: true,
  mount,
  activate,
  deactivate,
};

export default argosWorkspace;
