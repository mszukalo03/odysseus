// static/js/documentSelection.js
//
// "Select text, pin it as context for the next chat message" — the doc
// editor's selection-based AI editing feature. Extracted from document.js to
// reduce its size. Fully self-contained: reads/writes only its own module
// state (_selections) and the DOM, no dependency on document.js's doc store
// (docs/activeDocId) or API_BASE.

// ---- Selection-based AI editing ----

// Tracked selection state — when set, the next chat message auto-includes this context
export let _selections = [];  // [{ text, startLine, endLine, start, end }, ...]

// Pinned-selection overlays are positioned in pixel coords measured
// against the textarea's current size. When the window shrinks (or
// the sidebar collapses, or the panel resizes), the text wraps to
// more rows but the overlay rectangles stay where they were —
// visibly drifting off the real highlighted text. Re-render on any
// size change so the overlays follow the new wrap. Debounced via
// rAF to coalesce the rapid-fire ResizeObserver pulses during a
// drag-resize.
let _selResizeScheduled = false;
export function _scheduleSelRerender() {
  if (_selResizeScheduled || _selections.length === 0) return;
  _selResizeScheduled = true;
  requestAnimationFrame(() => {
    _selResizeScheduled = false;
    try { renderAllSelectionHighlights(); } catch (_) {}
  });
}
if (typeof window !== 'undefined') {
  window.addEventListener('resize', _scheduleSelRerender);
}
// Observe the textarea itself so internal layout changes (sidebar
// collapse, panel snap, mobile keyboard show/hide) also trigger a
// re-render. The observer attaches lazily on first selection so we
// don't churn before the editor mounts.
let _selResizeObserver = null;
function _ensureSelResizeObserver() {
  if (_selResizeObserver || typeof ResizeObserver === 'undefined') return;
  const ta = document.getElementById('doc-editor-textarea');
  if (!ta) return;
  _selResizeObserver = new ResizeObserver(_scheduleSelRerender);
  _selResizeObserver.observe(ta);
}

/** Update selection tracking, show badge + persistent highlight.
 *  Each new selection is added (pinned). Click without selecting to clear all. */
export function updateSelectionState() {
  // The mirror measurement below uses the textarea's live computed metrics,
  // so pinned selections remain valid with wrapped lines, larger font sizes,
  // mobile widths, and non-fullscreen panes. Older code disabled selection
  // whenever wrapping was detected; increasing the document font made that
  // path fire constantly, so selecting text appeared to stop working.
  _ensureSelResizeObserver();
  const textarea = document.getElementById('doc-editor-textarea');
  if (!textarea) return;

  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;

  if (start === end) {
    // Simple click — don't clear, user might be clicking into chat
    return;
  }

  const text = textarea.value;
  const selectedText = text.substring(start, end);
  const startLine = text.substring(0, start).split('\n').length;
  const endLine = text.substring(0, end).split('\n').length;

  // Check for overlap with existing selection — replace if overlapping
  const overlapIdx = _selections.findIndex(s =>
    (start >= s.start && start <= s.end) || (end >= s.start && end <= s.end) ||
    (start <= s.start && end >= s.end)
  );
  const entry = { text: selectedText, startLine, endLine, start, end };
  if (overlapIdx >= 0) {
    _selections[overlapIdx] = entry;
  } else {
    _selections.push(entry);
  }

  showSelectionBadge();
  renderAllSelectionHighlights();
}

/** Show a selection indicator badge with count + clear button */
function showSelectionBadge() {
  let badge = document.getElementById('doc-selection-badge');
  if (!badge) {
    badge = document.createElement('span');
    badge.id = 'doc-selection-badge';
    badge.className = 'doc-selection-badge';
    badge.title = 'Selected regions — type in chat to edit';
    // Sits directly under the formatting toolbar so it reads as part
    // of the toolbar row, not buried in the page header. Falls back
    // to the editor header if the toolbar isn't on screen.
    const toolbar = document.getElementById('doc-md-toolbar');
    if (toolbar && toolbar.parentNode) {
      toolbar.insertAdjacentElement('afterend', badge);
    } else {
      const header = document.querySelector('.doc-editor-header');
      if (header) header.insertBefore(badge, header.firstChild);
    }
  }
  if (_selections.length === 0) {
    badge.style.display = 'none';
    return;
  }
  const labels = _selections.map(s =>
    s.startLine === s.endLine ? `L${s.startLine}` : `L${s.startLine}-${s.endLine}`
  );
  const label = _selections.length === 1
    ? `${labels[0]} selected`
    : `${_selections.length} selections (${labels.join(', ')})`;
  badge.innerHTML = `${label}<button class="doc-selection-clear" title="Clear all selections">&times;</button>`;
  badge.style.display = '';
  badge.querySelector('.doc-selection-clear').addEventListener('click', (e) => {
    e.stopPropagation();
    clearSelection();
  });
}

