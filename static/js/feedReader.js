import uiModule from './ui.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';
import { snapModalToZone } from './tileManager.js';
import { clearDockSide } from './modalSnap.js';
import dragSortModule from './dragSort.js';

const API_BASE = window.location.origin;

const _DRAG_HANDLE_SVG = '<svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor"><circle cx="2" cy="2" r="1.3"/><circle cx="8" cy="2" r="1.3"/><circle cx="2" cy="7" r="1.3"/><circle cx="8" cy="7" r="1.3"/><circle cx="2" cy="12" r="1.3"/><circle cx="8" cy="12" r="1.3"/></svg>';

// Feed/group titles come from external RSS sources — escape before use in an
// HTML attribute (e.g. a title="" tooltip), since raw text content elsewhere
// in this file already goes through the DOM's own text-node escaping.
function _escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

let _open = false;
let _feeds = [];
let _groups = [];
let _articles = [];
let _totalArticles = 0;
let _activeFeedId = null;
let _activeGroupId = null;
let _activeArticleId = null;
let _filter = 'unread';
let _searchQuery = '';
let _pageOffset = 0;
let _articlesLoadingMore = false;
let _groupCollapsed = new Set();
let _selectedFeedIds = new Set();
let _selectMode = false;
let _groupSummary = '';
let _groupSummaryLoading = false;
// Snapshot of the article list taken when the reader opens, used for j/k and
// Prev/Next navigation. Opening an article marks it read, which triggers a
// background refresh of `_articles` (re-filtered if viewing "Unread") — if
// navigation read from `_articles` directly, the list could reshuffle out
// from under an in-progress reading session and "next" would jump to an
// unrelated article.
let _readerNavList = [];
const PAGE_LIMIT = 50;

