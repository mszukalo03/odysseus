"""Step-budget guard for the agent loop: always end a turn with an answer.

The loop's own breakers only catch a model repeating the *same* calls. A
model that keeps exploring with new, distinct calls (reading file after file
in a notes vault, say) runs until the round cap and used to stop there with
no answer at all. This guard, driven from ``stream_agent_loop``:

* warns the model two rounds before the cap that it should wrap up;
* in workspace project mode, nudges after a long streak of tool-only rounds;
* turns the final round (or the round after the tool budget is spent) into a
  tool-free answer round, so the existing force-answer path (with its grace
  synthesis) produces a reply from what was gathered.

The user still gets the ``rounds_exhausted`` event, so "Continue" keeps
working. Disable with the ``agent_answer_on_cap`` setting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Rounds before the cap at which the model is told to wrap up.
WARN_ROUNDS_LEFT = 2
# The guard only engages for caps at least this large. Tiny caps (1-4) are
# deliberate one-shot configurations (tests, scheduled probes) and keep the
# plain loop behaviour; real chats run with 12-20+.
MIN_GUARDED_ROUNDS = 5
# Consecutive tool-only rounds (no answer text) before a project-mode nudge.
PROJECT_MODE_TOOL_STREAK = 8

NOTE_PREFIX = "[Odysseus step budget]"

WRAP_UP_NOTE = (
    f"{NOTE_PREFIX} You have {{left}} tool step(s) left in this turn. If what you "
    "have already gathered answers the user's request, answer now. Otherwise use "
    "the remaining steps only for what is strictly needed."
)

STREAK_NOTE = (
    f"{NOTE_PREFIX} You have made {{n}} tool calls without answering. Re-read the "
    "user's request. If it is a quick question, answer it now from what you have; "
    "only keep exploring if the answer genuinely isn't in the results yet."
)

FINAL_NOTE = (
    f"{NOTE_PREFIX} This is the last step of this turn and tools are now off. "
    "Write your final answer to the user's request from the information already "
    "gathered. If something is still missing, say what in one short line; the "
    "user can press Continue to let you keep working."
)


@dataclass
class GuardNote:
    text: str
    force_answer: bool = False


class AgentBudgetGuard:
    def __init__(self, max_rounds: int, *, project_mode: bool = False, enabled: bool = True):
        self.max_rounds = int(max_rounds or 0)
        self.project_mode = bool(project_mode)
        self.enabled = bool(enabled) and self.max_rounds >= MIN_GUARDED_ROUNDS
        self.tool_calls = 0
        self._tool_only_streak = 0
        self._warned = False
        self._streak_nudged = False
        self._final_pending = False
        self.answered_at_cap = False

    def before_round(self, round_num: int, *, tools_used: bool) -> Optional[GuardNote]:
        """Note to append (and whether to drop tools) before ``round_num`` runs."""
        if not self.enabled or not tools_used:
            return None
        if self._final_pending or round_num >= self.max_rounds:
            self._final_pending = False
            self.answered_at_cap = True
            return GuardNote(FINAL_NOTE, force_answer=True)
        left = self.max_rounds - round_num
        if not self._warned and left <= WARN_ROUNDS_LEFT:
            self._warned = True
            return GuardNote(WRAP_UP_NOTE.format(left=left))
        if (self.project_mode and not self._streak_nudged
                and self._tool_only_streak >= PROJECT_MODE_TOOL_STREAK):
            self._streak_nudged = True
            return GuardNote(STREAK_NOTE.format(n=self.tool_calls))
        return None

    def after_round(self, *, tool_calls: int, has_text: bool) -> None:
        self.tool_calls += max(int(tool_calls or 0), 0)
        if tool_calls and not has_text:
            self._tool_only_streak += 1
        else:
            self._tool_only_streak = 0

    def on_tool_budget_hit(self, round_num: int) -> bool:
        """Tool budget spent: True if the next round should answer instead of
        the loop stopping silently."""
        if not self.enabled or round_num >= self.max_rounds:
            return False
        self._final_pending = True
        return True
