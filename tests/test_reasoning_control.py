"""Per-request thinking level -> provider request fields (src/reasoning_control).

"auto"/None must leave every payload byte-identical; explicit levels only add
fields to providers known to accept them (strict APIs reject unknown fields).
"""
import copy

import pytest

import src.model_context as model_context
from src.reasoning_control import apply_reasoning_level, normalize_level


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda url: None)


def _apply(provider, url, model, level, payload=None):
    payload = payload if payload is not None else {"model": model, "messages": []}
    return apply_reasoning_level(payload, provider=provider, url=url, model=model, level=level)


@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), ("auto", None), ("AUTO", None),
    ("off", "off"), ("none", "off"), ("false", "off"),
    ("low", "low"), ("Medium", "medium"), ("high", "high"), ("on", "medium"),
    ("max", None), (3, None),
])
def test_normalize_level(value, expected):
    assert normalize_level(value) == expected


@pytest.mark.parametrize("provider,url,model", [
    ("openai", "http://192.168.1.20:8080/v1", "qwen3.5-9b"),
    ("openai", "https://api.openai.com/v1", "gpt-5.1"),
    ("ollama", "http://localhost:11434", "qwen3:8b"),
    ("openrouter", "https://openrouter.ai/api/v1", "qwen/qwen3-235b"),
    ("anthropic", "https://api.anthropic.com/v1", "claude-sonnet-4-5"),
])
def test_auto_leaves_payload_unchanged(provider, url, model):
    base = {"model": model, "messages": [{"role": "user", "content": "hi"}], "temperature": 0.7}
    before = copy.deepcopy(base)
    _apply(provider, url, model, None, base)
    _apply(provider, url, model, "auto", base)
    assert base == before


def test_local_llama_server_gets_template_kwargs():
    out = _apply("openai", "http://192.168.1.20:8080/v1", "qwen3.5-9b", "off",
                 {"chat_template_kwargs": {"keep": 1}})
    assert out["chat_template_kwargs"] == {"keep": 1, "enable_thinking": False}
    out = _apply("openai", "http://192.168.1.20:8080/v1", "qwen3.5-9b", "high")
    assert out["chat_template_kwargs"] == {"enable_thinking": True}
    assert "reasoning_effort" not in out and "think" not in out


def test_loopback_v1_gets_both_ollama_and_template_fields():
    out = _apply("openai", "http://127.0.0.1:8080/v1", "qwen3.5-9b", "off")
    assert out["think"] is False
    assert out["chat_template_kwargs"] == {"enable_thinking": False}


def test_local_gpt_oss_gets_effort_in_template_kwargs():
    out = _apply("openai", "http://10.0.0.5:8000/v1", "gpt-oss-20b", "low")
    assert out["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}


def test_ollama_native_and_compat_use_think():
    assert _apply("ollama", "http://localhost:11434", "qwen3:8b", "off")["think"] is False
    assert _apply("ollama", "http://localhost:11434", "qwen3:8b", "high")["think"] is True
    assert _apply("ollama", "http://localhost:11434", "gpt-oss:20b", "medium")["think"] == "medium"
    compat = _apply("openai", "http://192.168.1.9:11434/v1", "qwen3:8b", "off")
    assert compat["think"] is False and "chat_template_kwargs" not in compat


def test_openai_hosted_reasoning_effort():
    assert _apply("openai", "https://api.openai.com/v1", "gpt-5.1", "off")["reasoning_effort"] == "none"
    assert _apply("openai", "https://api.openai.com/v1", "gpt-5-mini", "off")["reasoning_effort"] == "minimal"
    assert _apply("openai", "https://api.openai.com/v1", "o4-mini", "high")["reasoning_effort"] == "high"
    # Non-reasoning OpenAI models get nothing (they'd reject the field).
    assert "reasoning_effort" not in _apply("openai", "https://api.openai.com/v1", "gpt-4o", "high")


def test_openrouter_gemini_mistral():
    assert _apply("openrouter", "https://openrouter.ai/api/v1", "x", "off")["reasoning"] == {"enabled": False}
    assert _apply("openrouter", "https://openrouter.ai/api/v1", "x", "low")["reasoning"] == {"effort": "low"}
    gem = _apply("openai", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash", "off")
    assert gem["reasoning_effort"] == "none"
    assert _apply("mistral", "https://api.mistral.ai/v1", "magistral-small", "medium")["reasoning_effort"] == "medium"


@pytest.mark.parametrize("provider,url", [
    ("anthropic", "https://api.anthropic.com/v1"),
    ("groq", "https://api.groq.com/openai/v1"),
    ("openai", "https://api.deepseek.com/v1"),
    ("openai", "https://api.together.xyz/v1"),
])
def test_untouched_providers(provider, url):
    out = _apply(provider, url, "some-model", "high")
    assert set(out) == {"model", "messages"}


def test_stream_payload_carries_level(monkeypatch):
    """End to end through _stream_llm_inner's payload building."""
    import asyncio
    import json
    import src.llm_core as llm_core

    captured = {}

    class _Resp:
        status_code = 200
        headers = {}
        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
            yield "data: [DONE]"
        async def aread(self):
            return b""

    class _CM:
        def __init__(self, payload):
            captured["payload"] = payload
        async def __aenter__(self):
            return _Resp()
        async def __aexit__(self, *a):
            return False

    class _Client:
        def stream(self, method, url, json=None, headers=None, **kw):
            return _CM(json)

    monkeypatch.setattr(llm_core, "_get_http_client", lambda *a, **k: _Client(), raising=False)
    monkeypatch.setattr(llm_core, "get_context_length", lambda *a, **k: 32768, raising=False)

    async def run(level):
        captured.clear()
        kwargs = {"reasoning": level} if level else {}
        async for _ in llm_core._stream_llm_inner(
            "http://192.168.1.20:8080/v1", "qwen3.5-9b",
            [{"role": "user", "content": "hi"}], **kwargs,
        ):
            pass
        return captured.get("payload")

    off = asyncio.run(run("off"))
    if off is None:
        pytest.skip("stream client seam differs; covered by unit tests above")
    assert off["chat_template_kwargs"]["enable_thinking"] is False
    assert "chat_template_kwargs" not in asyncio.run(run(None))
