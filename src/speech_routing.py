"""Pick a speech (STT/TTS) provider per request: local box first, gateway fallback.

The default speech providers (``stt_provider`` / ``tts_provider``) point at
the always-on gateway (e.g. the XPS). A second, optional pair of settings
points at speech running next to a local model (e.g. Speaches on the GPU
PC, or an NPU/OpenVINO server):

    stt_provider_local  = "endpoint:<id>"   stt_model_local
    tts_provider_local  = "endpoint:<id>"   tts_model_local   tts_voice_local

The local pair is used only when the chat's model endpoint lives on the same
host as the local speech endpoint -- i.e. you're talking to the local model,
where low latency matters and the box is known to be on. Every other chat
(cloud models) keeps using the default gateway. If the local speech call
fails (box off, container stopped), the request falls back to the default
provider, so voice keeps working.

With no ``*_provider_local`` set, :func:`speech_attempts` returns ``[None]``
and requests behave exactly as before.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Short connect timeout for the local box so an offline PC costs ~1.5 s, not 60.
LOCAL_CONNECT_TIMEOUT = 1.5


def _endpoint_base_url(endpoint_id: str) -> Optional[str]:
    if not endpoint_id:
        return None
    try:
        from src.database import SessionLocal, ModelEndpoint
    except Exception:
        return None
    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
        return (ep.base_url or "") if ep else None
    except Exception as exc:
        logger.debug("speech routing: endpoint lookup failed for %s: %s", endpoint_id, exc)
        return None
    finally:
        db.close()


def _host(url: Optional[str]) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower()
    except Exception:
        return ""


def local_override(kind: str, chat_endpoint_id: Optional[str]) -> Optional[Dict[str, str]]:
    """Provider override for the local speech pair, or None to use the default.

    ``kind`` is "stt" or "tts". ``chat_endpoint_id`` is the chat model's
    endpoint id or its base URL.
    """
    from src.settings import load_settings

    settings = load_settings() or {}
    provider = str(settings.get(f"{kind}_provider_local") or "").strip()
    if not provider.startswith("endpoint:") or not chat_endpoint_id:
        return None
    speech_host = _host(_endpoint_base_url(provider.split(":", 1)[1]))
    # The browser knows either the chat's endpoint id or just its base URL.
    chat_ref = str(chat_endpoint_id).strip()
    chat_host = _host(chat_ref) if "://" in chat_ref else _host(_endpoint_base_url(chat_ref))
    if not speech_host or not chat_host or speech_host != chat_host:
        return None
    override = {"provider": provider}
    model = str(settings.get(f"{kind}_model_local") or "").strip()
    if model:
        override["model"] = model
    if kind == "tts":
        voice = str(settings.get("tts_voice_local") or "").strip()
        if voice:
            override["voice"] = voice
    override["connect_timeout"] = LOCAL_CONNECT_TIMEOUT
    return override


def speech_attempts(kind: str, chat_endpoint_id: Optional[str]) -> List[Optional[Dict[str, str]]]:
    """Ordered provider overrides to try; ``None`` means the default settings."""
    try:
        override = local_override(kind, chat_endpoint_id)
    except Exception as exc:
        logger.warning("speech routing (%s) failed, using default provider: %s", kind, exc)
        override = None
    return [override, None] if override else [None]
