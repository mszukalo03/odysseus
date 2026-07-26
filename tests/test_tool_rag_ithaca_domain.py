"""Regression: the Ithaca hub's agent tools were registered at all ~8 points of
the tool pipeline, yet the chat LLM never called them — asking "any software
updates for today?" got "I don't have real-time data access" instead of a
get_homelab_updates call.

Two independent gates were dropping the turn before the schema reached the model:

1. `src/action_intents.py`'s `classify_tool_intent` had no pattern for these
   questions, so a chat-mode turn was never promoted to agent mode — and
   `routes/chat_routes.py`'s `chat_mode == "chat"` branch calls the LLM with
   `tools=None`, so no tool is callable at all.
2. `_classify_agent_request` here had no `ithaca` domain, so even once promoted
   the turn matched no domain, `low_signal = not continuation and not domains`
   came out True, and `_direct_low_signal` answered from a bare user message
   with no system prompt and no tools (observed: input_tokens=16, tool_calls=0).

This is the same shape as the `contacts` bug (see test_tool_rag_contacts_domain)
and the api_call/`integrations` bug. Both classifiers are deterministic string
matching — no embeddings, no DB — so they can be exercised directly.
"""

from src.agent_loop import (
    _classify_agent_request,
    _DOMAIN_TOOL_MAP,
    _DOMAIN_RULES,
    _domain_rules_for_tools,
)


def _classify(text):
    return _classify_agent_request([{"role": "user", "content": text}], text)


def test_homelab_update_questions_get_ithaca_domain():
    prompts = [
        "any software updates for today?",
        "what needs updating",
        "any app updates",
        "homelab updates",
        "is sonarr outdated",
        "any updates available",
        "is radarr up to date",
        "check my software updates",
    ]
    for p in prompts:
        intent = _classify(p)
        assert "ithaca" in intent["domains"], f"expected ithaca domain for: {p!r}"
        assert intent["low_signal"] is False, f"must not be low_signal: {p!r}"


def test_own_location_weather_questions_get_ithaca_domain():
    prompts = [
        "is it raining",
        "is it cold outside",
        "how cold is it",
        "do i need an umbrella",
        "what's the weather",
        "weather?",
        "temperature outside",
        "what's it like outside",
    ]
    for p in prompts:
        intent = _classify(p)
        assert "ithaca" in intent["domains"], f"expected ithaca domain for: {p!r}"
        assert intent["low_signal"] is False, f"must not be low_signal: {p!r}"


def test_ithaca_domain_seeds_both_hub_tools():
    """The domain must seed the actual tools so they are offered even when
    semantic retrieval misses."""
    assert _DOMAIN_TOOL_MAP["ithaca"] == {"get_home_weather", "get_homelab_updates"}


def test_ithaca_domain_has_a_rule_pack():
    """Every domain in _DOMAIN_TOOL_MAP needs a matching _DOMAIN_RULES entry,
    otherwise _domain_rules_for_tools raises KeyError when the tools are
    selected."""
    assert "ithaca" in _DOMAIN_RULES
    rules = _domain_rules_for_tools({"get_homelab_updates"})
    assert any("homelab update rules" in r for r in rules)


def test_every_domain_tool_map_key_has_rules():
    """Guard the invariant directly, so the next domain added cannot KeyError."""
    assert not set(_DOMAIN_TOOL_MAP) - set(_DOMAIN_RULES)


def test_casual_turns_stay_low_signal():
    """The direct low-signal reply path is a real optimization for greetings —
    widening the domain matchers must not swallow it."""
    # NB: "ok" is deliberately excluded — it is an explicit continuation, which
    # sets low_signal False through a different branch and predates this change.
    for p in ("hi", "hello there", "thanks", "good morning"):
        assert _classify(p)["low_signal"] is True, p


def test_non_ithaca_requests_do_not_match_ithaca_domain():
    """Guard against over-triggering."""
    for p in ("what is the capital of France",
              "reply to the latest email in my inbox",
              "generate an image of a sunset",
              "what's 2 plus 2",
              "any updates on the PR?"):
        assert "ithaca" not in _classify(p)["domains"], p
