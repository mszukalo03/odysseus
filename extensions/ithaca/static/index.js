// extensions/ithaca/static/index.js — Ithaca hub workspace descriptor.
// Frontend for the n8n daily-digest workflow: a static 3×3 tile grid where
// tile A shows live OpenWeatherMap data and tile B renders the digest's
// Software Updates table. Remaining tiles are reserved slots for future
// digest headings / user-defined tiles.
//
// Registered with workspaceManager.js (see static/js/workspaceManager.js) —
// history, sidebar-collapse, title, active-nav-state and Escape arbitration
// are all owned by the host now; this module only builds DOM into the
// container it's given and starts/stops its own refresh timer.

import Workspace from '/static/js/workspaceManager.js';

const API_BASE = window.location.origin;

let _weatherTimer = null;
let _digestLoaded = false;

const WEATHER_REFRESH_MS = 10 * 60 * 1000; // matches the backend cache TTL

// Static tile map. Tiles are letter-addressed (A–I) so future digest
// headings / user tiles can bind to slots without renumbering.
const TILES = [
  { slot: 'A', key: 'weather', title: 'Weather' },
  { slot: 'B', key: 'updates', title: 'Software Updates' },
  { slot: 'C' }, { slot: 'D' }, { slot: 'E' },
  { slot: 'F' }, { slot: 'G' }, { slot: 'H' }, { slot: 'I' },
];

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function _el(id) { return document.getElementById(id); }

