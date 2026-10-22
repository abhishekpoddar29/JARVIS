import json
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from tools.schemas import ToolCall


_ALLOWED_TASK_STATUSES = {"ready", "blocked", "completed", "failed"}


def parse_tool_call(payload: Any) -> Optional[ToolCall]:
    """Normalize one Jarvis JSON call or Gemini Live native call."""
    if isinstance(payload, ToolCall):
        return payload

    if isinstance(payload, str):
        return try_parse_tool_call(payload)

    if not isinstance(payload, Mapping):
        return None

    name = payload.get("tool") or payload.get("name")
    arguments = payload.get("arguments", payload.get("args", {}))

    if not isinstance(name, str) or not name.strip():
        return None
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, Mapping):
        return None

    try:
        return ToolCall(tool=name.strip(), arguments=dict(arguments))
    except Exception:
        return None


def parse_tool_calls(payload: Any) -> list[ToolCall]:
    """Normalize batched or legacy tool-call payloads into ToolCall objects."""
    if isinstance(payload, ToolCall):
        return [payload]

    if isinstance(payload, str):
        return try_parse_tool_calls(payload)

    items: Any = payload
    if isinstance(payload, Mapping):
        if "tool_calls" in payload:
            items = payload.get("tool_calls")
        else:
            call = parse_tool_call(payload)
            return [call] if call else []

    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return []

    calls: list[ToolCall] = []
    for item in items:
        call = parse_tool_call(item)
        if call:
            calls.append(call)
    return calls


def parse_task_plan(payload: Any) -> list[dict[str, Any]]:
    """
    Normalize the main Jarvis planner contract.

    Expected shape:
      {
        "tasks": [
          {
            "id": "T1",
            "goal": "Find current Kimi K3 information",
            "tool": "web_search_tool",
            "arguments": {"query": "Kimi K3 model"},
            "depends_on": []
          }
        ]
      }

    The runtime owns status transitions; model-supplied status is accepted only
    from the small supported set and otherwise derived from dependencies.
    """
    if not isinstance(payload, Mapping):
        return []

    items = payload.get("tasks")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return []

    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            continue

        tool = item.get("tool") or item.get("name")
        if not isinstance(tool, str) or not tool.strip():
            continue

        task_id = str(item.get("id") or f"T{index}").strip() or f"T{index}"
        if task_id in seen_ids:
            task_id = f"T{index}"
        seen_ids.add(task_id)

        arguments = item.get("arguments", item.get("args", {}))
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            arguments = {}

        raw_deps = item.get("depends_on", [])
        if isinstance(raw_deps, Sequence) and not isinstance(raw_deps, (str, bytes, bytearray)):
            depends_on = [str(dep).strip() for dep in raw_deps if str(dep).strip()]
        else:
            depends_on = []

        supplied_status = str(item.get("status", "")).strip().lower()
        status = supplied_status if supplied_status in _ALLOWED_TASK_STATUSES else (
            "blocked" if depends_on else "ready"
        )

        goal = str(item.get("goal") or item.get("objective") or tool).strip()
        requires_approval = bool(item.get("requires_approval", False))

        tasks.append({
            "id": task_id,
            "goal": goal,
            "tool": tool.strip(),
            "arguments": dict(arguments),
            "depends_on": depends_on,
            "status": status,
            "requires_approval": requires_approval,
            "last_error": None,
        })

    return tasks


def parse_task_updates(payload: Any) -> list[dict[str, Any]]:
    """Normalize reviewer updates for existing task-ledger entries."""
    if not isinstance(payload, Mapping):
        return []

    items = payload.get("task_updates")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return []

    updates: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue

        task_id = str(item.get("id") or item.get("task_id") or "").strip()
        if not task_id:
            continue

        update: dict[str, Any] = {"id": task_id}

        tool = item.get("tool") or item.get("name")
        if isinstance(tool, str) and tool.strip():
            update["tool"] = tool.strip()

        if "arguments" in item or "args" in item:
            arguments = item.get("arguments", item.get("args", {}))
            if isinstance(arguments, Mapping):
                update["arguments"] = dict(arguments)

        status = str(item.get("status", "")).strip().lower()
        if status in _ALLOWED_TASK_STATUSES:
            update["status"] = status

        reason = item.get("reason") or item.get("last_error")
        if reason is not None:
            update["reason"] = str(reason)

        updates.append(update)

    return updates


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```json"):
        stripped = stripped[len("```json"):]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        return stripped.strip()
    if stripped.startswith("```"):
        stripped = stripped[3:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        return stripped.strip()
    return stripped


def _decode_first_json_value(text: str) -> Any | None:
    """Decode the first JSON object/array even if the model added stray prose."""
    decoder = json.JSONDecoder()

    try:
        return json.loads(text)
    except Exception:
        pass

    starts = [index for index, char in enumerate(text) if char in "{["]
    for start in starts:
        try:
            value, _ = decoder.raw_decode(text[start:])
            return value
        except Exception:
            continue
    return None


def _decode_response(response_text: str) -> Any | None:
    return _decode_first_json_value(_strip_json_fence(response_text))


def try_parse_task_plan(response_text: str) -> list[dict[str, Any]]:
    payload = _decode_response(response_text)
    return parse_task_plan(payload) if payload is not None else []


def try_parse_task_updates(response_text: str) -> list[dict[str, Any]]:
    payload = _decode_response(response_text)
    return parse_task_updates(payload) if payload is not None else []


def try_parse_tool_calls(response_text: str) -> list[ToolCall]:
    """Parse zero, one, or many tool calls from a model response."""
    payload = _decode_response(response_text)
    calls = parse_tool_calls(payload) if payload is not None else []
    if not calls:
        print("[PARSER] No valid tool-call batch found")
    return calls


def try_parse_tool_call(response_text: str) -> Optional[ToolCall]:
    """Backward-compatible single-call parser; returns the first parsed call."""
    calls = try_parse_tool_calls(response_text)
    return calls[0] if calls else None