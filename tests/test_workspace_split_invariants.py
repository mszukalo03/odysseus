"""Static-source invariant checks for the dual-pane split view
(static/js/workspaceManager.js, static/js/workspaceSplit.js).

workspaceManager.js is DOM-heavy by design (document.getElementById,
history.pushState, MutationObserver, ...) — it isn't a pure-logic module
like static/js/liveThinkingThrottle.js, so it can't run under plain
`node:test` without a jsdom-style harness, which this project doesn't have
set up. This suite follows the same fallback the document.js tests already
use (see tests/helpers/document_js_sources.py): source-level checks for the
invariants that were exhaustively verified live against a running instance
during development (both direct API calls and, for the header picker, a
throwaway-descriptor click-through) but have no automated regression guard.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WSM = (ROOT / "static/js/workspaceManager.js").read_text(encoding="utf-8")
SPLIT = (ROOT / "static/js/workspaceSplit.js").read_text(encoding="utf-8")


def test_open_replaces_by_closing_every_existing_pane_unless_split():
    # A plain (non-split) open must still replace whatever pane(s) exist —
    # the single-workspace era's behavior, preserved even with 2 panes.
    assert "if (!split) {" in WSM
    assert "for (const existing of [..._panes]) {" in WSM


def test_close_only_hides_the_host_when_the_last_pane_closes():
    # close() must not blank the second pane's surface by hiding the whole
    # #workspace-host on every close — only when _panes is empty afterward.
    assert "if (_panes.length === 0) {" in WSM
    assert "_ensureHost().classList.add('hidden');" in WSM


def test_close_hands_the_url_to_a_surviving_partner():
    assert "} else if (wasFocused) {" in WSM
    assert "_focusedId = _panes[0];" in WSM


def test_close_skips_url_rewrite_when_triggered_by_popstate():
    # The survivor hand-off must not fight a navigation already in progress.
    assert "if (!(silent || fromHistory) && survivorDesc?.route) {" in WSM


def test_escape_only_asks_and_closes_the_focused_pane():
    assert "if (e.key !== 'Escape' || !_focusedId) return;" in WSM
    assert "const desc = _registry.get(_focusedId);" in WSM


def test_auto_close_delegate_skips_modified_clicks_and_either_panes_button():
    # Ctrl/Cmd-click must not also trigger the "another tool opened" auto-close.
    assert "if (e.metaKey || (e.ctrlKey && !/Mac/i.test(navigator.platform || '')))" in WSM
    assert "_panes.some((id) => control.id === `tool-${id}-btn` || control.id === `rail-${id}`)" in WSM


def test_ctrl_click_entry_stops_propagation_before_the_rail_relay():
    # static/app.js's rail->sidebar-button relay is a bubble-phase listener on
    # the button; the capture-phase Ctrl-click delegate must preventDefault +
    # stopPropagation so a modified click never ALSO triggers a plain replace.
    assert "e.preventDefault();\n  e.stopPropagation();\n  if (!_panes.length) open(id);" in WSM


def test_open_beside_never_consults_display_mode():
    # A split pane is always the page rendering regardless of id's stored
    # popup/page setting — _showPageSurface(id, {split:true}) bypasses
    # displayMode() entirely (only open()/acquirePageSurface() consult it).
    beside_start = WSM.index("export function openBeside(id) {")
    beside_body = WSM[beside_start:WSM.index("\n}\n", beside_start)]
    assert "displayMode(" not in beside_body


def test_split_with_chat_routes_through_the_chat_pane_sentinel():
    assert "export const CHAT_PANE = '__chat__';" in WSM
    assert "if (partnerId === CHAT_PANE) return splitWithChat(primaryId);" in WSM


def test_clear_split_handles_both_chat_and_feature_partners():
    clear_start = WSM.index("export function clearSplit() {")
    clear_body = WSM[clear_start:WSM.index("\n}\n", clear_start)]
    assert "_chatPartner" in clear_body
    assert "_panes.length <= 1" in clear_body


def test_boot_restore_skips_a_plain_landing_on_chat():
    restore_start = WSM.index("export function resolveInitialRoute() {")
    restore_body = WSM[restore_start:WSM.index("\n}\n", restore_start)]
    assert "if (!id) return;" in restore_body


def test_boot_restore_refocuses_the_deep_linked_pane_not_the_partner():
    restore_start = WSM.index("export function resolveInitialRoute() {")
    restore_body = WSM[restore_start:WSM.index("\n}\n", restore_start)]
    assert "focusPane(id);" in restore_body


def test_split_degrades_below_the_shared_mobile_breakpoint():
    # Same 768px breakpoint every other split path in this app already uses
    # (modalSnap.js's email/doc split, document.js's own divider).
    assert "const MOBILE_BREAKPOINT = 768;" in SPLIT
    assert "if (Split.isMobile()) { open(id); return false; }" in WSM


def test_seam_uses_pointer_capture_not_leaked_document_listeners():
    # Modeled on modalSnap.js's _initSplitSeamIndicator, NOT document.js's
    # initDividerDrag (which attaches document-level mousemove/mouseup that
    # are never removed). The seam's move/up listeners must be added AND
    # removed within the same drag gesture.
    assert "stripe.setPointerCapture?.(e.pointerId);" in SPLIT
    assert "document.removeEventListener('pointermove', onMove, true);" in SPLIT
    assert "document.removeEventListener('pointerup', onUp, true);" in SPLIT