function _api(path, opts = {}) {
  return fetch(`${API_BASE}/api/ithaca${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
}

// ─── Weather icons — inline stroke SVGs in the app's icon language ─────────

const _W_ICONS = {
  sun: '<circle cx="12" cy="12" r="4.5"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12h2.5M19 12h2.5M5 5l1.8 1.8M17.2 17.2 19 19M19 5l-1.8 1.8M6.8 17.2 5 19"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
  partly: '<circle cx="8.5" cy="8.5" r="3.5"/><path d="M8.5 2.2v1.6M2.2 8.5h1.6M4 4l1.2 1.2"/><path d="M9 17.5h8.6a3.2 3.2 0 0 0 0-6.4 4.6 4.6 0 0 0-8.9 1.2A2.7 2.7 0 0 0 9 17.5z"/>',
  cloud: '<path d="M6.5 18h10.8a3.7 3.7 0 0 0 0-7.4 5.3 5.3 0 0 0-10.3 1.4A3.1 3.1 0 0 0 6.5 18z"/>',
  rain: '<path d="M6.5 15h10.8a3.7 3.7 0 0 0 0-7.4A5.3 5.3 0 0 0 7 9a3.1 3.1 0 0 0-.5 6z"/><path d="M8.5 18.5v2M12 18.5v2.8M15.5 18.5v2"/>',
  thunder: '<path d="M6.5 14h10.8a3.7 3.7 0 0 0 0-7.4A5.3 5.3 0 0 0 7 8a3.1 3.1 0 0 0-.5 6z"/><path d="M12.8 15.5 10.5 19h3l-2 3.5"/>',
  snow: '<path d="M6.5 15h10.8a3.7 3.7 0 0 0 0-7.4A5.3 5.3 0 0 0 7 9a3.1 3.1 0 0 0-.5 6z"/><path d="M9 18.7h.01M12.5 20h.01M15.5 18.2h.01M10.8 21.3h.01"/>',
  mist: '<path d="M4 9h16M6 13h13M4.5 17h14M8 21h9"/>',
};

// Condition color per icon group — the palette the tile's gradients and
// icon tints both draw from, so "sunny" reads warm everywhere it appears.
const _W_COLORS = {
  sun: '#f5a623', moon: '#8b93e8', partly: '#5b9bd5', cloud: '#8a94a6',
  rain: '#4a90d9', thunder: '#a259ff', snow: '#5fd0e8', mist: '#9aa5b1',
};

function _condInfo(owmIcon) {
  const code = String(owmIcon || '');
  const night = code.endsWith('n');
  const byPrefix = {
    '01': night ? 'moon' : 'sun', '02': 'partly', '03': 'cloud', '04': 'cloud',
    '09': 'rain', '10': 'rain', '11': 'thunder', '13': 'snow', '50': 'mist',
  };
  const name = byPrefix[code.slice(0, 2)] || 'cloud';
  return { name, night, color: _W_COLORS[name] };
}

function _weatherIconSvg(owmIcon, size = 22) {
  const { name, color } = _condInfo(owmIcon);
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${_W_ICONS[name]}</svg>`;
}

// ─── Tile A: live weather ──────────────────────────────────────────────────

async function _loadWeather(force = false) {
  const body = _el('ithaca-weather-body');
  if (!body) return;
  if (!body.dataset.loadedOnce) {
    body.innerHTML = '<div class="ithaca-tile-hint">Loading weather…</div>';
  }
  try {
    const resp = await _api(`/weather${force ? '?refresh=1' : ''}`);
    if (!resp.ok) {
      const detail = (await resp.json().catch(() => ({}))).detail || `HTTP ${resp.status}`;
      body.innerHTML = `<div class="ithaca-tile-hint">${_esc(detail)}</div>`;
      return;
    }
    _renderWeather(await resp.json());
    body.dataset.loadedOnce = '1';
  } catch (err) {
    console.error('Ithaca weather load failed:', err);
    body.innerHTML = '<div class="ithaca-tile-hint">Weather unavailable — is the server reachable?</div>';
  }
}

function _renderWeather(data) {
  const body = _el('ithaca-weather-body');
  if (!body) return;
  const unitT = data.units === 'imperial' ? '°F' : '°C';
  const unitW = data.units === 'imperial' ? 'mph' : 'm/s';
  const cur = data.current || {};
  const round = (v) => (typeof v === 'number' ? Math.round(v) : '—');
  const hourly = (data.hourly || []).map((h) => {
    const hh = new Date((h.dt || 0) * 1000).getHours();
    const label = `${String(hh).padStart(2, '0')}:00`;
    const pop = h.pop >= 20 ? `<span class="ithaca-hour-pop">${h.pop}%</span>` : '';
    return `<div class="ithaca-hour" title="${_esc(h.description)}${h.pop ? `, ${h.pop}% precip` : ''}">
      <span class="ithaca-hour-time">${label}</span>
      <span class="ithaca-hour-icon">${_weatherIconSvg(h.icon, 18)}</span>
      <span class="ithaca-hour-temp">${round(h.temp)}°</span>
      ${pop}
    </div>`;
  }).join('');
  const loc = _el('ithaca-weather-loc');
  if (loc) loc.textContent = data.location || '';
  const cond = _condInfo(cur.icon);
  const tile = body.closest('.ithaca-tile');
  if (tile) tile.style.setProperty('--wx-tint', cond.color);
  body.innerHTML = `
    <div class="ithaca-weather-now">
      <span class="ithaca-weather-now-icon" style="background: color-mix(in srgb, ${cond.color} 22%, transparent); color: ${cond.color}">${_weatherIconSvg(cur.icon, 34)}</span>
      <span class="ithaca-weather-now-temp" style="color: ${cond.color}">${round(cur.temp)}${unitT}</span>
      <div class="ithaca-weather-now-meta">
        <span class="ithaca-weather-desc">${_esc(cur.description)}</span>
        <span>Feels ${round(cur.feels_like)}° · ${cur.humidity ?? '—'}% RH · ${cur.wind_speed ?? '—'} ${unitW}</span>
      </div>
    </div>
    <div class="ithaca-hourly">${hourly || '<div class="ithaca-tile-hint">No forecast data</div>'}</div>`;
}

// ─── Tile B: software updates from the digest ──────────────────────────────

async function _loadDigest(force = false) {
  const body = _el('ithaca-updates-body');
  if (!body) return;
  if (!_digestLoaded) {
    body.innerHTML = '<div class="ithaca-tile-hint">Loading digest…</div>';
  }
  try {
    const resp = await _api(`/digest${force ? '?refresh=1' : ''}`);
    if (!resp.ok) {
      const detail = (await resp.json().catch(() => ({}))).detail || `HTTP ${resp.status}`;
      body.innerHTML = `<div class="ithaca-tile-hint">${_esc(detail)}</div>`;
      return;
    }
    _renderUpdates(await resp.json());
    _digestLoaded = true;
  } catch (err) {
    console.error('Ithaca digest load failed:', err);
    body.innerHTML = '<div class="ithaca-tile-hint">Digest unavailable — is the Obsidian API reachable?</div>';
  }
}

function _safeHttpUrl(url) {
  try {
    const u = new URL(url);
    if (u.protocol === 'http:' || u.protocol === 'https:') return u.href;
  } catch (_) { /* not a URL */ }
  return '';
}

function _renderUpdates(data) {
  const body = _el('ithaca-updates-body');
  if (!body) return;
  const dateEl = _el('ithaca-updates-date');
  if (dateEl) dateEl.textContent = data.date || '';
  const su = data.software_updates;
  if (!su || !su.rows || !su.rows.length) {
    const note = su && su.note ? _esc(su.note) : 'No Software Updates section in the latest digest.';
    body.innerHTML = `<div class="ithaca-tile-hint">${note}</div>`;
    return;
  }
  const rows = su.rows.map((row) => {
    const app = String(row.app || '—');
    const repo = _safeHttpUrl(row.repo_url);
    // Click title → git project. Falls back to plain text when no repo URL
    // is configured for this app.
    const title = repo
      ? `<a class="ithaca-app-link" href="${_esc(repo)}" target="_blank" rel="noopener noreferrer" title="Open ${_esc(app)} on GitHub">${_esc(app)}</a>`
      : `<span class="ithaca-app-link ithaca-app-link-plain">${_esc(app)}</span>`;
    const hosted = String(row.hosted_on || '').trim();
    // Click host → ssh into the deployed-on device and open the app's path
    // (admin-only backend action; host+path come from data/ithaca.json).
    const hostCell = !hosted ? '' : (row.ssh_host
      ? `<button class="ithaca-host-link" data-app="${_esc(app)}" title="ssh ${_esc(row.ssh_host)}${row.ssh_path ? ' → ' + _esc(row.ssh_path) : ''}">
           <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>${_esc(hosted)}</button>`
      : `<span class="ithaca-host-plain" title="Set ssh_host for '${_esc(app)}' in data/ithaca.json to enable ssh-open">${_esc(hosted)}</span>`);
    const badge = row.update_available
      ? '<span class="ithaca-badge ithaca-badge-update">Update</span>'
      : '<span class="ithaca-badge">Current</span>';
    const version = String(row.version || '').trim();
    const deployed = String(row.deployed || '').trim();
    // "Version No." already reads as a "current → new" transition when an
    // update is available (see the workflow's table shape), so showing it
    // next to "Currently Deployed" duplicated the same version twice
    // ("on v6.3.1 · v6.3.1 → v6.4.0"). Prefer the transition string; only
    // fall back to the deployed version when there's nothing newer to show.
    const versionBits = version && version !== '—' ? version : (deployed ? `Currently ${deployed}` : '');
    const desc = String(row.desc || '').trim();
    return `<div class="ithaca-update-row">
      <div class="ithaca-update-row-top">
        ${title}${badge}<span style="flex:1"></span>${hostCell}
      </div>
      ${versionBits ? `<div class="ithaca-update-version">${_esc(versionBits)}</div>` : ''}
      ${desc ? `<div class="ithaca-update-desc">${_esc(desc)}</div>` : ''}
    </div>`;
  }).join('');
  const note = su.note ? `<div class="ithaca-updates-note">${_esc(su.note)}</div>` : '';
  body.innerHTML = `<div class="ithaca-updates-list">${rows}</div>${note}`;
  body.querySelectorAll('.ithaca-host-link').forEach((btn) => {
    btn.addEventListener('click', () => _openSshModal(btn.dataset.app));
  });
}

// ─── SSH-open modal (deployed-on link action) ──────────────────────────────

function _openSshModal(appName) {
  _closeSshModal();
  const overlay = document.createElement('div');
  overlay.className = 'ithaca-ssh-overlay';
  overlay.id = 'ithaca-ssh-overlay';
  overlay.innerHTML = `
    <div class="ithaca-ssh-modal">
      <div class="ithaca-ssh-header">
        <span id="ithaca-ssh-title">${_esc(appName)} — connecting…</span>
        <span style="flex:1"></span>
        <button id="ithaca-ssh-close" class="doc-action-icon-btn" title="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <pre class="ithaca-ssh-output" id="ithaca-ssh-output">$ ssh …</pre>
    </div>`;
  // mousedown, not click: a click event from the same interaction that
  // opened the modal can land on the just-mounted backdrop and close it
  // immediately (click-through); mousedown only fires on a fresh press.
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) _closeSshModal(); });
  document.body.appendChild(overlay);
  _el('ithaca-ssh-close')?.addEventListener('click', _closeSshModal);

  _api('/ssh/open', { method: 'POST', body: JSON.stringify({ app: appName }) })
    .then(async (resp) => {
      const data = await resp.json().catch(() => ({}));
      const title = _el('ithaca-ssh-title');
      const out = _el('ithaca-ssh-output');
      if (!title || !out) return;
      if (!resp.ok) {
        title.textContent = `${appName} — failed`;
        out.textContent = data.detail || `HTTP ${resp.status}`;
        return;
      }
      if (!data.ok) {
        title.textContent = `${data.host || appName} — failed`;
        out.textContent = data.error || 'ssh failed';
        return;
      }
      title.textContent = `${data.host}:${data.path || '~'}`;
      out.textContent = data.output || '(no output)';
    })
    .catch((err) => {
      const out = _el('ithaca-ssh-output');
      if (out) out.textContent = `Request failed: ${err}`;
    });
}

