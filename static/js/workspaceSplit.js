// static/js/workspaceSplit.js
//
// The draggable seam and geometry math for the nav shell's dual-pane split
// view (static/js/workspaceManager.js owns which two features are open;
// this module owns where the line between them sits). Modeled directly on
// modalSnap.js's `_initSplitSeamIndicator` (the email+doc split's seam) —
// pointer-captured drag, clamped width, position published as a CSS var,
// repositioned via MutationObserver + resize. Deliberately NOT modeled on
// document.js's `initDividerDrag`, which attaches document-level
// mousemove/mouseup listeners per open and never removes them.
//
// Geometry contract: this module publishes `--ws-split-x` (the seam's
// viewport x) on `:root`; static/style.css's `body.workspace-split` rules
// read it to size the two `.workspace-surface[data-pane]` elements. JS
// computes numbers, CSS renders — same convention as
// `--email-doc-split-*`/`--left-dock-w`/`--icon-rail-w`.

const RATIO_KEY = 'odysseus-workspace-split-ratio';
const MOBILE_BREAKPOINT = 768;

let _seam = null;
let _active = false; // split is live and the seam should be shown/positioned

function _leftNavRight() {
  const sidebar = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  let x = 0;
  if (sidebar && !sidebar.classList.contains('hidden')) {
    const r = sidebar.getBoundingClientRect();
    if (r.width) x = Math.max(x, r.right);
  }
  if (rail && getComputedStyle(rail).display !== 'none') {
    const r = rail.getBoundingClientRect();
    if (r.width) x = Math.max(x, r.right);
  }
  return x;
}

/** Clamp a candidate seam x to leave both panes at least a usable minimum width. */
function _clampSeamX(x) {
  const left = _leftNavRight();
  const available = Math.max(0, window.innerWidth - left);
  if (!available) return left;
  const compact = available < 900;
  const minPane = compact ? 260 : 320;
  const lo = left + minPane;
  const hi = left + Math.max(minPane, available - minPane);
  return Math.min(hi, Math.max(lo, Math.round(x)));
}

function _loadRatio() {
  try {
    const v = parseFloat(localStorage.getItem(RATIO_KEY));
    return Number.isFinite(v) && v > 0 && v < 1 ? v : 0.5;
  } catch (_) { return 0.5; }
}

function _saveRatio(ratio) {
  try { localStorage.setItem(RATIO_KEY, String(ratio)); } catch (_) {}
}

/** Publish --ws-split-x from a ratio of the post-nav available width. */
function _applyRatio(ratio) {
  const left = _leftNavRight();
  const available = Math.max(0, window.innerWidth - left);
  const x = _clampSeamX(left + available * ratio);
  document.documentElement.style.setProperty('--ws-split-x', `${x}px`);
  return x;
}

function _ensureSeam() {
  if (_seam) return _seam;
  const stripe = document.createElement('div');
  stripe.id = 'workspace-split-seam';
  stripe.title = 'Drag to resize';
  stripe.style.display = 'none';
  document.body.appendChild(stripe);
  _seam = stripe;

  stripe.addEventListener('pointerdown', (e) => {
    if (!_active) return;
    e.preventDefault();
    stripe.setPointerCapture?.(e.pointerId);
    const prevCursor = document.body.style.cursor;
    const prevUserSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.body.classList.add('workspace-split-resizing');

    const dragTo = (clientX) => {
      const x = _clampSeamX(clientX);
      document.documentElement.style.setProperty('--ws-split-x', `${x}px`);
      _positionSeam();
    };
    dragTo(e.clientX);

    const onMove = (ev) => { ev.preventDefault(); dragTo(ev.clientX); };
    const onUp = () => {
      try { stripe.releasePointerCapture?.(e.pointerId); } catch (_) {}
      document.removeEventListener('pointermove', onMove, true);
      document.removeEventListener('pointerup', onUp, true);
      document.removeEventListener('pointercancel', onUp, true);
      document.body.classList.remove('workspace-split-resizing');
      document.body.style.cursor = prevCursor;
      document.body.style.userSelect = prevUserSelect;
      const left = _leftNavRight();
      const available = Math.max(1, window.innerWidth - left);
      const x = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ws-split-x')) || left + available / 2;
      _saveRatio(Math.min(0.85, Math.max(0.15, (x - left) / available)));
    };
    document.addEventListener('pointermove', onMove, true);
    document.addEventListener('pointerup', onUp, true);
    document.addEventListener('pointercancel', onUp, true);
  });

  return stripe;
}

function _positionSeam() {
  const stripe = _ensureSeam();
  if (!_active || window.innerWidth <= MOBILE_BREAKPOINT) {
    stripe.style.display = 'none';
    return;
  }
  const x = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ws-split-x')) || 0;
  if (!x) { stripe.style.display = 'none'; return; }
  stripe.style.display = 'block';
  stripe.style.left = `${x - 5}px`;
}

let _wired = false;
function _wireObservers() {
  if (_wired) return;
  _wired = true;
  new MutationObserver(_positionSeam).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  new MutationObserver(_positionSeam).observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
  window.addEventListener('resize', _positionSeam);
}

/** Show/hide the seam and (re)compute --ws-split-x. Call whenever split state changes. */
export function setSeamActive(on, ratio = null) {
  _active = !!on;
  _ensureSeam();
  _wireObservers();
  if (_active) {
    _applyRatio(ratio != null ? ratio : _loadRatio());
    document.body.classList.add('workspace-split');
  } else {
    document.body.classList.remove('workspace-split');
    document.documentElement.style.removeProperty('--ws-split-x');
  }
  _positionSeam();
}

/** Current split ratio (0..1), derived from the live --ws-split-x. */
export function currentRatio() {
  const left = _leftNavRight();
  const available = Math.max(1, window.innerWidth - left);
  const x = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ws-split-x')) || left + available / 2;
  return Math.min(0.85, Math.max(0.15, (x - left) / available));
}

/** True below the breakpoint where split degrades to a single pane. */
export function isMobile() {
  return window.innerWidth <= MOBILE_BREAKPOINT;
}

export function loadSplitPref() {
  try {
    const raw = localStorage.getItem('odysseus-workspace-split-pref');
    return raw ? JSON.parse(raw) : null;
  } catch (_) { return null; }
}

export function saveSplitPref(pref) {
  try {
    if (pref) localStorage.setItem('odysseus-workspace-split-pref', JSON.stringify(pref));
    else localStorage.removeItem('odysseus-workspace-split-pref');
  } catch (_) {}
}

export default { setSeamActive, currentRatio, isMobile, loadSplitPref, saveSplitPref };