/** Markdown / prose docs get character-precise highlights (like a
 *  normal browser selection but persistent). Code docs get line-based
 *  highlights — when working in code you usually operate on whole
 *  lines, and the character-based version reads as jittery against
 *  monospace alignment. */
function _isCodeDoc() {
  const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
  if (!lang) return false;
  // Prose / preview types that should get character-precise highlights.
  const prose = new Set(['markdown', 'md', 'text', 'txt', 'email', 'html', 'csv']);
  return !prose.has(lang);
}

/** Measure the visual x,y position of a character index inside the
 *  mirror element by inserting a zero-width marker span there and
 *  reading its bounding rect. Returns {x, y} relative to the mirror's
 *  content-box origin. */
function _measurePos(mirror, text, pos) {
  mirror.innerHTML = '';
  if (pos > 0) mirror.appendChild(document.createTextNode(text.substring(0, pos)));
  const marker = document.createElement('span');
  marker.textContent = '​';
  mirror.appendChild(marker);
  const r = marker.getBoundingClientRect();
  const m = mirror.getBoundingClientRect();
  return { x: r.left - m.left, y: r.top - m.top };
}

/** Render persistent highlight overlays for all selections */
// Re-anchor pinned selections against the live textarea content. After
// an undo or any other path that shrinks/shifts the text, captured
// {start, end} positions can point at unrelated content (or past the
// end of the buffer). We:
//   1. Verify the captured text still sits at [start, end].
//   2. If not, look for the captured text elsewhere in the doc and
//      re-anchor. Prefers the nearest occurrence to the old position.
//   3. If the captured text is gone entirely, drop the selection.
//   4. Refresh derived fields (startLine/endLine) when re-anchored.
// Cheap O(N) per selection; only runs when _selections is non-empty.
function _validateSelections(text) {
  if (_selections.length === 0) return;
  const survivors = [];
  for (const s of _selections) {
    const captured = s.text || '';
    if (!captured) continue;
    // Fast path: still at the same offsets.
    if (text.substring(s.start, s.end) === captured) {
      survivors.push(s);
      continue;
    }
    // Re-anchor: find the captured text and pick the occurrence
    // nearest to the old start so multi-match docs don't snap to the
    // wrong one. indexOf scans are cheap for typical doc sizes.
    let best = -1, bestDist = Infinity;
    let from = 0;
    while (true) {
      const idx = text.indexOf(captured, from);
      if (idx === -1) break;
      const dist = Math.abs(idx - s.start);
      if (dist < bestDist) { best = idx; bestDist = dist; }
      from = idx + 1;
    }
    if (best === -1) continue;  // text gone entirely → drop
    const newStart = best;
    const newEnd = best + captured.length;
    survivors.push({
      ...s,
      start: newStart,
      end: newEnd,
      startLine: text.substring(0, newStart).split('\n').length,
      endLine: text.substring(0, newEnd).split('\n').length,
    });
  }
  _selections = survivors;
}

