// static/js/documentMdToolbar.js
//
// Markdown formatting toolbar for the document editor — bold/italic/strike,
// link insertion, heading/list toggles, and the toolbar's overflow-menu
// wiring (initMdToolbar). Extracted from document.js to reduce its size.
//
// One dependency reaches back into document.js: applyMdFormat() needs to
// know whether the WYSIWYG email rich-body is the active editing surface
// (vs. the plain textarea) — see the "Injected references" slot below,
// filled once by document.js's init() the same way documentLibrary.js's
// initLibrary(config) already does for its own dependencies.

// ── Injected references from documentModule ──
let _emailRichbodyActive = () => null;

export function initMdToolbarDeps({ getEmailRichbodyActive } = {}) {
  if (typeof getEmailRichbodyActive === 'function') _emailRichbodyActive = getEmailRichbodyActive;
}

let _lastMdFormat = { action: null, t: 0 };
// Styled two-field link dialog (display text + URL). Resolves {url, text}
// or null on cancel. Reuses the styled-prompt CSS. Text is optional — left
// empty it falls back to the selected text, then the URL itself.
function _promptLink(defaultText = '') {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.id = 'doc-link-prompt-overlay';
    overlay.className = 'modal';
    overlay.innerHTML =
      '<div class="modal-content styled-confirm-box styled-prompt-box">' +
        '<div class="modal-header"><h4>Insert link</h4></div>' +
        '<div class="modal-body">' +
          '<input type="text" id="doc-link-text" class="styled-prompt-input" placeholder="Link text (optional)" maxlength="500" />' +
          '<input type="url" id="doc-link-url" class="styled-prompt-input" placeholder="https://example.com" maxlength="2048" style="margin-top:8px;" />' +
        '</div>' +
        '<div class="modal-footer">' +
          '<button id="doc-link-cancel" class="confirm-btn confirm-btn-secondary">Cancel</button>' +
          '<button id="doc-link-ok" class="confirm-btn confirm-btn-primary">Insert</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    const textEl = overlay.querySelector('#doc-link-text');
    const urlEl = overlay.querySelector('#doc-link-url');
    textEl.value = defaultText || '';
    function done(result) {
      overlay.remove();
      document.removeEventListener('keydown', onKey, true);
      resolve(result);
    }
    function submit() {
      const url = (urlEl.value || '').trim();
      if (!url) { urlEl.focus(); return; }
      done({ url, text: (textEl.value || '').trim() });
    }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(null); }
    }
    overlay.querySelector('#doc-link-ok').addEventListener('click', submit);
    overlay.querySelector('#doc-link-cancel').addEventListener('click', () => done(null));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(null); });
    urlEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
    textEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); urlEl.focus(); } });
    document.addEventListener('keydown', onKey, true);
    // Focus the URL field when the text is prefilled; otherwise start at text.
    requestAnimationFrame(() => { (defaultText ? urlEl : textEl).focus(); });
  });
}

// Email WYSIWYG link insertion. We snapshot the Range first (the dialog steals
// focus and would otherwise collapse it) and insert via direct DOM ops, since
// execCommand is unreliable once focus has moved to the modal.
async function _wysiwygInsertLink(rich) {
  const selObj = window.getSelection();
  let savedRange = null;
  if (selObj && selObj.rangeCount) {
    const r = selObj.getRangeAt(0);
    if (rich.contains(r.commonAncestorContainer)) savedRange = r.cloneRange();
  }
  const selText = savedRange ? savedRange.toString() : '';
  let res;
  try { res = await _promptLink(selText); } catch (_) { res = null; }
  if (!res) { rich.focus(); return; }
  let url = (res.url || '').trim();
  if (!url) { rich.focus(); return; }
  if (!/^[a-z][a-z0-9+.-]*:/i.test(url) && !url.startsWith('//')) url = 'https://' + url;
  const linkText = (res.text || '').trim() || selText || url;

  if (!savedRange) {
    savedRange = document.createRange();
    savedRange.selectNodeContents(rich);
    savedRange.collapse(false);
  }
  const a = document.createElement('a');
  a.href = url;
  if (selText && linkText === selText) {
    // Unchanged selection — wrap it to keep any inline formatting.
    a.appendChild(savedRange.extractContents());
  } else {
    savedRange.deleteContents();
    a.textContent = linkText;
  }
  savedRange.insertNode(a);
  // Place the caret right after the inserted link.
  const after = document.createRange();
  after.setStartAfter(a);
  after.collapse(true);
  rich.focus();
  const s = window.getSelection();
  s.removeAllRanges();
  s.addRange(after);
  _syncEmailRichbody(rich);
}

