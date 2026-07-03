from __future__ import annotations

from typing import Any, TypedDict


class JarvisState(TypedDict, total=False):
    # ── Inputs ──
    user_message: str
    history: list[dict[str, str]]
    memory_facts: list[str]
    image_data: str | None  # ← F10: Base64 image string for multimodal input

    # ── Set by personality_node ──
    system_prompt: str

    # ── Set by memory_retrieval_node (F9) ──
    memory_context: str

    # ── Set by gemini_node ──
    gemini_reply: str
    tool_call: dict[str, Any] | None

    # ── Set by tool_node / tool_response_node ──
    tool_result: Any
    tool_data: dict | None

    # ── Output ──
    final_reply: str