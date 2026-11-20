// Fork-only Settings cards: Feature Display, Ithaca Weather, DB Connections,
// and Webhook Targets. Kept in its own module so static/js/settings.js stays
// aligned with upstream's modularized Settings coordinator.

import { byId as el } from './dom.js';
import { postSettings as _postSettings } from './api.js';
import { esc } from './html.js';
import Workspace from '../workspaceManager.js';

export function initFeatureDisplaySettings() {
  var list = el('feature-display-list');
  if (!list) return; // settings modal markup not present (shouldn't happen)
  var msg = el('set-displayModeMsg');
  var features = Workspace.list();

  if (!features.length) {
    list.innerHTML = '<div class="admin-empty">No page/popup-capable features are registered.</div>';
    return;
  }

  list.innerHTML = '';
  features.forEach(function(feature) {
    var row = document.createElement('div');
    row.className = 'settings-row';
    var label = document.createElement('label');
    label.className = 'settings-label';
    label.textContent = feature.title;
    var select = document.createElement('select');
    select.className = 'settings-select';
    select.style.width = '200px';
    select.innerHTML =
      '<option value="page">Full page</option>' +
      '<option value="popup">Popup</option>';
    select.value = Workspace.displayMode(feature.id);
    select.addEventListener('change', function() {
      var prev = select.value === 'page' ? 'popup' : 'page';
      Workspace.setDisplayMode(feature.id, select.value).then(function() {
        if (msg) {
          msg.textContent = 'Saved'; msg.style.color = 'var(--fg)';
          setTimeout(function() { msg.textContent = ''; }, 2000);
        }
      }).catch(function(e) {
        select.value = prev;
        if (msg) { msg.textContent = 'Failed to save (admin only)'; msg.style.color = 'var(--red)'; }
        console.error('Failed to set display mode for', feature.id, e);
      });
    });
    row.appendChild(label);
    row.appendChild(select);
    list.appendChild(row);
  });
}

/* ── Ithaca hub's Weather tile API key ──
   Mirrors initSearchSettings()'s pattern: load full settings once (admin
   only — the card itself is .admin-only and hidden for non-admins), populate
   the fields, auto-save the whole group on any field's `change` event. */
export async function initIthacaSettings() {
  var keyInput = el('set-ithacaOwmKey');
  if (!keyInput) return; // settings modal markup not present (shouldn't happen)
  var latInput = el('set-ithacaLat');
  var lonInput = el('set-ithacaLon');
  var unitsSel = el('set-ithacaUnits');
  var msg = el('set-ithacaMsg');

  try {
    var res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    var s = await res.json();
    keyInput.value = s.openweather_api_key || '';
    latInput.value = s.openweather_lat || '';
    lonInput.value = s.openweather_lon || '';
    unitsSel.value = s.openweather_units || '';
  } catch (e) { console.warn('Failed to load Ithaca settings', e); }

  async function saveIthaca() {
    try {
      await _postSettings({
        openweather_api_key: keyInput.value.trim(),
        openweather_lat: latInput.value.trim(),
        openweather_lon: lonInput.value.trim(),
        openweather_units: unitsSel.value,
      });
      if (msg) {
        msg.textContent = 'Saved'; msg.style.color = 'var(--fg)';
        setTimeout(function() { msg.textContent = ''; }, 2000);
      }
    } catch (e) {
      if (msg) { msg.textContent = 'Failed to save'; msg.style.color = 'var(--red)'; }
    }
  }

  [keyInput, latInput, lonInput, unitsSel].forEach(function(input) {
    input.addEventListener('change', saveIthaca);
  });
}