function _api(path, opts = {}) {
  return fetch(`${API_BASE}/api/feeds${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  }).then(r => r.json());
}

function _el(id) { return document.getElementById(id); }

function _clearRssSnapStyles(pane) {
  if (!pane) return;
  const hadLeft = pane.classList.contains('modal-left-docked');
  const hadRight = pane.classList.contains('modal-right-docked');
  pane.classList.remove('rss-fullscreen', 'modal-left-docked', 'modal-right-docked');
  if (hadLeft) clearDockSide('left', pane);
  if (hadRight) clearDockSide('right', pane);
  ['position', 'left', 'top', 'right', 'bottom', 'width', 'max-width', 'height',
    'max-height', 'margin', 'transform', 'border-radius']
    .forEach(prop => pane.style.removeProperty(prop));
  delete pane.dataset._tilePreSnap;
  delete pane.dataset._tileZone;
  delete pane._preDockSnapshot;
  delete pane._dockSide;
  delete pane._dockSuspended;
}

function _wireRssWindow(pane) {
  if (!pane || pane.dataset.windowDragWired === '1') return;
  const header = pane.querySelector('.rss-pane-header');
  if (!header) return;
  pane.dataset.windowDragWired = '1';
  makeWindowDraggable(pane, {
    content: pane,
    header,
    fsClass: 'rss-fullscreen',
    skipSelector: 'button, input, select, textarea, label',
    enableDock: true,
    enableLeftDock: true,
    onEnterFullscreen: () => {
      pane.classList.add('rss-fullscreen');
      snapModalToZone(pane, {
        name: 'fullscreen',
        rect: { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight },
      });
    },
    onExitFullscreen: (cx, cy) => {
      _clearRssSnapStyles(pane);
      if (typeof cx === 'number' && typeof cy === 'number') {
        const w = Math.min(880, window.innerWidth * 0.92);
        const h = Math.min(window.innerHeight * 0.85, 820);
        pane.style.position = 'fixed';
        pane.style.left = Math.max(8, cx - w / 2) + 'px';
        pane.style.top = Math.max(8, cy - 30) + 'px';
        pane.style.width = w + 'px';
        pane.style.height = h + 'px';
        pane.style.transform = 'none';
        pane.style.margin = '0';
      }
    },
  });
}

function _openPanel() {
  if (_open) return;
  _open = true;
  const btn = _el('tool-rss-btn');
  if (btn) btn.classList.add('active');

  const backdrop = document.createElement('div');
  backdrop.className = 'rss-pane-backdrop';
  backdrop.id = 'rss-pane-backdrop';
  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) closePanel();
  });

  const pane = document.createElement('div');
  pane.id = 'rss-pane';
  pane.className = 'rss-pane';
  pane.innerHTML = `
    <div class="rss-pane-header">
      <h4 class="rss-pane-title">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2.5px;margin-right:6px">
          <path d="M4 11a9 9 0 0 1 9 9"/>
          <path d="M4 4a16 16 0 0 1 16 16"/>
          <circle cx="5" cy="19" r="1"/>
        </svg>
        Feeds
      </h4>
      <span style="flex:1"></span>
      <button id="rss-refresh-all-btn" class="doc-action-icon-btn" title="Refresh all feeds" style="opacity:0.8;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
      </button>
      <button id="rss-add-feed-btn" class="doc-action-icon-btn" title="Add feed" style="opacity:0.8;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      </button>
      <button id="rss-opml-btn" class="doc-action-icon-btn" title="Import/Export OPML" style="opacity:0.8;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      </button>
      <button id="rss-close-btn" class="doc-action-icon-btn" title="Close RSS" style="opacity:0.8;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="rss-body">
      <div class="rss-sidebar">
        <div class="rss-sidebar-header">
          <input type="text" id="rss-search" class="memory-search-input" placeholder="Search articles…" autocomplete="off" />
          <div class="rss-filter-bar">
            <button class="rss-filter-btn active" data-filter="unread">Unread</button>
            <button class="rss-filter-btn" data-filter="all">All</button>
            <button class="rss-filter-btn" data-filter="starred">Starred</button>
          </div>
          <div class="rss-groups-header">
            <span>Groups</span>
            <button id="rss-select-mode-btn" class="rss-select-mode-btn" title="Select multiple feeds"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg><span>Select</span></button>
            <button id="rss-add-group-btn" class="rss-groups-add" title="Add group">+</button>
          </div>
        </div>
        <div class="rss-batch-bar" id="rss-batch-bar" style="display:none"></div>
        <div class="rss-feed-list" id="rss-feed-list"></div>
      </div>
      <div class="rss-main">
        <div class="rss-article-list" id="rss-article-list">
          <div class="rss-empty-state">Select a feed to view articles</div>
        </div>
        <div class="rss-reader" id="rss-reader" style="display:none">
          <div class="rss-reader-toolbar">
            <button id="rss-reader-back" class="doc-action-icon-btn" title="Back to list">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
            </button>
            <button id="rss-reader-prev" class="doc-action-icon-btn" title="Previous article (k)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
            </button>
            <button id="rss-reader-next" class="doc-action-icon-btn" title="Next article (j)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </button>
            <a id="rss-reader-original" href="#" target="_blank" class="doc-action-icon-btn" title="Open original" style="text-decoration:none">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
            </a>
            <span class="rss-toolbar-sep" aria-hidden="true"></span>
            <button id="rss-reader-star" class="doc-action-icon-btn" title="Toggle star (s)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
            </button>
            <button id="rss-reader-tts" class="doc-action-icon-btn" title="Read aloud" style="display:none">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>
            </button>
            <button id="rss-reader-summarize" class="doc-action-icon-btn" title="AI Summary" style="opacity:0.8;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3L12 3Z"/></svg>
            </button>
            <button id="rss-reader-full" class="doc-action-icon-btn" title="Fetch full content" style="opacity:0.8;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            </button>
            <span style="flex:1"></span>
            <button id="rss-reader-mark-read" class="doc-action-icon-btn" title="Mark read (m)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>
            </button>
          </div>
          <div class="rss-reader-content" id="rss-reader-content"></div>
        </div>
      </div>
    </div>
  `;

  backdrop.appendChild(pane);
  document.body.appendChild(backdrop);
  _wireRssWindow(pane);
  _wireEvents(pane);
  _loadFeeds();

  if (!Modals.isRegistered('rss-pane')) {
    Modals.register('rss-pane', {
      label: 'Feeds',
      icon: '<path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
      closeFn: closePanel,
      restoreFn: () => _openPanel(),
    });
  }
}

function _wireEvents(pane) {
  _el('rss-add-feed-btn')?.addEventListener('click', _showAddFeedModal);
  _el('rss-refresh-all-btn')?.addEventListener('click', _refreshAll);
  _el('rss-opml-btn')?.addEventListener('click', _showOpmlModal);
  _el('rss-add-group-btn')?.addEventListener('click', () => _promptCreateGroup(null));
  _el('rss-close-btn')?.addEventListener('click', (e) => {
    e.preventDefault();
    closePanel();
  });
  _el('rss-reader-back')?.addEventListener('click', _closeReader);
  _el('rss-reader-prev')?.addEventListener('click', _prevArticle);
  _el('rss-reader-next')?.addEventListener('click', _nextArticle);
  _el('rss-reader-star')?.addEventListener('click', _toggleReaderStar);
  _el('rss-reader-mark-read')?.addEventListener('click', _markReaderRead);
  _el('rss-reader-summarize')?.addEventListener('click', _summarizeReaderArticle);
  _el('rss-reader-full')?.addEventListener('click', _fetchReaderFullContent);
  _el('rss-reader-original')?.addEventListener('click', (e) => {
    const a = _el('rss-reader-original');
    if (a && a.getAttribute('href') && a.getAttribute('href') !== '#') {
      window.open(a.href, '_blank');
    }
    e.preventDefault();
  });
  _el('rss-search')?.addEventListener('input', (e) => {
    _searchQuery = e.target.value.trim();
    _pageOffset = 0;
    _articlesLoadingMore = false;
    _loadArticles();
  });
  _el('rss-reader-tts')?.addEventListener('click', _playTTS);

  _el('rss-article-list')?.addEventListener('click', _onArticleListClick);
  _el('rss-article-list')?.addEventListener('scroll', () => {
    const list = _el('rss-article-list');
    if (!list || _articlesLoadingMore || _articles.length >= _totalArticles) return;
    if (list.scrollTop + list.clientHeight < list.scrollHeight - 300) return;
    _articlesLoadingMore = true;
    _loadArticles(true).finally(() => { _articlesLoadingMore = false; });
  }, { passive: true });

  pane.querySelectorAll('.rss-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      pane.querySelectorAll('.rss-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _filter = btn.dataset.filter;
      _pageOffset = 0;
      _articlesLoadingMore = false;
      _loadArticles();
    });
  });

  _el('rss-select-mode-btn')?.addEventListener('click', () => {
    _selectMode = !_selectMode;
    _el('rss-select-mode-btn')?.classList.toggle('active', _selectMode);
    if (!_selectMode) {
      _selectedFeedIds.clear();
      _updateBatchBar();
    }
    _renderFeedList();
  });

  _el('rss-reader-content')?.addEventListener('click', (e) => {
    const player = e.target.closest('.rss-video-player');
    if (player) {
      _openVideoModal(player.dataset.videoId, player.dataset.videoUrl || player.dataset.videoId);
    }
  });

  document.addEventListener('click', (e) => {
    if (e.target.closest('#rss-video-modal-close') || e.target === _el('rss-video-modal')) {
      _closeVideoModal();
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') _closeVideoModal();
    if (!_open) return;
    const tag = (e.target?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || e.target?.isContentEditable) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    switch (e.key) {
      case 'j':
        e.preventDefault();
        _nextArticle();
        break;
      case 'k':
        e.preventDefault();
        _prevArticle();
        break;
      case 'm':
        if (_activeArticleId) { e.preventDefault(); _markReaderRead(); }
        break;
      case 's':
        if (_activeArticleId) { e.preventDefault(); _toggleReaderStar(); }
        break;
    }
  });
}

function closePanel() {
  _open = false;
  _el('tool-rss-btn')?.classList.remove('active');
  const backdrop = _el('rss-pane-backdrop');
  if (backdrop) backdrop.remove();
  try { Modals.unregister('rss-pane'); } catch {}
}

function togglePanel() {
  if (_open) { closePanel(); return; }
  _openPanel();
}

async function _loadFeeds() {
  const [feedsRes, groupsRes] = await Promise.all([
    _api(''),
    _api('/groups'),
  ]);
  _feeds = feedsRes.feeds || [];
  _groups = groupsRes.groups || [];
  _selectedFeedIds.clear();
  _groupSummary = '';
  _updateBatchBar();
  _renderFeedList();
  // Auto-load the current selection's articles (All Feeds by default) so the
  // reader pane is never empty on open.
  _loadArticles();
}

function _renderFeedList() {
  const list = _el('rss-feed-list');
  if (!list) return;
  const ungroupedFeeds = _feeds.filter(f => !f.group_id);
  const tree = _buildGroupTree();

  let html = '';
  const totalUnread = _feeds.reduce((s, f) => s + (f.unread || 0), 0);
  html += `<div class="rss-feed-item ${!_activeFeedId && !_activeGroupId ? 'active' : ''}" data-feed-id="" data-group-id="">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/></svg>
    <span class="rss-feed-name">All Feeds</span>
    ${totalUnread > 0 ? `<span class="rss-unread-badge">${totalUnread}</span>` : ''}
  </div>`;

  for (const node of tree) {
    html += _renderGroupTreeNode(node, 0);
  }

  if (ungroupedFeeds.length > 0) {
    // Synthetic divider (not a real group) so there's an explicit boundary
    // between the last real group's feeds and these — both for the user
    // (a visible drop target for "ungroup this feed") and for
    // _onFeedListReordered's group-inference walk, which would otherwise
    // attribute these to whatever group happened to render last.
    html += `<div class="rss-group-header rss-group-root rss-group-header-ungrouped" data-group-id="">
      <span class="rss-group-toggle"></span>
      <span class="rss-group-name">Ungrouped</span>
    </div>`;
    for (const f of ungroupedFeeds) {
      html += _feedItemHtml(f);
    }
  }

  list.innerHTML = html;

  list.querySelectorAll('.rss-feed-item').forEach(el => {
    el.addEventListener('click', (e) => {
      const cb = el.querySelector('.rss-feed-checkbox');
      if (_selectMode || (cb && (e.target === cb || cb.contains(e.target)))) {
        const fid = el.dataset.feedId;
        if (!fid) return;
        if (_selectedFeedIds.has(fid)) {
          _selectedFeedIds.delete(fid);
          if (cb) cb.checked = false;
        } else {
          _selectedFeedIds.add(fid);
          if (cb) cb.checked = true;
        }
        _updateBatchBar();
        return;
      }
      const delBtn = el.querySelector('.rss-feed-delete');
      if (delBtn && (e.target === delBtn || delBtn.contains(e.target))) {
        return; // handled separately
      }
      const intervalBtn = el.querySelector('.rss-feed-interval');
      if (intervalBtn && (e.target === intervalBtn || intervalBtn.contains(e.target))) {
        return; // handled separately
      }
      list.querySelectorAll('.rss-feed-item').forEach(e => e.classList.remove('active'));
      list.querySelectorAll('.rss-group-header').forEach(e => e.classList.remove('active'));
      el.classList.add('active');
      _activeFeedId = el.dataset.feedId || null;
      _activeGroupId = el.dataset.groupId || null;
      _groupSummary = '';
      _pageOffset = 0;
      _articlesLoadingMore = false;
      _closeReader();
      _loadArticles();
    });
  });

  list.querySelectorAll('.rss-feed-delete').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const feedId = btn.closest('.rss-feed-item').dataset.feedId;
      if (feedId && confirm('Delete this feed and all its articles?')) {
        _api('/' + feedId, { method: 'DELETE' }).then(res => {
          if (res.ok) {
            if (_activeFeedId === feedId) {
              _activeFeedId = null;
              _closeReader();
            }
            _loadFeeds();
          }
        });
      }
    });
  });

  list.querySelectorAll('.rss-feed-interval').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const feedId = btn.closest('.rss-feed-item').dataset.feedId;
      const feed = _feeds.find(f => f.id === feedId);
      if (!feed) return;
      const input = prompt('Refresh interval in minutes:', String(feed.fetch_interval || 60));
      if (input === null) return;
      const n = parseInt(input, 10);
      if (!Number.isInteger(n) || n <= 0) {
        uiModule.showError('Enter a positive whole number of minutes');
        return;
      }
      _api(`/${feedId}`, { method: 'PUT', body: JSON.stringify({ fetch_interval: n }) }).then(res => {
        if (res.ok) {
          _loadFeeds();
        } else {
          uiModule.showError(res.error || 'Failed to update refresh interval');
        }
      });
    });
  });

  list.querySelectorAll('.rss-group-header').forEach(el => {
    let clickTimer = null;

    el.addEventListener('click', (e) => {
      const target = e.target;
      const addSubBtn = el.querySelector('.rss-group-add-sub');
      const renameBtn = el.querySelector('.rss-group-rename');
      const deleteBtn = el.querySelector('.rss-group-delete');

      if (addSubBtn && (target === addSubBtn || addSubBtn.contains(target))) {
        e.stopPropagation();
        _promptCreateGroup(el.dataset.groupId);
        return;
      }
      if (renameBtn && (target === renameBtn || renameBtn.contains(target))) {
        e.stopPropagation();
        const gid = el.dataset.groupId;
        const g = _groups.find(x => x.id === gid);
        if (g) _promptRenameGroup(g);
        return;
      }
      if (deleteBtn && (target === deleteBtn || deleteBtn.contains(target))) {
        e.stopPropagation();
        const gid = el.dataset.groupId;
        const g = _groups.find(x => x.id === gid);
        if (g) _promptDeleteGroup(g);
        return;
      }

      if (clickTimer) {
        clearTimeout(clickTimer);
        clickTimer = null;
        return;
      }

      clickTimer = setTimeout(() => {
        clickTimer = null;
        const gid = el.dataset.groupId;
        if (_groupCollapsed.has(gid)) {
          _groupCollapsed.delete(gid);
        } else {
          _groupCollapsed.add(gid);
        }
        _renderFeedList();
        if (_activeGroupId) {
          const activeEl = list.querySelector(`.rss-group-header[data-group-id="${_activeGroupId}"]`);
          if (activeEl) activeEl.classList.add('active');
        }
      }, 250);
    });

    el.addEventListener('dblclick', (e) => {
      if (clickTimer) {
        clearTimeout(clickTimer);
        clickTimer = null;
      }
      list.querySelectorAll('.rss-feed-item').forEach(item => item.classList.remove('active'));
      list.querySelectorAll('.rss-group-header').forEach(h => h.classList.remove('active'));
      el.classList.add('active');
      _activeFeedId = null;
      _activeGroupId = el.dataset.groupId || null;
      _groupSummary = '';
      _pageOffset = 0;
      _articlesLoadingMore = false;
      _closeReader();
      _loadArticles();
    });
  });

  // Drag-to-reorder + drag-onto-a-group-to-move: the whole list is one flat
  // container (group headers and feed items interleaved by render order, not
  // real DOM nesting — see _renderGroupTreeNode/_buildGroupTree), so dragSort's
  // existing vertical magnetic reorder already handles both in one gesture —
  // wherever a feed is dropped, _onFeedListReordered infers its new group from
  // the nearest preceding group header in the final DOM order.
  dragSortModule.enable('rss-feed-list', '.rss-feed-item', {
    handleSelector: '.rss-feed-drag-handle',
    excludeSelector: '[data-feed-id=""]',
    onReorder: _onFeedListReordered,
  });

  // A collapsed group renders only its header, no .rss-feed-item rows — since
  // dragSort's placeholder-positioning only compares against other feed items
  // (headers aren't part of that math), a collapsed group has no droppable
  // space to land in. dragSort assumes a stable DOM for one drag gesture (it
  // captures draggedEl/placeholder/items at drag-start), so re-rendering to
  // expand groups can't happen mid-drag without orphaning its refs — instead,
  // intercept in the CAPTURE phase, before dragSort's own bubble-phase
  // mousedown listener on this same container gets to run at all: if
  // anything is collapsed, expand everything and stopImmediatePropagation so
  // this click doesn't also start a drag. The user needs a second press to
  // actually drag, now against the expanded list. #rss-feed-list itself
  // persists across re-renders (only its innerHTML is replaced), so this
  // guard prevents re-adding the listener on every _renderFeedList() call —
  // unlike dragSortModule.enable() above, a plain addEventListener has no
  // built-in re-registration cleanup.
  if (!list.dataset.collapseDragWired) {
    list.dataset.collapseDragWired = '1';
    list.addEventListener('mousedown', (e) => {
      if (!e.target.closest('.rss-feed-drag-handle')) return;
      if (_groupCollapsed.size === 0) return;
      e.stopImmediatePropagation();
      _groupCollapsed.clear();
      _renderFeedList();
    }, { capture: true });
  }
}

async function _onFeedListReordered() {
  const list = _el('rss-feed-list');
  if (!list) return;
  // Walk the FULL list (not just the .rss-feed-item items dragSort passes
  // in) so group headers can be used to infer which group each feed landed
  // in — a feed's group membership is determined purely by its final
  // position relative to the nearest preceding header, the same section a
  // user visually sees it drop into.
  let currentGroupId = null;
  let indexInGroup = 0;
  const changes = [];
  for (const child of Array.from(list.children)) {
    if (child.classList.contains('rss-group-header')) {
      currentGroupId = child.dataset.groupId || null;
      indexInGroup = 0;
      continue;
    }
    if (child.classList.contains('rss-feed-item')) {
      const feedId = child.dataset.feedId;
      if (!feedId) continue; // "All Feeds" pseudo-item, never draggable
      changes.push({ feedId, group_id: currentGroupId, sort_order: indexInGroup });
      indexInGroup++;
    }
  }

  let ok = 0, attempted = 0;
  for (const c of changes) {
    const f = _feeds.find(x => x.id === c.feedId);
    if (!f) continue;
    const groupChanged = (f.group_id || null) !== c.group_id;
    const orderChanged = (f.sort_order || 0) !== c.sort_order;
    if (!groupChanged && !orderChanged) continue;
    attempted++;
    const res = await _api(`/${c.feedId}`, {
      method: 'PUT',
      body: JSON.stringify({ group_id: c.group_id, sort_order: c.sort_order }),
    });
    if (res.ok) ok++;
  }
  if (attempted > 0 && ok < attempted) {
    uiModule.showError(`Reordered ${ok}/${attempted} feeds — some failed to save`);
  }
  if (attempted > 0) {
    _loadFeeds();
  }
}

function _promptCreateGroup(parentId) {
  const parent = _groups.find(g => g.id === parentId);
  const hint = parent ? ` under "${parent.name}"` : '';
  const name = prompt(`Enter group name${hint}:`);
  if (!name || !name.trim()) return;
  _api('/groups', {
    method: 'POST',
    body: JSON.stringify({ name: name.trim(), parent_id: parentId || null }),
  }).then(res => {
    if (res.ok) {
      _loadFeeds();
    } else {
      uiModule.showError(res.error || 'Failed to create group');
    }
  });
}

function _promptRenameGroup(group) {
  const name = prompt('Rename group:', group.name);
  if (!name || !name.trim() || name.trim() === group.name) return;
  _api(`/groups/${group.id}`, {
    method: 'PUT',
    body: JSON.stringify({ name: name.trim() }),
  }).then(res => {
    if (res.ok) {
      _loadFeeds();
    } else {
      uiModule.showError(res.error || 'Failed to rename group');
    }
  });
}

function _promptDeleteGroup(group) {
  if (!confirm(`Delete group "${group.name}"? Its feeds and sub-groups will move up to the parent group, not be deleted.`)) return;
  _api(`/groups/${group.id}`, { method: 'DELETE' }).then(res => {
    if (res.ok) {
      if (_activeGroupId === group.id) _activeGroupId = null;
      _loadFeeds();
    } else {
      uiModule.showError(res.error || 'Failed to delete group');
    }
  });
}

function _updateBatchBar() {
  const bar = _el('rss-batch-bar');
  if (!bar) return;
  const count = _selectedFeedIds.size;
  if (count === 0) {
    bar.style.display = 'none';
    return;
  }
  const groupOptions = _groups.map(g => `<option value="${g.id}">${g.name}</option>`).join('');
  bar.style.display = 'flex';
  bar.innerHTML = `
    <span style="font-size:11px;opacity:0.7;flex-shrink:0">${count} selected</span>
    <select id="rss-batch-group" style="flex:1;font-size:11px;min-width:0">
      <option value="">Move to group…</option>
      <option value="__none__">(no group)</option>
      ${groupOptions}
    </select>
    <button id="rss-batch-move" class="secondary-btn" style="font-size:10px;padding:2px 6px">Move</button>
    <button id="rss-batch-delete" class="secondary-btn" style="font-size:10px;padding:2px 6px;color:var(--red)">Delete</button>
  `;
  _el('rss-batch-move')?.addEventListener('click', _batchMoveToGroup);
  _el('rss-batch-delete')?.addEventListener('click', _batchDelete);
}

async function _batchMoveToGroup() {
  const sel = _el('rss-batch-group');
  const targetGroupId = sel?.value;
  if (!targetGroupId) return;
  const gid = targetGroupId === '__none__' ? null : targetGroupId;
  const ids = [..._selectedFeedIds];
  _selectedFeedIds.clear();
  _updateBatchBar();
  let ok = 0;
  for (const fid of ids) {
    const res = await _api(`/${fid}`, {
      method: 'PUT',
      body: JSON.stringify({ group_id: gid }),
    });
    if (res.ok) ok++;
  }
  uiModule.showToast(`Moved ${ok}/${ids.length} feeds`);
  _loadFeeds();
}

async function _batchDelete() {
  const count = _selectedFeedIds.size;
  if (!confirm(`Delete ${count} feed${count > 1 ? 's' : ''} and all their articles?`)) return;
  const ids = [..._selectedFeedIds];
  _selectedFeedIds.clear();
  _updateBatchBar();
  let ok = 0;
  for (const fid of ids) {
    if (_activeFeedId === fid) {
      _activeFeedId = null;
      _closeReader();
    }
    const res = await _api(`/${fid}`, { method: 'DELETE' });
    if (res.ok) ok++;
  }
  uiModule.showToast(`Deleted ${ok}/${count} feeds`);
  _loadFeeds();
}

function _renderCheckbox(feedId) {
  if (!_selectMode) return '';
  const checked = _selectedFeedIds.has(feedId) ? 'checked' : '';
  return `<input type="checkbox" class="rss-feed-checkbox" ${checked} />`;
}

// Favicon guesses (feed.image href, or a guessed /favicon.ico) can 404 —
// onerror swaps to the letter-avatar fallback instead of a broken image icon.
// Uses textContent (not innerHTML) so the initial is inserted safely.
function _feedIconHtml(icon, title) {
  const initial = (title || '?')[0].toUpperCase();
  if (!icon) return initial;
  return `<img src="${icon}" alt="" width="14" height="14" onerror="this.parentElement.textContent=${JSON.stringify(initial)};" />`;
}

function _feedItemHtml(f) {
  let icon = f.icon || '';
  if (!icon && f.site_url && f.site_url.includes('youtube.com')) {
    icon = 'https://www.youtube.com/favicon.ico';
  }
  return `<div class="rss-feed-item ${_activeFeedId === f.id ? 'active' : ''}" data-feed-id="${f.id}" data-group-id="">
    <span class="rss-feed-drag-handle" title="Drag to reorder or move to a group">${_DRAG_HANDLE_SVG}</span>
    ${_renderCheckbox(f.id)}
    <span class="rss-feed-icon">${_feedIconHtml(icon, f.title)}</span>
    <span class="rss-feed-name" title="${_escapeHtml(f.title || 'Untitled')}">${f.title || 'Untitled'}</span>
    ${(f.unread || 0) > 0 ? `<span class="rss-unread-badge">${f.unread}</span>` : ''}
    <button class="rss-feed-interval" title="Set refresh interval">⏱</button>
    <button class="rss-feed-delete" title="Delete feed">&times;</button>
  </div>`;
}

function _buildGroupTree() {
  const map = {};
  const roots = [];
  for (const g of _groups) {
    map[g.id] = { ...g, children: [], feeds: [] };
  }
  for (const g of _groups) {
    if (g.parent_id && map[g.parent_id]) {
      map[g.parent_id].children.push(map[g.id]);
    } else if (!g.parent_id) {
      roots.push(map[g.id]);
    }
  }
  for (const f of _feeds) {
    if (f.group_id && map[f.group_id]) {
      map[f.group_id].feeds.push(f);
    }
  }
  return roots;
}

function _getDescendantGroupIds(groupId) {
  const ids = [groupId];
  const collect = (parentId) => {
    for (const g of _groups) {
      if (g.parent_id === parentId && !ids.includes(g.id)) {
        ids.push(g.id);
        collect(g.id);
      }
    }
  };
  collect(groupId);
  return ids;
}

function _groupUnread(groupNode) {
  let total = 0;
  for (const f of groupNode.feeds) total += f.unread || 0;
  for (const c of groupNode.children) total += _groupUnread(c);
  return total;
}

function _renderGroupTreeNode(node, depth) {
  const isCollapsed = _groupCollapsed.has(node.id);
  // A group's single-click toggle collapses whatever is nested under it —
  // sub-groups AND/OR feeds — not just sub-groups, so the chevron needs to
  // show whenever either is present. Previously only checked node.children
  // (sub-groups), leaving groups with feeds-but-no-sub-groups (the common
  // case) with no visible collapse affordance at all.
  const hasChildren = node.children.length > 0 || node.feeds.length > 0;
  const gUnread = _groupUnread(node);
  const toggleIcon = hasChildren ? (isCollapsed ? '▶' : '▼') : '';
  const indent = depth * 16;
  const isActive = _activeGroupId === node.id && !_activeFeedId;
  let html = `<div class="rss-group-header ${depth === 0 ? 'rss-group-root' : ''} ${isActive ? 'active' : ''}" data-group-id="${node.id}" style="padding-left:${10 + indent}px">
    ${hasChildren ? `<span class="rss-group-toggle">${toggleIcon}</span>` : '<span class="rss-group-toggle"></span>'}
    <span class="rss-group-name" title="${_escapeHtml(node.name)}">${node.name}</span>
    ${gUnread > 0 ? `<span class="rss-unread-badge">${gUnread}</span>` : ''}
    <span class="rss-group-actions">
      <button class="rss-group-add-sub" title="New sub-group">+</button>
      <button class="rss-group-rename" title="Rename group">✎</button>
      <button class="rss-group-delete" title="Delete group">&times;</button>
    </span>
  </div>`;
  if (!isCollapsed) {
    for (const f of node.feeds) {
      let icon = f.icon || '';
      if (!icon && f.site_url && f.site_url.includes('youtube.com')) {
        icon = 'https://www.youtube.com/favicon.ico';
      }
      html += `<div class="rss-feed-item ${_activeFeedId === f.id ? 'active' : ''}" data-feed-id="${f.id}" data-group-id="${node.id}" style="padding-left:${24 + indent}px">
        <span class="rss-feed-drag-handle" title="Drag to reorder or move to a group">${_DRAG_HANDLE_SVG}</span>
        ${_renderCheckbox(f.id)}
        <span class="rss-feed-icon">${_feedIconHtml(icon, f.title)}</span>
        <span class="rss-feed-name" title="${_escapeHtml(f.title || 'Untitled')}">${f.title || 'Untitled'}</span>
        ${(f.unread || 0) > 0 ? `<span class="rss-unread-badge">${f.unread}</span>` : ''}
        <button class="rss-feed-interval" title="Set refresh interval">⏱</button>
        <button class="rss-feed-delete" title="Delete feed">&times;</button>
      </div>`;
    }
    for (const child of node.children) {
      html += _renderGroupTreeNode(child, depth + 1);
    }
  }
  return html;
}

async function _loadArticles(append = false) {
  if (append) {
    _pageOffset += PAGE_LIMIT;
  } else {
    _pageOffset = 0;
  }
  const params = new URLSearchParams();
  params.set('limit', String(PAGE_LIMIT));
  params.set('offset', String(_pageOffset));
  if (_activeFeedId) params.set('feed_id', _activeFeedId);
  if (_activeGroupId) {
    // Collect all descendant group IDs for nested groups
    const descendantIds = _getDescendantGroupIds(_activeGroupId);
    if (descendantIds.length > 1) {
      params.set('group_ids', descendantIds.join(','));
    } else {
      params.set('group_id', _activeGroupId);
    }
  }
  if (_filter === 'unread') params.set('read', 'false');
  if (_filter === 'starred') params.set('starred', 'true');
  if (_searchQuery) params.set('search', _searchQuery);

  const res = await _api(`/articles?${params.toString()}`);
  const newBatch = res.articles || [];
  _articles = append ? _articles.concat(newBatch) : newBatch;
  _totalArticles = res.total || 0;
  _renderArticleList(newBatch, append);
}

function _articleItemHtml(a) {
  const feedTitle = a.feed?.title || '';
  const feedIcon = a.feed?.icon || '';
  const time = a.published_at ? _formatTime(a.published_at) : '';
  const rawSnippet = a.content ? a.content.replace(/<[^>]+>/g, '').trim().slice(0, 150) : '';
  const isVideo = !!(_getYoutubeVideoId(a.url) || _getYoutubeVideoId(a.guid));
  // feedparser leaves content/summary empty for a lot of YouTube entries
  // (mostly Shorts) — an empty snippet div left a dead blank line in the
  // list. Show a clear placeholder for videos, omit the row entirely
  // otherwise so non-video articles without a snippet don't get one either.
  const snippetHtml = rawSnippet
    ? `<div class="rss-article-snippet">${rawSnippet}</div>`
    : (isVideo ? '<div class="rss-article-snippet rss-article-snippet-empty">▶ No description available</div>' : '');
  return `<div class="rss-article-item ${a.is_read ? 'rss-article-read' : ''} ${_activeArticleId === a.id ? 'active' : ''}" data-article-id="${a.id}">
      ${a.image ? `<div class="rss-article-thumb" style="background-image: url('${a.image}')"></div>` : ''}
      <div class="rss-article-body">
        <div class="rss-article-title">${a.title || 'Untitled'}</div>
        <div class="rss-article-meta">
          <span class="rss-article-feed">${feedIcon ? `<img src="${feedIcon}" alt="" width="10" height="10" />` : ''} ${feedTitle}</span>
          ${time ? `<span class="rss-article-time">${time}</span>` : ''}
          ${a.is_starred ? '<span class="rss-starred-indicator">★</span>' : ''}
        </div>
        ${snippetHtml}
      </div>
    </div>`;
}

// Article click handling uses a single delegated listener (wired once in
// _wireEvents, not per-render) rather than per-item addEventListener — with
// infinite scroll appending batches into the same list, re-querying and
// re-attaching a fresh listener to every item on every render would pile up
// duplicate listeners on items already in the DOM from earlier pages.
function _onArticleListClick(e) {
  const list = _el('rss-article-list');
  const el = e.target.closest('.rss-article-item');
  if (!list || !el || !list.contains(el)) return;
  list.querySelectorAll('.rss-article-item').forEach(item => item.classList.remove('active'));
  el.classList.add('active');
  _activeArticleId = el.dataset.articleId;
  _readerNavList = _articles.slice();
  _openReader(_activeArticleId);
}

function _renderArticleList(newArticles = _articles, append = false) {
  const list = _el('rss-article-list');
  if (!list) return;
  if (!append && newArticles.length === 0) {
    list.innerHTML = `<div class="rss-empty-state">No articles found</div>`;
    return;
  }
  let html = '';
  if (!append && _activeGroupId && !_activeFeedId) {
    const group = _groups.find(g => g.id === _activeGroupId);
    html += `<div class="rss-summarize-bar">
      <span style="font-size:10px;opacity:0.6">${group ? group.name : 'Group'}</span>
      <span style="flex:1"></span>
      <button class="rss-summarize-btn" id="rss-summarize-group-btn" data-group-id="${_activeGroupId}">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3L12 3Z"/></svg>
        AI Summary
      </button>
    </div>`;
    if (_groupSummary) {
      html += `<div class="rss-summary-panel">
        <h4>Group Summary</h4>
        <p>${_groupSummary}</p>
      </div>`;
    } else if (_groupSummaryLoading) {
      html += `<div class="rss-summary-panel">
        <h4>Generating summary…</h4>
        <p style="opacity:0.5">Processing articles…</p>
      </div>`;
    }
  }
  for (const a of newArticles) {
    html += _articleItemHtml(a);
  }

  if (append) {
    list.insertAdjacentHTML('beforeend', html);
    return;
  }

  list.innerHTML = html;
  const summarizeBtn = _el('rss-summarize-group-btn');
  if (summarizeBtn) {
    summarizeBtn.addEventListener('click', _summarizeGroup);
  }
}

async function _summarizeGroup() {
  if (!_activeGroupId || _groupSummaryLoading) return;
  _groupSummaryLoading = true;
  _groupSummary = '';
  _renderArticleList();
  try {
    const res = await _api(`/groups/${_activeGroupId}/summarize`, { method: 'POST' });
    _groupSummary = (res.ok && res.summary) ? res.summary : 'Failed to generate summary.';
  } catch (e) {
    _groupSummary = 'Failed to generate summary: could not reach the server.';
  } finally {
    _groupSummaryLoading = false;
    _renderArticleList();
  }
}

function _openReader(articleId) {
  // Prefer the frozen nav-list snapshot so a background list refresh
  // (triggered by marking an article read) can't swap the article out from
  // under an in-progress reading session; fall back to the live list for
  // safety if the reader was somehow opened without a snapshot.
  const article = _readerNavList.find(a => a.id === articleId) || _articles.find(a => a.id === articleId);
  if (!article) return;
  _el('rss-article-list').style.display = 'none';
  const reader = _el('rss-reader');
  reader.style.display = 'flex';

  const content = _el('rss-reader-content');
  const feedName = article.feed?.title || '';
  const time = article.published_at ? _formatTime(article.published_at) : '';
  const author = article.author ? `By ${article.author}` : '';
  const body = article.content || '<p class="rss-reader-placeholder">No content available. Try fetching full content.</p>';

  const videoUrl = article.url || '';
  const videoId = _getYoutubeVideoId(videoUrl) || _getYoutubeVideoId(article.guid);
  const videoHtml = videoId ? `<div class="rss-video-player" data-video-id="${videoId}" data-video-url="${videoUrl}"${article.image ? ` style="background-image: url('${article.image}')"` : ''}><div class="rss-video-play-btn">▶</div></div>` : '';

  content.innerHTML = `
    ${videoHtml}
    <div class="rss-reader-header">
      <h2 class="rss-reader-title">${article.title || 'Untitled'}</h2>
      <div class="rss-reader-meta">${feedName}${time ? ` · ${time}` : ''}${author ? ` · ${author}` : ''}</div>
    </div>
    <div class="rss-reader-body">${body}</div>
  `;

  _el('rss-reader-original').setAttribute('href', article.url || '#');
  _el('rss-reader-star').classList.toggle('rss-star-active', article.is_starred);
  _el('rss-reader-tts').style.display = window.aiTTSManager ? '' : 'none';

  const idx = _readerNavList.findIndex(a => a.id === articleId);
  const prevBtn = _el('rss-reader-prev');
  const nextBtn = _el('rss-reader-next');
  if (prevBtn) prevBtn.disabled = idx <= 0;
  if (nextBtn) nextBtn.disabled = idx === -1 || idx >= _readerNavList.length - 1;

  if (!article.is_read) {
    _api(`/articles/${articleId}/read`, {
      method: 'PUT',
      body: JSON.stringify({ is_read: true }),
    });
    article.is_read = true;
    _loadFeeds();
    document.querySelector(`.rss-article-item[data-article-id="${articleId}"]`)?.classList.add('rss-article-read');
  }
}

function _readerArticleIndex() {
  return _readerNavList.findIndex(a => a.id === _activeArticleId);
}

function _openReaderByIndex(idx) {
  if (idx < 0 || idx >= _readerNavList.length) return;
  const article = _readerNavList[idx];
  _activeArticleId = article.id;
  document.querySelectorAll('.rss-article-item').forEach(el => {
    el.classList.toggle('active', el.dataset.articleId === article.id);
  });
  _openReader(article.id);
}

function _nextArticle() {
  const idx = _readerArticleIndex();
  if (idx === -1) {
    if (_readerNavList.length) _openReaderByIndex(0);
    return;
  }
  _openReaderByIndex(idx + 1);
}

function _prevArticle() {
  const idx = _readerArticleIndex();
  if (idx === -1) {
    if (_readerNavList.length) _openReaderByIndex(0);
    return;
  }
  _openReaderByIndex(idx - 1);
}

function _closeReader() {
  _el('rss-article-list').style.display = '';
  _el('rss-reader').style.display = 'none';
  _activeArticleId = null;
  _readerNavList = [];
}

async function _toggleReaderStar() {
  const article = _readerNavList.find(a => a.id === _activeArticleId) || _articles.find(a => a.id === _activeArticleId);
  if (!article) return;
  const newVal = !article.is_starred;
  await _api(`/articles/${_activeArticleId}/star`, {
    method: 'PUT',
    body: JSON.stringify({ is_starred: newVal }),
  });
  article.is_starred = newVal;
  _el('rss-reader-star').classList.toggle('rss-star-active', newVal);
}

async function _markReaderRead() {
  const article = _readerNavList.find(a => a.id === _activeArticleId) || _articles.find(a => a.id === _activeArticleId);
  if (!article) return;
  await _api(`/articles/${_activeArticleId}/read`, {
    method: 'PUT',
    body: JSON.stringify({ is_read: !article.is_read }),
  });
  article.is_read = !article.is_read;
  _loadFeeds();
}

async function _summarizeReaderArticle() {
  const summaryBtn = _el('rss-reader-summarize');
  if (!summaryBtn || summaryBtn.disabled) return;
  const content = _el('rss-reader-content');
  content?.querySelector('.rss-reader-summary')?.remove();

  const loadingDiv = document.createElement('div');
  loadingDiv.className = 'rss-reader-summary rss-reader-summary-loading';
  loadingDiv.innerHTML = '<h4>AI Summary</h4><p>Generating summary…</p>';
  content?.prepend(loadingDiv);

  summaryBtn.disabled = true;
  summaryBtn.style.opacity = '0.4';
  try {
    const res = await _api(`/articles/${_activeArticleId}/summarize`, { method: 'POST' });
    if (res.ok && res.summary) {
      loadingDiv.classList.remove('rss-reader-summary-loading');
      loadingDiv.innerHTML = `<h4>AI Summary</h4><p>${res.summary}</p>`;
      uiModule.showToast('Summary generated');
    } else {
      loadingDiv.remove();
      uiModule.showError(res.error || 'Summarization failed');
    }
  } catch (e) {
    loadingDiv.remove();
    uiModule.showError('Summarization failed: could not reach the server');
  } finally {
    summaryBtn.disabled = false;
    summaryBtn.style.opacity = '';
  }
}

async function _fetchReaderFullContent() {
  const res = await _api(`/articles/${_activeArticleId}/full-content`, { method: 'POST' });
  if (res.ok && res.content) {
    uiModule.showToast('Full content fetched');
    const body = _el('rss-reader-content')?.querySelector('.rss-reader-body');
    if (body) body.innerHTML = res.content;
  } else {
    uiModule.showError(res.error || 'Failed to fetch content');
  }
}

async function _refreshAll() {
  _el('rss-refresh-all-btn').classList.add('rss-spinning');
  await _api('/refresh-all', { method: 'POST' });
  uiModule.showToast('Feeds queued for refresh');
  setTimeout(() => {
    _loadFeeds();
    _el('rss-refresh-all-btn').classList.remove('rss-spinning');
  }, 3000);
}

async function _playTTS() {
  const article = _readerNavList.find(a => a.id === _activeArticleId) || _articles.find(a => a.id === _activeArticleId);
  if (!article || !window.aiTTSManager) return;
  const content = _el('rss-reader-content')?.querySelector('.rss-reader-body')?.textContent || article.content || '';
  // AITTSManager only exposes play(text) — there is no speak() method, so
  // clicking this button used to silently do nothing.
  try {
    await window.aiTTSManager.play(content);
  } catch (e) {
    uiModule.showError('Failed to play audio: ' + (e?.message || e));
  }
}

function _showAddFeedModal() {
  const modal = document.createElement('div');
  modal.className = 'rss-modal-backdrop';
  modal.innerHTML = `
    <div class="rss-modal">
      <div class="rss-modal-header">
        <h3>Add Feed</h3>
        <button class="rss-modal-close" id="rss-modal-close">&times;</button>
      </div>
      <div class="rss-modal-body">
        <label>Feed URL or website URL</label>
        <input type="text" id="rss-add-url" class="memory-search-input" placeholder="https://example.com/feed.xml" autocomplete="off" />
        <div id="rss-discovered-feeds" style="margin-top:8px"></div>
      </div>
      <div class="rss-modal-footer">
        <button id="rss-add-submit" class="primary-btn">Add Feed</button>
        <button id="rss-add-discover" class="secondary-btn">Discover</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const close = () => modal.remove();
  modal.querySelector('#rss-modal-close')?.addEventListener('click', close);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });

  modal.querySelector('#rss-add-submit')?.addEventListener('click', async () => {
    const selected = modal.querySelector('input[name="rss-discovered"]:checked');
    const url = selected ? selected.value : document.getElementById('rss-add-url')?.value?.trim();
    if (!url) return;
    const res = await _api('', {
      method: 'POST',
      body: JSON.stringify({ feed_url: url }),
    });
    if (res.ok) {
      uiModule.showToast('Feed added');
      close();
      _loadFeeds();
    } else {
      uiModule.showError(res.error || 'Failed to add feed');
    }
  });

  modal.querySelector('#rss-add-discover')?.addEventListener('click', async () => {
    const url = document.getElementById('rss-add-url')?.value?.trim();
    if (!url) return;
    const res = await _api('/discover', {
      method: 'POST',
      body: JSON.stringify({ url }),
    });
    const container = document.getElementById('rss-discovered-feeds');
    if (!container) return;
    if (res.feeds && res.feeds.length > 0) {
      container.innerHTML = res.feeds.map((f, i) =>
        `<label style="display:flex;align-items:center;gap:6px;margin:4px 0;cursor:pointer">
          <input type="radio" name="rss-discovered" value="${f.url}" ${i === 0 ? 'checked' : ''} />
          ${f.title} (${f.type || 'feed'})
        </label>`
      ).join('');
    } else {
      container.innerHTML = '<p style="opacity:0.6">No feeds found at this URL</p>';
    }
  });
}

