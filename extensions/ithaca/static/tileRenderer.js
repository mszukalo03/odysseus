// extensions/ithaca/static/tileRenderer.js
//
// Generic renderer for user-defined dashboard tiles (extensions/ithaca/tiles.py,
// extensions/ithaca/tile_schema.py). Dispatches on the shaped data's `type`
// (table | stat | bar | line | pie | list) — the built-in Weather tile keeps
// its own bespoke rendering in index.js and never goes through here.
//
// table/list/stat need no external library. bar/line/pie lazy-load the
// vendored Chart.js UMD build (static/lib/chart.umd.min.js) on first use,
// mirroring static/js/document.js's ensureDocx()/ensureHtml2Pdf() pattern —
// no bundler in this app, so third-party libs are plain vendored scripts.

import { esc as _esc } from './ithacaCommon.js';

let _chartJsReady = null;
function _ensureChartJs() {
  if (_chartJsReady) return _chartJsReady;
  if (window.Chart) return (_chartJsReady = Promise.resolve());
  _chartJsReady = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/static/lib/chart.umd.min.js';
    s.onload = resolve;
    s.onerror = () => reject(new Error('Failed to load chart library'));
    document.head.appendChild(s);
  });
  return _chartJsReady;
}

// A small fixed palette, consistent regardless of the viewer's theme —
// tiles render on both light and dark panels. Cycled by index for
// multi-slice pie/bar-with-many-categories tiles.
const _CHART_COLORS = [
  '#6ca0f6', '#f5a623', '#50c878', '#e05d6f', '#a78bfa',
  '#5fd0e8', '#f2994a', '#8a94a6',
];

export function renderTile(container, config, data) {
  const type = (data && data.type) || (config && config.viz && config.viz.type) || 'table';
  if (type === 'table') return _renderTable(container, config, data);
  if (type === 'list') return _renderList(container, data);
  if (type === 'stat') return _renderStat(container, data);
  if (type === 'bar' || type === 'line' || type === 'pie') return _renderChart(container, type, data);
  container.innerHTML = `<div class="ithaca-tile-hint">Unsupported viz type: ${_esc(type)}</div>`;
}

// Table sort/filter state (current sort column+direction, per-column filter
// values) is kept per container rather than per tile config, so it survives
// the auto-refresh re-render (same DOM node, fresh data) but resets if the
// tile is torn down and rebuilt.
const _tileTableState = new WeakMap();

// A column with few distinct values (status/category-like) gets a dropdown
// filter; anything with more variety (names, free text, ids) gets a
// substring text filter instead. No config needed — this is inferred fresh
// from whatever rows the query happens to return.
const _DISTINCT_FILTER_LIMIT = 12;

function _detectColumnType(field, rows) {
  let sawValue = false;
  let allNumeric = true;
  let allDate = true;
  for (const row of rows) {
    const v = row[field];
    if (v === null || v === undefined || v === '') continue;
    sawValue = true;
    const n = typeof v === 'number' ? v : Number(v);
    if (allNumeric && (typeof v === 'boolean' || v === '' || Number.isNaN(n))) allNumeric = false;
    if (allDate && (typeof v === 'number' || Number.isNaN(Date.parse(v)))) allDate = false;
  }
  if (!sawValue) return 'string';
  if (allNumeric) return 'number';
  if (allDate) return 'date';
  return 'string';
}

function _distinctValues(field, rows) {
  const set = new Set();
  for (const row of rows) {
    set.add(row[field] === null || row[field] === undefined ? '' : String(row[field]));
    if (set.size > _DISTINCT_FILTER_LIMIT) return null;
  }
  return Array.from(set).sort();
}

function _compareValues(a, b, type) {
  const aNull = a === null || a === undefined || a === '';
  const bNull = b === null || b === undefined || b === '';
  if (aNull && bNull) return 0;
  if (aNull) return -1;
  if (bNull) return 1;
  if (type === 'number') return Number(a) - Number(b);
  if (type === 'date') return Date.parse(a) - Date.parse(b);
  return String(a).localeCompare(String(b));
}