function renderAllSelectionHighlights() {
  const wrap = document.getElementById('doc-editor-wrap');
  if (!wrap) return;
  // Remove old overlays
  wrap.querySelectorAll('.doc-selection-overlay').forEach(el => el.remove());

  const textarea = document.getElementById('doc-editor-textarea');
  if (!textarea || _selections.length === 0) return;

  const text = textarea.value;
  // Pre-render guard: re-anchor or drop selections whose text has
  // shifted (undo, programmatic edits, etc.) so the overlays never
  // draw on the wrong region.
  _validateSelections(text);
  if (_selections.length === 0) return;
  const style = getComputedStyle(textarea);
  const paddingTop = parseFloat(style.paddingTop) || 10;
  const paddingLeft = parseFloat(style.paddingLeft) || 48;
  const lineHeight = parseFloat(style.lineHeight) || (parseFloat(style.fontSize) * 1.45);

  // Shared mirror for measurement — same box model as the textarea
  // so any measurement we take lines up 1:1 with the rendered text.
  let mirror = document.getElementById('doc-selection-mirror');
  if (!mirror) {
    mirror = document.createElement('div');
    mirror.id = 'doc-selection-mirror';
    // box-sizing:border-box is critical — without it the mirror's
    // actual box width = (width prop) + horizontal padding, which is
    // wider than the textarea's text-render area. Text wraps at a
    // different column inside the mirror, so every measured y-offset
    // drifts from where the real text sits. border-box makes
    // mirror.box = textarea.clientWidth exactly.
    mirror.style.cssText = 'position:absolute;top:0;left:0;right:0;visibility:hidden;pointer-events:none;' +
      'white-space:pre-wrap;word-wrap:break-word;overflow-wrap:break-word;overflow:hidden;box-sizing:border-box;';
    wrap.appendChild(mirror);
  }
  mirror.style.font = style.font;
  mirror.style.padding = style.padding;
  mirror.style.borderWidth = style.borderWidth;
  mirror.style.borderStyle = 'solid';
  mirror.style.borderColor = 'transparent';
  mirror.style.width = textarea.clientWidth + 'px';
  mirror.style.tabSize = style.tabSize;
  mirror.style.letterSpacing = style.letterSpacing;
  mirror.style.wordSpacing = style.wordSpacing;
  mirror.style.textIndent = style.textIndent;

  const codeDoc = _isCodeDoc();
  const scrollTop = textarea.scrollTop;

  for (const sel of _selections) {
    if (codeDoc) {
      // Line-based: span every line that contains any selected char.
      const beforeStart = text.substring(0, sel.start);
      const lastNewline = beforeStart.lastIndexOf('\n');
      const startLineBegin = lastNewline + 1;
      mirror.textContent = text.substring(0, startLineBegin);
      const startTop = mirror.scrollHeight - paddingTop;

      const afterEnd = text.indexOf('\n', sel.end);
      const endLineEnd = afterEnd === -1 ? text.length : afterEnd;
      mirror.textContent = text.substring(0, endLineEnd);
      const endBottom = mirror.scrollHeight - paddingTop;

      mirror.textContent = '';

      const top = paddingTop + startTop - scrollTop;
      const height = endBottom - startTop || lineHeight;
      const overlay = document.createElement('div');
      overlay.className = 'doc-selection-overlay';
      overlay.style.top = top + 'px';
      overlay.style.left = paddingLeft + 'px';
      overlay.style.right = '0';
      overlay.style.height = height + 'px';
      wrap.appendChild(overlay);
    } else {
      // Character-precise: measure the actual selection start/end via
      // a marker span. Render one rect for single-line selections, or
      // three rects (first partial, middle full, last partial) for
      // multi-line selections.
      const startPos = _measurePos(mirror, text, sel.start);
      const endPos = _measurePos(mirror, text, sel.end);
      mirror.innerHTML = '';

      const addRect = (top, left, width, height) => {
        const overlay = document.createElement('div');
        overlay.className = 'doc-selection-overlay';
        overlay.style.top = (paddingTop + top - scrollTop) + 'px';
        overlay.style.left = (paddingLeft + left) + 'px';
        if (width != null) overlay.style.width = width + 'px';
        else overlay.style.right = '0';
        overlay.style.height = height + 'px';
        wrap.appendChild(overlay);
      };

      if (Math.abs(endPos.y - startPos.y) < 1) {
        // Single visual line.
        addRect(startPos.y, startPos.x, endPos.x - startPos.x, lineHeight);
      } else {
        // First line: from selection start to right edge.
        addRect(startPos.y, startPos.x, null, lineHeight);
        // Middle lines (if any): full-width band between the two.
        const middleTop = startPos.y + lineHeight;
        const middleHeight = endPos.y - middleTop;
        if (middleHeight > 0) addRect(middleTop, 0, null, middleHeight);
        // Last line: from left edge to selection end.
        addRect(endPos.y, 0, endPos.x, lineHeight);
      }
    }
  }
}

/** Sync all selection highlight positions on scroll */
export function syncSelectionOverlay() {
  if (_selections.length === 0) return;
  renderAllSelectionHighlights();
}

/** Clear all selections, badge, and highlights */
export function clearSelection() {
  _selections = [];
  const badge = document.getElementById('doc-selection-badge');
  if (badge) badge.style.display = 'none';
  const wrap = document.getElementById('doc-editor-wrap');
  if (wrap) wrap.querySelectorAll('.doc-selection-overlay').forEach(el => el.remove());
}

/**
 * Get all selection contexts for chat injection.
 * Called by chat module before sending a message.
 * Returns null if no selections, or array of { text, startLine, endLine }.
 */
export function getSelectionContext() {
  if (_selections.length === 0) return null;
  // Re-anchor / drop stale selections before handing them to chat —
  // shipping text from a stale offset would mean the AI sees content
  // from a different region than what the user thinks they highlighted.
  const _ta = document.getElementById('doc-editor-textarea');
  if (_ta) _validateSelections(_ta.value);
  if (_selections.length === 0) return null;
  if (_selections.length === 1) {
    const ctx = _selections[0];
    clearSelection();
    return ctx;
  }
  // Multiple selections — return array
  const ctx = [..._selections];
  clearSelection();
  return ctx;
}
