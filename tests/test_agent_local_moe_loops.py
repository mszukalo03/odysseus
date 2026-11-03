"""Agent-loop guards for local (MoE) models that think and call tools in loops.

* repeated identical workspace reads are answered with a pointer to the
  earlier result instead of running again, and two rounds of nothing but
  repeats force a tool-free answer;
* earlier reasoning is not echoed back into the history;
* mid-turn system notes stay in place for self-hosted endpoints so the
  prompt prefix (and the server's prompt cache) is not rewritten;
* a truncated read_file says where to continue.
"""
import asyncio
import json

import pytest

import src.agent_loop as al
from src import llm_core
from src.agent_repeat_guard import (
    DUPLICATE_ROUNDS_TO_FORCE,
    FORCE_ANSWER_AFTER_REPEATS_NOTE,
    ToolRepeatGuard,
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


# --- repeat guard unit behaviour -------------------------------------------

def test_repeated_read_is_flagged_with_its_first_round():
    g = ToolRepeatGuard()
    args = '{"path": "notes/a.md"}'
    assert g.prior_round("read_file", args) is None
    g.record("read_file", args, 2, {"output": "x"})
    # Same args, different key order / whitespace: still the same call.
    assert g.prior_round("read_file", '{ "path":"notes/a.md" }') == 2
    assert g.prior_round("read_file", '{"path": "notes/a.md", "offset": 40}') is None
    result = g.duplicate_result("read_file", 2)
    assert "step 2" in result["output"] and result["exit_code"] == 0


def test_a_write_clears_the_cache():
    g = ToolRepeatGuard()
    g.record("grep", '{"pattern": "status"}', 1, {"output": "x"})
    g.record("edit_file", '{"path": "a.md"}', 2, {"output": "ok"})
    assert g.prior_round("grep", '{"pattern": "status"}') is None


def test_other_reads_keep_the_cache():
    g = ToolRepeatGuard()
    g.record("ls", '{"path": "."}', 1, {"output": "x"})
    g.record("read_file", '{"path": "b.md"}', 2, {"output": "y"})
    assert g.prior_round("ls", '{"path": "."}') == 1


def test_non_workspace_tools_are_never_skipped():
    g = ToolRepeatGuard()
    g.record("bash", "ls", 1, {"output": "x"})
    assert g.prior_round("bash", "ls") is None
    g.record("web_search", '{"query": "x"}', 1, {"output": "x"})
    assert g.prior_round("web_search", '{"query": "x"}') is None


def test_disabled_guard_does_nothing():
    g = ToolRepeatGuard(enabled=False)
    g.record("read_file", "a.md", 1, {})
    assert g.prior_round("read_file", "a.md") is None
    assert not any(g.end_round(calls=1, duplicates=1) for _ in range(5))


def test_force_after_consecutive_duplicate_only_rounds():
    g = ToolRepeatGuard()
    assert g.end_round(calls=2, duplicates=2) is (DUPLICATE_ROUNDS_TO_FORCE == 1)
    assert g.end_round(calls=1, duplicates=0) is False  # progress resets
    for _ in range(DUPLICATE_ROUNDS_TO_FORCE - 1):
        assert g.end_round(calls=1, duplicates=1) is False
    assert g.end_round(calls=1, duplicates=1) is True


# --- repeat guard in the loop ----------------------------------------------

def _run_loop(monkeypatch, script, *, settings=None, endpoint="http://x/v1", max_rounds=10):
    """Run stream_agent_loop with a scripted model. ``script(n, messages,
    kwargs)`` returns the text for round n."""
    settings = settings or {}
    monkeypatch.setattr(al, "get_setting", lambda k, d=None: settings.get(k, d), raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set(), raising=False)

    class _AllowAll(al.ToolRunSecurityContext):
        def decision_for(self, *a, **k):
            return type("D", (), {"allowed": True, "reason": ""})()
    monkeypatch.setattr(al, "ToolRunSecurityContext", _AllowAll, raising=False)

    executed = []

    async def _fake_exec(block, *a, **k):
        executed.append((block.tool_type, block.content))
        return (f"{block.tool_type}: {block.content}", {"output": f"contents of {block.content}", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    requests = []

    async def _fake_stream(_candidates, messages, **kwargs):
        requests.append(([dict(m) for m in messages], kwargs))
        text = script(len(requests), messages, kwargs)
        yield f'data: {json.dumps({"delta": text})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        endpoint, "m",
        [{"role": "user", "content": "what's the status of the heater task?"}],
        max_rounds=max_rounds,
        relevant_tools={"read_file", "edit_file"},
    )
    return _events(_collect(gen)), executed, requests


READ = "```read_file\nnotes/heater.md\n```"


def test_loop_skips_repeat_and_forces_answer(monkeypatch):
    def script(n, messages, kwargs):
        if any(m.get("content") == FORCE_ANSWER_AFTER_REPEATS_NOTE for m in messages):
            return "The heater task is done."
        return READ  # the same read, every round

    events, executed, requests = _run_loop(monkeypatch, script)
    # Ran once; the repeats were answered by the guard.
    assert executed == [("read_file", "notes/heater.md")]
    outputs = [e["output"] for e in events if e.get("type") == "tool_output"]
    assert len(outputs) == 1 + DUPLICATE_ROUNDS_TO_FORCE
    assert all("Not run again" in o for o in outputs[1:])
    text = "".join(e.get("delta", "") for e in events if "delta" in e)
    assert "heater task is done" in text
    # The answer round ran without tools.
    assert not requests[-1][1].get("tools")
    assert len(requests) == 2 + DUPLICATE_ROUNDS_TO_FORCE


def test_loop_reruns_read_after_a_write(monkeypatch):
    rounds = [
        READ,
        "```edit_file\nnotes/heater.md\nold\nnew\n```",
        READ,
        "Updated.",
    ]

    def script(n, messages, kwargs):
        return rounds[n - 1]

    _, executed, _ = _run_loop(monkeypatch, script)
    assert [t for t, _ in executed].count("read_file") == 2


def test_setting_off_runs_every_repeat(monkeypatch):
    rounds = [READ, READ, "Done."]

    def script(n, messages, kwargs):
        return rounds[n - 1]

    _, executed, _ = _run_loop(monkeypatch, script, settings={"agent_skip_duplicate_reads": False})
    assert len(executed) == 2


# --- reasoning in history ---------------------------------------------------

def test_strip_reasoning_text_shapes():
    assert al._strip_reasoning_text("<think>plan</think>\nAnswer") == "Answer"
    # Template opened <think> in the prompt; only the closer is in the output.
    assert al._strip_reasoning_text("let me see...\n</think>\n\nAnswer") == "Answer"
    assert al._strip_reasoning_text("No reasoning here") == "No reasoning here"
    assert al._strip_reasoning_text(None) is None


def test_history_reasoning_stripped_without_mutating_input():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "<think>long musing</think>Hello!", "reasoning_content": "r"},
        {"role": "user", "content": "<think> is a tag I typed</think>"},
    ]
    out = al._strip_history_reasoning(history)
    assert out[1] == {"role": "assistant", "content": "Hello!"}
    assert out[2] == history[2]  # user text is never touched
    assert history[1]["reasoning_content"] == "r"


