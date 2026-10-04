"""Env-driven setup for an external OpenAI-compatible speech gateway.

Speech settings normally live in data/settings.json and point at a model
endpoint row (``stt_provider = "endpoint:<id>"``). A node that runs its own
STT/TTS containers can describe that gateway in .env instead:

    ODYSSEUS_SPEECH_BASE_URL=http://host.docker.internal:4000/v1
    ODYSSEUS_SPEECH_API_KEY=sk-...

On startup this registers (or re-keys) the endpoint and points STT/TTS at it
while they are still unconfigured. A provider already chosen in the UI or via
/api/auth/settings is never overwritten, so the env only seeds a fresh node.
"""

import logging
import os
import uuid

logger = logging.getLogger(__name__)

DEFAULT_NAME = "Speech gateway"
DEFAULT_STT_MODEL = "stt-local"
DEFAULT_TTS_MODEL = "tts-local"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _find_or_create_endpoint(db, base_url: str, api_key: str, name: str):
    from core.database import ModelEndpoint

    for ep in db.query(ModelEndpoint).all():
        if (ep.base_url or "").rstrip("/") == base_url:
            # .env is the source of truth for the key, so a rotated gateway
            # key takes effect on the next restart.
            if api_key and ep.api_key != api_key:
                ep.api_key = api_key
                db.commit()
                logger.info("Speech gateway %s: API key updated from env", ep.id)
            if not ep.is_enabled:
                ep.is_enabled = True
                db.commit()
            return ep, False

    ep = ModelEndpoint(
        id=str(uuid.uuid4())[:8],
        name=name,
        base_url=base_url,
        api_key=api_key or None,
        is_enabled=True,
        model_type="llm",
        endpoint_kind="auto",
        # Shared (null owner): speech settings are global, not per-user.
        owner=None,
    )
    db.add(ep)
    db.commit()
    return ep, True


def ensure_speech_gateway(session_factory=None) -> dict:
    """Apply ODYSSEUS_SPEECH_* env vars. Returns a summary of what changed."""
    base_url = _env("ODYSSEUS_SPEECH_BASE_URL").rstrip("/")
    if not base_url:
        return {"configured": False}

    from src.settings import load_settings, save_settings

    if session_factory is None:
        from core.database import SessionLocal as session_factory

    db = session_factory()
    try:
        ep, created = _find_or_create_endpoint(
            db,
            base_url,
            _env("ODYSSEUS_SPEECH_API_KEY"),
            _env("ODYSSEUS_SPEECH_NAME", DEFAULT_NAME),
        )
        ep_id = ep.id
    finally:
        db.close()

    provider = f"endpoint:{ep_id}"
    settings = dict(load_settings())
    changed = []

    if (settings.get("stt_provider") or "disabled") == "disabled":
        settings.update({
            "stt_enabled": True,
            "stt_provider": provider,
            "stt_model": _env("ODYSSEUS_STT_MODEL", DEFAULT_STT_MODEL),
            "stt_language": _env("ODYSSEUS_STT_LANGUAGE"),
        })
        changed.append("stt")

    if (settings.get("tts_provider") or "disabled") == "disabled":
        settings.update({
            "tts_enabled": True,
            "tts_provider": provider,
            "tts_model": _env("ODYSSEUS_TTS_MODEL", DEFAULT_TTS_MODEL),
        })
        voice = _env("ODYSSEUS_TTS_VOICE")
        if voice:
            settings["tts_voice"] = voice
        changed.append("tts")

    if changed:
        save_settings(settings)

    logger.info(
        "Speech gateway %s at %s (%s); seeded: %s",
        ep_id, base_url, "registered" if created else "existing",
        ", ".join(changed) or "nothing, already configured",
    )
    return {"configured": True, "endpoint_id": ep_id, "created": created, "seeded": changed}


DEFAULT_LOCAL_NAME = "Local speech"


def ensure_local_speech(session_factory=None) -> dict:
    """Apply ODYSSEUS_SPEECH_LOCAL_* env vars: the optional speech server that
    runs next to a local model (see src/speech_routing.py).

        ODYSSEUS_SPEECH_LOCAL_BASE_URL=http://pc.tailnet:8000/v1
        ODYSSEUS_SPEECH_LOCAL_API_KEY=...            (optional)
        ODYSSEUS_STT_LOCAL_MODEL=Systran/faster-whisper-large-v3-turbo
        ODYSSEUS_TTS_LOCAL_MODEL=speaches-ai/Kokoro-82M-v1.0-ONNX
        ODYSSEUS_TTS_LOCAL_VOICE=af_heart

    Seeds ``stt_provider_local`` / ``tts_provider_local`` (and their model /
    voice) only while they are unset, like the main gateway seeding.
    """
    base_url = _env("ODYSSEUS_SPEECH_LOCAL_BASE_URL").rstrip("/")
    if not base_url:
        return {"configured": False}

    from src.settings import load_settings, save_settings

    if session_factory is None:
        from core.database import SessionLocal as session_factory

    db = session_factory()
    try:
        ep, created = _find_or_create_endpoint(
            db,
            base_url,
            _env("ODYSSEUS_SPEECH_LOCAL_API_KEY"),
            _env("ODYSSEUS_SPEECH_LOCAL_NAME", DEFAULT_LOCAL_NAME),
        )
        ep_id = ep.id
    finally:
        db.close()

    provider = f"endpoint:{ep_id}"
    settings = dict(load_settings())
    changed = []
    if not settings.get("stt_provider_local"):
        settings["stt_provider_local"] = provider
        model = _env("ODYSSEUS_STT_LOCAL_MODEL")
        if model:
            settings["stt_model_local"] = model
        changed.append("stt")
    if not settings.get("tts_provider_local"):
        settings["tts_provider_local"] = provider
        model = _env("ODYSSEUS_TTS_LOCAL_MODEL")
        if model:
            settings["tts_model_local"] = model
        voice = _env("ODYSSEUS_TTS_LOCAL_VOICE")
        if voice:
            settings["tts_voice_local"] = voice
        changed.append("tts")
    if changed:
        save_settings(settings)

    logger.info(
        "Local speech %s at %s (%s); seeded: %s",
        ep_id, base_url, "registered" if created else "existing",
        ", ".join(changed) or "nothing, already configured",
    )
    return {"configured": True, "endpoint_id": ep_id, "created": created, "seeded": changed}
