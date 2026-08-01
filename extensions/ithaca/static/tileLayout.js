// extensions/ithaca/static/tileLayout.js
//
// Drag-to-move and drag-corner-to-resize for Ithaca dashboard tiles, backed
// by the CSS grid `.ithaca-grid` already uses (grid-auto-rows: 64px,
// gap: 12px — ROW_HEIGHT/GAP below must match static/style.css). Applies to
// any tile element, built-in (weather/updates) or user-defined, since
// layout is stored separately from tile content (extensions/ithaca/tiles.py
// LAYOUT_FILE) — see backend GET/PUT /api/ithaca/layout.
//
// Placement math is pixel-based (bounding-rect deltas rounded to the
// nearest grid cell), not computed-style parsing — that stays correct
// whether a tile's current position came from an explicit saved layout or
// the browser's own auto-flow placement, and needs no per-browser handling
// of how `grid-column-end: span N` is exposed via getComputedStyle.

const ROW_HEIGHT = 64; // matches .ithaca-grid's grid-auto-rows
const GAP = 12;        // matches .ithaca-grid's gap

function _gridMetrics(grid) {
  const rect = grid.getBoundingClientRect();
  const cols = getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length || 1;
  const colWidth = (rect.width - GAP * (cols - 1)) / cols;
  return { rect, cols, colWidth, rowHeight: ROW_HEIGHT, gap: GAP };
}

function _applyPlacement(tileEl, col, row, w, h) {
  tileEl.style.gridColumn = `${col} / span ${w}`;
  tileEl.style.gridRow = `${row} / span ${h}`;
}

/** Read a tile's actual rendered grid cell (col/row/w/h), regardless of
 * whether it got there via an explicit style or the browser's auto-flow —
 * used to seed drag/resize state and to persist a tile's position the
 * first time it's touched (before any layout.json entry exists for it). */
function currentPlacement(tileEl, grid) {
  const m = _gridMetrics(grid);
  const tileRect = tileEl.getBoundingClientRect();
  const col = Math.round((tileRect.left - m.rect.left) / (m.colWidth + m.gap)) + 1;
  const row = Math.round((tileRect.top - m.rect.top + grid.scrollTop) / (m.rowHeight + m.gap)) + 1;
  const w = Math.max(1, Math.round((tileRect.width + m.gap) / (m.colWidth + m.gap)));
  const h = Math.max(1, Math.round((tileRect.height + m.gap) / (m.rowHeight + m.gap)));
  return { col, row, w, h };
}

/** Apply a saved {col,row,w,h}, clamped to the grid's current column count
 * (a layout saved at a wider viewport could otherwise place a tile
 * partially off-grid on a narrower one). */
export function applySavedLayout(tileEl, grid, saved) {
  if (!saved) return;
  const m = _gridMetrics(grid);
  const w = Math.max(1, Math.min(saved.w || 1, m.cols));
  const col = Math.max(1, Math.min(saved.col || 1, m.cols - w + 1));
  const row = Math.max(1, saved.row || 1);
  _applyPlacement(tileEl, col, row, w, saved.h || 1);
}

/** Wire up drag-to-move (via the tile's header) and drag-corner-to-resize.
 * `onPersist(placement)` is called once per completed drag/resize with the
 * final {col,row,w,h} — the caller is responsible for saving it (PUT
 * /api/ithaca/layout/{id}) and for not calling this at all for non-admins. */
export function makeTileLayoutable(tileEl, grid, onPersist) {
  const header = tileEl.querySelector('.ithaca-tile-header');
  if (header) {
    header.classList.add('ithaca-tile-draggable');
    header.addEventListener('mousedown', (e) => {
      if (e.target.closest('button')) return; // don't hijack refresh/download/delete clicks
      e.preventDefault();
      const state = currentPlacement(tileEl, grid);
      const m = _gridMetrics(grid);
      const startX = e.clientX;
      const startY = e.clientY;
      tileEl.classList.add('ithaca-tile-dragging');
      let finalCol = state.col;
      let finalRow = state.row;

      function onMove(ev) {
        const colDelta = Math.round((ev.clientX - startX) / (m.colWidth + m.gap));
        const rowDelta = Math.round((ev.clientY - startY) / (m.rowHeight + m.gap));
        finalCol = Math.max(1, Math.min(state.col + colDelta, m.cols - state.w + 1));
        finalRow = Math.max(1, state.row + rowDelta);
        _applyPlacement(tileEl, finalCol, finalRow, state.w, state.h);
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        tileEl.classList.remove('ithaca-tile-dragging');
        onPersist({ col: finalCol, row: finalRow, w: state.w, h: state.h });
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  const handle = document.createElement('div');
  handle.className = 'ithaca-tile-resize-handle';
  handle.title = 'Drag to resize';
  tileEl.appendChild(handle);
  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    e.stopPropagation(); // don't also trigger the header's drag-to-move
    const state = currentPlacement(tileEl, grid);
    const m = _gridMetrics(grid);
    const startX = e.clientX;
    const startY = e.clientY;
    tileEl.classList.add('ithaca-tile-resizing');
    let finalW = state.w;
    let finalH = state.h;

    function onMove(ev) {
      const colDelta = Math.round((ev.clientX - startX) / (m.colWidth + m.gap));
      const rowDelta = Math.round((ev.clientY - startY) / (m.rowHeight + m.gap));
      finalW = Math.max(1, Math.min(state.w + colDelta, m.cols - state.col + 1));
      finalH = Math.max(1, state.h + rowDelta);
      _applyPlacement(tileEl, state.col, state.row, finalW, finalH);
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      tileEl.classList.remove('ithaca-tile-resizing');
      onPersist({ col: state.col, row: state.row, w: finalW, h: finalH });
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}
