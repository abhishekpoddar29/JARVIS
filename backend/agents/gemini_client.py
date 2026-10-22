from __future__ import annotations

import json
import random
import socket
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


def _is_timeout_error(exc: BaseException) -> bool:
    """
    FIX (timeout retry gap): _post's retry loop only ever caught HTTPError
    with code 429/503. A genuine socket read timeout raises
    `socket.timeout` (a subclass of OSError) or a `URLError` wrapping a
    `TimeoutError`/`socket.timeout` as its `.reason` - neither of those is an
    HTTPError, so they fell straight to the `except error.URLError` branch
    and were raised immediately with ZERO retries. That is what
    "The read operation timed out" in the logs actually was: a fatal,
    non-retried failure on the very first attempt, hitting every node
    that happened to be mid-call when Gemini was slow/overloaded.
    This helper detects that failure shape so _post can retry it exactly
    like a 429/503, with the same backoff.
    """
    if isinstance(exc, socket.timeout):
        return True
    if isinstance(exc, error.URLError):
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return True
        if isinstance(reason, str) and "timed out" in reason.lower():
            return True
    if isinstance(exc, TimeoutError):
        return True
    return False


def _backoff_delay(attempt: int, base: float = 1.0, cap: float = 20.0) -> float:
    """
    FIX (thundering herd on retry): the old backoff was flat 2**attempt with
    no jitter. When backend/frontend/devops engineers fired concurrent LLM
    calls (or, in the new sequential design, when a single node retries),
    identical backoff timing means retries can still bunch up and hit an
    already-overloaded endpoint at the same moment. Adding jitter spreads
    retries out instead of re-synchronizing them.
    """
    exp = min(cap, base * (2 ** attempt))
    return random.uniform(exp * 0.5, exp)


# ── Existing non-streaming method ─────────────────────────────
def generate_reply(
    *,
    api_key: str,
    model: str,
    user_message: str,
    system_instruction: str,
    temperature: float = 0.7,
    max_output_tokens: int = 1024,
    timeout: int = 90,
    response_mime_type: str | None = None,
    response_schema: dict[str, Any] | None = None,
) -> GeminiReply:
    """Single-shot generation. Keep for simple tool calls.

    FIX: `timeout` is now a caller-configurable parameter (was hardcoded to 60s
    inside _post). Large generation calls (up to 8192 output tokens) can
    legitimately take longer than small reasoning calls; callers that know
    they're asking for a big file can pass a longer timeout instead of
    universally eating a short ceiling tuned for small responses.
    """
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    generation_config: dict[str, Any] = {
        "temperature": temperature,
        "topP": 0.95,
        "maxOutputTokens": max_output_tokens,
    }
    if response_mime_type:
        generation_config["responseMimeType"] = response_mime_type
    if response_schema:
        generation_config["responseSchema"] = response_schema

    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "generationConfig": generation_config,
    }

    raw = _post(url, payload, timeout=timeout)
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
def _post(url: str, payload: dict, max_retries: int = 3, timeout: int = 90) -> dict[str, Any]:
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
            with request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # If Google is overloaded (503) or rate-limiting (429), wait and retry
            if exc.code in (429, 503) and attempt < max_retries - 1:
                wait_time = _backoff_delay(attempt)
                print(f"[Gemini Client] API overloaded ({exc.code}). Retrying in {wait_time:.1f}s... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            raise GeminiClientError(f"Gemini request failed: {exc.code} {exc.reason}: {detail}") from exc
        except (error.URLError, socket.timeout, TimeoutError) as exc:
            # FIX (the actual root cause of "read operation timed out" being
            # instantly fatal): previously ANY URLError - including a plain
            # socket read timeout - was raised immediately with no retry at
            # all, unlike the 429/503 HTTPError path above. Timeouts are
            # exactly the kind of transient, load-dependent failure that
            # backoff-and-retry is meant for, so they now get the same
            # treatment as 429/503 rather than being treated as fatal on
            # the first occurrence.
            if _is_timeout_error(exc) and attempt < max_retries - 1:
                wait_time = _backoff_delay(attempt)
                print(f"[Gemini Client] Read timed out. Retrying in {wait_time:.1f}s... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            reason = getattr(exc, "reason", exc)
            raise GeminiClientError(f"Gemini request failed: {reason}") from exc

    raise GeminiClientError("Gemini request failed after maximum retries.")
    
def generate_reply_with_history(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],  # Full conversation
    system_instruction: str,
    temperature: float = 0.7,
    max_output_tokens: int = 1024,
    timeout: int = 90,
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
    raw = _post(url, payload, timeout=timeout)
    text = _extract_text(raw)
    if not text:
        raise GeminiClientError("Gemini returned no text content")
    return GeminiReply(text=text, raw=raw)