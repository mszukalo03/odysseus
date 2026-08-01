from src.action_intents import classify_tool_intent, message_needs_tools


def test_calendar_entry_request_promotes_to_agent():
    assert message_needs_tools("Can you add an entry to my calendar?")
    intent = classify_tool_intent("Can you add an entry to my calendar?")
    assert intent.needs_tools
    assert intent.category == "calendar"


def test_calendar_imperative_variants_promote_to_agent():
    assert message_needs_tools("add lunch with Sam to my calendar tomorrow at noon")
    assert message_needs_tools("schedule a call with Mina next Friday")
    assert message_needs_tools("put dentist appointment on my calendar")
    assert message_needs_tools("Alright. Recreate that same appointment")
    assert message_needs_tools("Okay delete that doctor appointment from the calendar")
    assert message_needs_tools("have another go at adding a test entry to the calendar")
    assert message_needs_tools(
        "Okay so you should be able to create that calendar event for tomorrow at 1:30 p.m. right for me to go to the hardware store"
    )
    assert message_needs_tools(
        "make it an appointment at 12pm for me to visit the doctor it's tomorrow the 2nd of June 2026"
    )


def test_calendar_read_requests_promote_to_agent():
    assert message_needs_tools("What upcoming events do I have?")
    assert message_needs_tools("Can you show my next appointments?")
    assert message_needs_tools("Do I have upcoming Taekwondo classes this week?")
    assert message_needs_tools("What's on my calendar tomorrow?")
    assert message_needs_tools("When is my next meeting?")


def test_note_todo_and_reminder_actions_promote_to_agent():
    assert message_needs_tools("add milk to my todo list")
    assert message_needs_tools("take a note that the server needs checking")
    assert message_needs_tools("set a reminder to call Pat at 4pm")


def test_email_and_ui_actions_promote_to_agent():
    assert message_needs_tools("reply to that email")
    assert message_needs_tools("mark those emails as read")
    assert message_needs_tools("open my calendar")
    assert message_needs_tools("turn off web search")


def test_research_action_promotes_to_agent():
    assert message_needs_tools("research cost effective local models")
    assert message_needs_tools("can you look into GPU hosting options")


def test_explicit_web_search_promotes_to_agent():
    assert message_needs_tools("use web search and find a recipe for chocolate chip cookies")
    assert message_needs_tools("do a web search for the best chocolate chip cookies")
    assert message_needs_tools("search the web for current RTX 3090 prices")
    assert classify_tool_intent("use web search and find a recipe").category == "web"


def test_workspace_agent_requests_promote_to_shell_workspace():
    prompts = [
        "fix the bug in this repo",
        "run the tests for this project",
        "debug the server logs",
        "run terminal-bench on this task",
        "inspect the traceback and patch the code",
    ]
    for prompt in prompts:
        intent = classify_tool_intent(prompt)
        assert intent.needs_tools
        assert intent.category == "workspace"


def test_explanatory_calendar_questions_stay_plain_chat():
    assert not message_needs_tools("How do I add an entry to my calendar?")
    assert not message_needs_tools("What about the built-in Odysseus calendar, is that linked to email?")
    assert not message_needs_tools("Can you explain how calendar reminders work?")
    intent = classify_tool_intent("How do I add an entry to my calendar?")
    assert not intent.needs_tools
    assert intent.reason == "explanatory feature question"


def test_router_reports_non_calendar_categories():
    assert classify_tool_intent("reply to that email").category == "email"
    assert classify_tool_intent("open my calendar").category == "ui"
    assert classify_tool_intent("research cost effective local models").category == "research"


# ─── Ithaca hub tools (get_home_weather / query_ithaca_tile) ─────────────────
#
# Chat mode passes tools=None, so an unmatched intent here means the model
# answers from its training data instead of calling the tool. Regression guard
# for exactly that: these phrasings used to return needs_tools=False.


def test_own_location_weather_questions_promote_to_agent():
    # Indirect phrasings that never name "weather" next to a qualifier, so the
    # pre-existing web patterns missed them entirely.
    prompts = [
        "is it going to rain",
        "is it raining",
        "will it rain later",
        "how cold is it",
        "how hot is it out",
        "is it cold outside",
        "do i need an umbrella",
        "what's it like outside",
        "what's the temperature",
        "temperature outside",
        "weather?",
    ]
    for prompt in prompts:
        intent = classify_tool_intent(prompt)
        assert intent.needs_tools, prompt
        assert intent.category == "weather", prompt


def test_weather_phrasings_already_claimed_by_web_keep_that_category():
    # The Ithaca patterns are appended last on purpose, so a phrasing an older
    # web pattern already matched is left exactly as it was. It still escalates,
    # and tool RAG offers get_home_weather alongside web_search either way — the
    # category only drives logging and the workspace/shell tool carve-out.
    for prompt in ("how's the weather", "what's the weather", "whats the weather today"):
        intent = classify_tool_intent(prompt)
        assert intent.needs_tools, prompt
        assert intent.category == "web", prompt


def test_location_qualified_weather_still_routes_to_web():
    # "weather in Tokyo" is not the user's own location, so it must keep the
    # web category and reach web_search rather than get_home_weather.
    for prompt in ("whats the weather in tokyo", "weather in London?"):
        intent = classify_tool_intent(prompt)
        assert intent.needs_tools, prompt
        assert intent.category == "web", prompt


def test_homelab_update_questions_promote_to_agent():
    prompts = [
        "any software updates for today",
        "what software updates are there today",
        "do any of my homelab apps need updating",
        "is jellyfin up to date",
        "what needs updating",
        "any app updates",
        "homelab updates",
        "do i have any updates",
        "what version of radarr is running",
        "is sonarr outdated",
        "any updates available",
        "check my software updates",
        "which apps are flagged for review",
    ]
    for prompt in prompts:
        intent = classify_tool_intent(prompt)
        assert intent.needs_tools, prompt
        assert intent.category == "ithaca_tile", prompt


def test_ithaca_patterns_do_not_steal_coding_or_status_turns():
    # Placed last in _ROUTING_PATTERNS precisely so these keep their old
    # behavior. A coding turn mis-tagged as ithaca_tile would lose bash/python/
    # read_file/write_file, which auto-escalation withholds for every category
    # except shell/workspace.
    for prompt in ("update the readme in my repo", "fix the failing tests in the codebase"):
        assert classify_tool_intent(prompt).category == "workspace", prompt
    assert classify_tool_intent("search the web for jellyfin news").category == "web"
    # "any updates on X" is a status-chase, not a homelab lookup.
    assert not message_needs_tools("any updates on the PR?")
    assert not message_needs_tools("how do i update my resume")
