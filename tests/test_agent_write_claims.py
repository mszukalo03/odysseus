"""Answers that report a change no write tool made.

2026-11-22 V3 persona sandbox run: three turns answered "Ticked ...",
"Added ..." and "Logged 2 h ..." after only read_file calls, and the forced
answer after a loop-breaker trip claimed an inventory row it never wrote. The
completion gate released all of them as ``unverified, can_complete: true``.
"""
import asyncio
import json

import pytest

import src.agent_loop as al
from src.agent_evidence import EvidenceLedger
from src.agent_runtime.completion import completion_answer
from src.agent_write_claims import (
    NO_WRITE_NUDGE,
    NOT_DONE_REASON,
    mutation_claim,
    successful_write_ran,
    unbacked_mutation_claim,
)


# --- claim detection ------------------------------------------------------

@pytest.mark.parametrize("answer", [
    'Ticked "Order the 2S → 12 V boost converter for the eDP board and cost every BOM line" in [[portable-screen]].',
    'Added "Magnetic cable organiser" to INBOX.md (2026-11-22).',
    "Added UGREEN Revodok Pro 210 (qty 1, $44, Amazon) to `areas/home/inventory.md` (location: on the desk).",
    "Logged 2 hours on ChemPlot today:\n\n`- 2026-11-22: worked 2 h on ChemPlot.`",
    "I've updated the project note with the new date.",
    "The task is now ticked off in NOW.md.",
    "- ✅ Renamed the folder to graphing",
])
def test_reported_changes_are_claims(answer):
    assert mutation_claim(answer)


@pytest.mark.parametrize("answer", [
    "The timeout is set to 300 s in the config.",
    "Added sugar is bad for you.",
    "I'll add it to INBOX.md once you confirm.",
    "If you want, I can tick that task in NOW.md.",
    "For example, I created the file and updated config.py.",
    "I haven't added anything yet.",
    "The file was created in 2024 and lists 12 items.",
    "```\nAdded X to Y\n```",
    "Which do you want: copy or move?",
])
def test_plans_questions_and_facts_are_not_claims(answer):
    assert mutation_claim(answer) == ""


def _event(tool, exit_code=0, **extra):
    return {"tool": tool, "command": extra.pop("command", ""), "exit_code": exit_code, **extra}


def test_read_only_tools_are_not_writes():
    events = [_event("read_file"), _event("grep"), _event("mcp__email__list_emails"),
              _event("bash", command="cat NOW.md"),
              _event("manage_tasks", command=json.dumps({"action": "list"}))]
    assert not successful_write_ran(events)


@pytest.mark.parametrize("event", [
    _event("edit_file"),
    _event("write_file"),
    _event("apply_patch"),
    _event("bash", command="echo '- [x] done' >> NOW.md"),
    _event("manage_tasks", command=json.dumps({"action": "complete", "id": "t1"})),
])
def test_successful_writes_count(event):
    assert successful_write_ran([event])


@pytest.mark.parametrize("event", [
    _event("edit_file", exit_code=1),
    _event("edit_file", approval_required=True),
    _event("edit_file", blocked=True),
    _event("edit_file", error="old_string not found"),
])
def test_failed_or_blocked_writes_do_not_count(event):
    assert not successful_write_ran([event])


def test_unbacked_claim_needs_both_halves():
    claim = "Added the row to inventory.md."
    assert unbacked_mutation_claim(claim, [_event("read_file")]) == claim
    assert unbacked_mutation_claim(claim, [_event("read_file"), _event("edit_file")]) == ""
    assert unbacked_mutation_claim("Here is the file.", [_event("read_file")]) == ""


# --- completion gate ------------------------------------------------------

def _ledger(*events):
    return EvidenceLedger.from_tool_events(list(events))


