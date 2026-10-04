# routes/stt_routes.py
"""STT API routes — multi-provider (local Whisper, API endpoint, browser)."""

from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.concurrency import run_in_threadpool
import logging

from src.upload_limits import read_upload_limited, STT_MAX_AUDIO_BYTES

logger = logging.getLogger(__name__)


def setup_stt_routes(stt_service):
    """Setup STT routes with the provided STT service"""
    router = APIRouter(prefix="/api/stt", tags=["stt"])

    @router.get("/stats")
    async def get_stt_stats():
        """Get STT service statistics"""
        try:
            return stt_service.get_stats()
        except Exception as e:
            logger.error(f"Failed to get STT stats: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/transcribe")
    async def transcribe_audio(
        file: UploadFile = File(...),
        model_endpoint_id: Optional[str] = Form(None),
    ):
        """Transcribe uploaded audio file to text.

        ``model_endpoint_id`` (optional) is the chat's model endpoint; when it
        sits on the same host as a configured local speech endpoint, that one
        is tried first (src/speech_routing.py).
        """
        try:
            if not stt_service.available:
                raise HTTPException(
                    status_code=503,
                    detail={"message": "STT service not available or set to browser mode"}
                )

            audio_bytes = await read_upload_limited(file, STT_MAX_AUDIO_BYTES, "Audio file")
            if not audio_bytes:
                raise HTTPException(status_code=400, detail={"message": "Empty audio file"})

            # Whisper / the HTTP call are blocking: keep them off the event loop.
            from src.speech_routing import speech_attempts
            text = None
            for override in speech_attempts("stt", model_endpoint_id):
                if override:
                    text = await run_in_threadpool(stt_service.transcribe, audio_bytes, override)
                else:
                    text = await run_in_threadpool(stt_service.transcribe, audio_bytes)
                if text is not None:
                    break
                if override:
                    logger.info("Local STT unavailable; falling back to the default provider")
            if text is None:
                raise HTTPException(
                    status_code=500,
                    detail={"message": "Transcription failed"}
                )

            return {"text": text}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"message": f"Transcription failed: {str(e)}"}
            )

    return router
