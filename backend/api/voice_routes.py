from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, HTTPException, UploadFile

from config import get_settings
from voice.stt_service import SpeechToTextError, transcribe_audio


router = APIRouter(prefix="/api/voice", tags=["voice"])
settings = get_settings()


@router.post("/transcribe")
async def transcribe_voice(
    audio: UploadFile = File(...),
) -> dict[str, str]:
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY is not configured.",
        )

    content_type = (
        audio.content_type or "audio/wav"
    ).split(";", 1)[0].lower()

    if not content_type.startswith("audio/"):
        raise HTTPException(
            status_code=415,
            detail="The uploaded file must be audio.",
        )

    raw = await audio.read(settings.voice_max_audio_bytes + 1)

    if len(raw) > settings.voice_max_audio_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Audio exceeds the "
                f"{settings.voice_max_audio_bytes}-byte limit."
            ),
        )

    try:
        result = await asyncio.to_thread(
            transcribe_audio,
            api_key=settings.gemini_api_key,
            model=settings.gemini_stt_model,
            audio_bytes=raw,
            mime_type=content_type,
            timeout=settings.gemini_stt_timeout_seconds,
        )
    except SpeechToTextError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    return {
        "transcript": result.transcript,
        "model": result.model,
    }