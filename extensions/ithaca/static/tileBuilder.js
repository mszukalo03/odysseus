// extensions/ithaca/static/tileBuilder.js
//
// Admin-only modal: connection picker → paste context doc → NL instruction →
// Generate (POST /api/ithaca/tiles/ai-propose) → editable draft → Preview
// (POST /api/ithaca/tiles/preview) → Save (POST /api/ithaca/tiles).
// Uses the shared `.ithaca-modal*` overlay styling (mousedown-to-close, no
// framework) since this app has no bundler/component system.

import { esc as _esc, api as _api } from './ithacaCommon.js';
import { renderTile } from './tileRenderer.js';

function _slugify(title) {
  const base = String(title || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return `${base || 'tile'}_${Date.now().toString(36)}`;
}

let _draft = null; // current proposed/edited TileConfig, or null before Generate
let _draftActions = []; // TileAction list being built for the current draft

async function _fetchWebhookTargets() {
  try {
    const r = await fetch('/api/webhook-targets', { credentials: 'same-origin' });
    const d = await r.json();
    return d.targets || [];
  } catch (err) {
    return [];
  }
}

export function closeTileBuilder() {
  document.getElementById('ithaca-tilebuilder-overlay')?.remove();
}

export async function openTileBuilder(onSaved) {
  closeTileBuilder();
  _draft = null;
  _draftActions = [];

  const overlay = document.createElement('div');
  overlay.className = 'ithaca-modal-overlay'; // reuse the existing modal-overlay look
  overlay.id = 'ithaca-tilebuilder-overlay';
  overlay.innerHTML = `
    <div class="ithaca-modal ithaca-tilebuilder-modal">
      <div class="ithaca-modal-header">
        <span>Add Tile</span>
        <span style="flex:1"></span>
        <button id="itb-close" class="doc-action-icon-btn" title="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <div class="ithaca-tilebuilder-body">
        <div class="settings-row"><label class="settings-label">Connection</label>
          <select id="itb-connection" class="settings-select"></select>
        </div>
        <div class="settings-row"><label class="settings-label">Title</label>
          <input id="itb-title" class="settings-input" placeholder="e.g. Flagged for Review"></div>
        <div class="settings-row" style="align-items:flex-start"><label class="settings-label">Context notes</label>
          <textarea id="itb-context" class="settings-input" rows="4" placeholder="Paste any notes you have about this table/schema (optional)"></textarea></div>
        <div class="settings-row" style="align-items:flex-start"><label class="settings-label">What to show</label>
          <textarea id="itb-instruction" class="settings-input" rows="2" placeholder="e.g. Show me apps flagged for review in a table"></textarea></div>
        <div class="settings-row">
          <button class="admin-btn-add" id="itb-generate">Generate</button>
          <span id="itb-msg" style="font-size:11px;flex:1;margin-left:8px;"></span>
        </div>
        <div id="itb-draft" style="display:none;">
          <hr style="border-color:var(--border);margin:10px 0">
          <div class="settings-row"><label class="settings-label">Title</label><input id="itb-d-title" class="settings-input"></div>
          <div class="settings-row" style="align-items:flex-start"><label class="settings-label">SQL query</label><textarea id="itb-d-query" class="settings-input" rows="4" style="font-family:monospace;font-size:11px;"></textarea></div>
          <div class="settings-row"><label class="settings-label">Viz type</label>
            <select id="itb-d-viztype" class="settings-select" style="width:140px;">
              <option value="table">table</option><option value="stat">stat</option>
              <option value="bar">bar</option><option value="line">line</option>
              <option value="pie">pie</option><option value="list">list</option>
            </select>
          </div>
          <div class="settings-row">
            <button class="admin-btn-add" id="itb-preview">Preview</button>
            <button class="admin-btn-add" id="itb-save" style="background:var(--red);border-color:var(--red);color:#fff;">Save Tile</button>
            <span id="itb-save-msg" style="font-size:11px;flex:1;margin-left:8px;"></span>
          </div>
          <div id="itb-preview-body" class="ithaca-tile-body" style="border:1px solid var(--border);border-radius:6px;min-height:60px;"></div>

          <hr style="border-color:var(--border);margin:10px 0">
          <div style="font-size:12px;font-weight:600;margin-bottom:6px;">Action buttons (optional)</div>
          <div id="itb-actions-list"></div>
          <div class="settings-row"><label class="settings-label">Button label</label>
            <input id="itb-action-label" class="settings-input" placeholder="e.g. Reprocess Roadmap"></div>
          <div class="settings-row"><label class="settings-label">Webhook endpoint</label>
            <select id="itb-action-endpoint" class="settings-select"></select></div>
          <div class="settings-row">
            <button class="admin-btn-add" id="itb-action-add">+ Add Action</button>
          </div>
        </div>
      </div>
    </div>`;
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) closeTileBuilder(); });
  document.body.appendChild(overlay);
  document.getElementById('itb-close').addEventListener('click', closeTileBuilder);

  const connSel = document.getElementById('itb-connection');
  try {
    const r = await fetch('/api/db-connections', { credentials: 'same-origin' });
    const d = await r.json();
    const conns = d.connections || [];
    connSel.innerHTML = conns.length
      ? conns.map((c) => `<option value="${_esc(c.id)}">${_esc(c.label)}</option>`).join('')
      : '<option value="">No connections configured — add one in Settings first</option>';
  } catch (err) {
    connSel.innerHTML = '<option value="">Failed to load connections</option>';
  }

  const endpointSel = document.getElementById('itb-action-endpoint');
  const webhookTargets = await _fetchWebhookTargets();
  endpointSel.innerHTML = webhookTargets.length
    ? webhookTargets.map((t) => `<option value="${_esc(t.id)}">${_esc(t.label)}</option>`).join('')
    : '<option value="">No webhook endpoints configured — add one in Settings first</option>';

  function _renderActionsList() {
    const listEl = document.getElementById('itb-actions-list');
    if (!_draftActions.length) {
      listEl.innerHTML = '';
      return;
    }
    listEl.innerHTML = _draftActions.map((a, i) => `
      <div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px;">
        <span style="flex:1;">${_esc(a.label)}</span>
        <button type="button" class="doc-action-icon-btn itb-action-remove" data-idx="${i}" title="Remove">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>`).join('');
    listEl.querySelectorAll('.itb-action-remove').forEach((btn) => {
      btn.addEventListener('click', () => {
        _draftActions.splice(parseInt(btn.dataset.idx, 10), 1);
        _renderActionsList();
      });
    });
  }

  document.getElementById('itb-action-add').addEventListener('click', () => {
    const label = document.getElementById('itb-action-label').value.trim();
    const endpointRef = endpointSel.value;
    if (!label) return;
    if (!endpointRef) return;
    _draftActions.push({
      id: _slugify(label),
      label,
      endpoint_ref: endpointRef,
      confirm: true,
    });
    document.getElementById('itb-action-label').value = '';
    _renderActionsList();
  });

  document.getElementById('itb-generate').addEventListener('click', async () => {
    const msg = document.getElementById('itb-msg');
    const connectionRef = connSel.value;
    const title = document.getElementById('itb-title').value.trim();
    const instruction = document.getElementById('itb-instruction').value.trim();
    const contextDoc = document.getElementById('itb-context').value;
    if (!connectionRef) { msg.textContent = 'Pick a connection first'; msg.style.color = 'var(--red)'; return; }
    if (!instruction) { msg.textContent = 'Describe what you want to see'; msg.style.color = 'var(--red)'; return; }
    msg.textContent = 'Generating…';
    msg.style.color = '';
    try {
      const resp = await _api('/tiles/ai-propose', {
        method: 'POST',
        body: JSON.stringify({
          id: _slugify(title || instruction),
          connection_ref: connectionRef,
          instruction,
          context_doc: contextDoc,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        msg.textContent = data.detail || `HTTP ${resp.status}`;
        msg.style.color = 'var(--red)';
        return;
      }
      _draft = data;
      msg.textContent = 'Generated — review and save below.';
      msg.style.color = 'var(--green, #50fa7b)';
      document.getElementById('itb-draft').style.display = '';
      document.getElementById('itb-d-title').value = _draft.title || '';
      document.getElementById('itb-d-query').value = _draft.data_source?.query || '';
      document.getElementById('itb-d-viztype').value = _draft.viz?.type || 'table';
    } catch (err) {
      msg.textContent = `Request failed: ${err}`;
      msg.style.color = 'var(--red)';
    }
  });

  function _draftFromForm() {
    if (!_draft) return null;
    return {
      ..._draft,
      title: document.getElementById('itb-d-title').value.trim() || _draft.title,
      data_source: { ..._draft.data_source, query: document.getElementById('itb-d-query').value },
      viz: { ..._draft.viz, type: document.getElementById('itb-d-viztype').value },
      actions: _draftActions,
    };
  }

  document.getElementById('itb-preview').addEventListener('click', async () => {
    const cfg = _draftFromForm();
    const body = document.getElementById('itb-preview-body');
    if (!cfg) return;
    body.innerHTML = '<div class="ithaca-tile-hint">Running query…</div>';
    try {
      const resp = await _api('/tiles/preview', { method: 'POST', body: JSON.stringify(cfg) });
      const data = await resp.json();
      if (!resp.ok) {
        body.innerHTML = `<div class="ithaca-tile-hint">${_esc(data.detail || `HTTP ${resp.status}`)}</div>`;
        return;
      }
      renderTile(body, cfg, data);
    } catch (err) {
      body.innerHTML = `<div class="ithaca-tile-hint">Preview failed: ${_esc(err)}</div>`;
    }
  });

  document.getElementById('itb-save').addEventListener('click', async () => {
    const cfg = _draftFromForm();
    const saveMsg = document.getElementById('itb-save-msg');
    if (!cfg) return;
    saveMsg.textContent = 'Saving…';
    saveMsg.style.color = '';
    try {
      const resp = await _api('/tiles', { method: 'POST', body: JSON.stringify(cfg) });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        saveMsg.textContent = data.detail || data.error || `HTTP ${resp.status}`;
        saveMsg.style.color = 'var(--red)';
        return;
      }
      closeTileBuilder();
      if (typeof onSaved === 'function') onSaved();
    } catch (err) {
      saveMsg.textContent = `Save failed: ${err}`;
      saveMsg.style.color = 'var(--red)';
    }
  });
}

// ─── Export ──────────────────────────────────────────────────────────────
//
// Downloads the tile's package.json (extensions/ithaca/tile_packaging.py) —
// the config plus a non-secret connection hint, never credentials. Portable
// to any other Odysseus instance via openImportTileModal below.

export async function downloadTilePackage(tileId, title) {
  const resp = await _api(`/tiles/${encodeURIComponent(tileId)}/package.json`);
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    window.styledAlert ? window.styledAlert(data.detail || `HTTP ${resp.status}`) : alert(data.detail || `HTTP ${resp.status}`);
    return;
  }
  const pkg = await resp.json();
  const blob = new Blob([JSON.stringify(pkg, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${tileId || _slugify(title)}.tile.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

// ─── Import ──────────────────────────────────────────────────────────────
//
// A tile package's connection_ref is meaningless on this instance (it was a
// local id on whatever instance exported it) — the admin MUST explicitly
// pick one of THIS instance's own connections to bind it to. Never
// auto-matched by name/id, since a same-named-but-different connection
// would otherwise silently run the tile's query against the wrong database.

export function closeImportTileModal() {
  document.getElementById('ithaca-import-overlay')?.remove();
}

export async function openImportTileModal(onImported) {
  closeImportTileModal();

  const overlay = document.createElement('div');
  overlay.className = 'ithaca-modal-overlay';
  overlay.id = 'ithaca-import-overlay';
  overlay.innerHTML = `
    <div class="ithaca-modal ithaca-tilebuilder-modal">
      <div class="ithaca-modal-header">
        <span>Import Tile</span>
        <span style="flex:1"></span>
        <button id="iti-close" class="doc-action-icon-btn" title="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <div class="ithaca-tilebuilder-body">
        <div class="settings-row" style="align-items:flex-start"><label class="settings-label">Package JSON</label>
          <textarea id="iti-package" class="settings-input" rows="6" style="font-family:monospace;font-size:11px;" placeholder="Paste the exported .tile.json contents here"></textarea></div>
        <div id="iti-hint" style="font-size:11px;opacity:0.7;margin:-4px 0 4px;"></div>
        <div class="settings-row"><label class="settings-label">Bind to connection</label>
          <select id="iti-connection" class="settings-select"></select></div>
        <div id="iti-action-bindings"></div>
        <div class="settings-row">
          <button class="admin-btn-add" id="iti-import" style="background:var(--red);border-color:var(--red);color:#fff;">Import</button>
          <span id="iti-msg" style="font-size:11px;flex:1;margin-left:8px;"></span>
        </div>
      </div>
    </div>`;
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) closeImportTileModal(); });
  document.body.appendChild(overlay);
  document.getElementById('iti-close').addEventListener('click', closeImportTileModal);

  const connSel = document.getElementById('iti-connection');
  try {
    const r = await fetch('/api/db-connections', { credentials: 'same-origin' });
    const d = await r.json();
    const conns = d.connections || [];
    connSel.innerHTML = conns.length
      ? conns.map((c) => `<option value="${_esc(c.id)}">${_esc(c.label)}</option>`).join('')
      : '<option value="">No connections configured — add one in Settings first</option>';
  } catch (err) {
    connSel.innerHTML = '<option value="">Failed to load connections</option>';
  }

  const webhookTargets = await _fetchWebhookTargets();

  document.getElementById('iti-package').addEventListener('input', (e) => {
    const hintEl = document.getElementById('iti-hint');
    const bindingsEl = document.getElementById('iti-action-bindings');
    try {
      const pkg = JSON.parse(e.target.value);
      const hint = pkg.connection_hint;
      hintEl.textContent = hint
        ? `This tile expects a ${_esc(hint.kind || 'postgres')} connection like "${_esc(hint.label || '')}"${hint.database ? ` (db: ${_esc(hint.database)})` : ''}.`
        : '';
      const actionHints = pkg.action_hints || [];
      bindingsEl.innerHTML = actionHints.map((h) => `
        <div class="settings-row"><label class="settings-label">${_esc(h.label)} action</label>
          <select class="settings-select iti-action-binding" data-action-id="${_esc(h.action_id)}">
            ${webhookTargets.length
              ? webhookTargets.map((t) => `<option value="${_esc(t.id)}">${_esc(t.label)}</option>`).join('')
              : '<option value="">No webhook endpoints configured — add one in Settings first</option>'}
          </select>
        </div>`).join('');
    } catch (_) {
      hintEl.textContent = '';
      bindingsEl.innerHTML = '';
    }
  });

  document.getElementById('iti-import').addEventListener('click', async () => {
    const msg = document.getElementById('iti-msg');
    let pkg;
    try {
      pkg = JSON.parse(document.getElementById('iti-package').value);
    } catch (err) {
      msg.textContent = 'Package is not valid JSON';
      msg.style.color = 'var(--red)';
      return;
    }
    const connectionBinding = connSel.value;
    if (!connectionBinding) {
      msg.textContent = 'Pick a connection to bind this tile to';
      msg.style.color = 'var(--red)';
      return;
    }
    const actionBindings = {};
    document.querySelectorAll('.iti-action-binding').forEach((sel) => {
      actionBindings[sel.dataset.actionId] = sel.value;
    });
    msg.textContent = 'Importing…';
    msg.style.color = '';
    try {
      const resp = await _api('/tiles/import', {
        method: 'POST',
        body: JSON.stringify({ package: pkg, connection_binding: connectionBinding, action_bindings: actionBindings }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        msg.textContent = data.detail || data.error || `HTTP ${resp.status}`;
        msg.style.color = 'var(--red)';
        return;
      }
      closeImportTileModal();
      if (typeof onImported === 'function') onImported();
    } catch (err) {
      msg.textContent = `Import failed: ${err}`;
      msg.style.color = 'var(--red)';
    }
  });
}
