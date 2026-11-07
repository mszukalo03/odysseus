"""Voice mode maps onto upstream's request-scoped thinking_mode."""
from routes.chat_routes import _voice_thinking_mode


def test_voice_turn_turns_thinking_off_by_default():
    assert _voice_thinking_mode(True, None) == "off"


def test_explicit_request_mode_wins_over_voice():
    assert _voice_thinking_mode(True, "on") == "on"
    assert _voice_thinking_mode(True, "off") == "off"


def test_non_voice_turn_is_unchanged():
    assert _voice_thinking_mode(False, None) is None
    assert _voice_thinking_mode(False, "on") == "on"