function _closeSshModal() {
  _el('ithaca-ssh-overlay')?.remove();
}

// ─── Workspace descriptor ───────────────────────────────────────────────────

function mount(container) {
  const tiles = TILES.map((t) => {
    if (t.key === 'weather') {
      return `<div class="ithaca-tile" data-slot="A">
        <div class="ithaca-tile-header">
          <span class="ithaca-tile-title">Weather</span>
          <span class="ithaca-tile-sub" id="ithaca-weather-loc"></span>
          <span style="flex:1"></span>
          <button class="doc-action-icon-btn ithaca-tile-refresh" id="ithaca-weather-refresh" title="Refresh weather">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
        </div>
        <div class="ithaca-tile-body" id="ithaca-weather-body"></div>
      </div>`;
    }
    if (t.key === 'updates') {
      return `<div class="ithaca-tile" data-slot="B">
        <div class="ithaca-tile-header">
          <span class="ithaca-tile-title">Software Updates</span>
          <span class="ithaca-tile-sub" id="ithaca-updates-date"></span>
          <span style="flex:1"></span>
          <button class="doc-action-icon-btn ithaca-tile-refresh" id="ithaca-updates-refresh" title="Re-read the digest">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
        </div>
        <div class="ithaca-tile-body" id="ithaca-updates-body"></div>
      </div>`;
    }
    return `<div class="ithaca-tile ithaca-tile-empty" data-slot="${t.slot}">
      <div class="ithaca-tile-empty-label">Tile ${t.slot}</div>
    </div>`;
  }).join('');

  const screen = document.createElement('div');
  screen.className = 'ithaca-screen ithaca-screen-open';
  screen.id = 'ithaca-screen';
  screen.innerHTML = `
    <div class="ithaca-header">
      <button class="ithaca-back-btn" id="ithaca-back-btn" title="Back to chat">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
        <span>Chat</span>
      </button>
      <h2 class="ithaca-title">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9.5 12 4l9 5.5"/><line x1="5" y1="12" x2="19" y2="12"/><line x1="7" y1="12" x2="7" y2="18"/><line x1="12" y1="12" x2="12" y2="18"/><line x1="17" y1="12" x2="17" y2="18"/><line x1="4" y1="20.5" x2="20" y2="20.5"/></svg>
        Ithaca
      </h2>
      <span class="ithaca-header-sub">hub</span>
      <span style="flex:1"></span>
    </div>
    <div class="ithaca-grid">${tiles}</div>`;
  container.appendChild(screen);

  screen.querySelector('#ithaca-back-btn')?.addEventListener('click', () => Workspace.close('ithaca'));
  screen.querySelector('#ithaca-weather-refresh')?.addEventListener('click', () => _loadWeather(true));
  screen.querySelector('#ithaca-updates-refresh')?.addEventListener('click', () => _loadDigest(true));
}

function activate() {
  _loadWeather();
  _loadDigest();
  if (_weatherTimer) clearInterval(_weatherTimer);
  _weatherTimer = setInterval(() => _loadWeather(), WEATHER_REFRESH_MS);
}

function deactivate() {
  _closeSshModal();
  if (_weatherTimer) { clearInterval(_weatherTimer); _weatherTimer = null; }
}

function onEscape() {
  if (_el('ithaca-ssh-overlay')) { _closeSshModal(); return true; }
  return false;
}

const ithacaWorkspace = {
  id: 'ithaca',
  route: '/ithaca',
  title: 'Ithaca',
  surface: 'both',
  collapseSidebar: true,
  mount,
  activate,
  deactivate,
  onEscape,
};

export default ithacaWorkspace;