function _showOpmlModal() {
  const modal = document.createElement('div');
  modal.className = 'rss-modal-backdrop';
  modal.innerHTML = `
    <div class="rss-modal">
      <div class="rss-modal-header">
        <h3>OPML Import/Export</h3>
        <button class="rss-modal-close" id="rss-opml-close">&times;</button>
      </div>
      <div class="rss-modal-body">
        <button id="rss-opml-export-btn" class="primary-btn" style="margin-bottom:12px;width:100%">📥 Export OPML</button>
        <label>Import OPML</label>
        <input type="file" id="rss-opml-file" accept=".opml,.xml" style="display:block;margin-bottom:8px;font-size:12px" />
        <textarea id="rss-opml-import-text" class="memory-search-input" rows="6" placeholder="Or paste OPML content here..." style="width:100%;resize:vertical;font-size:11px;font-family:monospace"></textarea>
        <button id="rss-opml-import-btn" class="secondary-btn" style="margin-top:8px;width:100%">📤 Import OPML</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const close = () => modal.remove();
  modal.querySelector('#rss-opml-close')?.addEventListener('click', close);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });

  modal.querySelector('#rss-opml-file')?.addEventListener('change', (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const ta = document.getElementById('rss-opml-import-text');
      if (ta) ta.value = reader.result;
    };
    reader.readAsText(file);
  });

  modal.querySelector('#rss-opml-export-btn')?.addEventListener('click', async () => {
    const res = await fetch(`${API_BASE}/api/feeds/opml/export`, { credentials: 'same-origin' });
    const text = await res.text();
    const blob = new Blob([text], { type: 'text/xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'odysseus-feeds.opml';
    a.click();
    URL.revokeObjectURL(url);
  });

  modal.querySelector('#rss-opml-import-btn')?.addEventListener('click', async () => {
    const text = document.getElementById('rss-opml-import-text')?.value?.trim();
    if (!text) return;
    const res = await _api('/opml/import', {
      method: 'POST',
      body: JSON.stringify({ opml: text }),
    });
    if (res.ok) {
      uiModule.showToast(`Imported ${res.imported} feeds`);
      close();
      _loadFeeds();
    } else {
      uiModule.showError(res.error || 'Import failed');
    }
  });
}



function _getYoutubeVideoId(url) {
  if (!url) return null;
  const m = (url || '').match(/(?:youtube\.com\/(?:watch\?v=|shorts\/)|youtu\.be\/)([\w-]+)/);
  if (m) return m[1];
  const mg = (url || '').match(/^yt:video:([\w-]+)/);
  return mg ? mg[1] : null;
}

function _formatTime(iso) {
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = now - d;
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
    return d.toLocaleDateString();
  } catch { return ''; }
}

function _openVideoModal(videoId, videoUrl) {
  let modal = _el('rss-video-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'rss-video-modal';
    modal.innerHTML = `
      <div class="rss-video-modal-content">
        <button id="rss-video-modal-close" class="rss-video-modal-close">&times;</button>
        <div id="rss-video-modal-player" class="rss-video-modal-player"></div>
        <a id="rss-video-modal-link" class="rss-video-modal-link" target="_blank" rel="noopener">Watch on YouTube</a>
      </div>`;
    document.body.appendChild(modal);
  }
  const player = _el('rss-video-modal-player');
  const link = _el('rss-video-modal-link');
  link.href = videoUrl.includes('youtube.com') || videoUrl.includes('youtu.be') ? videoUrl : `https://www.youtube.com/watch?v=${videoId}`;
  player.innerHTML = `<iframe src="https://www.youtube.com/embed/${videoId}?autoplay=1" allow="autoplay; fullscreen" allowfullscreen></iframe>`;
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function _closeVideoModal() {
  const modal = _el('rss-video-modal');
  if (modal) {
    modal.style.display = 'none';
    const player = _el('rss-video-modal-player');
    if (player) player.innerHTML = '';
    document.body.style.overflow = '';
  }
}

const feedReaderModule = { openPanel: _openPanel, closePanel, togglePanel, isOpen: () => _open };
export default feedReaderModule;
export { _openPanel as openPanel, closePanel, togglePanel };
window.feedReaderModule = feedReaderModule;