export async function initDbConnectionsSettings() {
  const listEl = el('set-dbconn-list');
  const formEl = el('set-dbconn-form');
  const addBtn = el('set-dbconn-add-btn');
  if (!listEl || !formEl || !addBtn) return;

  let kindInfo = null; // { postgres: {default_port, fields, driver_available, driver_hint}, ... }
  async function fetchKinds() {
    if (kindInfo) return kindInfo;
    const r = await fetch('/api/db-connections/kinds', { credentials: 'same-origin' });
    kindInfo = {};
    if (r.ok) {
      const d = await r.json();
      for (const k of (d.kinds || [])) kindInfo[k.kind] = k;
    }
    return kindInfo;
  }

  async function fetchConnections() {
    const r = await fetch('/api/db-connections', { credentials: 'same-origin' });
    if (!r.ok) return [];
    const d = await r.json();
    return d.connections || [];
  }

  function renderRow(c) {
    const subtitle = c.kind === 'sqlite'
      ? esc(c.file_path)
      : `${esc(c.username)}@${esc(c.host)}:${esc(c.port)}/${esc(c.database)}`;
    return `<div class="dbconn-row" data-conn-id="${esc(c.id)}" style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;">
      <span style="font-size:9px;text-transform:uppercase;letter-spacing:0.04em;opacity:0.55;flex-shrink:0;border:1px solid var(--border);border-radius:3px;padding:1px 4px;">${esc(c.kind || 'postgres')}</span>
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:600;">${esc(c.label)}</div>
        <div style="font-size:11px;opacity:0.6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${subtitle}</div>
      </div>
      <span class="dbconn-test-dot" title="Test connection" style="width:8px;height:8px;border-radius:50%;background:var(--border);cursor:pointer;flex-shrink:0;"></span>
      <button class="admin-btn-sm dbconn-edit-btn" style="font-size:10px">Edit</button>
      <button class="admin-btn-sm dbconn-del-btn" style="font-size:10px;opacity:0.6">Delete</button>
    </div>`;
  }

  async function renderList() {
    const conns = await fetchConnections();
    if (!conns.length) {
      listEl.innerHTML = '<div style="padding:12px;opacity:0.5;font-size:12px;text-align:center">No database connections configured</div>';
      return;
    }
    listEl.innerHTML = conns.map(renderRow).join('');
    listEl.querySelectorAll('.dbconn-row').forEach(row => {
      const id = row.dataset.connId;
      row.querySelector('.dbconn-test-dot')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        const dot = e.currentTarget;
        dot.style.background = 'var(--border)';
        const r = await fetch(`/api/db-connections/${id}/test`, { method: 'POST', credentials: 'same-origin' });
        const d = await r.json().catch(() => ({ ok: false }));
        dot.style.background = d.ok ? 'var(--green, #50fa7b)' : 'var(--red)';
        dot.title = d.ok ? 'Connected' : (d.error || 'Connection failed');
      });
      row.querySelector('.dbconn-edit-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        showForm(conns.find(c => c.id === id));
      });
      row.querySelector('.dbconn-del-btn')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        const label = conns.find(c => c.id === id)?.label;
        if (!await window.styledConfirm(`Delete connection "${label}"? Any tiles using it will stop loading.`, { confirmText: 'Delete', danger: true })) return;
        await fetch(`/api/db-connections/${id}`, { method: 'DELETE', credentials: 'same-origin' });
        renderList();
      });
    });
  }

  async function showForm(existing) {
    const c = existing || {};
    const isEdit = !!existing;
    const kinds = await fetchKinds();
    const kindList = Object.keys(kinds).length ? Object.keys(kinds) : ['postgres', 'sqlite', 'mysql'];
    const kind = c.kind || 'postgres';

    const kindOptions = kindList.map(k => {
      const info = kinds[k] || {};
      const disabled = info.driver_available === false ? 'disabled' : '';
      const suffix = info.driver_available === false ? ' (driver not installed)' : '';
      return `<option value="${esc(k)}" ${k === kind ? 'selected' : ''} ${disabled}>${esc(k)}${suffix}</option>`;
    }).join('');

    formEl.style.display = '';
    formEl.innerHTML = `
      <h3 style="font-size:12px;margin:0 0 8px">${isEdit ? 'Edit Connection' : 'New Connection'}</h3>
      <div class="settings-col">
        <div class="settings-row"><label class="settings-label">Label</label><input id="dcf-label" class="settings-input" placeholder="homelab_main_db" value="${esc(c.label || '')}"></div>
        <div class="settings-row"><label class="settings-label">Kind</label><select id="dcf-kind" class="settings-select" style="width:160px;" ${isEdit ? 'disabled' : ''}>${kindOptions}</select></div>
        <div class="dcf-field" data-field="host"><div class="settings-row"><label class="settings-label">Host</label><input id="dcf-host" class="settings-input" value="${esc(c.host || '')}"></div></div>
        <div class="dcf-field" data-field="port"><div class="settings-row"><label class="settings-label">Port</label><input id="dcf-port" class="settings-input" type="number" value="${esc(c.port || '')}" style="max-width:100px"></div></div>
        <div class="dcf-field" data-field="database"><div class="settings-row"><label class="settings-label">Database</label><input id="dcf-database" class="settings-input" value="${esc(c.database || '')}"></div></div>
        <div class="dcf-field" data-field="username"><div class="settings-row"><label class="settings-label">Username</label><input id="dcf-username" class="settings-input" value="${esc(c.username || '')}"></div></div>
        <div class="dcf-field" data-field="password"><div class="settings-row"><label class="settings-label">Password</label><input id="dcf-password" class="settings-input" type="password" placeholder="${isEdit && c.has_password ? '(unchanged)' : ''}"></div></div>
        <div class="dcf-field" data-field="sslmode"><div class="settings-row"><label class="settings-label">SSL mode</label><select id="dcf-sslmode" class="settings-select" style="width:160px;">
          <option value="prefer">prefer</option><option value="require">require</option><option value="disable">disable</option>
        </select></div></div>
        <div class="dcf-field" data-field="file_path"><div class="settings-row"><label class="settings-label">File path</label><input id="dcf-file-path" class="settings-input" placeholder="/srv/homelab/metrics.db" value="${esc(c.file_path || '')}"></div></div>
        <div id="dcf-driver-hint" style="font-size:11px;color:var(--red);display:none;"></div>
        <div class="settings-row" style="margin-top:10px;align-items:center;">
          <button class="admin-btn-add" id="dcf-save">${isEdit ? 'Save' : 'Create'}</button>
          <span id="dcf-msg" style="font-size:11px;flex:1;margin-left:8px;"></span>
          <button class="admin-btn-add" id="dcf-cancel" style="opacity:0.7;margin-left:auto;">Cancel</button>
        </div>
      </div>`;
    el('dcf-sslmode').value = c.sslmode || 'prefer';

    function applyKindVisibility(k) {
      const info = kinds[k] || {};
      const fields = info.fields || ['host', 'port', 'database', 'username', 'password', 'sslmode'];
      formEl.querySelectorAll('.dcf-field').forEach(div => {
        div.style.display = fields.includes(div.dataset.field) ? '' : 'none';
      });
      const portEl = el('dcf-port');
      if (portEl && info.default_port && (!portEl.dataset.touched || !portEl.value)) {
        portEl.value = info.default_port;
      }
      const hint = el('dcf-driver-hint');
      if (info.driver_available === false) {
        hint.textContent = info.driver_hint || `${k} driver not installed`;
        hint.style.display = '';
      } else {
        hint.style.display = 'none';
      }
    }
    applyKindVisibility(kind);
    el('dcf-kind').addEventListener('change', (e) => applyKindVisibility(e.target.value));
    el('dcf-port')?.addEventListener('input', () => { el('dcf-port').dataset.touched = '1'; });

    el('dcf-cancel').addEventListener('click', () => { formEl.style.display = 'none'; formEl.innerHTML = ''; });
    el('dcf-save').addEventListener('click', async () => {
      const msg = el('dcf-msg');
      const selectedKind = el('dcf-kind').value;
      const body = {
        label: el('dcf-label').value.trim(),
        kind: selectedKind,
        host: el('dcf-host')?.value.trim() || '',
        port: parseInt(el('dcf-port')?.value, 10) || null,
        database: el('dcf-database')?.value.trim() || '',
        username: el('dcf-username')?.value.trim() || '',
        password: el('dcf-password')?.value || '',
        sslmode: el('dcf-sslmode')?.value || 'prefer',
        file_path: el('dcf-file-path')?.value.trim() || '',
      };
      try {
        const url = isEdit ? `/api/db-connections/${c.id}` : '/api/db-connections';
        const r = await fetch(url, {
          method: isEdit ? 'PUT' : 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const d = await r.json().catch(() => ({ ok: false }));
        if (!d.ok) { msg.textContent = d.error || 'Save failed'; msg.style.color = 'var(--red)'; return; }
        formEl.style.display = 'none';
        formEl.innerHTML = '';
        renderList();
      } catch (e) {
        msg.textContent = 'Save failed: ' + e.message;
        msg.style.color = 'var(--red)';
      }
    });
  }

  addBtn.addEventListener('click', () => showForm(null));
  renderList();
}

/* ── Webhook endpoints (Ithaca tile action buttons) ──
   Mirrors initDbConnectionsSettings()'s list+form pattern. */
export async function initWebhookTargetsSettings() {
  const listEl = el('set-webhook-list');
  const formEl = el('set-webhook-form');
  const addBtn = el('set-webhook-add-btn');
  if (!listEl || !formEl || !addBtn) return;

  async function fetchTargets() {
    const r = await fetch('/api/webhook-targets', { credentials: 'same-origin' });
    if (!r.ok) return [];
    const d = await r.json();
    return d.targets || [];
  }

  function renderRow(t) {
    return `<div class="whtarget-row" data-target-id="${esc(t.id)}" style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;">
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:600;">${esc(t.label)}</div>
        <div style="font-size:11px;opacity:0.6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(t.method)} ${esc(t.url)}</div>
      </div>
      <span class="whtarget-test-dot" title="Check endpoint" style="width:8px;height:8px;border-radius:50%;background:var(--border);cursor:pointer;flex-shrink:0;"></span>
      <button class="admin-btn-sm whtarget-edit-btn" style="font-size:10px">Edit</button>
      <button class="admin-btn-sm whtarget-del-btn" style="font-size:10px;opacity:0.6">Delete</button>
    </div>`;
  }

  async function renderList() {
    const targets = await fetchTargets();
    if (!targets.length) {
      listEl.innerHTML = '<div style="padding:12px;opacity:0.5;font-size:12px;text-align:center">No webhook endpoints configured</div>';
      return;
    }
    listEl.innerHTML = targets.map(renderRow).join('');
    listEl.querySelectorAll('.whtarget-row').forEach(row => {
      const id = row.dataset.targetId;
      row.querySelector('.whtarget-test-dot')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        const dot = e.currentTarget;
        dot.style.background = 'var(--border)';
        const r = await fetch(`/api/webhook-targets/${id}/test`, { method: 'POST', credentials: 'same-origin' });
        const d = await r.json().catch(() => ({ ok: false }));
        dot.style.background = d.ok ? 'var(--green, #50fa7b)' : 'var(--red)';
        dot.title = d.ok ? 'Configured' : (d.error || 'Not found');
      });
      row.querySelector('.whtarget-edit-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        showForm(targets.find(t => t.id === id));
      });
      row.querySelector('.whtarget-del-btn')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        const label = targets.find(t => t.id === id)?.label;
        if (!await window.styledConfirm(`Delete webhook endpoint "${label}"? Any tile action buttons using it will stop working.`, { confirmText: 'Delete', danger: true })) return;
        await fetch(`/api/webhook-targets/${id}`, { method: 'DELETE', credentials: 'same-origin' });
        renderList();
      });
    });
  }

  function showForm(existing) {
    const t = existing || {};
    const isEdit = !!existing;
    formEl.style.display = '';
    formEl.innerHTML = `
      <h3 style="font-size:12px;margin:0 0 8px">${isEdit ? 'Edit Endpoint' : 'New Endpoint'}</h3>
      <div class="settings-col">
        <div class="settings-row"><label class="settings-label">Label</label><input id="whf-label" class="settings-input" placeholder="roadmap_reprocess" value="${esc(t.label || '')}"></div>
        <div class="settings-row"><label class="settings-label">URL</label><input id="whf-url" class="settings-input" placeholder="https://n8n.example.com/webhook/..." value="${esc(t.url || '')}"></div>
        <div class="settings-row"><label class="settings-label">Method</label><select id="whf-method" class="settings-select" style="width:120px;">
          <option value="POST">POST</option><option value="GET">GET</option><option value="PUT">PUT</option><option value="PATCH">PATCH</option>
        </select></div>
        <div class="settings-row"><label class="settings-label">Auth</label><select id="whf-auth-scheme" class="settings-select" style="width:160px;">
          <option value="none">None</option><option value="bearer">Bearer token</option><option value="basic">Basic (user:pass)</option>
        </select></div>
        <div class="settings-row"><label class="settings-label">Token</label><input id="whf-auth-token" class="settings-input" type="password" placeholder="${isEdit && t.has_auth_token ? '(unchanged)' : ''}"></div>
        <div class="settings-row"><label class="settings-label">Timeout (s)</label><input id="whf-timeout" class="settings-input" type="number" value="${esc(t.timeout_seconds || 15)}" style="max-width:100px"></div>
        <div class="settings-row" style="margin-top:10px;align-items:center;">
          <button class="admin-btn-add" id="whf-save">${isEdit ? 'Save' : 'Create'}</button>
          <span id="whf-msg" style="font-size:11px;flex:1;margin-left:8px;"></span>
          <button class="admin-btn-add" id="whf-cancel" style="opacity:0.7;margin-left:auto;">Cancel</button>
        </div>
      </div>`;
    el('whf-method').value = t.method || 'POST';
    el('whf-auth-scheme').value = t.auth_scheme || 'none';
    el('whf-cancel').addEventListener('click', () => { formEl.style.display = 'none'; formEl.innerHTML = ''; });
    el('whf-save').addEventListener('click', async () => {
      const msg = el('whf-msg');
      const body = {
        label: el('whf-label').value.trim(),
        url: el('whf-url').value.trim(),
        method: el('whf-method').value,
        auth_scheme: el('whf-auth-scheme').value,
        auth_token: el('whf-auth-token').value,
        timeout_seconds: parseInt(el('whf-timeout').value, 10) || 15,
      };
      try {
        const url = isEdit ? `/api/webhook-targets/${t.id}` : '/api/webhook-targets';
        const r = await fetch(url, {
          method: isEdit ? 'PUT' : 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const d = await r.json().catch(() => ({ ok: false }));
        if (!d.ok) { msg.textContent = d.error || 'Save failed'; msg.style.color = 'var(--red)'; return; }
        formEl.style.display = 'none';
        formEl.innerHTML = '';
        renderList();
      } catch (e) {
        msg.textContent = 'Save failed: ' + e.message;
        msg.style.color = 'var(--red)';
      }
    });
  }

  addBtn.addEventListener('click', () => showForm(null));
  renderList();
}

