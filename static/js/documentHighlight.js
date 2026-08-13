// static/js/documentHighlight.js
//
// Syntax highlighting, the line-number gutter, and in-document find
// rendering for the document editor. Extracted from document.js to reduce
// its size. Self-contained: reads only the DOM (the active textarea/gutter/
// highlight elements by id) and hljs — no dependency on document.js's doc
// store (docs/activeDocId). attemptAutoDetect() stays in document.js because
// it needs the active doc record to check/set userSetLanguage; it still
// calls _looksLikeMarkdown() and syncHighlighting() from here.

/** Post-process hljs markdown output: colorize [brackets] and heading # markers */
function _postProcessMarkdown(codeEl) {
  const walker = document.createTreeWalker(codeEl, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  for (const node of textNodes) {
    const text = node.textContent;
    // Skip nodes already inside hljs spans (like [link text] which is .hljs-string)
    if (node.parentElement !== codeEl && node.parentElement.className &&
        /hljs-(string|link|code|section)/.test(node.parentElement.className)) continue;
    // Match standalone [bracketed text] not followed by (url)
    if (/\[[^\]]+\](?!\()/.test(text)) {
      const frag = document.createDocumentFragment();
      let last = 0;
      const re = /\[([^\]]+)\](?!\()/g;
      let m;
      while ((m = re.exec(text)) !== null) {
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        const span = document.createElement('span');
        span.className = 'md-bracket';
        span.textContent = m[0];
        frag.appendChild(span);
        last = re.lastIndex;
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      if (last > 0) node.parentNode.replaceChild(frag, node);
    }
  }
  // Colorize heading # markers inside .hljs-section spans
  codeEl.querySelectorAll('.hljs-section').forEach(span => {
    const text = span.textContent;
    const hashMatch = text.match(/^(#{1,6})\s/);
    if (hashMatch) {
      const marker = document.createElement('span');
      marker.className = 'md-heading-marker';
      marker.textContent = hashMatch[1] + ' ';
      span.textContent = text.slice(hashMatch[0].length);
      span.prepend(marker);
    }
  });
}

// Find-result rectangles drawn ON TOP of the textarea — bypasses
// the syntax-highlight overlay entirely so visibility works in
// markdown, email, and any other mode regardless of single-layer-
// rendering quirks. Same mirror-measurement approach as pinned
// selections so wrap matches the textarea exactly.
//
// `matches` is an array of [start, end] offsets; `currentIdx` is
// the focused one (gets brighter accent). Pass empty matches to
// clear all rects.
export function renderFindRects(matches, currentIdx) {
  const wrap = document.getElementById('doc-editor-wrap');
  if (!wrap) return;
  wrap.querySelectorAll('.doc-find-rect').forEach(el => el.remove());
  if (!matches || matches.length === 0) return;
  const textarea = document.getElementById('doc-editor-textarea');
  if (!textarea) return;
  const text = textarea.value;
  const style = getComputedStyle(textarea);
  const paddingTop = parseFloat(style.paddingTop) || 10;
  const paddingLeft = parseFloat(style.paddingLeft) || 48;
  const lineHeight = parseFloat(style.lineHeight) || (parseFloat(style.fontSize) * 1.45);

  let mirror = document.getElementById('doc-find-rect-mirror');
  if (!mirror) {
    mirror = document.createElement('div');
    mirror.id = 'doc-find-rect-mirror';
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

  const scrollTop = textarea.scrollTop;
  for (let i = 0; i < matches.length; i++) {
    const [s, e] = matches[i];
    // Line-band style: highlight the FULL visual row containing the
    // match. Cheap, always-visible, doesn't need character-precise
    // mirror measurement that varies across email/markdown/code modes.
    mirror.textContent = text.substring(0, s);
    const startTop = mirror.scrollHeight - paddingTop;
    // Find the wrap-row's end by measuring with one extra char beyond
    // the match end and stepping back to the last whitespace boundary.
    mirror.textContent = text.substring(0, e);
    const endHeight = mirror.scrollHeight - paddingTop;
    mirror.textContent = '';

    const top = paddingTop + startTop - scrollTop;
    const height = Math.max(endHeight - startTop, lineHeight);
    const rect = document.createElement('div');
    rect.className = 'doc-find-rect' + (i === currentIdx ? ' current' : '');
    rect.style.cssText =
      `position:absolute;left:${paddingLeft}px;right:8px;` +
      `top:${top}px;height:${height}px;` +
      `pointer-events:none;z-index:6;border-radius:2px;`;
    wrap.appendChild(rect);
  }
}

/** Wrap find-matches in the syntax-highlighted overlay with <mark> spans.
 * Walks text nodes so existing hljs spans are preserved. Matches that cross
 * syntax tokens are skipped (rare for user searches). */
export function applyFindMarks(codeEl) {
  if (!codeEl) return;
  // Remove prior find marks (unwrap)
  codeEl.querySelectorAll('mark.doc-find-mark').forEach(m => {
    const parent = m.parentNode;
    while (m.firstChild) parent.insertBefore(m.firstChild, m);
    parent.removeChild(m);
    parent.normalize();
  });
  const q = codeEl.dataset.findQuery || '';
  if (!q) return;
  const currentIdx = parseInt(codeEl.dataset.findCurrent || '-1', 10);
  const lq = q.toLowerCase();
  let occurrence = 0;
  const walker = document.createTreeWalker(codeEl, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  let n;
  while ((n = walker.nextNode())) nodes.push(n);
  for (const node of nodes) {
    const val = node.nodeValue || '';
    const lv = val.toLowerCase();
    if (!lv.includes(lq)) continue;
    const frag = document.createDocumentFragment();
    let i = 0;
    while (i < val.length) {
      const hit = lv.indexOf(lq, i);
      if (hit < 0) { frag.appendChild(document.createTextNode(val.slice(i))); break; }
      if (hit > i) frag.appendChild(document.createTextNode(val.slice(i, hit)));
      const mark = document.createElement('mark');
      mark.className = 'doc-find-mark' + (occurrence === currentIdx ? ' current' : '');
      mark.textContent = val.slice(hit, hit + q.length);
      frag.appendChild(mark);
      occurrence++;
      i = hit + q.length;
    }
    node.parentNode.replaceChild(frag, node);
  }
}

/** Sync highlighted overlay with textarea content */
export function syncHighlighting() {
  const textarea = document.getElementById('doc-editor-textarea');
  const codeEl = document.getElementById('doc-editor-code');
  const pre = document.getElementById('doc-editor-highlight');
  if (!textarea || !codeEl) return;

  // Don't overwrite inline diff markers
  if (codeEl.dataset.hasDiff) return;

  const text = textarea.value;
  // Trailing newline prevents scroll mismatch on last line
  codeEl.textContent = text + '\n';

  const lang = document.getElementById('doc-language-select')?.value;
  // hljs has no 'svg' grammar — highlight it as xml (the dropdown value stays
  // 'svg' so the preview/run routing still treats it as renderable markup).
  const _hlLang = lang === 'svg' ? 'xml' : lang;
  codeEl.className = _hlLang ? `language-${_hlLang}` : '';
  if (window.hljs && _hlLang) {
    codeEl.removeAttribute('data-highlighted');
    window.hljs.highlightElement(codeEl);
  }
  // Markdown post-processing: colorize standalone [brackets] and heading markers
  if (lang === 'markdown') {
    _postProcessMarkdown(codeEl);
  }

  // Reapply find highlights after hljs rewrote the DOM
  if (codeEl.dataset.findQuery) applyFindMarks(codeEl);

  // Keep scroll in sync
  if (pre) {
    codeEl.style.minHeight = textarea.scrollHeight + 'px';
    pre.scrollTop = textarea.scrollTop;
    pre.scrollLeft = textarea.scrollLeft;
  }

  // Update line numbers
  updateLineNumbers(text);
}

/** Update the line number gutter */
let _lineNumberResizeObserver = null;
let _lineNumberObservedTextarea = null;
let _lineNumberResizeRaf = null;

function _lineNumberContentEl(gutter) {
  let inner = gutter.querySelector('.doc-line-number-content');
  if (!inner) {
    inner = document.createElement('div');
    inner.className = 'doc-line-number-content';
    gutter.textContent = '';
    gutter.appendChild(inner);
  }
  return inner;
}

function _lineNumberStyleSignature(style) {
  return [
    style.fontFamily,
    style.fontSize,
    style.fontWeight,
    style.fontStyle,
    style.lineHeight,
    style.letterSpacing,
    style.tabSize,
    style.fontFeatureSettings,
    style.fontVariantLigatures,
    style.fontKerning,
  ].join('|');
}

function _textareaTextWidth(textarea, style) {
  const paddingLeft = parseFloat(style.paddingLeft) || 0;
  const paddingRight = parseFloat(style.paddingRight) || 0;
  return Math.max(0, textarea.clientWidth - paddingLeft - paddingRight);
}

function _lineHeightPx(style) {
  const parsed = parseFloat(style.lineHeight);
  if (Number.isFinite(parsed) && parsed > 0) return parsed;
  const fontSize = parseFloat(style.fontSize) || 11;
  return fontSize * 1.45;
}

function _lineNumberMeasureEl(textarea) {
  const wrap = document.getElementById('doc-editor-wrap') || textarea.parentElement || document.body;
  let probe = wrap.querySelector('.doc-line-number-measure');
  if (!probe) {
    probe = document.createElement('textarea');
    probe.className = 'doc-line-number-measure';
    probe.setAttribute('aria-hidden', 'true');
    probe.tabIndex = -1;
    probe.readOnly = true;
    probe.wrap = 'soft';
    wrap.appendChild(probe);
  }
  return probe;
}

function _syncLineNumberMeasureStyle(probe, style, textWidth) {
  probe.style.width = textWidth + 'px';
  probe.style.fontFamily = style.fontFamily;
  probe.style.fontSize = style.fontSize;
  probe.style.fontWeight = style.fontWeight;
  probe.style.fontStyle = style.fontStyle;
  probe.style.lineHeight = style.lineHeight;
  probe.style.letterSpacing = style.letterSpacing;
  probe.style.tabSize = style.tabSize;
  probe.style.fontFeatureSettings = style.fontFeatureSettings;
  probe.style.fontVariantLigatures = style.fontVariantLigatures;
  probe.style.fontKerning = style.fontKerning;
  probe.style.textRendering = style.textRendering;
  probe.style.whiteSpace = style.whiteSpace;
  probe.style.wordWrap = style.wordWrap;
  probe.style.overflowWrap = style.overflowWrap;
}

function _measureLineNumberHeights(textarea, lines, textWidth, style) {
  const probe = _lineNumberMeasureEl(textarea);
  _syncLineNumberMeasureStyle(probe, style, textWidth);
  const lineHeight = _lineHeightPx(style);
  return lines.map(line => {
    probe.value = line || ' ';
    const visualRows = Math.max(1, Math.round(probe.scrollHeight / lineHeight));
    return visualRows * lineHeight;
  });
}

function _renderLineNumberRows(inner, heights) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < heights.length; i++) {
    const row = document.createElement('div');
    row.className = 'doc-line-number-row';
    row.style.height = `${heights[i]}px`;

    const label = document.createElement('span');
    label.className = 'doc-line-number-label';
    label.textContent = String(i + 1);
    row.appendChild(label);
    frag.appendChild(row);
  }
  inner.textContent = '';
  inner.appendChild(frag);
}

function _scheduleLineNumberRerender() {
  if (_lineNumberResizeRaf) return;
  const run = () => {
    _lineNumberResizeRaf = null;
    const textarea = document.getElementById('doc-editor-textarea');
    if (textarea) updateLineNumbers(textarea.value, true);
  };
  if (typeof requestAnimationFrame === 'function') {
    _lineNumberResizeRaf = requestAnimationFrame(run);
  } else {
    run();
  }
}

function _ensureLineNumberResizeObserver(textarea) {
  if (typeof ResizeObserver === 'undefined') return;
  if (!_lineNumberResizeObserver) {
    _lineNumberResizeObserver = new ResizeObserver(_scheduleLineNumberRerender);
  }
  if (_lineNumberObservedTextarea === textarea) return;
  if (_lineNumberObservedTextarea) {
    _lineNumberResizeObserver.unobserve(_lineNumberObservedTextarea);
  }
  _lineNumberObservedTextarea = textarea;
  _lineNumberResizeObserver.observe(textarea);
}

if (typeof window !== 'undefined') {
  window.addEventListener('resize', _scheduleLineNumberRerender);
}

export function updateLineNumbers(text, force = false) {
  const textarea = document.getElementById('doc-editor-textarea');
  const gutter = document.getElementById('doc-line-numbers');
  if (!textarea || !gutter) return;

  const value = text || '';
  const lines = value.split('\n');
  const inner = _lineNumberContentEl(gutter);
  const style = getComputedStyle(textarea);
  const textWidth = _textareaTextWidth(textarea, style);
  const styleSig = _lineNumberStyleSignature(style);

  _ensureLineNumberResizeObserver(textarea);
  if (
    !force &&
    inner._lineNumberText === value &&
    inner._lineNumberWidth === textWidth &&
    inner._lineNumberStyleSig === styleSig
  ) {
    syncGutterScroll();
    return;
  }

  const heights = _measureLineNumberHeights(textarea, lines, textWidth, style);
  _renderLineNumberRows(inner, heights);
  inner._lineNumberText = value;
  inner._lineNumberWidth = textWidth;
  inner._lineNumberStyleSig = styleSig;
  syncGutterScroll();
}

/** Sync line number gutter scroll with textarea */
export function syncGutterScroll() {
  const textarea = document.getElementById('doc-editor-textarea');
  const gutter = document.getElementById('doc-line-numbers');
  if (textarea && gutter) {
    _lineNumberContentEl(gutter).style.transform = `translateY(${-textarea.scrollTop}px)`;
  }
}

/** Attempt language auto-detection using hljs.highlightAuto() */
/** Quick heuristic check for markdown before falling back to hljs */
export function _looksLikeMarkdown(text) {
  const lines = text.slice(0, 2000).split('\n');
  let score = 0;
  for (const line of lines) {
    if (/^#{1,6}\s/.test(line)) score += 3;         // headings
    else if (/^\s*[-*+]\s/.test(line)) score += 1;  // list items
    else if (/^\s*\d+\.\s/.test(line)) score += 1;  // ordered list
    else if (/^\s*>/.test(line)) score += 1;         // blockquote
    else if (/\[.+\]\(.+\)/.test(line)) score += 2; // links
    else if (/^```/.test(line)) score += 2;          // fenced code
    else if (/\*\*.+\*\*/.test(line)) score += 1;   // bold
    else if (/^---\s*$/.test(line)) score += 1;      // horizontal rule
  }
  return score >= 3;
}
