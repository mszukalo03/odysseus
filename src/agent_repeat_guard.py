"""Duplicate tool-call guard for the agent loop.

Local models (quantized MoE served through llama.cpp-style runtimes in
particular) often re-issue a read they already made: the same ``read_file``
range, the same ``grep``. The loop-breaker only trips after several identical
*rounds*, and until then every repeat runs again and pushes another copy of
the same output into context, which feeds the loop.

This guard, driven from ``stream_agent_loop``:

* skips a workspace read (``read_file``/``grep``/``glob``/``ls``) whose exact
  arguments already ran earlier in the turn, and answers with a short note
  instead of re-running it;
* forgets everything once a tool that may change state runs (``edit_file``,
  ``apply_patch``, ``bash``, MCP, ...), so a re-read after an edit runs;
* reports when a round was made up only of such repeats, so the loop can force
  a tool-free answer after a couple of those.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from src.tool_capabilities import ToolEffect, capabilities_for_action

# Consecutive rounds made up only of repeated reads before the loop forces a
# tool-free answer round.
DUPLICATE_ROUNDS_TO_FORCE = 2

# Effects that never change state. A tool with only these leaves the cache
# alone; anything else (writes, code execution, unknown MCP tools) clears it.
_NON_MUTATING = frozenset({
    ToolEffect.READ_PUBLIC,
    ToolEffect.READ_WORKSPACE,
    ToolEffect.READ_PRIVATE,
    ToolEffect.BROKERED_NETWORK_READ,
    ToolEffect.USER_INTERACTION,
})

DUPLICATE_NOTE = (
    "[Odysseus] Not run again: you already called `{tool}` with these exact "
    "arguments in step {round} of this turn, and nothing has changed since. Its "
    "result is earlier in this conversation. Use that result. Read a different "
    "range or file only if you need new information; otherwise answer now."
)

FORCE_ANSWER_AFTER_REPEATS_NOTE = (
    "[Odysseus] Your last tool calls only repeated reads you had already made, so "
    "tools are now off for this step. Answer the user's request from the results "
    "already gathered. If something is still missing, say what in one short line."
)


def _normalized_args(content: Any) -> str:
    text = (content or "").strip() if isinstance(content, str) else str(content or "")
    if text.startswith("{"):
        try:
            return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return text


def _is_cacheable(tool: str, content: Any) -> bool:
    caps = capabilities_for_action(tool, content)
    return caps.known and caps.effects == frozenset({ToolEffect.READ_WORKSPACE})


def _is_mutating(tool: str, content: Any) -> bool:
    caps = capabilities_for_action(tool, content)
    return not caps.known or not caps.effects <= _NON_MUTATING


class ToolRepeatGuard:
    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)
        self._seen: Dict[str, int] = {}
        self._duplicate_rounds = 0
        self.skipped = 0

    @staticmethod
    def _key(tool: str, content: Any) -> str:
        return f"{tool}\x00{_normalized_args(content)}"

    def prior_round(self, tool: str, content: Any) -> Optional[int]:
        """Round in which this exact read already ran this turn, else None."""
        if not self.enabled or not _is_cacheable(tool, content):
            return None
        return self._seen.get(self._key(tool, content))

    def duplicate_result(self, tool: str, prior_round: int) -> dict:
        """Tool result to feed back instead of running the call again."""
        self.skipped += 1
        return {
            "output": DUPLICATE_NOTE.format(tool=tool, round=prior_round),
            "exit_code": 0,
            "duplicate_of_round": prior_round,
        }

    def record(self, tool: str, content: Any, round_num: int, result: Any) -> None:
        """Note a call that actually ran."""
        if not self.enabled:
            return
        if _is_mutating(tool, content):
            self._seen.clear()
            return
        if _is_cacheable(tool, content):
            self._seen.setdefault(self._key(tool, content), round_num)

    def end_round(self, *, calls: int, duplicates: int) -> bool:
        """True when the loop should stop offering tools: the last
        ``DUPLICATE_ROUNDS_TO_FORCE`` rounds were nothing but repeats."""
        if not self.enabled:
            return False
        if calls and duplicates >= calls:
            self._duplicate_rounds += 1
        else:
            self._duplicate_rounds = 0
        return self._duplicate_rounds >= DUPLICATE_ROUNDS_TO_FORCE