def test_gate_replaces_claim_when_only_reads_ran():
    ledger = _ledger(_event("read_file", command="NOW.md"), _event("read_file", command="projects/x.md"))
    answer, reason = completion_answer(
        'Ticked "Order the boost converter" in [[portable-screen]].', ledger, ledger.evaluate())
    assert reason == NOT_DONE_REASON
    assert answer.startswith("Not done:")
    assert "Ticked" not in answer


def test_gate_keeps_other_prose_and_drops_only_the_claim():
    ledger = _ledger(_event("read_file", command="NOW.md"))
    answer, reason = completion_answer(
        "Logged 2 hours on ChemPlot today. The project is in projects/active/chemplot.",
        ledger, ledger.evaluate())
    assert reason == NOT_DONE_REASON
    assert "Logged" not in answer
    assert "projects/active/chemplot" in answer


def test_gate_keeps_claim_after_a_successful_write():
    ledger = _ledger(_event("read_file", command="NOW.md"),
                     _event("edit_file", command=json.dumps({"path": "NOW.md"})))
    text = 'Ticked "Order the boost converter" in [[portable-screen]].'
    answer, reason = completion_answer(text, ledger, ledger.evaluate())
    assert reason == ""
    assert answer == text


def test_gate_leaves_read_only_answers_alone():
    ledger = _ledger(_event("read_file", command="NOW.md"))
    text = "Your week has three open tasks. The timeout is set to 300 s."
    answer, reason = completion_answer(text, ledger, ledger.evaluate())
    assert (answer, reason) == (text, "")


# --- agent loop -----------------------------------------------------------

def _collect(gen):
    async def _run():
        return [c async for c in gen]
    return asyncio.run(_run())


def _events(chunks):
    out = []
    for c in chunks:
        if c.startswith("data: ") and not c.startswith("data: [DONE]"):
            try:
                out.append(json.loads(c[6:]))
            except Exception:
                pass
    return out


def _final_text(events):
    """What the user sees after the completion gate released the answer."""
    text = ""
    for e in events:
        if e.get("type") == "final_response":
            text = e.get("content") or ""
        elif "delta" in e and e.get("thinking") is not True:
            text += e.get("delta") or ""
    return text


def _fence(tool, args):
    """A native tool call from the scripted model."""
    return {"name": tool, "arguments": json.dumps(args)}


def _run_loop(monkeypatch, replies, *, relevant_tools, thinking_mode=None, max_rounds=12, executed=None,
              workspace=None):
    """Drive stream_agent_loop with scripted model replies.

    ``replies`` is a callable (round_index, messages, kwargs) -> reply text.
    """
    from src.tool_capabilities import ToolGateDecision
    from tests.runtime_evidence_helpers import authoritative_executor

    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set(), raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    import src.context_compactor as context_compactor
    monkeypatch.setattr(context_compactor, "get_context_length", lambda *a, **k: 128_000)
    monkeypatch.setattr(al, "tool_result_should_arm_gate", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(
        al.ToolRunSecurityContext, "decision_for",
        lambda self, *a, **k: ToolGateDecision(True), raising=False,
    )
    monkeypatch.setattr(al, "_exact_approval_required_for_tool", lambda *a, **k: False, raising=False)

    executed = [] if executed is None else executed
    if workspace is None:
        # File tools are offered only inside a workspace.
        import tempfile
        workspace = tempfile.mkdtemp(prefix="write-claims-")
        with open(f"{workspace}/NOW.md", "w", encoding="utf-8") as handle:
            handle.write("## This week\n- [ ] Order the boost converter\n")

    async def _fake_exec(block, *a, **k):
        executed.append(block.tool_type)
        return (block.tool_type, {"output": f"result {len(executed)} for {block.content[:40]}", "exit_code": 0})

    async def _no_direct_provider(*args, **kwargs):
        raise RuntimeError("no direct provider in this fixture")
        yield
    monkeypatch.setattr(al, "stream_llm", _no_direct_provider, raising=False)

    async def _no_synthesis(*args, **kwargs):
        raise RuntimeError("no grace synthesis provider in this fixture")
    import src.llm_core as llm_core
    monkeypatch.setattr(llm_core, "llm_call_async", _no_synthesis)
    monkeypatch.setattr(al, "execute_tool_block", authoritative_executor(_fake_exec), raising=False)

    calls = []

    async def _fake_stream(_candidates, messages, **kwargs):
        factory = kwargs.get("candidate_request_factory")
        request = {}
        if factory is not None:
            url, model, headers = _candidates[0][:3]
            request = await factory(0, url, model, headers) or {}
        merged = {**kwargs, **(request.get("kwargs") or {})}
        sent = request.get("messages", messages)
        calls.append({"messages": list(sent), "kwargs": merged})
        reply = replies(len(calls) - 1, sent, merged)
        if isinstance(reply, dict):
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [reply]})}\n\n'
        else:
            yield f'data: {json.dumps({"delta": reply})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "I ordered the boost converter; tick that task in NOW.md."}],
        max_rounds=max_rounds,
        relevant_tools=set(relevant_tools),
        owner="pewds",
        thinking_mode=thinking_mode,
        workspace=str(workspace) if workspace else None,
    )
    return _events(_collect(gen)), calls, executed


