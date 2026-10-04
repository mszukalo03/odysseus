"""The agent answers at its step cap instead of stopping silently.

A model that keeps exploring with distinct calls never trips the loop-breaker
(which only catches repeats). The budget guard turns the final round into a
tool-free answer round and still emits ``rounds_exhausted`` for Continue.
"""
import asyncio
import json

import src.agent_loop as al
from src.agent_budget import (
    FINAL_NOTE,
    PROJECT_MODE_TOOL_STREAK,
    AgentBudgetGuard,
)


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


def _run(monkeypatch, *, max_rounds, setting_enabled=True):
    monkeypatch.setattr(
        al, "get_setting",
        lambda key, default=None: setting_enabled if key == "agent_answer_on_cap" else default,
        raising=False,
    )
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

    async def _fake_exec(block, *a, **k):
        return (block.tool_type, {"output": "ok", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    seen_tools = []
    calls = {"n": 0}

    async def _fake_stream(_candidates, messages, **kwargs):
        calls["n"] += 1
        tools = kwargs.get("tools")
        seen_tools.append(bool(tools))
        if any(m.get("content") == FINAL_NOTE for m in messages):
            yield f'data: {json.dumps({"delta": "There are 27 papers in the queue."})}\n\n'
        else:
            # A new, distinct plan step every round: never a repeat.
            plan = json.dumps({"plan": f"- [ ] step {calls['n']}"})
            yield f'data: {json.dumps({"delta": "```update_plan\n" + plan + "\n```"})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "how many papers are in my reading queue?"}],
        max_rounds=max_rounds,
        relevant_tools={"update_plan"},
    )
    return _events(_collect(gen)), seen_tools


def test_final_round_answers_and_still_offers_continue(monkeypatch):
    events, _ = _run(monkeypatch, max_rounds=6)
    text = "".join(e.get("delta", "") for e in events if "delta" in e)
    assert "27 papers" in text
    assert any(e.get("type") == "rounds_exhausted" for e in events)


def test_setting_off_keeps_old_behaviour(monkeypatch):
    events, _ = _run(monkeypatch, max_rounds=6, setting_enabled=False)
    text = "".join(e.get("delta", "") for e in events if "delta" in e)
    assert "27 papers" not in text
    assert any(e.get("type") == "rounds_exhausted" for e in events)


# --- guard unit behaviour -------------------------------------------------

def test_small_caps_are_left_alone():
    g = AgentBudgetGuard(4)
    assert g.before_round(4, tools_used=True) is None
    assert g.on_tool_budget_hit(1) is False


def test_no_note_before_any_tool_use():
    g = AgentBudgetGuard(10)
    assert g.before_round(10, tools_used=False) is None


def test_wrap_up_warning_then_final():
    g = AgentBudgetGuard(10)
    assert g.before_round(7, tools_used=True) is None
    warn = g.before_round(8, tools_used=True)
    assert warn and not warn.force_answer and "2 tool step" in warn.text
    assert g.before_round(9, tools_used=True) is None
    final = g.before_round(10, tools_used=True)
    assert final and final.force_answer and g.answered_at_cap


def test_project_mode_streak_nudge_once():
    g = AgentBudgetGuard(50, project_mode=True)
    for _ in range(PROJECT_MODE_TOOL_STREAK):
        g.after_round(tool_calls=2, has_text=False)
    note = g.before_round(10, tools_used=True)
    assert note and not note.force_answer and "16 tool calls" in note.text
    assert g.before_round(11, tools_used=True) is None


def test_streak_resets_on_text_and_is_off_outside_project_mode():
    g = AgentBudgetGuard(50, project_mode=False)
    for _ in range(PROJECT_MODE_TOOL_STREAK + 3):
        g.after_round(tool_calls=1, has_text=False)
    assert g.before_round(12, tools_used=True) is None


def test_tool_budget_hit_forces_next_round():
    g = AgentBudgetGuard(10)
    assert g.on_tool_budget_hit(3) is True
    note = g.before_round(4, tools_used=True)
    assert note and note.force_answer
    assert AgentBudgetGuard(10).on_tool_budget_hit(10) is False
    assert AgentBudgetGuard(10, enabled=False).on_tool_budget_hit(3) is False
