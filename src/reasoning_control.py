"""Per-request thinking level → provider-specific request fields.

The chat composer (and a persona default) can ask for a thinking level:
``off``, ``low``, ``medium`` or ``high``. ``None``/``"auto"`` means "leave the
request exactly as before", which is the default everywhere, so nothing
changes unless a level is chosen.

Each provider spells this differently, and strict cloud APIs reject fields
they don't know (OpenAI 400, Mistral 422), so a field is only added where the
target is known to accept it:

* self-hosted OpenAI-compatible servers (llama.cpp ``llama-server``, vLLM,
  SGLang; see ``llm_core._is_self_hosted_openai_compatible``):
  ``chat_template_kwargs.enable_thinking`` (Qwen3/3.5, DeepSeek-V3.1, GLM...),
  plus ``reasoning_effort`` inside the template kwargs for gpt-oss templates;
* Ollama (native and its ``/v1`` API): ``think`` (bool, or the level string
  for gpt-oss, which takes low/medium/high);
* OpenAI-hosted reasoning models: ``reasoning_effort``;
* Google Gemini's OpenAI-compatible API: ``reasoning_effort``;
* OpenRouter: ``reasoning: {effort}`` / ``reasoning: {enabled: false}``;
* Mistral thinking models: ``reasoning_effort`` (overrides the env default).

Anthropic, Groq, DeepSeek and other providers are left untouched for now.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

LEVELS = ("off", "low", "medium", "high")


def normalize_level(value: Any) -> Optional[str]:
    """A valid level, or None for auto/unknown/empty."""
    if value is None:
        return None
    level = str(value).strip().lower()
    if level in {"", "auto", "default"}:
        return None
    if level in {"none", "false", "0", "no"}:
        return "off"
    if level in {"on", "true", "1", "yes"}:
        return "medium"
    if level in {"med", "mid"}:
        return "medium"
    return level if level in LEVELS else None


def _is_gpt_oss(model: str) -> bool:
    return "gpt-oss" in (model or "").lower()


def _openai_effort(model: str, level: str) -> Optional[str]:
    """``reasoning_effort`` for an OpenAI-hosted model, or None to skip."""
    m = (model or "").lower()
    is_reasoning = m.startswith(("o1", "o3", "o4")) or m.startswith("gpt-5") or "/o3" in m or "/o4" in m
    if not is_reasoning:
        return None
    if level == "off":
        # gpt-5.1+ accepts "none"; gpt-5 and the o-series bottom out at
        # "minimal" / "low".
        if m.startswith("gpt-5.") or m.startswith("gpt-5-") and m[6:7].isdigit():
            return "none"
        if m.startswith("gpt-5"):
            return "minimal"
        return "low"
    return level


def apply_reasoning_level(
    payload: Dict[str, Any],
    *,
    provider: str,
    url: str,
    model: str,
    level: Any,
) -> Dict[str, Any]:
    """Return ``payload`` with the thinking level applied (mutated in place)."""
    level = normalize_level(level)
    if level is None or not isinstance(payload, dict):
        return payload

    # Imported lazily: llm_core imports this module.
    from src import llm_core

    on = level != "off"
    ollama_think = level if (on and _is_gpt_oss(model)) else on

    # llm_core treats any loopback /v1 as possibly Ollama; only the default
    # Ollama port is certain. A loopback llama.cpp/vLLM gets both fields --
    # each server ignores the one it doesn't use.
    ollama_compat = llm_core._is_ollama_openai_compat_url(url)
    if provider == "ollama" or (ollama_compat and urlparse(url).port == 11434):
        payload["think"] = ollama_think
        return payload
    if ollama_compat:
        payload["think"] = ollama_think

    if provider == "openrouter":
        payload["reasoning"] = {"effort": level} if on else {"enabled": False}
        return payload

    if provider == "mistral":
        payload["reasoning_effort"] = level if on else "none"
        return payload

    if provider != "openai":
        return payload

    if llm_core._is_self_hosted_openai_compatible(url):
        kwargs = dict(payload.get("chat_template_kwargs") or {})
        kwargs["enable_thinking"] = on
        if _is_gpt_oss(model):
            kwargs["reasoning_effort"] = level if on else "low"
        payload["chat_template_kwargs"] = kwargs
        return payload

    if llm_core._host_match(url, "generativelanguage.googleapis.com"):
        payload["reasoning_effort"] = level if on else "none"
        return payload

    if llm_core._host_match(url, "openai.com"):
        effort = _openai_effort(model, level)
        if effort:
            payload["reasoning_effort"] = effort
        return payload

    return payload
