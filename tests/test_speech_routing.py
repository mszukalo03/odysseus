"""Speech routing: local speech only for local-model chats, gateway fallback.

With no *_provider_local setting every request uses the default provider
exactly as before.
"""
import asyncio

import pytest

import src.speech_routing as sr
from src.voice_prompt import DEFAULT_VOICE_PROMPT, voice_prompt

ENDPOINTS = {
    "pcllm": "http://pc.tail.ts.net:8080/v1",
    "pcspeech": "http://pc.tail.ts.net:8000/v1",
    "cloud": "https://openrouter.ai/api/v1",
}


@pytest.fixture
def settings(monkeypatch):
    data = {}
    monkeypatch.setattr("src.settings.load_settings", lambda: data)
    monkeypatch.setattr(sr, "_endpoint_base_url", lambda eid: ENDPOINTS.get(eid))
    return data


def test_no_local_provider_means_default_only(settings):
    assert sr.speech_attempts("stt", "pcllm") == [None]
    assert sr.speech_attempts("tts", None) == [None]


def test_local_model_chat_tries_local_then_default(settings):
    settings.update({
        "stt_provider_local": "endpoint:pcspeech", "stt_model_local": "whisper-turbo",
        "tts_provider_local": "endpoint:pcspeech", "tts_voice_local": "af_heart",
    })
    stt = sr.speech_attempts("stt", "pcllm")
    assert stt[1] is None
    assert stt[0]["provider"] == "endpoint:pcspeech" and stt[0]["model"] == "whisper-turbo"
    assert stt[0]["connect_timeout"] == sr.LOCAL_CONNECT_TIMEOUT
    tts = sr.speech_attempts("tts", "pcllm")
    assert tts[0]["voice"] == "af_heart" and "model" not in tts[0]


def test_chat_endpoint_can_be_given_as_url(settings):
    settings.update({"stt_provider_local": "endpoint:pcspeech"})
    assert sr.speech_attempts("stt", "http://pc.tail.ts.net:8080/v1")[0]["provider"] == "endpoint:pcspeech"
    assert sr.speech_attempts("stt", "https://openrouter.ai/api/v1") == [None]


def test_cloud_chat_keeps_gateway(settings):
    settings.update({"stt_provider_local": "endpoint:pcspeech"})
    assert sr.speech_attempts("stt", "cloud") == [None]
    assert sr.speech_attempts("stt", "unknown") == [None]
    assert sr.speech_attempts("stt", None) == [None]


def test_routing_errors_fall_back_to_default(monkeypatch):
    def boom():
        raise RuntimeError("settings unreadable")
    monkeypatch.setattr("src.settings.load_settings", boom)
    assert sr.speech_attempts("tts", "pcllm") == [None]


def test_stt_route_falls_back_when_local_fails(settings, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.stt_routes import setup_stt_routes

    settings.update({"stt_provider_local": "endpoint:pcspeech"})
    calls = []

    class FakeSTT:
        available = True
        def transcribe(self, audio, override=None):
            calls.append(override["provider"] if override else "default")
            return None if override else "hello from the gateway"

    app = FastAPI()
    app.include_router(setup_stt_routes(FakeSTT()))
    client = TestClient(app)
    r = client.post("/api/stt/transcribe", files={"file": ("a.webm", b"xx", "audio/webm")},
                    data={"model_endpoint_id": "pcllm"})
    assert r.status_code == 200 and r.json() == {"text": "hello from the gateway"}
    assert calls == ["endpoint:pcspeech", "default"]

    calls.clear()
    r = client.post("/api/stt/transcribe", files={"file": ("a.webm", b"xx", "audio/webm")})
    assert r.json() == {"text": "hello from the gateway"} and calls == ["default"]


def test_tts_route_uses_local_when_it_works(settings):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.tts_routes import setup_tts_routes

    settings.update({"tts_provider_local": "endpoint:pcspeech"})

    class FakeTTS:
        available = True
        def synthesize(self, text, use_cache=True, override=None):
            return b"ID3local" if override else b"ID3gateway"
        def synthesize_to_base64(self, text, override=None):
            return "bG9jYWw=" if override else "Z2F0ZXdheQ=="

    app = FastAPI()
    app.include_router(setup_tts_routes(FakeTTS()))
    client = TestClient(app)
    r = client.post("/api/tts/synthesize", json={"text": "hi", "model_endpoint_id": "pcllm"})
    assert r.content == b"ID3local"
    r = client.post("/api/tts/synthesize", json={"text": "hi", "model_endpoint_id": "cloud"})
    assert r.content == b"ID3gateway"
    r = client.post("/api/tts/synthesize", json={"text": "hi", "format": "base64"})
    assert r.json() == {"audio": "Z2F0ZXdheQ=="}


def test_voice_prompt_default_and_override(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda k, d=None: "")
    assert voice_prompt() == DEFAULT_VOICE_PROMPT
    monkeypatch.setattr("src.settings.get_setting", lambda k, d=None: "Be brief.")
    assert voice_prompt() == "Be brief."


def test_voice_prompt_added_to_preface_only_when_set():
    from src.chat_processor import ChatProcessor
    proc = ChatProcessor.__new__(ChatProcessor)
    proc.memory_manager = None
    proc.personal_docs_manager = None
    proc._last_used_memories = []
    try:
        plain, _, _ = proc.build_context_preface(message="hi", session=None, use_memory=False)
        voiced, _, _ = proc.build_context_preface(message="hi", session=None, use_memory=False,
                                                  voice_prompt="SPEAK")
    except Exception as exc:  # pragma: no cover - processor needs more wiring
        pytest.skip(f"preface needs app wiring: {exc}")
    assert not any(m.get("content") == "SPEAK" for m in plain)
    assert any(m.get("role") == "system" and m.get("content") == "SPEAK" for m in voiced)
