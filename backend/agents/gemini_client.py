from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator
from urllib import error, request
import time



@dataclass(slots=True)
class GeminiReply:
    text: str
    raw: dict[str, Any] | None  # None for streaming chunks


class GeminiClientError(RuntimeError):
    pass


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""

    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    texts: list[str] = []
    for part in parts:
        text = part.get("text")
        if text:
            texts.append(str(text))
    return "".join(texts).strip()


# ── Existing non-streaming method ─────────────────────────────
def generate_reply(
    *,
    api_key: str,
    model: str,
    user_message: str,
    system_instruction: str,
    temperature: float = 0.7,
    max_output_tokens: int = 1024,
) -> GeminiReply:
    """Single-shot generation. Keep for simple tool calls."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "generationConfig": {
            "temperature": temperature,
            "topP": 0.95,
            "maxOutputTokens": max_output_tokens,
        },
    }

    raw = _post(url, payload)
    text = _extract_text(raw)
    if not text:
        raise GeminiClientError("Gemini returned no text content")
    return GeminiReply(text=text, raw=raw)


# ── NEW: Streaming for chat UI ──────────────────────────────
def stream_reply(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],  # [{"role": "user"|"model", "content": "..."}]
    system_instruction: str,
    temperature: float = 0.7,
    max_output_tokens: int = 4096,
) -> Iterator[str]:
    """
    Stream text chunks for real-time chat.
    Yields partial text strings; caller assembles full response.
    """
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:streamGenerateContent?key={api_key}"
    )
    
    # Convert to Gemini's contents format
    contents = [
        {
            "role": msg["role"],
            "parts": msg.get("parts") or [{"text": msg.get("content", "")}],
        }
        for msg in messages
    ]

    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "topP": 0.95,
            "maxOutputTokens": max_output_tokens,
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with request.urlopen(req, timeout=60) as resp:
            # Stream response: each line is a JSON object
            for line in resp:
                line = line.strip()
                if not line:
                    continue
                # Response is a stream of JSON objects, sometimes wrapped in array brackets
                if line == b'[' or line == b']' or line == b',':
                    continue
                if line.startswith(b'['):
                    line = line[1:]
                if line.endswith(b']'):
                    line = line[:-1]
                if line.endswith(b','):
                    line = line[:-1]
                    
                try:
                    chunk = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                text = _extract_text(chunk)
                if text:
                    yield text
                    
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GeminiClientError(f"Gemini stream failed: {exc.code} {exc.reason}: {detail}") from exc
    except error.URLError as exc:
        raise GeminiClientError(f"Gemini stream failed: {exc.reason}") from exc



# ── Shared HTTP helper ──────────────────────────────────────
def _post(url: str, payload: dict, max_retries: int = 3) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    
    for attempt in range(max_retries):
        # We must recreate the request object because urllib caches the body stream
        req = request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # If Google is overloaded (503) or rate-limiting (429), wait and retry
            if exc.code in (429, 503) and attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 1s, 2s, 4s
                print(f"[Gemini Client] API overloaded ({exc.code}). Retrying in {wait_time}s... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            raise GeminiClientError(f"Gemini request failed: {exc.code} {exc.reason}: {detail}") from exc
        except error.URLError as exc:
            raise GeminiClientError(f"Gemini request failed: {exc.reason}") from exc
            
    raise GeminiClientError("Gemini request failed after maximum retries.")
    
def generate_reply_with_history(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],  # Full conversation
    system_instruction: str,
    temperature: float = 0.7,
    max_output_tokens: int = 1024,
) -> GeminiReply:
    """Multi-turn conversation. Use when session memory is available."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    
    # F10: Support multimodal payloads. If the message already has "parts" (e.g., image data), use it.
    # Otherwise, wrap the "content" string into the parts format.
    contents = [
        {
            "role": msg["role"],  # "user" or "model"
            "parts": msg.get("parts") or [{"text": msg.get("content", "")}],
        }
        for msg in messages
    ]
    
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "topP": 0.95,
            "maxOutputTokens": max_output_tokens,
        },
    }
    raw = _post(url, payload)
    text = _extract_text(raw)
    if not text:
        raise GeminiClientError("Gemini returned no text content")
    return GeminiReply(text=text, raw=raw)