"""trim_for_context keeps workspace instructions and the user's request.

A vault persona's long tool exploration used to (a) truncate the merged
persona+instructions system prompt to 2000 chars and (b) drop the user's
question once enough tool results piled up, leaving the model with neither.
"""
from src.context_compactor import estimate_tokens, trim_for_context
from src.prompt_security import untrusted_context_message


def _tool_round(i, size=4000):
    return [
        {"role": "assistant", "content": f"checking file {i}"},
        untrusted_context_message("tool execution results", f"result {i} " + "x" * size),
    ]


def _convo(rounds):
    msgs = [{"role": "user", "content": "how many papers are in my reading queue?"}]
    for i in range(rounds):
        msgs += _tool_round(i)
    return msgs


def test_lead_protected_instructions_survive_and_stay_first():
    instructions = {"role": "system", "content": "PROJECT RULES " + "r" * 6000, "_protected": "lead"}
    agent = {"role": "system", "content": "AGENT PROMPT " + "a" * 6000}
    msgs = [instructions, agent] + _convo(12)
    out = trim_for_context(msgs, 6000)
    assert out[0]["content"] == instructions["content"]
    assert estimate_tokens(out) < estimate_tokens(msgs)


def test_user_question_is_pinned_through_long_tool_exchange():
    msgs = [{"role": "system", "content": "sys"}] + _convo(20)
    out = trim_for_context(msgs, 4000)
    contents = [m.get("content") for m in out]
    assert "how many papers are in my reading queue?" in contents
    # The newest tool result (current turn) is still last.
    assert out[-1] is msgs[-1] or out[-1].get("content") == msgs[-1].get("content")
    # The question comes before every remaining tool exchange.
    q = contents.index("how many papers are in my reading queue?")
    assert all(m.get("role") != "user" or (m.get("metadata") or {}).get("trusted") is False
               for m in out[q + 1:])


def test_untouched_when_under_budget():
    msgs = [{"role": "system", "content": "s", "_protected": "lead"}] + _convo(1)
    assert trim_for_context(msgs, 100_000) is msgs


def test_plain_conversation_behaviour_unchanged():
    # Chat without tools: the latest user message is the last message, so
    # pinning adds nothing new.
    msgs = [{"role": "system", "content": "s"}]
    for i in range(30):
        msgs += [{"role": "user", "content": f"q{i} " + "y" * 800},
                 {"role": "assistant", "content": f"a{i} " + "z" * 800}]
    msgs.append({"role": "user", "content": "final"})
    out = trim_for_context(msgs, 3000)
    assert out[-1]["content"] == "final"
    assert sum(1 for m in out if m["content"] == "final") == 1
