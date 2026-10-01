"""Env-driven speech gateway setup (src/speech_bootstrap.py)."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, ModelEndpoint
from src import settings as settings_mod
from src.speech_bootstrap import ensure_speech_gateway

URL = "http://host.docker.internal:4000/v1"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[ModelEndpoint.__table__])
    factory = sessionmaker(bind=engine)
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", str(settings_file))
    settings_mod._invalidate_caches()
    for var in ("ODYSSEUS_SPEECH_BASE_URL", "ODYSSEUS_SPEECH_API_KEY", "ODYSSEUS_STT_MODEL",
                "ODYSSEUS_STT_LANGUAGE", "ODYSSEUS_TTS_MODEL", "ODYSSEUS_TTS_VOICE"):
        monkeypatch.delenv(var, raising=False)
    yield factory, settings_file, monkeypatch
    settings_mod._invalidate_caches()


def _endpoints(factory):
    db = factory()
    try:
        return db.query(ModelEndpoint).all()
    finally:
        db.close()


def test_noop_without_base_url(env):
    factory, settings_file, _ = env
    assert ensure_speech_gateway(factory) == {"configured": False}
    assert _endpoints(factory) == []
    assert not settings_file.exists()


def test_registers_endpoint_and_seeds_speech_settings(env):
    factory, settings_file, mp = env
    mp.setenv("ODYSSEUS_SPEECH_BASE_URL", URL + "/")
    mp.setenv("ODYSSEUS_SPEECH_API_KEY", "sk-one")
    mp.setenv("ODYSSEUS_STT_LANGUAGE", "en")
    mp.setenv("ODYSSEUS_TTS_VOICE", "af_heart")

    result = ensure_speech_gateway(factory)

    (ep,) = _endpoints(factory)
    assert ep.base_url == URL and ep.api_key == "sk-one" and ep.owner is None
    assert result == {"configured": True, "endpoint_id": ep.id, "created": True, "seeded": ["stt", "tts"]}
    saved = json.loads(settings_file.read_text())
    assert saved["stt_enabled"] is True and saved["tts_enabled"] is True
    assert saved["stt_provider"] == saved["tts_provider"] == f"endpoint:{ep.id}"
    assert (saved["stt_model"], saved["stt_language"]) == ("stt-local", "en")
    assert (saved["tts_model"], saved["tts_voice"]) == ("tts-local", "af_heart")


def test_restart_reuses_endpoint_rotates_key_and_keeps_user_choices(env):
    factory, settings_file, mp = env
    mp.setenv("ODYSSEUS_SPEECH_BASE_URL", URL)
    mp.setenv("ODYSSEUS_SPEECH_API_KEY", "sk-one")
    first = ensure_speech_gateway(factory)

    # User later switches STT to a cloud model in the UI.
    saved = json.loads(settings_file.read_text())
    saved["stt_model"] = "stt-groq"
    settings_file.write_text(json.dumps(saved))
    settings_mod._invalidate_caches()

    mp.setenv("ODYSSEUS_SPEECH_API_KEY", "sk-two")
    second = ensure_speech_gateway(factory)

    (ep,) = _endpoints(factory)
    assert second == {"configured": True, "endpoint_id": first["endpoint_id"], "created": False, "seeded": []}
    assert ep.api_key == "sk-two"
    assert json.loads(settings_file.read_text())["stt_model"] == "stt-groq"


def test_does_not_override_existing_provider(env):
    factory, settings_file, mp = env
    settings_file.write_text(json.dumps({"stt_provider": "browser", "stt_enabled": True}))
    mp.setenv("ODYSSEUS_SPEECH_BASE_URL", URL)

    result = ensure_speech_gateway(factory)

    saved = json.loads(settings_file.read_text())
    assert result["seeded"] == ["tts"]
    assert saved["stt_provider"] == "browser"
    assert saved["tts_provider"] == f"endpoint:{result['endpoint_id']}"


def test_stt_gateway_models_are_not_offered_as_chat_models():
    from routes.model_routes import _is_chat_model

    assert not _is_chat_model("stt-local")
    assert not _is_chat_model("stt-groq")
    assert not _is_chat_model("tts-local")
