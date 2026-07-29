"""In-memory sudo credential broker for agent shell commands.

The agent's bash subprocess is spawned with pipes, not a TTY, so `sudo`
can never prompt for a password itself — it just dies with "a terminal is
required to read the password". This module lets the tool ask the *browser*
instead: the tool registers a pending request and blocks, the UI renders a
password field, and the submitted password comes back over an asyncio.Event
so the original command can carry on via `sudo -S`.

Security posture (deliberate, please preserve if you touch this):
  - Passwords live only in this process's memory. Never written to disk,
    never logged, never placed in tool output, the chat transcript, or
    anything sent to the model.
  - Cached per-owner with a short TTL so a multi-step task doesn't re-prompt
    on every command — mirroring sudo's own ticket lifetime rather than
    inventing a new one.
  - `pending_for()` deliberately exposes only the request id + the command
    being authorized, never the password.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Awaitable, Callable, Dict, Optional, Tuple

# How long the tool waits for the user to type a password before giving up.
# Long enough to go find a password manager, short enough that an abandoned
# tab doesn't pin an agent turn open forever.
DEFAULT_PROMPT_TIMEOUT_S = 180.0

# Matches sudo's own default ticket lifetime, so "how long until it asks me
# again" behaves the way a terminal user already expects.
DEFAULT_CACHE_TTL_S = 15 * 60


class _Pending:
    __slots__ = ("request_id", "command", "event", "password", "created_at")

    def __init__(self, request_id: str, command: str) -> None:
        self.request_id = request_id
        self.command = command
        self.event = asyncio.Event()
        self.password: Optional[str] = None
        self.created_at = time.time()


# owner -> in-flight prompt. One at a time per owner: a second concurrent
# sudo command would just re-prompt, and the cache makes that rare.
_pending: Dict[str, _Pending] = {}

# owner -> (password, expires_at)
_cache: Dict[str, Tuple[str, float]] = {}


def _key(owner: Optional[str]) -> str:
    return owner or ""


def get_cached(owner: Optional[str]) -> Optional[str]:
    """Return a still-valid cached password, or None. Expired entries are dropped."""
    entry = _cache.get(_key(owner))
    if not entry:
        return None
    password, expires_at = entry
    if time.time() >= expires_at:
        _cache.pop(_key(owner), None)
        return None
    return password


def cache_password(
    owner: Optional[str],
    password: str,
    ttl_s: float = DEFAULT_CACHE_TTL_S,
) -> None:
    if not password:
        return
    _cache[_key(owner)] = (password, time.time() + ttl_s)


def clear(owner: Optional[str] = None) -> None:
    """Forget the cached password for one owner, or all of them when owner is None."""
    if owner is None:
        _cache.clear()
    else:
        _cache.pop(_key(owner), None)


def has_cached(owner: Optional[str]) -> bool:
    return get_cached(owner) is not None


def pending_for(owner: Optional[str]) -> Optional[Dict[str, str]]:
    """Metadata for the in-flight prompt (never the password itself)."""
    pending = _pending.get(_key(owner))
    if pending is None:
        return None
    return {"request_id": pending.request_id, "command": pending.command}


def submit(
    owner: Optional[str],
    request_id: str,
    password: str,
    remember: bool = True,
) -> bool:
    """Hand a password to the waiting tool. False if there's no matching request."""
    pending = _pending.get(_key(owner))
    if pending is None or pending.request_id != request_id:
        return False
    pending.password = password
    if remember:
        cache_password(owner, password)
    pending.event.set()
    return True


def cancel(owner: Optional[str], request_id: str) -> bool:
    """Unblock the waiting tool with no password — the command then fails cleanly."""
    pending = _pending.get(_key(owner))
    if pending is None or pending.request_id != request_id:
        return False
    pending.password = None
    pending.event.set()
    return True


async def request_password(
    owner: Optional[str],
    command: str,
    notify: Callable[[Dict], Awaitable[None]],
    timeout_s: float = DEFAULT_PROMPT_TIMEOUT_S,
) -> Optional[str]:
    """Get a sudo password, prompting the UI only if nothing is cached.

    `notify` pushes the prompt event to the client (in practice the agent
    loop's progress callback, which forwards it as a `tool_progress` SSE
    frame). Returns the password, or None if the user cancelled or never
    answered.
    """
    cached = get_cached(owner)
    if cached:
        return cached

    key = _key(owner)
    pending = _Pending(uuid.uuid4().hex, command)
    _pending[key] = pending
    try:
        await notify({
            "sudo_prompt": True,
            "request_id": pending.request_id,
            "command": command,
        })
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
        return pending.password
    finally:
        # Drop the pending entry even on cancellation/timeout so a later
        # command isn't matched against a stale request id.
        if _pending.get(key) is pending:
            _pending.pop(key, None)
