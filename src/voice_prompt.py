"""Instructions for live voice-mode turns (static/js/voiceMode.js).

A voice turn's reply is spoken by TTS, so it should sound like speech: short,
no markdown, numbers said naturally. The chat route adds this as an extra
system message only when the request carries ``voice=true``; admins can
replace it with the ``voice_mode_prompt`` setting.
"""
from __future__ import annotations

DEFAULT_VOICE_PROMPT = (
    "## Voice conversation\n"
    "You are talking with the user out loud: their message was transcribed from "
    "speech and your reply will be read aloud by text-to-speech.\n"
    "- Speak, don't format. No markdown, headings, bullet lists, tables, code "
    "blocks, emoji or URLs. Use plain sentences.\n"
    "- Keep it short: one to three sentences unless the user asks for more or "
    "the answer genuinely needs it. Lead with the answer.\n"
    "- Say numbers, dates and units the way a person would (\"about twenty-seven\", "
    "\"next Tuesday\"), and spell out symbols.\n"
    "- If the request is ambiguous, ask one short clarifying question instead of "
    "listing options.\n"
    "- When you use tools, report the result, not the steps you took.\n"
    "- The transcript may contain recognition errors; infer the likely meaning "
    "and only ask if it really is unclear."
)


def voice_prompt() -> str:
    """The configured voice-mode prompt (setting) or the built-in default."""
    try:
        from src.settings import get_setting
        custom = str(get_setting("voice_mode_prompt", "") or "").strip()
    except Exception:
        custom = ""
    return custom or DEFAULT_VOICE_PROMPT