function _renderTable(container, config, data) {
  const columns = data.columns || [];
  const rows = data.rows || [];
  const configured = (config && config.viz && config.viz.columns) || [];
  const colDefs = configured.length ? configured : columns.map((f) => ({ field: f, label: f }));
  if (!rows.length) {
    container.innerHTML = '<div class="ithaca-tile-hint">No rows returned.</div>';
    _tileTableState.delete(container);
    return;
  }

  let state = _tileTableState.get(container);
  if (!state) {
    state = { sort: null, filters: {} };
    _tileTableState.set(container, state);
  }

  const colMeta = colDefs.map((c) => {
    const type = _detectColumnType(c.field, rows);
    return { field: c.field, label: c.label || c.field, type, distinct: type === 'string' ? _distinctValues(c.field, rows) : null };
  });

  container.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'ithaca-tile-table-wrap';

  const table = document.createElement('table');
  table.className = 'ithaca-tile-table';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const filterRow = document.createElement('tr');
  filterRow.className = 'ithaca-tile-filter-row';
  const tbody = document.createElement('tbody');

  function applyAndRender() {
    let filtered = rows.filter((row) => colMeta.every((c) => {
      const fv = state.filters[c.field];
      if (!fv) return true;
      const val = row[c.field];
      if (c.distinct) return String(val === null || val === undefined ? '' : val) === fv;
      return String(val === null || val === undefined ? '' : val).toLowerCase().includes(fv.toLowerCase());
    }));
    if (state.sort) {
      const { field, dir } = state.sort;
      const type = (colMeta.find((c) => c.field === field) || {}).type || 'string';
      filtered = filtered.slice().sort((a, b) => _compareValues(a[field], b[field], type) * (dir === 'desc' ? -1 : 1));
    }
    tbody.innerHTML = filtered.length
      ? filtered.map((row) => `<tr>${colMeta.map((c) => `<td>${_esc(row[c.field])}</td>`).join('')}</tr>`).join('')
      : `<tr><td colspan="${colMeta.length}" class="ithaca-tile-hint">No matching rows.</td></tr>`;
  }

  colMeta.forEach((c) => {
    const th = document.createElement('th');
    th.className = 'ithaca-tile-sortable';
    const active = state.sort && state.sort.field === c.field;
    th.textContent = c.label + (active ? (state.sort.dir === 'desc' ? ' ▼' : ' ▲') : '');
    if (active) th.classList.add('ithaca-tile-sort-active');
    th.addEventListener('click', () => {
      if (active) {
        state.sort = state.sort.dir === 'asc' ? { field: c.field, dir: 'desc' } : null;
      } else {
        state.sort = { field: c.field, dir: 'asc' };
      }
      _renderTable(container, config, data);
    });
    headRow.appendChild(th);

    const filterCell = document.createElement('td');
    if (c.distinct && c.distinct.length > 1) {
      const select = document.createElement('select');
      select.className = 'ithaca-tile-filter-input';
      select.innerHTML = '<option value="">All</option>' + c.distinct.map((v) => `<option value="${_esc(v)}">${_esc(v || '(blank)')}</option>`).join('');
      select.value = state.filters[c.field] || '';
      select.addEventListener('change', () => {
        if (select.value) state.filters[c.field] = select.value; else delete state.filters[c.field];
        applyAndRender();
      });
      filterCell.appendChild(select);
    } else {
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'ithaca-tile-filter-input';
      input.placeholder = 'Filter…';
      input.value = state.filters[c.field] || '';
      input.addEventListener('input', () => {
        if (input.value) state.filters[c.field] = input.value; else delete state.filters[c.field];
        applyAndRender();
      });
      filterCell.appendChild(input);
    }
    filterRow.appendChild(filterCell);
  });

  thead.appendChild(headRow);
  thead.appendChild(filterRow);
  table.appendChild(thead);
  table.appendChild(tbody);
  wrap.appendChild(table);
  container.appendChild(wrap);

  applyAndRender();
}

function _renderList(container, data) {
  const items = data.items || [];
  if (!items.length) {
    container.innerHTML = '<div class="ithaca-tile-hint">No items.</div>';
    return;
  }
  container.innerHTML = `<ul class="ithaca-tile-list">${items.map((i) => `<li>${_esc(i)}</li>`).join('')}</ul>`;
}

function _renderStat(container, data) {
  const value = data.value;
  container.innerHTML = `<div class="ithaca-tile-stat">${value == null ? '—' : _esc(value)}</div>`;
}

function _renderChart(container, type, data) {
  const points = data.points || [];
  if (!points.length) {
    container.innerHTML = '<div class="ithaca-tile-hint">No data points.</div>';
    return;
  }
  // A fresh <canvas> per render — this fires on every refresh tick, and a
  // brand-new element (rather than reusing one Chart.js already bound)
  // avoids its "Canvas is already in use" guard without needing to track
  // and .destroy() a previous instance across async loads.
  container.innerHTML = '<canvas class="ithaca-tile-canvas"></canvas>';
  const canvas = container.querySelector('canvas');
  _ensureChartJs().then(() => {
    // The tile may have been torn down (tab switch, re-render) before the
    // library finished loading.
    if (!canvas.isConnected) return;
    const labels = points.map((p) => p.label);
    const values = points.map((p) => p.value);
    const colors = points.map((_, i) => _CHART_COLORS[i % _CHART_COLORS.length]);
    const isPie = type === 'pie';
    new window.Chart(canvas, {
      type,
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: isPie ? colors : colors[0],
          borderColor: isPie ? 'transparent' : colors[0],
          fill: type === 'line' ? false : undefined,
          tension: type === 'line' ? 0.25 : undefined,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: isPie, labels: { boxWidth: 10, font: { size: 10 } } } },
        scales: isPie ? {} : { y: { beginAtZero: true } },
      },
    });
  }).catch((err) => {
    console.error('Ithaca chart tile failed to load Chart.js:', err);
    container.innerHTML = '<div class="ithaca-tile-hint">Chart library failed to load.</div>';
  });
}
