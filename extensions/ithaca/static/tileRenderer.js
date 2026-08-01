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

function _renderTable(container, config, data) {
  const columns = data.columns || [];
  const rows = data.rows || [];
  const configured = (config && config.viz && config.viz.columns) || [];
  const colDefs = configured.length ? configured : columns.map((f) => ({ field: f, label: f }));
  if (!rows.length) {
    container.innerHTML = '<div class="ithaca-tile-hint">No rows returned.</div>';
    return;
  }
  const head = colDefs.map((c) => `<th>${_esc(c.label || c.field)}</th>`).join('');
  const body = rows.map((row) => `<tr>${colDefs.map((c) => `<td>${_esc(row[c.field])}</td>`).join('')}</tr>`).join('');
  container.innerHTML = `<table class="ithaca-tile-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
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
