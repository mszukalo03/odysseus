"""Tool-approval pauses must not teach the model to answer with the card.

2026-11-22, chat ``522edf36``: W1 and W2 each paused at the approval gate and
were saved as the assistant message "Allow this task to continue?". Both went
into later prompts as plain assistant history, and from W3 on the model
answered every request with that question alone, no tool call, nothing
written, and the completion gate released it as ``unverified``.
"""
import pytest

from core.models import ChatMessage, Session
from src.agent_evidence import CompletionStatus, EvidenceLedger
from src.agent_runtime.completion import APPROVAL_ECHO_REASON, completion_answer
from src.tool_approval_scopes import TOOL_APPROVAL_QUESTION, is_approval_placeholder


def _session(history):
    return Session(id="s1", name="t", endpoint_url="http://x/v1", model="m", history=history)


def _approval_metadata(status="awaiting_user"):
    return {
        "completion_decision": {"status": status, "can_complete": False},
        "render_owner": "structured",
        "round_texts": ["", TOOL_APPROVAL_QUESTION],
        "tool_events": [{
            "tool": "edit_file", "exit_code": None, "approval_required": True,
            "ask_user": {"kind": "tool_approval", "approval_id": "a1",
                         "question": TOOL_APPROVAL_QUESTION},
        }],
    }


def test_card_question_uses_the_shared_constant():
    import inspect
    import src.tool_approvals as tool_approvals
    source = inspect.getsource(tool_approvals)
    assert '"question": TOOL_APPROVAL_QUESTION' in source
    assert '"Allow this task to continue?"' not in source


@pytest.mark.parametrize("content", [
    TOOL_APPROVAL_QUESTION,
    "  allow this task to continue?\n",
])
def test_placeholder_detection(content):
    assert is_approval_placeholder("assistant", content)


@pytest.mark.parametrize("role,content", [
    ("user", TOOL_APPROVAL_QUESTION),
    ("assistant", "Done. Added the idea to INBOX.md."),
    ("assistant", "Which do you want: copy or move?"),
    ("assistant", "Allow this task to continue? I need to edit NOW.md first."),
])
def test_real_messages_are_not_placeholders(role, content):
    assert not is_approval_placeholder(role, content)


def test_context_drops_approval_pauses_and_copied_questions():
    history = [
        ChatMessage("user", "Add an idea to my inbox: a magnetic cable organiser."),
        ChatMessage("assistant", TOOL_APPROVAL_QUESTION, _approval_metadata()),
        ChatMessage("assistant", 'Done. Added "Magnetic cable organiser" to INBOX.md.', {}),
        ChatMessage("user", "Add to inventory: USB hub."),
        # The model's copy of the card (W3): saved as an ordinary answer.
        ChatMessage("assistant", TOOL_APPROVAL_QUESTION,
                    {"completion_decision": {"status": "unverified", "can_complete": True}}),
        ChatMessage("user", "Rename the chemplot folder to graphing."),
        ChatMessage("assistant", "Which do you want: copy or move?",
                    {"completion_decision": {"status": "awaiting_user"}}),
        ChatMessage("user", "Copy."),
    ]
    context = _session(history).get_context_messages()
    assert [(m["role"], m["content"]) for m in context] == [
        ("user", "Add an idea to my inbox: a magnetic cable organiser."),
        ("assistant", 'Done. Added "Magnetic cable organiser" to INBOX.md.'),
        ("user", "Add to inventory: USB hub."),
        ("user", "Rename the chemplot folder to graphing."),
        ("assistant", "Which do you want: copy or move?"),
        ("user", "Copy."),
    ]


def test_transcript_history_keeps_the_card():
    history = [
        ChatMessage("user", "Tick the task."),
        ChatMessage("assistant", TOOL_APPROVAL_QUESTION, _approval_metadata()),
    ]
    session = _session(history)
    session.get_context_messages()
    assert [m.content for m in session.history] == ["Tick the task.", TOOL_APPROVAL_QUESTION]


# --- completion gate ------------------------------------------------------

def _ledger(*events):
    return EvidenceLedger.from_tool_events(list(events))


def test_gate_replaces_a_copied_question_when_nothing_is_pending():
    ledger = _ledger({"tool": "read_file", "command": "NOW.md", "exit_code": 0})
    answer, reason = completion_answer(TOOL_APPROVAL_QUESTION, ledger, ledger.evaluate())
    assert reason == APPROVAL_ECHO_REASON
    assert answer == "Not done: no approval is pending and nothing was written in this turn."


def test_gate_flags_a_copied_question_after_a_write():
    ledger = _ledger({"tool": "edit_file", "command": '{"path": "NOW.md"}', "exit_code": 0})
    answer, reason = completion_answer(TOOL_APPROVAL_QUESTION, ledger, ledger.evaluate())
    assert reason == APPROVAL_ECHO_REASON
    assert answer.startswith("No approval is pending.")


def test_gate_keeps_a_real_approval_pause():
    ledger = _ledger()
    decision = ledger.evaluate(awaiting_user=True)
    assert decision.status == CompletionStatus.AWAITING_USER
    answer, reason = completion_answer(TOOL_APPROVAL_QUESTION, ledger, decision)
    assert (answer, reason) == (TOOL_APPROVAL_QUESTION, "")


def test_gate_leaves_other_questions_alone():
    ledger = _ledger({"tool": "read_file", "command": "NOW.md", "exit_code": 0})
    text = "Which do you want: copy or move?"
    assert completion_answer(text, ledger, ledger.evaluate()) == (text, "")


# --- agent loop -----------------------------------------------------------

def test_loop_answer_that_copies_the_card_is_replaced(monkeypatch):
    from tests.test_agent_write_claims import _final_text, _fence, _run_loop

    def replies(i, messages, kwargs):
        if i == 0:
            return _fence("read_file", {"path": "NOW.md"})
        return TOOL_APPROVAL_QUESTION

    events, calls, executed = _run_loop(
        monkeypatch, replies, relevant_tools={"read_file", "edit_file"})
    assert executed == ["read_file"]
    final = _final_text(events)
    assert final == "Not done: no approval is pending and nothing was written in this turn."
    decision = next(e for e in events if e.get("type") == "completion_decision")
    assert decision["data"]["status"] != "awaiting_user"
