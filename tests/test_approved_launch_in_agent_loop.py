"""An approved bash/python launch must run when the agent loop resumes it.

2026-11-22, chat ``be44ed9d`` (W6): after "Allow for this task", the approved
``mv ... && mv ...`` failed in 0.05 s with "Producer operation has no request
admission or exact claim". Inside the agent loop, execute_tool_block rebinds
the loop's own authority narrowed by tool policy; ``intersect`` marked that
same-request narrowing ``inherited``, and the producer refuses an exact claim
from an inherited authority. Direct dispatch (no active parent) was fine.
"""
import json
from dataclasses import replace

import pytest

import src.agent_loop as al
from src.agent_runtime.authority import OperationGrant, RequestAuthority
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import capabilities_for_action
from tests.test_agent_write_claims import _collect, _events


@pytest.fixture(autouse=True)
def admin_owner(monkeypatch):
    from src import tool_execution as execution
    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: True)


def _authority(workspace, *tools, request_id="approval-request"):
    return RequestAuthority(request_id, "alice", "s", str(workspace),
                            tuple(OperationGrant(t) for t in tools))


# --- binding the active authority's policy narrowing -----------------------

def test_policy_narrowing_of_the_active_authority_binds_as_itself(tmp_path):
    from src.agent_runtime.authority import (
        active_request_authority, bind_active_restriction, bind_request_authority)
    parent = _authority(tmp_path, "bash", "read_file")
    narrowed = parent.restrict(None, {"read_file"})
    with bind_request_authority(parent):
        with bind_active_restriction(narrowed):
            active = active_request_authority()
            assert active == narrowed
            assert active.inherited is False


def test_anything_else_still_binds_as_an_inherited_child(tmp_path):
    from src.agent_runtime.authority import active_request_authority, bind_active_restriction, bind_request_authority
    parent = _authority(tmp_path, "bash")
    # Same request id but different grants is not a policy narrowing: a child.
    child = _authority(tmp_path, "bash", "read_file")
    with bind_request_authority(parent):
        with bind_active_restriction(child):
            assert active_request_authority().inherited is True


def test_widening_is_never_a_restriction(tmp_path):
    from src.agent_runtime.authority import is_policy_restriction_of
    parent = _authority(tmp_path, "bash").restrict(None, {"read_file"})
    assert not is_policy_restriction_of(replace(parent, denied=frozenset()), parent)
    assert not is_policy_restriction_of(replace(parent, inherited=True), parent)
    assert is_policy_restriction_of(parent.restrict(None, {"ls"}), parent)


# --- the W6 path ------------------------------------------------------------

def _approved(authority, cmd, decision="approve_task"):
    store = ToolApprovalStore()
    pending = store.create(
        owner=authority.owner, session_id=authority.session_id, origin_run_id="run",
        tool_name="bash", content=cmd, workspace=authority.workspace, request_authority=authority,
        external_untrusted_context_seen=True, capabilities=capabilities_for_action("bash", cmd),
        selected_tools=["bash", "read_file", "ls"],
        request_text="Rename the chemplot project folder to graphing.")
    return store.consume(pending.approval_id, decision=decision,
                         owner=authority.owner, session_id=authority.session_id)


def _resume(monkeypatch, authority, exact):
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set(), raising=False)

    async def _fake_stream(_candidates, messages, **kwargs):
        yield f'data: {json.dumps({"delta": "Done."})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    return _events(_collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Rename the chemplot project folder to graphing."}],
        owner=authority.owner, session_id=authority.session_id, workspace=authority.workspace,
        relevant_tools={"bash", "read_file", "ls"}, exact_approval=exact,
        request_authority=authority, max_rounds=3)))


@pytest.mark.parametrize("decision", ["approve_task", "approve"])
@pytest.mark.parametrize("grants", [("get_workspace",), ("get_workspace", "bash")])
def test_approved_compound_bash_runs_when_resumed(monkeypatch, tmp_path, grants, decision):
    authority = _authority(tmp_path, *grants)
    cmd = "printf one && printf two"
    events = _resume(monkeypatch, authority, _approved(authority, cmd, decision))
    output = next(e for e in events if e.get("type") == "tool_output" and e.get("approved"))
    assert output["exit_code"] == 0, output
    assert output["output"] == "onetwo"


def test_approved_action_is_single_use_when_resumed(monkeypatch, tmp_path):
    authority = _authority(tmp_path, "get_workspace")
    exact = _approved(authority, "printf once")
    _resume(monkeypatch, authority, exact)
    from src import tool_execution as execution
    from src.tool_capabilities import ToolRunSecurityContext
    from src.tool_types import ToolBlock
    import asyncio
    _, replay = asyncio.run(execution.execute_tool_block(
        ToolBlock("bash", "printf once"), owner=authority.owner, session_id=authority.session_id,
        workspace=authority.workspace, request_authority=authority, exact_approval=exact,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True)))
    assert replay.get("blocked") is True


# --- plain-words failure ------------------------------------------------------

def test_refused_approved_action_is_explained_plainly():
    text = al._approved_action_failure_text(
        "bash", {"failure_kind": "resource_identity_denied", "blocked": True},
        "Producer operation has no request admission or exact claim")
    assert text.startswith("You approved the bash action, but the server refused to start it")
    assert "nothing was changed" in text


def test_ordinary_failure_keeps_its_detail():
    text = al._approved_action_failure_text("edit_file", {"exit_code": 1}, "old_string not found")
    assert text == "The approved edit_file action could not run: old_string not found"


# --- prompt prefix on resume --------------------------------------------------

def test_resume_offers_exactly_the_paused_rounds_tools(monkeypatch, tmp_path, caplog):
    import logging
    authority = _authority(tmp_path, "get_workspace")
    exact = _approved(authority, "printf resumed")
    sealed = set(exact.pending.selected_tools)
    assert sealed == {"bash", "read_file", "ls"}
    with caplog.at_level(logging.INFO, logger="src.agent_loop"):
        # The request text alone would select a wider workspace toolset.
        _resume(monkeypatch, authority, exact)
    selected = [r.getMessage() for r in caplog.records if "[agent-intent] selected_tools=" in r.getMessage()]
    assert selected, "selection log missing"
    assert selected[-1] == f"[agent-intent] selected_tools={sorted(sealed)}"
    assert any("tools pinned to the paused round's 3" in r.getMessage() for r in caplog.records)


def test_resume_never_offers_a_tool_disabled_since_the_pause(monkeypatch, tmp_path, caplog):
    import logging
    authority = _authority(tmp_path, "get_workspace")
    exact = _approved(authority, "printf resumed")
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set(), raising=False)

    async def _fake_stream(_candidates, messages, **kwargs):
        yield f'data: {json.dumps({"delta": "Done."})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    with caplog.at_level(logging.INFO, logger="src.agent_loop"):
        _collect(al.stream_agent_loop(
            "http://x/v1", "m", [{"role": "user", "content": "Rename the chemplot project folder to graphing."}],
            owner=authority.owner, session_id=authority.session_id, workspace=authority.workspace,
            relevant_tools={"bash", "read_file", "ls"}, exact_approval=exact,
            request_authority=authority, disabled_tools={"ls"}, max_rounds=3))
    selected = [r.getMessage() for r in caplog.records if "[agent-intent] selected_tools=" in r.getMessage()]
    assert "'ls'" not in selected[-1]