export function applyMdFormat(action) {
  // Guard against a duplicate/"ghost" click firing the same toggle twice in
  // quick succession — that would wrap then immediately unwrap, so the
  // markers appear for a split second and vanish.
  const _now = Date.now();
  if (_lastMdFormat.action === action && _now - _lastMdFormat.t < 350) return;
  _lastMdFormat = { action, t: _now };
  // Email WYSIWYG: format the live rich text via execCommand instead of
  // inserting markdown markers into the (hidden) source textarea.
  const _rich = _emailRichbodyActive();
  if (_rich) {
    _rich.focus();
    // Link needs an async styled URL prompt — handle it separately so we can
    // save/restore the selection (opening the modal collapses it otherwise).
    if (action === 'link') { _wysiwygInsertLink(_rich); return; }
    const _cmd = { bold: 'bold', italic: 'italic', strike: 'strikeThrough',
                   ul: 'insertUnorderedList', ol: 'insertOrderedList', hr: 'insertHorizontalRule' };
    try {
      if (_cmd[action]) document.execCommand(_cmd[action]);
      else if (action === 'h1' || action === 'h2' || action === 'h3') {
        // Toggle: if the block is already this heading, revert to a normal
        // paragraph; otherwise apply (or switch to) the heading.
        const cur = _currentBlockTag(_rich);
        document.execCommand('formatBlock', false, (cur === action) ? 'div' : action);
      } else if (action === 'code') {
        const cur = _currentBlockTag(_rich);
        document.execCommand('formatBlock', false, (cur === 'pre') ? 'div' : 'pre');
      }
      // quote/check/codeblock have no clean execCommand — skipped in WYSIWYG v1.
    } catch (_) {}
    _syncEmailRichbody(_rich);
    if (_rich._syncActive) _rich._syncActive();
    return;
  }
  const ta = document.getElementById('doc-editor-textarea');
  if (!ta) return;
  const start = ta.selectionStart;
  const end = ta.selectionEnd;
  const val = ta.value;
  const sel = val.substring(start, end);
  const before = val.substring(0, start);
  const after = val.substring(end);

  // Inline wrap toggles: bold, italic, strike, code
  const wrapMarks = { bold: '**', italic: '*', strike: '~~', code: '`' };
  if (wrapMarks[action]) {
    const m = wrapMarks[action];
    _applyWrapToggle(ta, before, sel, after, start, end, m, action);
    return;
  }

  // Numbered list — special handling for incrementing numbers
  if (action === 'ol') {
    _applyOrderedList(ta, start, end);
    return;
  }

  // Headings get their own toggle so applying the same level removes it and
  // a different level switches cleanly (rather than stacking # markers).
  if (action === 'h1' || action === 'h2' || action === 'h3') {
    _applyHeadingToggle(ta, start, { h1: '# ', h2: '## ', h3: '### ' }[action]);
    return;
  }

  // Line prefix toggles: quote, lists, checkbox
  const prefixMap = { quote: '> ', ul: '- ', check: '- [ ] ' };
  if (prefixMap[action]) {
    _applyLinePrefixToggle(ta, start, end, prefixMap[action]);
    return;
  }

  // Non-toggle actions
  let insert = '';
  let sS = start, sE = start;
  switch (action) {
    case 'link':
      if (sel) {
        insert = `[${sel}](url)`;
        sS = start + 1; sE = start + 1 + sel.length;
      } else {
        insert = '[text](url)';
        sS = start + 1; sE = start + 5;
      }
      break;
    case 'codeblock': {
      // Toggle: find if current line/selection is inside a ``` block
      const linesBefore = val.substring(0, start).split('\n');
      const linesAfter = val.substring(end).split('\n');
      // Look backward for opening ```
      let openIdx = -1;
      for (let i = linesBefore.length - 1; i >= 0; i--) {
        if (/^```/.test(linesBefore[i].trimEnd())) { openIdx = i; break; }
      }
      // Look forward for closing ```
      let closeIdx = -1;
      for (let i = 0; i < linesAfter.length; i++) {
        if (/^```\s*$/.test(linesAfter[i].trimEnd())) { closeIdx = i; break; }
      }
      if (openIdx >= 0 && closeIdx >= 0) {
        // Unwrap: remove the opening and closing fence lines
        const openLineStart = linesBefore.slice(0, openIdx).join('\n').length + (openIdx > 0 ? 1 : 0);
        const openLineEnd = openLineStart + linesBefore[openIdx].length + 1; // +1 for \n
        const closeLineStart = end + linesAfter.slice(0, closeIdx).join('\n').length + (closeIdx > 0 ? 1 : 0);
        const closeLineEnd = closeLineStart + linesAfter[closeIdx].length + (closeIdx < linesAfter.length - 1 ? 1 : 0);
        // Remove closing first (so indices stay valid), then opening
        _replaceRange(ta, closeLineStart, closeLineEnd, '');
        _replaceRange(ta, openLineStart, openLineEnd, '');
        const inner = val.substring(openLineEnd, closeLineStart);
        ta.selectionStart = openLineStart;
        ta.selectionEnd = openLineStart + inner.length;
        return;
      }
      // Wrap in code block
      const nl = before.length > 0 && !before.endsWith('\n') ? '\n' : '';
      insert = nl + '```\n' + (sel || '') + '\n```\n';
      sS = start + nl.length + 4;
      sE = sS + (sel ? sel.length : 0);
      break;
    }
    case 'hr': {
      const nl = before.length > 0 && !before.endsWith('\n') ? '\n' : '';
      insert = `${nl}---\n`;
      sE = sS = start + insert.length;
      break;
    }
    default: return;
  }
  _replaceRange(ta, start, end, insert);
  ta.selectionStart = sS;
  ta.selectionEnd = sE;
}

/** Replace a range in the textarea using execCommand to preserve undo stack */
export function _replaceRange(ta, from, to, text) {
  ta.focus();
  ta.selectionStart = from;
  ta.selectionEnd = to;
  const before = ta.value;
  let ok = false;
  try { ok = document.execCommand('insertText', false, text); } catch (_) { ok = false; }
  // execCommand('insertText') keeps native undo working. It silently no-ops on
  // some mobile browsers though — so ONLY when it changed nothing do we splice
  // the value directly (using the pre-edit value + original range, so we never
  // double-insert). execCommand fires its own input event; the splice path
  // dispatches one manually.
  if (!ok && ta.value === before) {
    ta.value = before.slice(0, from) + text + before.slice(to);
    ta.selectionStart = ta.selectionEnd = from + text.length;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

/** Toggle inline wrap markers (**, *, ~~, `) */
function _applyWrapToggle(ta, before, sel, after, start, end, mark, action) {
  const mLen = mark.length;

  // Case 1: selection is wrapped inside — e.g. selected "**bold**" → unwrap to "bold"
  if (sel.startsWith(mark) && sel.endsWith(mark) && sel.length > mLen * 2) {
    const inner = sel.slice(mLen, -mLen);
    _replaceRange(ta, start, end, inner);
    ta.selectionStart = start;
    ta.selectionEnd = start + inner.length;
    return;
  }

  // Case 2: markers are outside selection — e.g. **|bold|** → unwrap
  if (before.endsWith(mark) && after.startsWith(mark)) {
    _replaceRange(ta, start - mLen, end + mLen, sel);
    ta.selectionStart = start - mLen;
    ta.selectionEnd = end - mLen;
    return;
  }

  // Case 3: wrap — add markers. With no selection, insert empty markers and
  // drop the cursor between them (don't inject the action name as text).
  const inner = sel;
  const wrapped = mark + inner + mark;
  _replaceRange(ta, start, end, wrapped);
  ta.selectionStart = start + mLen;
  ta.selectionEnd = start + mLen + inner.length;
}

/** Toggle line prefix (headings, quotes, lists) */
// The block-level tag (h1/h2/h3/pre/p/…) containing the current selection in
// a contenteditable root — used to decide whether a heading toggle should
// apply or revert.
export function _currentBlockTag(root) {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return '';
  let node = sel.getRangeAt(0).startContainer;
  if (node.nodeType === 3) node = node.parentNode;
  while (node && node !== root) {
    const tag = node.tagName && node.tagName.toLowerCase();
    if (tag && /^(h1|h2|h3|h4|h5|h6|p|div|pre|blockquote|li)$/.test(tag)) return tag;
    node = node.parentNode;
  }
  return '';
}

// Heading toggle for the markdown textarea: strips any existing leading
// `#{1,6} `, then removes it (toggle off) if it was the same level, or applies
// the new level otherwise.
function _applyHeadingToggle(ta, caret, prefix) {
  const val = ta.value;
  const lineStart = val.lastIndexOf('\n', caret - 1) + 1;
  const nlIdx = val.indexOf('\n', caret);
  const lineEnd = nlIdx === -1 ? val.length : nlIdx;
  const line = val.substring(lineStart, lineEnd);
  const m = line.match(/^(#{1,6}) /);
  let newLine;
  if (m && m[1].length === prefix.trim().length) {
    newLine = line.slice(m[0].length);            // same level → toggle off
  } else if (m) {
    newLine = prefix + line.slice(m[0].length);   // different level → switch
  } else {
    newLine = prefix + line;                       // none → add
  }
  _replaceRange(ta, lineStart, lineEnd, newLine);
  const delta = newLine.length - line.length;
  const pos = Math.max(lineStart, caret + delta);
  ta.selectionStart = ta.selectionEnd = pos;
  ta.focus();
}

function _applyLinePrefixToggle(ta, start, end, prefix) {
  const val = ta.value;
  const sel = val.substring(start, end);
  const lineStart = val.lastIndexOf('\n', start - 1) + 1;

  if (sel) {
    // Multi-line: toggle prefix on each line
    const lines = sel.split('\n');
    const nonEmpty = lines.filter(l => l.trim());
    const allPrefixed = nonEmpty.length > 0 && nonEmpty.every(l => l.startsWith(prefix));
    const result = allPrefixed
      ? lines.map(l => l.startsWith(prefix) ? l.slice(prefix.length) : l).join('\n')
      : lines.map(l => l.trim() ? prefix + l : l).join('\n');
    _replaceRange(ta, start, end, result);
    ta.selectionStart = start;
    ta.selectionEnd = start + result.length;
  } else {
    // No selection: toggle prefix on the current line
    const lineBefore = val.substring(lineStart, start);

    if (lineBefore.startsWith(prefix)) {
      // Remove prefix
      _replaceRange(ta, lineStart, lineStart + prefix.length, '');
    } else {
      // Add prefix at line start
      _replaceRange(ta, lineStart, lineStart, prefix);
    }
  }
}

/** Toggle ordered list with incrementing numbers */
function _applyOrderedList(ta, start, end) {
  const val = ta.value;
  const sel = val.substring(start, end);
  const lineStart = val.lastIndexOf('\n', start - 1) + 1;

  if (sel) {
    const lines = sel.split('\n');
    const nonEmpty = lines.filter(l => l.trim());
    const allNumbered = nonEmpty.length > 0 && nonEmpty.every(l => /^\d+\.\s/.test(l));
    const result = allNumbered
      ? lines.map(l => l.replace(/^\d+\.\s/, '')).join('\n')
      : (() => { let n = 0; return lines.map(l => l.trim() ? `${++n}. ${l}` : l).join('\n'); })();
    _replaceRange(ta, start, end, result);
    ta.selectionStart = start;
    ta.selectionEnd = start + result.length;
  } else {
    const lineBefore = val.substring(lineStart, start);
    if (/^\d+\.\s/.test(lineBefore)) {
      const prefixLen = lineBefore.match(/^\d+\.\s/)[0].length;
      _replaceRange(ta, lineStart, lineStart + prefixLen, '');
    } else {
      // Find the previous numbered line to continue the sequence
      const prevText = val.substring(0, lineStart);
      const prevMatch = prevText.match(/(\d+)\.\s[^\n]*\n$/);
      const num = prevMatch ? parseInt(prevMatch[1]) + 1 : 1;
      _replaceRange(ta, lineStart, lineStart, `${num}. `);
    }
  }
}

/** Wire up the markdown formatting toolbar */
// Grouped formatting dropdown (headings / code / lists). Menu is appended to
// <body> so the draggable panel's transform can't clip its fixed position.
let _mdDdOpenedAt = 0;
function _showMdDropdown(toggleBtn) {
  const kind = toggleBtn.dataset.dd;
  const now = Date.now();
  const existing = document.getElementById('doc-md-dd-menu');
  // Mobile fires a duplicate/ghost click right after the real one. If it lands
  // on the same toggle it would re-toggle the menu shut the instant it opened.
  // Ignore a same-kind re-invocation within 400ms so the menu stays up.
  if (existing && existing.dataset.dd === kind && (now - _mdDdOpenedAt) < 400) return;
  const prevKind = existing && existing.dataset.dd;
  if (existing) existing.remove();
  if (existing && prevKind === kind) return; // same toggle clicked → just close
  _mdDdOpenedAt = now;

  const groups = {
    heading: [['h1', 'Heading 1', 'H1'], ['h2', 'Heading 2', 'H2'], ['h3', 'Heading 3', 'H3']],
    code: [['code', 'Inline code', '`'], ['codeblock', 'Code block', '```']],
    list: [['ul', 'Bullet list', '•'], ['ol', 'Numbered list', '1.']],
  };
  const items = groups[kind];
  if (!items) return;

  const rect = toggleBtn.getBoundingClientRect();
  const menu = document.createElement('div');
  menu.id = 'doc-md-dd-menu';
  menu.dataset.dd = kind;
  menu.className = 'doc-overflow-menu open';
  menu.style.position = 'fixed';
  menu.style.top = (rect.bottom + 4) + 'px';
  menu.style.left = rect.left + 'px';
  menu.style.zIndex = '9999';
  items.forEach(([md, label, ico]) => {
    const it = document.createElement('button');
    it.className = 'doc-overflow-item';
    const icoSpan = document.createElement('span');
    icoSpan.className = 'md-dd-ico';
    icoSpan.textContent = ico;
    const lbl = document.createElement('span');
    lbl.textContent = label;
    it.append(icoSpan, lbl);
    // Don't let the menu item steal focus from the editor (preserve selection).
    it.addEventListener('mousedown', (ev) => ev.preventDefault());
    it.addEventListener('click', (ev) => { ev.stopPropagation(); menu.remove(); applyMdFormat(md); });
    menu.appendChild(it);
  });
  document.body.appendChild(menu);

  const close = (ev) => {
    if (ev && ev.type === 'keydown') {
      if (ev.key !== 'Escape') return;
      ev.preventDefault();
      ev.stopPropagation();
      ev.stopImmediatePropagation?.();
    }
    if (ev && ev.type === 'click') {
      // Ignore the ghost/duplicate click mobile fires right after opening.
      if (Date.now() - _mdDdOpenedAt < 400) return;
      if (menu.contains(ev.target) || toggleBtn.contains(ev.target)) return;
    }
    menu.remove();
    document.removeEventListener('click', close, true);
    document.removeEventListener('keydown', close, true);
    window.removeEventListener('scroll', close, true);
    window.removeEventListener('resize', close, true);
  };
  setTimeout(() => {
    document.addEventListener('click', close, true);
    document.addEventListener('keydown', close, true);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close, true);
  }, 0);
}

export function initMdToolbar() {
  const toolbar = document.getElementById('doc-md-toolbar');
  if (!toolbar) return;

  const itemsWrap = document.getElementById('md-toolbar-items');
  const overflowWrapper = document.getElementById('md-toolbar-overflow-wrapper');
  const overflowToggle = document.getElementById('md-toolbar-overflow-toggle');
  const overflowMenu = document.getElementById('md-toolbar-overflow-menu');
  const undoBtn = document.getElementById('md-toolbar-undo');

  // Click handler for format buttons + the grouped dropdown toggles. The menu
  // is appended to <body> (not nested in the toolbar) so the draggable panel's
  // CSS transform doesn't reparent its fixed positioning or clip it.
  // Keep the editor's focus + selection when a format button / dropdown
  // toggle is pressed. Without this the button steals focus on press, which
  // collapses the textarea selection (so B/I/S apply to nothing) and, on
  // mobile, drops the keyboard — whose viewport resize then instantly closes
  // any dropdown that just opened. Preventing the default mousedown keeps the
  // textarea focused, so formatting hits the live selection and menus stay up.
  toolbar.addEventListener('mousedown', (e) => {
    if (e.target.closest('[data-md], .md-dd-toggle, .emoji-picker-btn, .md-toolbar-attach-btn')) e.preventDefault();
  });

  toolbar.addEventListener('click', (e) => {
    const dd = e.target.closest('.md-dd-toggle');
    if (dd) { e.preventDefault(); _showMdDropdown(dd); return; }
    const btn = e.target.closest('[data-md]');
    if (!btn) return;
    e.preventDefault();
    applyMdFormat(btn.dataset.md);
  });

  // Undo button
  if (undoBtn) {
    undoBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const ta = document.getElementById('doc-editor-textarea');
      if (ta) { ta.focus(); document.execCommand('undo'); }
    });
  }

  // Overflow collapse logic
  let _mdMenuOpen = false;
  // Horizontal-scroll affordance: the toolbar scrolls its icons; edge arrows
  // appear when there's more off either side and smoothly scroll to that edge.
  const scrollLeftBtn = document.getElementById('md-scroll-left');
  const scrollRightBtn = document.getElementById('md-scroll-right');
  function updateScrollArrows() {
    if (!itemsWrap || !scrollLeftBtn || !scrollRightBtn) return;
    const maxScroll = itemsWrap.scrollWidth - itemsWrap.clientWidth;
    const overflowing = maxScroll > 2;
    scrollLeftBtn.style.display = (overflowing && itemsWrap.scrollLeft > 1) ? 'flex' : 'none';
    scrollRightBtn.style.display = (overflowing && itemsWrap.scrollLeft < maxScroll - 1) ? 'flex' : 'none';
  }
  scrollLeftBtn?.addEventListener('click', () => itemsWrap.scrollTo({ left: 0, behavior: 'smooth' }));
  scrollRightBtn?.addEventListener('click', () => itemsWrap.scrollTo({ left: itemsWrap.scrollWidth, behavior: 'smooth' }));
  itemsWrap?.addEventListener('scroll', updateScrollArrows, { passive: true });
  if (itemsWrap) {
    let swipeStartX = 0;
    let swipeStartY = 0;
    let swipeStartScroll = 0;
    itemsWrap.addEventListener('touchstart', (e) => {
      const t = e.touches && e.touches[0];
      if (!t) return;
      swipeStartX = t.clientX;
      swipeStartY = t.clientY;
      swipeStartScroll = itemsWrap.scrollLeft;
    }, { passive: true });
    itemsWrap.addEventListener('touchend', (e) => {
      const t = e.changedTouches && e.changedTouches[0];
      if (!t) return;
      const dx = t.clientX - swipeStartX;
      const dy = t.clientY - swipeStartY;
      if (Math.abs(dx) < 42 || Math.abs(dx) < Math.abs(dy) * 1.4) return;
      const maxScroll = Math.max(0, itemsWrap.scrollWidth - itemsWrap.clientWidth);
      const page = Math.max(90, Math.round(itemsWrap.clientWidth * 0.75));
      const nextLeft = Math.max(0, Math.min(maxScroll, swipeStartScroll - Math.sign(dx) * page));
      itemsWrap.scrollTo({ left: nextLeft, behavior: 'smooth' });
    }, { passive: true });
  }
  if (window.ResizeObserver && itemsWrap) {
    new ResizeObserver(updateScrollArrows).observe(itemsWrap);
  }

  function syncMdOverflow() {
    if (overflowWrapper) overflowWrapper.style.display = 'none';
    updateScrollArrows();
  }

  function closeMdMenu() {
    _mdMenuOpen = false;
    if (overflowMenu) overflowMenu.classList.remove('open');
  }

  if (overflowToggle) {
    overflowToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      _mdMenuOpen = !_mdMenuOpen;
      if (_mdMenuOpen) {
        document.body.appendChild(overflowMenu);
        const rect = overflowToggle.getBoundingClientRect();
        overflowMenu.style.position = 'fixed';
        overflowMenu.style.top = (rect.bottom + 2) + 'px';
        overflowMenu.style.right = (window.innerWidth - rect.right) + 'px';
        overflowMenu.style.left = 'auto';
      } else {
        overflowWrapper.appendChild(overflowMenu);
      }
      overflowMenu.classList.toggle('open', _mdMenuOpen);
    });
  }
  document.addEventListener('click', () => {
    if (_mdMenuOpen) { closeMdMenu(); overflowWrapper.appendChild(overflowMenu); }
  });

  // Re-check overflow on resize
  let _mdResizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(_mdResizeTimer);
    _mdResizeTimer = setTimeout(syncMdOverflow, 100);
  });

  // Show toolbar if language is already markdown
  const lang = document.getElementById('doc-language-select')?.value;
  if (lang === 'markdown') toolbar.style.display = '';

  // Initial sync after layout
  requestAnimationFrame(syncMdOverflow);
  // Expose for external calls (e.g. after fullscreen toggle)
  toolbar._syncOverflow = syncMdOverflow;
}
