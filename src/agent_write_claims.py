"""Detect final answers that report a change no write tool made.

A local model asked to "tick that task" or "add this to the inventory" can
read the file, then answer "Ticked ..." or "Added ..." without ever calling a
write tool. The completion gate's older claim patterns only knew verbs such as
"created" and "updated", so those answers were released as finished work.

This module holds the two halves of the check, shared by the agent loop (one
corrective round) and the completion gate (the final, provider-free filter):

* ``mutation_claim(text)`` finds a reported change in the answer prose.
* ``successful_write_ran(events)`` says whether any tool in this turn could
  have made it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

# Past-tense verbs that report a change to a file, task list, note or record
# when a first-person subject says it did them.
_CLAIM_VERBS = (
    r"ticked(?:\s+off)?|checked\s+off|crossed\s+off|added|appended|logged|recorded|inserted|"
    r"filed|updated|edited|changed|modified|replaced|renamed|moved|marked|wrote|written|"
    r"saved|created|removed|deleted|scheduled|booked"
)

# "I ticked ...", "I've added ...", "We have logged ...".
_SUBJECT_CLAIM = re.compile(
    rf"\b(?:I|we)(?:\s*'ve|\s+have|\s+just|\s+now|\s+also)*\s+(?:successfully\s+)?(?:{_CLAIM_VERBS})\b",
    re.IGNORECASE,
)
# "The task is now ticked", "The row has been added". Plain "was"/"is" forms
# are left out: "the timeout is set to 300 s" or "the file was created in
# 2024" describe what a read found, not a change made in this turn.
_PASSIVE_CLAIM = re.compile(
    r"\b(?:is\s+now|are\s+now|has\s+(?:now\s+)?been|have\s+(?:now\s+)?been)\s+(?:successfully\s+)?"
    r"(?:ticked(?:\s+off)?|checked\s+off|crossed\s+off|added|appended|logged|recorded|inserted|"
    r"updated|edited|changed|modified|replaced|renamed|moved|marked|written|saved|created|removed|deleted)\b",
    re.IGNORECASE,
)
# A statement that opens with the verb: "Ticked "X" in [[p]].", "Added X to Y."
# The verb must be followed by a target phrase (to/in/on/into/under/as/from or
# a quoted/linked/coded object) so ordinary prose such as "Added sugar is
# bad for you" is not read as a report.
_LEADING_CLAIM = re.compile(
    r"^\s*(?:[-*>]\s*)?(?:[✅✔☑]\s*)?(?:successfully\s+)?"
    r"(?P<verb>ticked(?:\s+off)?|checked\s+off|crossed\s+off|added|appended|logged|recorded|inserted|"
    r"filed|updated|edited|changed|modified|replaced|renamed|marked|wrote|saved|created|removed|deleted)\b"
    r"(?P<rest>[^\n]*)",
    re.IGNORECASE,
)
_TARGET_HINT = re.compile(r"\b(?:to|in|on|into|under|as|from)\b|[\"'`\[]|\.md\b", re.IGNORECASE)
# Conditional, modal or future framing: a plan, an offer or an example.
_NON_REPORT = re.compile(
    r"\b(?:if|unless|when|whenever|until|would|could|should|can|will|shall|might|may|must|"
    r"going\s+to|want\s+me|let\s+me|i'll|we'll|not|never|haven't|didn't|wasn't|"
    r"suppose|imagine|hypothetically|example|instance|e\.g|says?|said|states?|stated)\b|n't\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_FENCE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")

# Tools that only read or look things up. Anything else that succeeds counts
# as a possible write; a false "write ran" only lets a claim through, which is
# the behaviour before this check existed.
READ_ONLY_TOOLS = frozenset({
    "read_file", "grep", "glob", "ls", "list_files", "search_files", "file_search",
    "find", "get_workspace", "view_file", "open_file", "stat_file",
    "web_search", "web_fetch", "fetch_url", "private_browser", "youtube_tool",
    "get_weather", "ask_user", "ask_teacher", "recall", "search_memory",
    "search_notes", "list_notes", "read_note", "list_emails", "read_email",
    "search_emails", "list_email_accounts", "inspect_media", "rag_search",
    "update_plan", "think",
})
_READ_ONLY_PREFIXES = ("list_", "get_", "search_", "read_", "view_", "find_", "lookup_", "fetch_")
_READ_ONLY_ACTIONS = frozenset({
    "list", "get", "read", "search", "show", "view", "find", "query", "status", "count", "lookup",
})
_SHELL_TOOLS = frozenset({"bash", "host_shell", "python", "shell"})
_SHELL_WRITE_HINT = re.compile(
    r"(?:^|[\s;&|(])(?:>>?|tee\b|sed\s+-i|perl\s+-[a-z]*i|mv\b|cp\b|rm\b|mkdir\b|touch\b|"
    r"truncate\b|git\s+(?:commit|mv|rm|apply)|patch\b|install\b|chmod\b|ln\b)"
    r"|\.write\(|write_text\(|open\([^)]*['\"][wa]",
)


def _statements(text: str) -> Iterable[str]:
    body = _FENCE.sub(" ", str(text or ""))
    for part in _SENTENCE_SPLIT.split(body):
        part = part.strip()
        if part:
            yield part


def mutation_claim(text: str) -> str:
    """Return the first statement that reports a completed change, else ''."""
    for statement in _statements(text):
        leading = _LEADING_CLAIM.match(statement)
        if leading and _TARGET_HINT.search(leading.group("rest") or ""):
            if not _NON_REPORT.search(statement[: leading.start("verb")]):
                return statement
        for pattern in (_SUBJECT_CLAIM, _PASSIVE_CLAIM):
            match = pattern.search(statement)
            if match and not _NON_REPORT.search(statement[: match.end()]):
                return statement
    return ""


def _tool_name(event: Mapping[str, Any]) -> str:
    return str(event.get("tool") or event.get("tool_name") or event.get("name") or "").strip().lower()


def _event_succeeded(event: Mapping[str, Any]) -> bool:
    if event.get("blocked") or event.get("approval_required") or event.get("context_only"):
        return False
    if event.get("execution_attempted") is False or event.get("error"):
        return False
    exit_code = event.get("exit_code")
    if isinstance(exit_code, bool):
        return False
    if isinstance(exit_code, int):
        return exit_code == 0
    success = event.get("success")
    return success is True


def _command_action(command: Any) -> str:
    if isinstance(command, Mapping):
        data = command
    else:
        try:
            data = json.loads(str(command or ""))
        except (TypeError, ValueError):
            return ""
    if not isinstance(data, Mapping):
        return ""
    return str(data.get("action") or data.get("operation") or data.get("op") or "").strip().lower()


def tool_name_is_read_only(name: str) -> bool:
    """True for tools that only read or look things up, by name alone."""
    leaf = str(name or "").strip().lower().rsplit("__", 1)[-1]
    return bool(leaf) and (leaf in READ_ONLY_TOOLS or leaf.startswith(_READ_ONLY_PREFIXES))


def tool_event_is_write(event: Mapping[str, Any]) -> bool:
    """True when this successful tool event could have changed something."""
    if not isinstance(event, Mapping) or not _event_succeeded(event):
        return False
    tool = _tool_name(event)
    if not tool:
        return False
    if tool_name_is_read_only(tool):
        return False
    leaf = tool.rsplit("__", 1)[-1]
    command = event.get("command")
    if leaf in _SHELL_TOOLS:
        return bool(_SHELL_WRITE_HINT.search(str(command or "")))
    if _command_action(command) in _READ_ONLY_ACTIONS:
        return False
    return True


def successful_write_ran(events: Iterable[Mapping[str, Any]] | None) -> bool:
    return any(tool_event_is_write(event) for event in events or ())


def unbacked_mutation_claim(text: str, events: Iterable[Mapping[str, Any]] | None) -> str:
    """The claimed-change statement when no write ran this turn, else ''."""
    claim = mutation_claim(text)
    if not claim:
        return ""
    return "" if successful_write_ran(events) else claim


NO_WRITE_NUDGE = (
    "Your answer says a change was made, but no write tool has run in this turn: "
    "nothing has been written yet. Either make the change now with the write tool "
    "(edit_file, write_file or apply_patch), or, if you cannot, tell the user plainly "
    "that the change was NOT made and why. Do not report a change you have not made."
)

NOT_DONE_REASON = "nothing was written in this turn, so the change described was not made"