def _nudged(messages):
    return any(m.get("content") == NO_WRITE_NUDGE for m in messages)


def test_claim_without_write_gets_one_round_to_write(monkeypatch):
    executed = []

    def replies(i, messages, kwargs):
        if i == 0:
            return _fence("read_file", {"path": "NOW.md"})
        if _nudged(messages) and "edit_file" not in executed:
            return _fence("edit_file", {"path": "NOW.md", "old_string": "- [ ] Order", "new_string": "- [x] Order"})
        return 'Ticked "Order the boost converter" in NOW.md.'

    events, calls, _ = _run_loop(
        monkeypatch, replies, relevant_tools={"read_file", "edit_file"}, executed=executed)
    assert executed == ["read_file", "edit_file"]
    assert len(calls) == 4
    final = _final_text(events)
    assert "Ticked" in final
    assert "Not done" not in final


def test_claim_without_write_is_replaced_by_not_done(monkeypatch):
    def replies(i, messages, kwargs):
        if i == 0:
            return _fence("read_file", {"path": "NOW.md"})
        return 'Ticked "Order the boost converter" in [[portable-screen]].'

    events, calls, executed = _run_loop(
        monkeypatch, replies, relevant_tools={"read_file", "edit_file"})
    assert executed == ["read_file"]
    # Exactly one corrective round, then the gate's honest answer.
    assert len(calls) == 3
    final = _final_text(events)
    assert final.startswith("Not done:")
    assert "Ticked" not in final
    decision = next(e for e in events if e.get("type") == "completion_decision")
    assert decision["data"]["status"] == "unverified"


def test_read_only_answer_is_not_nudged(monkeypatch):
    def replies(i, messages, kwargs):
        if i == 0:
            return _fence("read_file", {"path": "NOW.md"})
        return "Your week has three open tasks."

    events, calls, executed = _run_loop(
        monkeypatch, replies, relevant_tools={"read_file", "edit_file"})
    assert len(calls) == 2
    assert not any(_nudged(c["messages"]) for c in calls)
    assert _final_text(events) == "Your week has three open tasks."


def _write_only(messages):
    return any("Only the write tools are available" in str(m.get("content")) for m in messages)


