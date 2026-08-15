// extensions/ithaca/static/index.js — Ithaca hub workspace descriptor.
//
// Weather is the one persistent built-in tile (live OpenWeatherMap data).
// Every other tile is user-defined — built by hand, AI-generated
// (tileBuilder.js), or imported from another instance — and stored in
// extensions/ithaca/tiles.py. All tiles are resizable/rearrangeable
// (tileLayout.js) and rendered generically (tileRenderer.js) except
// Weather, which keeps its own bespoke rendering here.
//
// Registered with workspaceManager.js (see static/js/workspaceManager.js) —
// history, sidebar-collapse, title, active-nav-state and Escape arbitration
// are all owned by the host now; this module only builds DOM into the
// container it's given and starts/stops its own refresh timer.

import Workspace from '/static/js/workspaceManager.js';
import { esc, api } from './ithacaCommon.js';
import { renderTile } from './tileRenderer.js';
import { openTileBuilder, openImportTileModal, downloadTilePackage } from './tileBuilder.js';
import { applySavedLayout, makeTileLayoutable } from './tileLayout.js';

let _weatherTimer = null;
let _userTileTimers = [];

const WEATHER_REFRESH_MS = 10 * 60 * 1000; // matches the backend cache TTL

function _el(id) { return document.getElementById(id); }

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

// ─── Weather tile ───────────────────────────────────────────────────────────

