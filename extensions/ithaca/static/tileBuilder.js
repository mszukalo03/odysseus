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
let _editingId = null; // id of the tile being edited, or null when adding a new one

async function _fetchWebhookTargets() {
  try {
    const r = await fetch('/api/webhook-targets', { credentials: 'same-origin' });
    const d = await r.json();
    return d.targets || [];
  } catch (err) {
    return [];
  }
}

// One sentence of dialect-specific SQL nudge per connection kind — no
// client-side query validation, just a hint next to the SQL textarea.
const _SQL_HINTS = {
  postgres: 'Postgres: schema-qualify tables if needed, ILIKE for case-insensitive matching, NOW() for the current time.',
  mysql: 'MySQL: use backtick-quoted identifiers if needed, NOW() for the current time; no ILIKE — use LOWER(x) LIKE instead.',
  sqlite: 'SQLite: no schema prefix on table names, LIKE is case-insensitive for ASCII, datetime(\'now\') for the current time.',
};

// Shared by the tile-builder connection picker and the import modal's
// bind-to-connection picker. An "http" tile's connection_ref names a
// WebhookTarget (reused from action buttons, see core/webhook_action.py)
// rather than an ExternalDbConnection, so the picker offers both, each
// tagged with its own data-kind for _updateSourceKindUI to branch on.
async function _loadDataSourceOptions(selectEl, selectedId) {
  const [dbConns, targets] = await Promise.all([
    fetch('/api/db-connections', { credentials: 'same-origin' }).then((r) => r.json()).catch(() => ({ connections: [] })),
    _fetchWebhookTargets(),
  ]);
  const options = [
    ...(dbConns.connections || []).map((c) => ({ id: c.id, label: `${c.label} · ${c.kind || 'postgres'}`, kind: c.kind || 'postgres' })),
    ...targets.map((t) => ({ id: t.id, label: `${t.label} · http`, kind: 'http' })),
  ];
  selectEl.innerHTML = options.length
    ? options.map((o) => `<option value="${_esc(o.id)}" data-kind="${_esc(o.kind)}">${_esc(o.label)}</option>`).join('')
    : '<option value="">No connections or endpoints configured — add one in Settings first</option>';
  if (selectedId) selectEl.value = selectedId;
  return options;
}

function _parseQueryParams(text) {
  const out = {};
  String(text || '').split('\n').forEach((line) => {
    const idx = line.indexOf('=');
    if (idx > 0) {
      const key = line.slice(0, idx).trim();
      const value = line.slice(idx + 1).trim();
      if (key) out[key] = value;
    }
  });
  return Object.keys(out).length ? out : null;
}

function _formatQueryParams(obj) {
  return Object.entries(obj || {}).map(([k, v]) => `${k}=${v}`).join('\n');
}

export function closeTileBuilder() {
  document.getElementById('ithaca-tilebuilder-overlay')?.remove();
}