def test_loop_breaker_offers_one_write_only_round_before_forcing(monkeypatch):
    executed = []

    def replies(i, messages, kwargs):
        if _write_only(messages):
            if "edit_file" not in executed:
                return _fence("edit_file", {"path": "NOW.md", "old_string": "a", "new_string": "b"})
            return "Ticked the task in NOW.md."
        # Distinct reads every round, so only the read-only stall counter trips.
        return _fence("read_file", {"path": f"notes/part{i}.md"})

    events, calls, _ = _run_loop(
        monkeypatch, replies, relevant_tools={"read_file", "edit_file"},
        max_rounds=14, executed=executed)
    write_round = [c for c in calls if _write_only(c["messages"])]
    assert write_round, "the loop-breaker should offer a write-only round"
    offered = write_round[0]["kwargs"].get("tools") or []
    names = {(t.get("function") or {}).get("name") for t in offered}
    assert names <= {"edit_file", "write_file", "apply_patch"}
    assert "edit_file" in executed
    assert not any("tools are now off" in str(m.get("content"))
                   for c in calls for m in c["messages"])
    assert "Ticked the task" in _final_text(events)


def test_forced_answer_prompt_demands_unfinished_parts():
    none_written = al._loop_breaker_force_answer_prompt(wrote=False)
    assert "nothing has been changed" in none_written
    assert "did NOT finish" in none_written
    assert "added, ticked, logged" in none_written
    some_written = al._loop_breaker_force_answer_prompt(wrote=True)
    assert "report only those changes" in some_written


def test_forced_answer_after_read_stall_uses_honest_prompt(monkeypatch):
    # No write tool offered: the loop-breaker goes straight to the tool-free
    # round, and that round's prompt says nothing was changed.
    def replies(i, messages, kwargs):
        if any("tools are now off" in str(m.get("content")) for m in messages):
            return "Added the row to inventory.md."
        return _fence("read_file", {"path": f"notes/part{i}.md"})

    events, calls, executed = _run_loop(
        monkeypatch, replies, relevant_tools={"read_file"}, max_rounds=14)
    forced = [c for c in calls if any("tools are now off" in str(m.get("content")) for m in c["messages"])]
    assert forced
    prompt = next(m["content"] for m in forced[0]["messages"] if "tools are now off" in str(m.get("content")))
    assert "nothing has been changed" in prompt
    final = _final_text(events)
    assert "Added the row" not in final
    assert final.startswith("Not done:")


@pytest.mark.parametrize("mode", ["off", "on"])
def test_thinking_mode_reaches_every_round(monkeypatch, mode):
    def replies(i, messages, kwargs):
        if i < 2:
            return _fence("read_file", {"path": f"notes/part{i}.md"})
        return "Your week has three open tasks."

    events, calls, executed = _run_loop(
        monkeypatch, replies, relevant_tools={"read_file"}, thinking_mode=mode)
    assert len(calls) == 3
    assert [c["kwargs"].get("thinking_mode") for c in calls] == [mode] * 3


# --- prompt prefix stability (Strata prompt cache) -------------------------

def test_late_system_notes_stay_in_place_for_local_endpoints():
    from src.llm_core import _consolidate_system_messages
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "system", "content": "tools"},
        {"role": "user", "content": "tick the task"},
        {"role": "assistant", "content": "Ticked it."},
        {"role": "system", "content": NO_WRITE_NUDGE},
    ]
    local = _consolidate_system_messages(messages, "http://100.96.143.85:8080/v1/chat/completions")
    assert local[0] == {"role": "system", "content": "persona\n\ntools"}
    assert [m["role"] for m in local] == ["system", "user", "assistant", "user"]
    assert local[-1]["content"] == NO_WRITE_NUDGE
    # Adding a note never changes the tokens before it.
    shorter = _consolidate_system_messages(messages[:-1], "http://127.0.0.1:8080/v1/chat/completions")
    assert local[: len(shorter)] == shorter


def test_hosted_endpoints_keep_the_merged_system_message():
    from src.llm_core import _consolidate_system_messages
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "note"},
    ]
    hosted = _consolidate_system_messages(messages, "https://api.openai.com/v1/chat/completions")
    assert hosted == [{"role": "system", "content": "persona\n\nnote"}, {"role": "user", "content": "hi"}]