async function _loadWeather(force = false) {
  const body = _el('ithaca-weather-body');
  if (!body) return;
  if (!body.dataset.loadedOnce) {
    body.innerHTML = '<div class="ithaca-tile-hint">Loading weather…</div>';
  }
  try {
    const resp = await api(`/weather${force ? '?refresh=1' : ''}`);
    if (!resp.ok) {
      const detail = (await resp.json().catch(() => ({}))).detail || `HTTP ${resp.status}`;
      body.innerHTML = `<div class="ithaca-tile-hint">${esc(detail)}</div>`;
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
    return `<div class="ithaca-hour" title="${esc(h.description)}${h.pop ? `, ${h.pop}% precip` : ''}">
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
        <span class="ithaca-weather-desc">${esc(cur.description)}</span>
        <span>Feels ${round(cur.feels_like)}° · ${cur.humidity ?? '—'}% RH · ${cur.wind_speed ?? '—'} ${unitW}</span>
      </div>
    </div>
    <div class="ithaca-hourly">${hourly || '<div class="ithaca-tile-hint">No forecast data</div>'}</div>`;
}

// ─── Workspace descriptor ───────────────────────────────────────────────────

function mount(container) {
  const weatherTile = `<div class="ithaca-tile" data-slot="A" data-tile-id="weather">
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

  const screen = document.createElement('div');
  screen.className = 'ithaca-screen ithaca-screen-open';
  screen.id = 'ithaca-screen';
  screen.innerHTML = `
    <div class="ithaca-header" data-ws-popup-header>
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
      <button class="doc-action-icon-btn" data-ws-split title="Split screen with another feature">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="12" y1="4" x2="12" y2="20"/></svg>
      </button>
      ${window._isAdmin ? `<button class="doc-action-icon-btn" id="ithaca-import-tile-btn" title="Import a tile package">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      </button>
      <button class="doc-action-icon-btn" id="ithaca-add-tile-btn" title="Add a tile">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      </button>` : ''}
    </div>
    <div class="ithaca-grid">${weatherTile}</div>`;
  container.appendChild(screen);

  screen.querySelector('#ithaca-back-btn')?.addEventListener('click', () => Workspace.closeAny('ithaca'));
  screen.querySelector('#ithaca-weather-refresh')?.addEventListener('click', () => _loadWeather(true));
  screen.querySelector('#ithaca-add-tile-btn')?.addEventListener('click', () => openTileBuilder(() => _loadUserTiles()));
  screen.querySelector('#ithaca-import-tile-btn')?.addEventListener('click', () => openImportTileModal(() => _loadUserTiles()));

  // Layout must be applied after the browser has actually laid the grid
  // out (getBoundingClientRect needs real geometry) — rAF, not a 0ms
  // setTimeout, so it runs on the next paint rather than racing it.
  requestAnimationFrame(() => _applyBuiltinTileLayout(screen));
}

// ─── Grid layout (drag/resize, persisted via /api/ithaca/layout) ──────────

let _layoutCache = null;

async function _loadLayout(force = false) {
  if (_layoutCache && !force) return _layoutCache;
  try {
    const resp = await api('/layout');
    _layoutCache = resp.ok ? ((await resp.json()).layout || {}) : {};
  } catch (err) {
    _layoutCache = {};
  }
  return _layoutCache;
}

function _persistLayout(tileId, placement) {
  if (_layoutCache) _layoutCache[tileId] = placement;
  api(`/layout/${encodeURIComponent(tileId)}`, { method: 'PUT', body: JSON.stringify(placement) })
    .catch((err) => console.error(`Ithaca layout save failed for '${tileId}':`, err));
}

async function _applyBuiltinTileLayout(screen) {
  const grid = screen.querySelector('.ithaca-grid');
  if (!grid) return;
  const layout = await _loadLayout();
  const tileEl = grid.querySelector('[data-tile-id="weather"]');
  if (!tileEl) return;
  if (layout.weather) applySavedLayout(tileEl, grid, layout.weather);
  if (window._isAdmin) makeTileLayoutable(tileEl, grid, (placement) => _persistLayout('weather', placement));
}

// ─── Tile action buttons (extensions/ithaca/tile_schema.py's TileAction) ──

async function _runTileAction(cfg, action, btn) {
  if (action.confirm !== false) {
    const message = action.confirm_message || `Run "${action.label}" on "${cfg.title}"?`;
    const confirmed = window.styledConfirm
      ? await window.styledConfirm(message, { confirmText: 'Run' })
      : window.confirm(message);
    if (!confirmed) return;
  }
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Running…';
  try {
    const resp = await api(`/tiles/${encodeURIComponent(cfg.id)}/actions/${encodeURIComponent(action.id)}/run`, {
      method: 'POST',
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) {
      btn.textContent = 'Failed';
      console.error(`Ithaca tile action '${action.id}' failed:`, data.detail || data.error || resp.status);
    } else {
      btn.textContent = 'Done';
    }
  } catch (err) {
    btn.textContent = 'Failed';
    console.error(`Ithaca tile action '${action.id}' failed:`, err);
  } finally {
    setTimeout(() => { btn.textContent = originalText; btn.disabled = false; }, 2000);
  }
}

// ─── User-defined tiles (config-driven, auto-flow past Weather) ───────────

async function _loadUserTiles() {
  const grid = document.querySelector('#ithaca-screen .ithaca-grid');
  if (!grid) return;
  _userTileTimers.forEach(clearInterval);
  _userTileTimers = [];
  grid.querySelectorAll('.ithaca-tile-dynamic').forEach((el) => el.remove());
  try {
    const [resp, layout] = await Promise.all([api('/tiles'), _loadLayout()]);
    if (!resp.ok) return;
    const { tiles } = await resp.json();
    for (const cfg of tiles || []) {
      const tile = document.createElement('div');
      tile.className = 'ithaca-tile ithaca-tile-dynamic';
      tile.dataset.tileId = cfg.id;
      tile.innerHTML = `
        <div class="ithaca-tile-header">
          <span class="ithaca-tile-title">${esc(cfg.title)}</span>
          <span style="flex:1"></span>
          ${window._isAdmin ? `<button class="doc-action-icon-btn ithaca-tile-edit" title="Edit tile">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
          </button>
          <button class="doc-action-icon-btn ithaca-tile-download" title="Download tile package">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          </button>` : ''}
          <button class="doc-action-icon-btn ithaca-tile-refresh" title="Refresh">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
          ${window._isAdmin ? `<button class="doc-action-icon-btn ithaca-tile-delete" title="Delete tile">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
          </button>` : ''}
        </div>
        <div class="ithaca-tile-body"><div class="ithaca-tile-hint">Loading…</div></div>
        ${(cfg.actions || []).length ? `<div class="ithaca-tile-actions">${(cfg.actions || []).map((a) => `
          <button class="ithaca-tile-action-btn" data-action-id="${esc(a.id)}">${esc(a.label)}</button>
        `).join('')}</div>` : ''}`;
      grid.appendChild(tile);
      tile.querySelector('.ithaca-tile-edit')?.addEventListener('click', () => openTileBuilder(() => _loadUserTiles(), cfg));
      tile.querySelector('.ithaca-tile-download')?.addEventListener('click', () => downloadTilePackage(cfg.id, cfg.title));
      tile.querySelectorAll('.ithaca-tile-action-btn').forEach((btn) => {
        const action = (cfg.actions || []).find((a) => a.id === btn.dataset.actionId);
        if (!action) return;
        btn.addEventListener('click', () => _runTileAction(cfg, action, btn));
      });
      tile.querySelector('.ithaca-tile-delete')?.addEventListener('click', async () => {
        const confirmed = window.styledConfirm
          ? await window.styledConfirm(`Delete tile "${cfg.title}"?`, { confirmText: 'Delete', danger: true })
          : window.confirm(`Delete tile "${cfg.title}"?`);
        if (!confirmed) return;
        await api(`/tiles/${encodeURIComponent(cfg.id)}`, { method: 'DELETE' });
        _loadUserTiles();
      });
      requestAnimationFrame(() => {
        if (layout[cfg.id]) applySavedLayout(tile, grid, layout[cfg.id]);
        if (window._isAdmin) makeTileLayoutable(tile, grid, (placement) => _persistLayout(cfg.id, placement));
      });
      const body = tile.querySelector('.ithaca-tile-body');
      const load = async (force) => {
        try {
          const r = await api(`/tiles/${encodeURIComponent(cfg.id)}/data${force ? '?refresh=1' : ''}`);
          if (!r.ok) {
            const detail = (await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`;
            body.innerHTML = `<div class="ithaca-tile-hint">${esc(detail)}</div>`;
            return;
          }
          renderTile(body, cfg, await r.json());
        } catch (err) {
          console.error(`Ithaca tile '${cfg.id}' load failed:`, err);
          body.innerHTML = '<div class="ithaca-tile-hint">Tile data unavailable.</div>';
        }
      };
      tile.querySelector('.ithaca-tile-refresh')?.addEventListener('click', () => load(true));
      load(false);
      const ms = Math.max(60, cfg.refresh_interval_seconds || 900) * 1000;
      _userTileTimers.push(setInterval(() => load(false), ms));
    }
  } catch (err) {
    console.error('Ithaca user tiles load failed:', err);
  }
}

function activate() {
  _loadWeather();
  _loadUserTiles();
  if (_weatherTimer) clearInterval(_weatherTimer);
  _weatherTimer = setInterval(() => _loadWeather(), WEATHER_REFRESH_MS);
}

function deactivate() {
  if (_weatherTimer) { clearInterval(_weatherTimer); _weatherTimer = null; }
  _userTileTimers.forEach(clearInterval);
  _userTileTimers = [];
}

const ithacaWorkspace = {
  id: 'ithaca',
  route: '/ithaca',
  title: 'Ithaca',
  surface: 'both',
  // Falls back to page when the user has no stored `feature_display_modes`
  // choice for Ithaca — the dashboard is a destination, not something you
  // glance at beside a chat. Popup still works (the host re-parents this same
  // DOM into a floating window); nothing here is mode-specific because the
  // surface owns geometry in both modes.
  defaultDisplay: 'page',
  collapseSidebar: true,
  mount,
  activate,
  deactivate,
};

export default ithacaWorkspace;
