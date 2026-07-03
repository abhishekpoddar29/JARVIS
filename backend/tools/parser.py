import json
import re
from typing import Optional

from tools.schemas import ToolCall


def try_parse_tool_call(response_text: str) -> Optional[ToolCall]:
    text = response_text.strip()

    # Strip ```json fences
    if text.startswith("```json"):
        text = text.replace("```json", "").replace("```", "").strip()

    # Try pure JSON first (fast path)
    result = _parse_json(text)
    if result:
        return result

    # Extract JSON object embedded anywhere in prose
    # Find the first { and last } that form a valid JSON object
    start = text.find("{")
    if start != -1:
        # Try progressively larger substrings from last } back
        end = text.rfind("}")
        while end > start:
            candidate = text[start:end + 1]
            result = _parse_json(candidate)
            if result:
                return result
            end = text.rfind("}", start, end)

    print(f"[PARSER] Failed: no valid tool JSON found")
    return None


def _parse_json(text: str) -> Optional[ToolCall]:
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return None
        if "tool" not in payload:
            return None
        return ToolCall(
            tool=payload["tool"],
            arguments=payload.get("arguments", {}),
        )
    except Exception:
        return None