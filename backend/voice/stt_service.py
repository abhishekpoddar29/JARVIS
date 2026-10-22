from __future__ import annotations

import base64
import json
import random
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class SpeechToTextError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    transcript: str
    model: str


_TRANSCRIPTION_PROMPT = """    Transcribe only the intelligible spoken words in this audio.
Return only the transcript with normal punctuation.
Do not describe sounds, identify speakers, add timestamps, or answer the speaker.
If there is no intelligible speech, return an empty string.
""".strip()


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(str(part.get("text", "")) for part in parts).strip()
    if not text:
        return ""

    # Remove occasional model-added labels without changing the transcript itself.
    text = text.strip().strip('"')
    lowered = text.casefold()
    for prefix in ("transcript:", "transcription:"):
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: float,
    max_retries: int = 3,
) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    for attempt in range(max_retries):
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {429, 503} and attempt < max_retries - 1:
                time.sleep(random.uniform(0.5, 1.5) * (2**attempt))
                continue
            raise SpeechToTextError(
                f"Gemini STT failed: {exc.code} {exc.reason}: {detail}"
            ) from exc
        except (error.URLError, socket.timeout, TimeoutError) as exc:
            if attempt < max_retries - 1:
                time.sleep(random.uniform(0.5, 1.5) * (2**attempt))
                continue
            reason = getattr(exc, "reason", exc)
            raise SpeechToTextError(f"Gemini STT request failed: {reason}") from exc

    raise SpeechToTextError("Gemini STT failed after maximum retries.")


def transcribe_audio(
    *,
    api_key: str,
    model: str,
    audio_bytes: bytes,
    mime_type: str,
    timeout: float = 60.0,
) -> TranscriptionResult:
    if not api_key:
        raise SpeechToTextError("GEMINI_API_KEY is not configured.")
    if not audio_bytes:
        raise SpeechToTextError("The uploaded audio is empty.")

    normalized_mime = (mime_type or "audio/wav").split(";", 1)[0].strip().lower()
    supported = {
        "audio/wav",
        "audio/x-wav",
        "audio/mp3",
        "audio/mpeg",
        "audio/aiff",
        "audio/aac",
        "audio/ogg",
        "audio/flac",
    }
    if normalized_mime not in supported:
        raise SpeechToTextError(f"Unsupported audio type: {normalized_mime}")
    if normalized_mime == "audio/x-wav":
        normalized_mime = "audio/wav"
    elif normalized_mime == "audio/mpeg":
        normalized_mime = "audio/mp3"

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": _TRANSCRIPTION_PROMPT},
                    {
                        "inlineData": {
                            "mimeType": normalized_mime,
                            "data": base64.b64encode(audio_bytes).decode("ascii"),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 512,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    raw = _post_json(
        url,
        payload,
        api_key=api_key,
        timeout=timeout,
    )
    return TranscriptionResult(
        transcript=_extract_text(raw),
        model=model,
    )