def _run_reasoning_loop(monkeypatch, *, mode, endpoint="http://x/v1"):
    def script(n, messages, kwargs):
        return "<think>I should read the note.</think>" + READ if n == 1 else (
            "<think>reading more</think>```read_file\nnotes/other.md\n```" if n == 2 else "Done.")
    history_settings = {"agent_reasoning_history": mode} if mode else {}
    return _run_loop(monkeypatch, script, settings=history_settings, endpoint=endpoint)


def test_inline_think_never_echoed_back(monkeypatch):
    _, _, requests = _run_reasoning_loop(monkeypatch, mode="latest")
    later = requests[-1][0]
    for m in later:
        if m.get("role") == "assistant":
            assert "<think>" not in (m.get("content") or "")
            assert "I should read the note" not in (m.get("content") or "")


def test_append_tool_results_echo_modes():
    def _round(messages, mode, n):
        al._append_tool_results(
            messages, f"answer {n}", [{"id": f"c{n}", "name": "read_file", "arguments": "{}"}],
            [f"r{n}"], [f"r{n}"], True, n, round_reasoning=f"thought {n}",
            echo_reasoning=mode,
        )

    latest = []
    _round(latest, "latest", 1)
    _round(latest, "latest", 2)
    assistants = [m for m in latest if m["role"] == "assistant"]
    assert "reasoning_content" not in assistants[0]
    assert assistants[1]["reasoning_content"] == "thought 2"

    none = []
    _round(none, "none", 1)
    snapshot = json.dumps(none)
    _round(none, "none", 2)
    # Round 2 only appended: what round 1 sent is byte-identical.
    assert json.dumps(none[: len(json.loads(snapshot))]) == snapshot
    assert not any("reasoning_content" in m for m in none)


