from __future__ import annotations

from typing import Any, TypedDict


class JarvisState(TypedDict, total=False):
    # ── Inputs ──
    user_message: str
    history: list[dict[str, str]]
    memory_facts: list[str]
    image_data: str | None

    # ── Prompt / memory context ──
    system_prompt: str
    memory_context: str

    # ── Model output ──
    gemini_reply: str

    # Legacy single-call fields kept for compatibility with existing code paths.
    tool_call: dict[str, Any] | None
    tool_result: Any

    # ── Persistent task ledger ──
    # Every tool-backed outcome discovered in the original user request lives here
    # until it is completed, failed, or explicitly blocked. This prevents dependent
    # actions (for example search -> email) from disappearing between ReAct rounds.
    planned_tasks: list[dict[str, Any]]

    # ── Batched orchestration state ──
    # Calls ready to execute in the next dispatcher round. Each entry may include
    # task_id in addition to tool/arguments; task_id is orchestration metadata only
    # and is never passed into the tool itself.
    tool_calls: list[dict[str, Any]]

    # Calls intentionally deferred by the dispatcher (for example a second
    # approval-gated action or work above the per-round concurrency cap).
    deferred_tool_calls: list[dict[str, Any]]

    # All observations gathered during this user turn and the latest batch only.
    tool_results: list[dict[str, Any]]
    current_tool_results: list[dict[str, Any]]

    # Compact execution ledger used for duplicate prevention and budgeting.
    executed_calls: list[dict[str, Any]]
    tool_calls_made: int
    tool_rounds_made: int

    # Aggregated UI/tool metadata. Existing consumers such as main.py still read
    # music_command and code_result from this dictionary.
    tool_data: dict[str, Any] | None

    # ── Output ──
    final_reply: str