export async function openTileBuilder(onSaved, existingTile = null) {
  closeTileBuilder();
  _draft = existingTile ? { ...existingTile } : null;
  _draftActions = existingTile ? [...(existingTile.actions || [])] : [];
  _editingId = existingTile ? existingTile.id : null;

  const overlay = document.createElement('div');
  overlay.className = 'ithaca-modal-overlay'; // reuse the existing modal-overlay look
  overlay.id = 'ithaca-tilebuilder-overlay';
  overlay.innerHTML = `
    <div class="ithaca-modal ithaca-tilebuilder-modal">
      <div class="ithaca-modal-header">
        <span>${_editingId ? `Edit Tile: ${_esc(existingTile.title || _editingId)}` : 'Add Tile'}</span>
        <span style="flex:1"></span>
        <button id="itb-close" class="doc-action-icon-btn" title="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <div class="ithaca-tilebuilder-body">
        <div class="settings-row"><label class="settings-label">Connection</label>
          <select id="itb-connection" class="settings-select" ${_editingId ? 'disabled' : ''}></select>
        </div>
        <div class="settings-row"><label class="settings-label">Title</label>
          <input id="itb-title" class="settings-input" placeholder="e.g. Flagged for Review"></div>
        <div id="itb-ai-section">
          <div class="settings-row" style="align-items:flex-start"><label class="settings-label">Context notes</label>
            <textarea id="itb-context" class="settings-input" rows="4" placeholder="Paste any notes you have about this table/schema (optional)"></textarea></div>
          <div class="settings-row" style="align-items:flex-start"><label class="settings-label">${_editingId ? 'What to change' : 'What to show'}</label>
            <textarea id="itb-instruction" class="settings-input" rows="2" placeholder="${_editingId ? 'e.g. Add a filter for status = open, make it a bar chart' : 'e.g. Show me apps flagged for review in a table'}"></textarea></div>
          <div class="settings-row">
            <button class="admin-btn-add" id="itb-generate">${_editingId ? 'Regenerate with AI' : 'Generate'}</button>
            <span id="itb-msg" style="font-size:11px;flex:1;margin-left:8px;"></span>
          </div>
        </div>
        <div class="settings-row" id="itb-http-continue-row" style="display:none;">
          <button class="admin-btn-add" id="itb-http-continue">Continue</button>
          <span id="itb-http-msg" style="font-size:11px;flex:1;margin-left:8px;"></span>
        </div>
        <div id="itb-draft" style="${_editingId ? '' : 'display:none;'}">
          <hr style="border-color:var(--border);margin:10px 0">
          <div class="settings-row"><label class="settings-label">Title</label><input id="itb-d-title" class="settings-input"></div>
          <div id="itb-sql-row" class="settings-row" style="align-items:flex-start"><label class="settings-label">SQL query</label><textarea id="itb-d-query" class="settings-input" rows="4" style="font-family:monospace;font-size:11px;"></textarea></div>
          <div id="itb-d-sql-hint" style="font-size:11px;opacity:0.6;margin:-6px 0 8px;"></div>
          <div id="itb-http-row" style="display:none;">
            <div class="settings-row"><label class="settings-label">Path</label>
              <input id="itb-d-path" class="settings-input" placeholder="e.g. /api/dashboards/reactions (appended to the endpoint's URL)"></div>
            <div class="settings-row"><label class="settings-label">JSON path</label>
              <input id="itb-d-json-path" class="settings-input" placeholder="optional, e.g. data.items — omit if the response is itself an array"></div>
            <div class="settings-row" style="align-items:flex-start"><label class="settings-label">Query params</label>
              <textarea id="itb-d-query-params" class="settings-input" rows="2" placeholder="optional, one per line: key=value"></textarea></div>
          </div>
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
  await _loadDataSourceOptions(connSel, existingTile?.data_source?.connection_ref);

  function _updateSqlHint() {
    const kind = connSel.selectedOptions[0]?.dataset.kind || 'postgres';
    const hintEl = document.getElementById('itb-d-sql-hint');
    if (hintEl) hintEl.textContent = _SQL_HINTS[kind] || '';
  }

  // AI generation (extensions/ithaca/ai_tile_builder.py) only speaks SQL —
  // an http source has no schema to introspect or query to write, so it
  // skips straight to the editable draft via "Continue" instead.
  function _updateSourceKindUI() {
    const kind = connSel.selectedOptions[0]?.dataset.kind || 'postgres';
    const isHttp = kind === 'http';
    document.getElementById('itb-ai-section').style.display = isHttp ? 'none' : '';
    document.getElementById('itb-http-continue-row').style.display = isHttp && !_editingId ? '' : 'none';
    document.getElementById('itb-sql-row').style.display = isHttp ? 'none' : '';
    document.getElementById('itb-http-row').style.display = isHttp ? '' : 'none';
    _updateSqlHint();
  }
  connSel.addEventListener('change', _updateSourceKindUI);
  _updateSourceKindUI();

  const endpointSel = document.getElementById('itb-action-endpoint');
  const webhookTargets = await _fetchWebhookTargets();
  endpointSel.innerHTML = webhookTargets.length
    ? webhookTargets.map((t) => `<option value="${_esc(t.id)}">${_esc(t.label)}</option>`).join('')
    : '<option value="">No webhook endpoints configured — add one in Settings first</option>';

  if (existingTile) {
    document.getElementById('itb-title').value = existingTile.title || '';
    document.getElementById('itb-d-title').value = existingTile.title || '';
    document.getElementById('itb-d-query').value = existingTile.data_source?.query || '';
    document.getElementById('itb-d-path').value = existingTile.data_source?.path || '';
    document.getElementById('itb-d-json-path').value = existingTile.data_source?.json_path || '';
    document.getElementById('itb-d-query-params').value = _formatQueryParams(existingTile.data_source?.query_params);
    document.getElementById('itb-d-viztype').value = existingTile.viz?.type || 'table';
    _updateSourceKindUI();
  }

  document.getElementById('itb-http-continue').addEventListener('click', () => {
    const msg = document.getElementById('itb-http-msg');
    const title = document.getElementById('itb-title').value.trim();
    if (!title) { msg.textContent = 'Give the tile a title first'; msg.style.color = 'var(--red)'; return; }
    _draft = {
      id: _editingId || _slugify(title),
      title,
      data_source: { type: 'http', connection_ref: connSel.value },
      viz: { type: 'table' },
    };
    msg.textContent = '';
    document.getElementById('itb-draft').style.display = '';
    document.getElementById('itb-d-title').value = title;
    document.getElementById('itb-d-viztype').value = 'table';
    _updateSourceKindUI();
  });

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
  _renderActionsList();

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
    if (!instruction) {
      msg.textContent = _editingId ? 'Describe what to change' : 'Describe what you want to see';
      msg.style.color = 'var(--red)';
      return;
    }
    msg.textContent = 'Generating…';
    msg.style.color = '';
    // When editing, revise the tile's *current* form state (including any
    // manual edits made since opening) rather than the version it was
    // originally saved with, so Regenerate builds on what's on screen.
    const currentConfig = _editingId ? {
      title: document.getElementById('itb-d-title').value.trim() || title,
      query: document.getElementById('itb-d-query').value,
      viz_type: document.getElementById('itb-d-viztype').value,
    } : null;
    try {
      const resp = await _api('/tiles/ai-propose', {
        method: 'POST',
        body: JSON.stringify({
          id: _editingId || _slugify(title || instruction),
          connection_ref: connectionRef,
          instruction,
          context_doc: contextDoc,
          current_config: currentConfig,
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
    const kind = connSel.selectedOptions[0]?.dataset.kind || _draft.data_source?.type || 'postgres';
    const dataSource = { ..._draft.data_source, type: kind, connection_ref: connSel.value };
    if (kind === 'http') {
      dataSource.query = null;
      dataSource.path = document.getElementById('itb-d-path').value.trim() || null;
      dataSource.json_path = document.getElementById('itb-d-json-path').value.trim() || null;
      dataSource.query_params = _parseQueryParams(document.getElementById('itb-d-query-params').value);
    } else {
      dataSource.query = document.getElementById('itb-d-query').value;
    }
    return {
      ..._draft,
      title: document.getElementById('itb-d-title').value.trim() || _draft.title,
      data_source: dataSource,
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
  const importedConns = await _loadDataSourceOptions(connSel);

  const webhookTargets = await _fetchWebhookTargets();

  function _updateImportHint() {
    const hintEl = document.getElementById('iti-hint');
    let pkg;
    try {
      pkg = JSON.parse(document.getElementById('iti-package').value);
    } catch (_) {
      hintEl.textContent = '';
      return;
    }
    const hint = pkg.connection_hint;
    if (!hint) { hintEl.textContent = ''; return; }
    let text = `This tile expects a ${_esc(hint.kind || 'postgres')} connection like "${_esc(hint.label || '')}"${hint.database ? ` (db: ${_esc(hint.database)})` : ''}.`;
    const bound = importedConns.find((c) => c.id === connSel.value);
    if (bound && hint.kind && bound.kind && bound.kind !== hint.kind) {
      text += ` Warning: you picked a ${_esc(bound.kind)} connection — the query may need adjustment for that dialect.`;
    }
    hintEl.textContent = text;
  }
  connSel.addEventListener('change', _updateImportHint);

  document.getElementById('iti-package').addEventListener('input', (e) => {
    _updateImportHint();
    const bindingsEl = document.getElementById('iti-action-bindings');
    try {
      const pkg = JSON.parse(e.target.value);
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
