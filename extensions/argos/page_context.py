"""Page-context wrapping for the Argos browser extension.

Pure functions, kept separate from backend.py so the security-critical parts
(truncation, source labeling, dedupe) are directly unit-testable without an
app boot. Page text is attacker-controlled by definition -- it comes from
whatever site the user happened to have open -- so it is never trusted, only
wrapped via untrusted_context_message before it touches a session.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any, Dict, Optional

# Matches the truncation convention for fetched web pages already used at
# src/chat_processor.py:463-465.
PAGE_TEXT_MAX_CHARS = 10000

# Reject oversized POST bodies before any processing -- generous enough for
# a full page capture, small enough to keep a request cheap to reject.
PAGE_TEXT_HARD_CAP = 400_000

# Bounded per-session dedupe cache: session_id -> sha256(text)[:16]. Capped
# and LRU-evicted so a long-running server doesn't grow this unbounded across
# many sessions.
_MAX_DEDUPE_ENTRIES = 256
_last_hash: "OrderedDict[str, str]" = OrderedDict()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def is_duplicate_context(session_id: str, text: str) -> bool:
    """True if this exact page text was already injected for this session."""
    return _last_hash.get(session_id) == _content_hash(text)


def remember_context(session_id: str, text: str) -> None:
    """Record the hash of the most recently injected text for this session."""
    _last_hash[session_id] = _content_hash(text)
    _last_hash.move_to_end(session_id)
    while len(_last_hash) > _MAX_DEDUPE_ENTRIES:
        _last_hash.popitem(last=False)


def _flatten(value: str) -> str:
    """Collapse CR/LF to a single space. Applied to url/title before they are
    embedded in the content body (not just the label) so an attacker-supplied
    URL or title can't inject a fake `\\nSource:` line into the guarded
    block. untrusted_context_message already does this for the label; this
    covers the body, where our own code -- not prompt_security -- controls
    formatting."""
    return " ".join(value.split())


def build_page_context_message(
    url: str,
    title: str,
    text: str,
    selection: str = "",
) -> Optional[Dict[str, Any]]:
    """Wrap page content as an untrusted-context chat message.

    Selection (if the user highlighted something) takes priority over the
    full-page text -- matching Leo-style "ask about this selection" behavior.
    Returns None when there is no content to wrap at all.

    Never hand-build the wrapper: untrusted_context_message already escapes
    guard-marker literals, sanitizes the label, and forces role="user" so the
    content can never masquerade as a system instruction.
    """
    from src.prompt_security import untrusted_context_message

    body = (selection or text or "").strip()
    if not body:
        return None
    body = body[:PAGE_TEXT_MAX_CHARS]

    safe_url = _flatten(url)
    safe_title = _flatten(title)

    label = f"web page: {safe_url}" if safe_url else "web page"
    content = f"Content from {safe_url}:\n\n{body}" if safe_url else body
    if safe_title:
        content = f"Title: {safe_title}\n{content}"

    return untrusted_context_message(label, content)