@pytest.mark.parametrize("local,expected", [(True, "none"), (False, "latest")])
def test_echo_mode_auto_follows_endpoint(monkeypatch, local, expected):
    monkeypatch.setattr(al, "get_setting", lambda k, d=None: d, raising=False)
    monkeypatch.setattr(llm_core, "_is_self_hosted_openai_compatible", lambda url: local)
    assert al._echo_reasoning_mode("http://strata:8080/v1") == expected


def test_echo_mode_setting_wins(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda k, d=None: "latest" if k == "agent_reasoning_history" else d, raising=False)
    monkeypatch.setattr(llm_core, "_is_self_hosted_openai_compatible", lambda url: True)
    assert al._echo_reasoning_mode("http://strata:8080/v1") == "latest"


# --- system notes and the prompt prefix ------------------------------------

def _turn():
    return [
        {"role": "system", "content": "agent prompt"},
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "result"},
        {"role": "system", "content": "[Odysseus step budget] wrap up"},
    ]


def test_self_hosted_keeps_mid_turn_notes_in_place(monkeypatch):
    monkeypatch.setattr(llm_core, "_is_self_hosted_openai_compatible", lambda url: True)
    out = llm_core._consolidate_system_messages(_turn(), "http://strata/v1")
    assert out[0] == {"role": "system", "content": "agent prompt\n\npersona"}
    assert out[-1] == {"role": "user", "content": "[Odysseus step budget] wrap up"}
    # Same leading prefix as the round before the note was added.
    before = llm_core._consolidate_system_messages(_turn()[:-1], "http://strata/v1")
    assert out[: len(before)] == before


def test_note_after_user_message_is_merged(monkeypatch):
    monkeypatch.setattr(llm_core, "_is_self_hosted_openai_compatible", lambda url: True)
    msgs = [
        {"role": "system", "content": "p"},
        {"role": "user", "content": "tool results"},
        {"role": "system", "content": "note"},
    ]
    out = llm_core._consolidate_system_messages(msgs, "http://strata/v1")
    assert out == [{"role": "system", "content": "p"}, {"role": "user", "content": "tool results\n\nnote"}]


def test_cloud_endpoints_keep_folding_into_first(monkeypatch):
    monkeypatch.setattr(llm_core, "_is_self_hosted_openai_compatible", lambda url: False)
    out = llm_core._consolidate_system_messages(_turn(), "https://api.openai.com/v1")
    assert out[0]["content"].endswith("[Odysseus step budget] wrap up")
    assert [m["role"] for m in out].count("system") == 1


# --- read_file continuation ------------------------------------------------

def _read(monkeypatch, tmp_path, args):
    import src.tool_execution as te
    from src.agent_tools.filesystem_tools import ReadFileTool
    monkeypatch.setattr(te, "_resolve_tool_path", lambda p: str(tmp_path / p))
    return asyncio.run(ReadFileTool().execute(json.dumps(args), {}))["output"]


def test_read_file_truncation_names_next_offset(monkeypatch, tmp_path):
    from src.agent_tools import filesystem_tools as ft
    monkeypatch.setattr(ft, "MAX_READ_CHARS", 100)
    (tmp_path / "big.md").write_text("".join(f"line {i:03d} xxxxxxxxxx\n" for i in range(1, 51)))
    out = _read(monkeypatch, tmp_path, {"path": "big.md"})
    assert "showed lines 1-5" in out and "offset=6" in out
    nxt = _read(monkeypatch, tmp_path, {"path": "big.md", "offset": 6})
    assert nxt.startswith("line 006")


def test_read_file_limit_says_more_follows(monkeypatch, tmp_path):
    (tmp_path / "a.md").write_text("".join(f"{i}\n" for i in range(1, 11)))
    out = _read(monkeypatch, tmp_path, {"path": "a.md", "offset": 3, "limit": 2})
    assert out.startswith("3\n4\n")
    assert "more lines follow" in out and "offset=5" in out
    assert "more lines" not in _read(monkeypatch, tmp_path, {"path": "a.md", "offset": 9, "limit": 5})


def test_read_file_small_file_unchanged(monkeypatch, tmp_path):
    (tmp_path / "s.md").write_text("one\ntwo\n")
    assert _read(monkeypatch, tmp_path, {"path": "s.md"}) == "one\ntwo\n"
