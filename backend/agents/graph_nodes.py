from __future__ import annotations

import asyncio
import contextvars
import importlib.util
import inspect
import json
import re
from pathlib import Path
from typing import Awaitable, Callable, Any

from agents.gemini_client import GeminiClientError, generate_reply_with_history
from agents.graph_state import JarvisState
from agents.personality import PersonalityEngine
from agents.prompts.prompts import TOOL_RESPONSE_PROMPT, TOOL_ROUTER_PROMPT
from config import get_settings
from langgraph.types import interrupt
from memory.semantic import semantic_memory
from tools.base import BaseTool
from tools.parser import try_parse_task_plan, try_parse_task_updates, try_parse_tool_calls
from tools.registry import registry
from tools.schemas import ToolCall
from tools.tool_executer import tool_executor

settings = get_settings()

# Bounded autonomy. These are intentionally small enough for an interactive
# assistant while allowing genuinely compound requests to run efficiently.
MAX_TOOL_ROUNDS = 4
MAX_TOTAL_TOOL_CALLS = 8
MAX_PARALLEL_TOOLS = 4
MAX_RESULT_CONTEXT_CHARS = 18_000
MAX_TOTAL_OBSERVATION_CHARS = 60_000

# These capabilities may write external state, trigger an approval flow, or run
# nested HIL workflows. Only one is dispatched in a graph round. This prevents
# multiple concurrent LangGraph interrupts and avoids replaying unrelated side
# effects when an interrupted node resumes.
_SERIAL_TOOL_NAMES = {
    "self_modify_tool",
    "engineering_team_tool",
    "location_share_tool",
    "mcp_filesystem_write_file",
}


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
personality = PersonalityEngine(
    config_path=_PROJECT_ROOT / "personality.yaml",
    templates_dir=_PROJECT_ROOT / "agents" / "prompts",
)


# ── UI broadcast context ──────────────────────────────────────────────────────
orb_emitter: contextvars.ContextVar[
    Callable[[str], Awaitable[None]] | None
] = contextvars.ContextVar("orb_emitter", default=None)

progress_emitter: contextvars.ContextVar[
    Callable[[str], Awaitable[None]] | None
] = contextvars.ContextVar("progress_emitter", default=None)

# Generic real-time tool event channel. It lets a fast side-effect/UI tool (most
# importantly music_player_tool) reach the frontend while slower parallel work
# is still running. main.py binds this emitter to the active WebSocket.
tool_event_emitter: contextvars.ContextVar[
    Callable[[dict[str, Any]], Awaitable[None]] | None
] = contextvars.ContextVar("tool_event_emitter", default=None)


async def _emit_orb(state: str) -> None:
    emit = orb_emitter.get()
    if emit:
        try:
            await emit(state)
        except Exception:
            pass


async def _emit_tool_event(payload: dict[str, Any]) -> bool:
    emit = tool_event_emitter.get()
    if not emit:
        return False
    try:
        await emit(payload)
        return True
    except Exception:
        return False


async def _emit_result_events(result: dict[str, Any]) -> None:
    data = result.get("data") or {}
    if not isinstance(data, dict):
        return

    music_command = data.get("music_command")
    if isinstance(music_command, dict):
        emitted = await _emit_tool_event({
            "type": "music_command",
            **music_command,
        })
        if emitted:
            # main.py uses this marker to avoid replaying the same command when
            # the completed graph result is serialized later.
            data["music_command_emitted"] = True


# ── Small execution helpers ───────────────────────────────────────────────────
def _tool_result_ok(result: Any) -> bool:
    return bool(getattr(result, "ok", getattr(result, "success", False)))


def _set_tool_result_ok(result: Any, value: bool) -> None:
    if hasattr(result, "ok"):
        result.ok = value
    elif hasattr(result, "success"):
        result.success = value


def _call_dict(call: ToolCall) -> dict[str, Any]:
    return call.model_dump() if hasattr(call, "model_dump") else {
        "tool": call.tool,
        "arguments": dict(call.arguments),
    }


def _call_fingerprint(call_or_dict: ToolCall | dict[str, Any]) -> str:
    if isinstance(call_or_dict, ToolCall):
        payload = _call_dict(call_or_dict)
    else:
        payload = {
            "task_id": str(call_or_dict.get("task_id", "")),
            "tool": str(call_or_dict.get("tool", "")),
            "arguments": call_or_dict.get("arguments") or {},
        }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _result_dict(call: ToolCall, result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        success = bool(result.get("success", result.get("ok", False)))
        content = str(result.get("content", ""))
        data = result.get("data") or {}
        error = result.get("error")
    else:
        success = bool(getattr(result, "success", getattr(result, "ok", False)))
        content = str(getattr(result, "content", ""))
        data = getattr(result, "data", {}) or {}
        error = getattr(result, "error", None)

    if not isinstance(data, dict):
        data = {}

    return {
        "tool": call.tool,
        "arguments": dict(call.arguments),
        "success": success,
        "content": content,
        "data": data,
        "error": str(error) if error else None,
    }


def _error_result(
    *,
    tool: str,
    arguments: dict[str, Any] | None,
    message: str,
) -> dict[str, Any]:
    return {
        "tool": tool or "tool",
        "arguments": arguments or {},
        "success": False,
        "content": message,
        "data": {},
        "error": message,
    }


def _merge_tool_data(
    existing: dict[str, Any] | None,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Keep the existing frontend contract intact. In particular main.py expects
    music_command and code_result at the top level of tool_data.
    """
    merged = dict(existing or {})
    for result in results:
        data = result.get("data") or {}
        if isinstance(data, dict):
            merged.update(data)
    return merged


def _tool_requires_serial_execution(call: ToolCall) -> bool:
    name = call.tool
    args = call.arguments or {}
    tool = registry.get(name)

    if name in _SERIAL_TOOL_NAMES:
        return True

    # Existing write/approval paths.
    if name == "email_tool" and str(args.get("mode", "")).lower() == "send":
        return True
    if name == "calendar_tool" and str(args.get("mode", "")).lower() == "create_event":
        return True
    if name == "task_memory_tool" and str(args.get("action", "")).lower() in {"remember", "clear"}:
        return True

    if tool is not None and bool(getattr(tool, "requires_approval", False)):
        return True

    risk = getattr(tool, "risk", "low") if tool is not None else "low"
    risk_value = str(getattr(risk, "value", risk)).lower()
    return risk_value in {"high", "critical"}


def _prepare_call(raw: dict[str, Any]) -> tuple[ToolCall | None, str | None]:
    """Validate a planner call against the live registry without changing tools."""
    try:
        normalized = {
            "tool": raw.get("tool"),
            "arguments": dict(raw.get("arguments") or {}),
        }
        call = ToolCall(**normalized)
    except Exception as exc:
        return None, f"Invalid tool-call payload: {exc}"

    # Backward compatibility with an old code_execution_tool planner mistake.
    if call.tool == "code_execution_tool" and "code" in call.arguments:
        args = dict(call.arguments)
        raw_code = args.pop("code")
        args["task"] = f"Execute this Python code:\n\n{raw_code}"
        call = ToolCall(tool=call.tool, arguments=args)

    tool = registry.get(call.tool)
    if not tool:
        return None, f"Unknown tool: {call.tool}"

    # Custom Jarvis tools expose their real kwargs in run(). MCP/LangChain tools
    # are normalized by ToolExecutor, so binding their wrapper signature here
    # would incorrectly reject valid calls.
    if isinstance(tool, BaseTool):
        try:
            inspect.signature(tool.run).bind(**call.arguments)
        except TypeError as exc:
            return None, f"Invalid arguments for {call.tool}: {exc}"

    # Some existing tools intentionally use optional Python defaults even though
    # particular modes require meaningful values. Enforce those execution-level
    # requirements here so a dependency task cannot become ready with an empty
    # email body or incomplete calendar draft.
    mode = str(call.arguments.get("mode", "")).lower()
    if call.tool == "email_tool" and mode == "send":
        missing = [key for key in ("to", "subject", "body") if not str(call.arguments.get(key, "")).strip()]
        if missing:
            return None, f"email_tool mode=send requires non-empty: {', '.join(missing)}"
    if call.tool == "calendar_tool" and mode == "create_event":
        missing = [
            key for key in ("title", "start_time", "end_time")
            if not str(call.arguments.get(key, "")).strip()
        ]
        if missing:
            return None, f"calendar_tool mode=create_event requires non-empty: {', '.join(missing)}"

    return call, None


def _hot_load_self_modified_tool(draft: dict[str, Any], tool_result: Any) -> None:
    filename = str(draft.get("filename", "")).replace(".py", "").strip()
    if not filename:
        return

    try:
        path = _PROJECT_ROOT / "tools" / f"{filename}.py"
        spec = importlib.util.spec_from_file_location(filename, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load tool module: {path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        registered_name = ""
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, BaseTool) and attr is not BaseTool:
                instance = attr()
                registry.register(instance)
                registered_name = instance.name
                break

        if not registered_name:
            tool_result.content = "File loaded, but no valid BaseTool class found."
            _set_tool_result_ok(tool_result, False)
            return

        tool_result.content = (
            f"Tool {filename}.py successfully loaded and registered as "
            f"'{registered_name}'! You must use '{registered_name}' to call it."
        )
        _set_tool_result_ok(tool_result, True)
    except Exception as exc:
        tool_result.content = f"Failed to dynamically load tool: {exc}"
        _set_tool_result_ok(tool_result, False)


async def _execute_call_with_existing_hil(call: ToolCall) -> Any:
    """Execute one call while preserving Jarvis's existing approval semantics."""
    tool_result = await tool_executor.execute(call)
    data = getattr(tool_result, "data", None) or {}

    if not isinstance(data, dict) or not data.get("action_type"):
        return tool_result

    action_type = data["action_type"]
    draft = data.get("draft") or {}
    approval_payload = {
        "action_type": action_type,
        "draft": draft,
        "tool_name": call.tool,
    }

    resume_value = interrupt(approval_payload)
    if not isinstance(resume_value, dict):
        resume_value = {}

    if not resume_value.get("approved"):
        tool_result.content = "User rejected the action."
        _set_tool_result_ok(tool_result, False)
        tool_result.data = {}
        return tool_result

    args_to_use = dict(call.arguments)
    if "edited_code" in resume_value and call.tool == "self_modify_tool":
        args_to_use["code"] = resume_value["edited_code"]
        if isinstance(draft, dict):
            draft["code"] = resume_value["edited_code"]

    execute_args = {**args_to_use, "mode": "execute", "draft": draft}
    execute_call = ToolCall(tool=call.tool, arguments=execute_args)
    tool_result = await tool_executor.execute(execute_call)

    if call.tool == "self_modify_tool" and _tool_result_ok(tool_result):
        _hot_load_self_modified_tool(draft, tool_result)

    return tool_result


def _terminal_result(result: dict[str, Any]) -> bool:
    data = result.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    return bool(data.get("terminal") or data.get("passthrough")) or result.get("tool") == "research_tool"


def _best_effort_final(results: list[dict[str, Any]], reason: str = "") -> str:
    usable = [result for result in results if str(result.get("content", "")).strip()]
    if not usable:
        return reason or "I could not complete the requested tool work."

    if len(usable) == 1:
        text = str(usable[0].get("content", "")).strip()
        return f"{text}\n\n{reason}".strip() if reason else text

    sections: list[str] = []
    for result in usable:
        tool = str(result.get("tool", "tool"))
        content = str(result.get("content", "")).strip()
        sections.append(f"### {tool}\n{content}")

    if reason:
        sections.append(f"### Limitation\n{reason}")
    return "\n\n".join(sections)


def _build_observation_context(results: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    used = 0

    for index, result in enumerate(results, start=1):
        tool = str(result.get("tool", "tool"))
        success = bool(result.get("success", False))
        arguments = result.get("arguments") or {}
        content = str(result.get("content", ""))
        error = result.get("error")
        data = result.get("data") or {}

        compact_data = {}
        if isinstance(data, dict):
            for key in (
                "topic", "status", "result", "summary", "next_action",
                "retryable", "error", "warning", "terminal", "passthrough",
            ):
                if key in data:
                    compact_data[key] = data[key]

        remaining = MAX_TOTAL_OBSERVATION_CHARS - used
        if remaining <= 0:
            chunks.append("[Additional observations omitted from model context due to size limit.]")
            break

        content_limit = min(MAX_RESULT_CONTEXT_CHARS, remaining)
        clipped_content = content[:content_limit]
        if len(content) > content_limit:
            clipped_content += "\n[Result clipped for orchestration context.]"

        chunk = (
            f"[Observation {index}]\n"
            f"Tool: {tool}\n"
            f"Arguments: {json.dumps(arguments, ensure_ascii=False, default=str)}\n"
            f"Status: {'success' if success else 'failed'}\n"
            f"Result:\n{clipped_content}\n"
            f"Metadata: {json.dumps(compact_data, ensure_ascii=False, default=str)}"
        )
        if error:
            chunk += f"\nError: {error}"

        chunks.append(chunk)
        used += len(chunk)

    return "\n\n".join(chunks)


# ── Persistent task-ledger helpers ─────────────────────────────────────────────
_TASK_TERMINAL = {"completed", "failed"}


def _task_map(tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(task.get("id", "")): task for task in tasks if str(task.get("id", ""))}


def _initialize_tasks(raw_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make planner tasks deterministic and safe for LangGraph checkpointing."""
    tasks: list[dict[str, Any]] = []
    ids = {str(task.get("id", "")).strip() for task in raw_tasks if str(task.get("id", "")).strip()}

    for index, raw in enumerate(raw_tasks, start=1):
        task_id = str(raw.get("id") or f"T{index}").strip() or f"T{index}"
        deps = [str(dep).strip() for dep in (raw.get("depends_on") or []) if str(dep).strip()]
        unknown = [dep for dep in deps if dep not in ids]

        task = {
            "id": task_id,
            "goal": str(raw.get("goal") or raw.get("tool") or "Task").strip(),
            "tool": str(raw.get("tool") or "").strip(),
            "arguments": dict(raw.get("arguments") or {}),
            "depends_on": deps,
            # The runtime, not the model, owns initial readiness.
            "status": "blocked" if deps else "ready",
            "requires_approval": bool(raw.get("requires_approval", False)),
            "last_error": None,
        }
        if unknown:
            task["status"] = "failed"
            task["last_error"] = f"Planner referenced unknown dependencies: {', '.join(unknown)}"
        tasks.append(task)

    return tasks


def _legacy_tasks_from_calls(calls: list[ToolCall]) -> list[dict[str, Any]]:
    """Backward-compatible fallback when a model emits the old tool_calls shape."""
    return [
        {
            "id": f"T{index}",
            "goal": f"Execute {call.tool}",
            "tool": call.tool,
            "arguments": dict(call.arguments),
            "depends_on": [],
            "status": "ready",
            "requires_approval": _tool_requires_serial_execution(call),
            "last_error": None,
        }
        for index, call in enumerate(calls, start=1)
    ]


def _task_call_entry(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(task.get("id", "")),
        "tool": str(task.get("tool", "")),
        "arguments": dict(task.get("arguments") or {}),
    }


def _dependencies_completed(task: dict[str, Any], tasks: list[dict[str, Any]]) -> bool:
    by_id = _task_map(tasks)
    deps = task.get("depends_on") or []
    return all(by_id.get(str(dep), {}).get("status") == "completed" for dep in deps)


def _dependency_failed(task: dict[str, Any], tasks: list[dict[str, Any]]) -> str | None:
    by_id = _task_map(tasks)
    for dep in task.get("depends_on") or []:
        dep_task = by_id.get(str(dep))
        if dep_task and dep_task.get("status") == "failed":
            return str(dep)
    return None


def _mark_failed_dependencies(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated = [dict(task) for task in tasks]
    for task in updated:
        if task.get("status") in _TASK_TERMINAL:
            continue
        failed_dep = _dependency_failed(task, updated)
        if failed_dep:
            task["status"] = "failed"
            task["last_error"] = f"Dependency {failed_dep} failed."
    return updated


def _ready_task_calls(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _task_call_entry(task)
        for task in tasks
        if task.get("status") == "ready" and _dependencies_completed(task, tasks)
    ]


def _eligible_blocked_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Blocked tasks whose dependencies are done and can now be materialized."""
    return [
        task
        for task in tasks
        if task.get("status") == "blocked" and _dependencies_completed(task, tasks)
    ]


def _all_tasks_terminal(tasks: list[dict[str, Any]]) -> bool:
    return bool(tasks) and all(task.get("status") in _TASK_TERMINAL for task in tasks)


def _task_ledger_context(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "No persistent task plan is available."

    lines: list[str] = []
    for task in tasks:
        args = json.dumps(task.get("arguments") or {}, ensure_ascii=False, default=str)
        if len(args) > 3500:
            args = args[:3500] + "…"
        lines.append(
            f"[{task.get('id')}] status={task.get('status')}\n"
            f"Goal: {task.get('goal')}\n"
            f"Tool: {task.get('tool')}\n"
            f"Depends on: {task.get('depends_on') or []}\n"
            f"Arguments: {args}\n"
            f"Last error: {task.get('last_error') or 'none'}"
        )
    return "\n\n".join(lines)


def _relevant_tool_contracts(tasks: list[dict[str, Any]]) -> str:
    """Expose only unfinished tools' real schemas to the reviewer."""
    names = {
        str(task.get("tool", ""))
        for task in tasks
        if task.get("status") != "completed" and str(task.get("tool", ""))
    }
    if not names:
        return "No unfinished tool contracts."

    declarations: list[dict[str, Any]] = []
    if hasattr(registry, "function_declarations"):
        try:
            declarations = [
                item for item in registry.function_declarations()
                if str(item.get("name", "")) in names
            ]
        except Exception:
            declarations = []

    if declarations:
        return json.dumps(declarations, ensure_ascii=False, default=str)

    # Fallback for older registries.
    lines: list[str] = []
    for name in sorted(names):
        tool = registry.get(name)
        if tool is None:
            lines.append(f"{name}: unavailable")
            continue
        run = getattr(tool, "run", None) or getattr(tool, "arun", None)
        signature = str(inspect.signature(run)) if callable(run) else "(**kwargs)"
        lines.append(f"{name}{signature}")
    return "\n".join(lines)


def _set_task_validation_error(
    tasks: list[dict[str, Any]],
    task_id: str | None,
    message: str,
) -> list[dict[str, Any]]:
    if not task_id:
        return tasks
    updated = [dict(task) for task in tasks]
    for task in updated:
        if str(task.get("id")) == task_id and task.get("status") not in _TASK_TERMINAL:
            task["status"] = "blocked"
            task["last_error"] = message
            break
    return updated


def _set_task_result(
    tasks: list[dict[str, Any]],
    task_id: str | None,
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    if not task_id:
        return tasks
    updated = [dict(task) for task in tasks]
    for task in updated:
        if str(task.get("id")) != task_id:
            continue
        task["status"] = "completed" if result.get("success") else "failed"
        task["last_error"] = None if result.get("success") else (
            result.get("error") or result.get("content") or "Tool execution failed."
        )
        break
    return updated


def _apply_task_updates(
    tasks: list[dict[str, Any]],
    updates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Merge reviewer-produced updates without allowing completed work to be rewritten.
    Returns the updated ledger and the valid calls that became ready.
    """
    updated = [dict(task) for task in tasks]
    by_id = _task_map(updated)

    for patch in updates:
        task_id = str(patch.get("id", "")).strip()
        task = by_id.get(task_id)
        if not task:
            print(f"[ORCHESTRATOR] Ignoring update for unknown task {task_id!r}.")
            continue
        if task.get("status") == "completed":
            continue

        if isinstance(patch.get("tool"), str) and patch.get("tool"):
            task["tool"] = str(patch["tool"])
        if isinstance(patch.get("arguments"), dict):
            task["arguments"] = {**dict(task.get("arguments") or {}), **patch["arguments"]}

        requested_status = str(patch.get("status", "")).lower()
        if requested_status == "failed":
            task["status"] = "failed"
            task["last_error"] = str(patch.get("reason") or "Task could not be completed.")
            continue
        if requested_status == "blocked":
            task["status"] = "blocked"
            task["last_error"] = str(patch.get("reason") or task.get("last_error") or "Task remains blocked.")
            continue

        # Supplying materialized arguments/tool implies the task should be tried now.
        wants_ready = requested_status == "ready" or "arguments" in patch or "tool" in patch
        if not wants_ready:
            continue

        # A dependency-blocked task must be deliberately materialized after its
        # prerequisite observations arrive. Require the reviewer to send an
        # arguments object, even when it only confirms already-known arguments.
        if task.get("depends_on") and requested_status == "ready" and "arguments" not in patch:
            task["status"] = "blocked"
            task["last_error"] = "Dependency-ready task was not materialized with arguments."
            continue

        if not _dependencies_completed(task, updated):
            task["status"] = "blocked"
            task["last_error"] = "Dependencies are not complete yet."
            continue

        call, validation_error = _prepare_call(_task_call_entry(task))
        if call is None:
            task["status"] = "blocked"
            task["last_error"] = validation_error or "Task arguments are not executable yet."
            print(f"[ORCHESTRATOR] Task {task_id} update is not executable: {task['last_error']}")
            continue

        task["status"] = "ready"
        task["last_error"] = None

    return updated, _ready_task_calls(updated)


def _legacy_calls_as_task_updates(
    calls: list[ToolCall],
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map an old reviewer tool_calls response onto an existing unfinished task."""
    updates: list[dict[str, Any]] = []
    claimed: set[str] = set()

    for call in calls:
        match = next(
            (
                task for task in tasks
                if str(task.get("id")) not in claimed
                and task.get("status") != "completed"
                and str(task.get("tool")) == call.tool
                and _dependencies_completed(task, tasks)
            ),
            None,
        )
        if not match:
            continue
        task_id = str(match.get("id"))
        claimed.add(task_id)
        updates.append({
            "id": task_id,
            "tool": call.tool,
            "arguments": dict(call.arguments),
            "status": "ready",
        })

    return updates

# ── Memory / personality ──────────────────────────────────────────────────────
async def memory_retrieval_node(state: JarvisState) -> dict:
    """Query ChromaDB for long-term memories relevant to the current message."""
    user_message = state.get("user_message", "")
    if not user_message:
        return {"memory_context": ""}

    loop = asyncio.get_running_loop()

    def _query_db():
        return semantic_memory.query_memories(query_text=user_message, n_results=3)

    memories = await loop.run_in_executor(None, _query_db)
    if not memories:
        return {"memory_context": ""}

    memory_context = "\n".join(
        f"- {mem.content} (Type: {mem.type.value}, Source: {mem.source_date})"
        for mem in memories
    )
    print(f"[MEMORY F9] Retrieved context:\n{memory_context}")
    return {"memory_context": memory_context}


async def personality_node(state: JarvisState) -> dict:
    personality_prompt = personality.build_system_prompt(
        user_message=state["user_message"],
        memory_facts=state.get("memory_facts") or [],
    )
    system_prompt = personality_prompt + "\n\n" + TOOL_ROUTER_PROMPT

    memory_context = state.get("memory_context", "")
    if memory_context:
        system_prompt += (
            "\n\nLONG-TERM MEMORY CONTEXT:\n"
            "You remember the following facts about the user. Use them naturally if relevant:\n"
            f"{memory_context}"
        )

    # Per-turn orchestration state is reset here. tool_data is deliberately only
    # cleared at the start of a new user turn so completed UI events survive until
    # main.py has emitted them.
    return {
        "system_prompt": system_prompt,
        "final_reply": None,
        "tool_call": None,
        "tool_calls": [],
        "planned_tasks": [],
        "deferred_tool_calls": [],
        "tool_result": None,
        "tool_results": [],
        "current_tool_results": [],
        "executed_calls": [],
        "tool_data": {},
        "tool_calls_made": 0,
        "tool_rounds_made": 0,
    }


async def _repair_legacy_plan(
    *,
    history: list[dict[str, Any]],
    system_prompt: str,
) -> list[dict[str, Any]]:
    """
    One bounded recovery call when Gemini violates the persistent-plan contract
    and returns only executable calls. This is intentionally not used on a valid
    task plan, so normal requests do not pay an extra LLM round trip.
    """
    loop = asyncio.get_running_loop()
    repair_instruction = (
        system_prompt
        + "\n\nPLANNER FORMAT RECOVERY — MANDATORY:\n"
        "Your previous response did not contain the required complete task ledger. "
        "Re-plan the ORIGINAL user request now. Preserve every requested outcome, "
        "including work that must wait for another tool result. Return ONLY the "
        "required {\"tasks\":[...]} JSON plan. Do not return tool_calls and do not answer in prose."
    )
    try:
        reply = await loop.run_in_executor(
            None,
            lambda: generate_reply_with_history(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                messages=history,
                system_instruction=repair_instruction,
                max_output_tokens=4000,
            ),
        )
    except GeminiClientError:
        return []
    return try_parse_task_plan(reply.text)


# ── Planner ───────────────────────────────────────────────────────────────────
async def gemini_node(state: JarvisState) -> dict:
    await _emit_orb("processing")

    loop = asyncio.get_running_loop()
    history: list[dict[str, Any]] = list(state.get("history", []))
    image_data = state.get("image_data")

    if image_data:
        match = re.match(r"data:(image/[\w+]+);base64,(.*)", image_data)
        if match:
            mime_type = match.group(1)
            b64_data = match.group(2)
            if history and history[-1].get("role") == "user":
                last_user_text = history[-1].get("content", state.get("user_message", ""))
                history[-1] = {
                    "role": "user",
                    "parts": [
                        {"text": last_user_text},
                        {"inline_data": {"mime_type": mime_type, "data": b64_data}},
                    ],
                }
            else:
                history.append({
                    "role": "user",
                    "parts": [
                        {"text": state.get("user_message", "")},
                        {"inline_data": {"mime_type": mime_type, "data": b64_data}},
                    ],
                })

    try:
        reply = await loop.run_in_executor(
            None,
            lambda: generate_reply_with_history(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                messages=history,
                system_instruction=state["system_prompt"],
                max_output_tokens=4000,
            ),
        )
    except GeminiClientError as exc:
        await _emit_orb("error")
        message = f"Gemini request failed. Details: {exc}"
        return {
            "gemini_reply": message,
            "tool_call": None,
            "tool_calls": [],
            "planned_tasks": [],
            "final_reply": message,
        }

    raw_tasks = try_parse_task_plan(reply.text)
    legacy_calls: list[ToolCall] = []

    if not raw_tasks:
        legacy_calls = try_parse_tool_calls(reply.text)
        if legacy_calls:
            print("[ORCHESTRATOR] Planner emitted legacy tool_calls; requesting complete task ledger once.")
            repaired = await _repair_legacy_plan(
                history=history,
                system_prompt=state["system_prompt"],
            )
            if repaired:
                raw_tasks = repaired

    tasks = _initialize_tasks(raw_tasks) if raw_tasks else _legacy_tasks_from_calls(legacy_calls)
    ready_calls = _ready_task_calls(tasks)

    print("=" * 80)
    print("RAW GEMINI RESPONSE:")
    print(reply.text[:700])
    print(f"TASK PLAN ({len(tasks)}): {tasks}")
    print(f"READY CALLS ({len(ready_calls)}): {ready_calls}")
    print("=" * 80)

    # If the model did not request tools, its text is the direct response.
    if not tasks:
        return {
            "gemini_reply": reply.text,
            "planned_tasks": [],
            "tool_calls": [],
            "tool_call": None,
        }

    return {
        "gemini_reply": reply.text,
        "planned_tasks": tasks,
        "tool_calls": ready_calls,
        "tool_call": ready_calls[0] if ready_calls else None,
    }


def route_after_gemini(state: JarvisState) -> str:
    if state.get("final_reply"):
        return "done"
    pending = state.get("tool_calls") or []
    if not pending and state.get("tool_call"):
        pending = [state["tool_call"]]
    # A valid plan can contain only blocked tasks. Route through tool_response via
    # the tool node so the reviewer can materialize the first dependency-ready task.
    if pending:
        return "tool"
    if state.get("planned_tasks"):
        return "tool"
    return "done"


# ── Parallel dispatcher ───────────────────────────────────────────────────────
async def tool_node(state: JarvisState) -> dict:
    pending_raw = list(state.get("tool_calls") or [])
    if not pending_raw and state.get("tool_call"):
        pending_raw = [state["tool_call"]]

    previous_results = list(state.get("tool_results") or [])
    executed_calls = list(state.get("executed_calls") or [])
    planned_tasks = [dict(task) for task in (state.get("planned_tasks") or [])]
    executed_fingerprints = {_call_fingerprint(item) for item in executed_calls}
    calls_made = int(state.get("tool_calls_made", 0) or 0)
    rounds_made = int(state.get("tool_rounds_made", 0) or 0)

    if not pending_raw:
        return {
            "tool_calls": [],
            "tool_call": None,
            "current_tool_results": [],
            "planned_tasks": planned_tasks,
        }

    await _emit_orb("processing")

    immediate_results: list[dict[str, Any]] = []
    prepared: list[tuple[str | None, ToolCall]] = []
    seen_this_batch: set[str] = set()

    for raw in pending_raw:
        if not isinstance(raw, dict):
            immediate_results.append(_error_result(
                tool="tool",
                arguments={},
                message="Invalid planner output: tool call must be an object.",
            ))
            continue

        task_id = str(raw.get("task_id") or "").strip() or None
        if task_id:
            task = _task_map(planned_tasks).get(task_id)
            if not task:
                message = f"Unknown task_id: {task_id}"
                print(f"[ORCHESTRATOR] Rejected call: {message}; raw={raw}")
                immediate_results.append(_error_result(
                    tool=str(raw.get("tool", "tool")),
                    arguments=dict(raw.get("arguments") or {}),
                    message=message,
                ))
                continue
            if task.get("status") == "completed":
                message = f"Task {task_id} is already completed."
                print(f"[ORCHESTRATOR] Rejected call: {message}")
                immediate_results.append(_error_result(
                    tool=str(raw.get("tool", "tool")),
                    arguments=dict(raw.get("arguments") or {}),
                    message=message,
                ))
                continue
            if not _dependencies_completed(task, planned_tasks):
                message = f"Task {task_id} dependencies are not complete."
                print(f"[ORCHESTRATOR] Rejected call: {message}; raw={raw}")
                planned_tasks = _set_task_validation_error(planned_tasks, task_id, message)
                immediate_results.append(_error_result(
                    tool=str(raw.get("tool", "tool")),
                    arguments=dict(raw.get("arguments") or {}),
                    message=message,
                ))
                continue

        call, validation_error = _prepare_call(raw)
        if call is None:
            message = validation_error or "Invalid tool call."
            print(f"[ORCHESTRATOR] Rejected call task={task_id}: {message}; raw={raw}")
            planned_tasks = _set_task_validation_error(planned_tasks, task_id, message)
            immediate_results.append(_error_result(
                tool=str(raw.get("tool", "tool")),
                arguments=dict(raw.get("arguments") or {}),
                message=message,
            ))
            if task_id:
                immediate_results[-1]["task_id"] = task_id
            continue

        # Include task_id in the duplicate fingerprint when available so two
        # distinct tasks can intentionally use identical tool arguments.
        fingerprint_payload = {
            "task_id": task_id,
            "tool": call.tool,
            "arguments": dict(call.arguments),
        }
        fingerprint = _call_fingerprint(fingerprint_payload)
        if fingerprint in executed_fingerprints or fingerprint in seen_this_batch:
            message = "Duplicate tool call blocked; this task/call already ran in the current turn."
            print(f"[ORCHESTRATOR] Rejected duplicate task={task_id} tool={call.tool}")
            planned_tasks = _set_task_validation_error(planned_tasks, task_id, message)
            result = _error_result(
                tool=call.tool,
                arguments=dict(call.arguments),
                message=message,
            )
            if task_id:
                result["task_id"] = task_id
            immediate_results.append(result)
            continue

        seen_this_batch.add(fingerprint)
        prepared.append((task_id, call))

    if rounds_made >= MAX_TOOL_ROUNDS:
        for task_id, call in prepared:
            message = f"Tool-round limit reached ({MAX_TOOL_ROUNDS})."
            planned_tasks = _set_task_validation_error(planned_tasks, task_id, message)
            result = _error_result(tool=call.tool, arguments=dict(call.arguments), message=message)
            if task_id:
                result["task_id"] = task_id
            immediate_results.append(result)
        cumulative = previous_results + immediate_results
        return {
            "tool_calls": [],
            "tool_call": None,
            "deferred_tool_calls": [],
            "current_tool_results": immediate_results,
            "tool_results": cumulative,
            "tool_result": immediate_results[-1] if immediate_results else None,
            "tool_data": _merge_tool_data(state.get("tool_data"), immediate_results),
            "planned_tasks": planned_tasks,
            "tool_rounds_made": rounds_made,
            "tool_calls_made": calls_made,
        }

    remaining_budget = max(0, MAX_TOTAL_TOOL_CALLS - calls_made)
    eligible = prepared[:remaining_budget]
    over_budget = prepared[remaining_budget:]
    for task_id, call in over_budget:
        message = f"Total tool-call limit reached ({MAX_TOTAL_TOOL_CALLS})."
        planned_tasks = _set_task_validation_error(planned_tasks, task_id, message)
        result = _error_result(tool=call.tool, arguments=dict(call.arguments), message=message)
        if task_id:
            result["task_id"] = task_id
        immediate_results.append(result)

    serial_calls = [item for item in eligible if _tool_requires_serial_execution(item[1])]
    parallel_calls = [item for item in eligible if not _tool_requires_serial_execution(item[1])]

    # Only one potentially interrupting/side-effect-sensitive capability per
    # round. Extra serial work is deferred and executed later from the persistent
    # task plan rather than being rediscovered by the model.
    serial_now = serial_calls[:1]
    deferred_items = serial_calls[1:]

    parallel_now = parallel_calls[:MAX_PARALLEL_TOOLS]
    deferred_items.extend(parallel_calls[MAX_PARALLEL_TOOLS:])

    def _execution_entry(item: tuple[str | None, ToolCall]) -> dict[str, Any]:
        task_id, call = item
        payload = _call_dict(call)
        if task_id:
            payload["task_id"] = task_id
        return payload

    executed_now: list[tuple[str | None, ToolCall, Any]] = []

    # Serial/HIL work runs first. If it interrupts, no unrelated side effect has
    # run yet, so LangGraph can safely resume this node without replaying siblings.
    if serial_now:
        task_id, serial_call = serial_now[0]
        serial_result = await _execute_call_with_existing_hil(serial_call)
        serial_result_dict = _result_dict(serial_call, serial_result)
        if task_id:
            serial_result_dict["task_id"] = task_id
        await _emit_result_events(serial_result_dict)
        planned_tasks = _set_task_result(planned_tasks, task_id, serial_result_dict)
        executed_now.append((task_id, serial_call, serial_result))

    completed_parallel_results: list[dict[str, Any]] = []
    if parallel_now:
        if any(call.tool == "code_execution_tool" for _, call in parallel_now):
            await _emit_orb("processing_code")

        async def _run_parallel(
            item: tuple[str | None, ToolCall],
        ) -> tuple[str | None, ToolCall, Any]:
            task_id, call = item
            try:
                return task_id, call, await tool_executor.execute(call)
            except Exception as exc:
                return task_id, call, exc

        tasks = [asyncio.create_task(_run_parallel(item)) for item in parallel_now]
        for completed in asyncio.as_completed(tasks):
            task_id, call, result = await completed
            if isinstance(result, Exception):
                result_dict = _error_result(
                    tool=call.tool,
                    arguments=dict(call.arguments),
                    message=f"Tool execution failed: {result}",
                )
                if task_id:
                    result_dict["task_id"] = task_id
                completed_parallel_results.append(result_dict)
                planned_tasks = _set_task_result(planned_tasks, task_id, result_dict)
                continue

            result_dict = _result_dict(call, result)
            if task_id:
                result_dict["task_id"] = task_id
            completed_parallel_results.append(result_dict)
            await _emit_result_events(result_dict)
            planned_tasks = _set_task_result(planned_tasks, task_id, result_dict)
            executed_now.append((task_id, call, result))

    serial_result_dicts: list[dict[str, Any]] = []
    for task_id, call, result in executed_now:
        if any(call is serial_call for _, serial_call in serial_now):
            result_dict = _result_dict(call, result)
            if task_id:
                result_dict["task_id"] = task_id
            serial_result_dicts.append(result_dict)

    batch_results = immediate_results + serial_result_dicts + completed_parallel_results
    executed_call_dicts = [
        _execution_entry((task_id, call))
        for task_id, call, _ in executed_now
    ]
    cumulative_results = previous_results + batch_results
    new_executed_calls = executed_calls + executed_call_dicts
    actual_call_count = len(executed_call_dicts)

    print(
        f"[ORCHESTRATOR] Round {rounds_made + 1}: "
        f"executed={actual_call_count}, deferred={len(deferred_items)}, "
        f"total_calls={calls_made + actual_call_count}"
    )

    return {
        "tool_calls": [],
        "tool_call": None,
        "deferred_tool_calls": [_execution_entry(item) for item in deferred_items],
        "current_tool_results": batch_results,
        "tool_results": cumulative_results,
        "tool_result": batch_results[-1] if batch_results else None,
        "executed_calls": new_executed_calls,
        "tool_data": _merge_tool_data(state.get("tool_data"), batch_results),
        "planned_tasks": planned_tasks,
        "tool_calls_made": calls_made + actual_call_count,
        "tool_rounds_made": rounds_made + 1,
    }


# ── Reviewer / next-step planner ──────────────────────────────────────────────
async def _review_tasks_with_model(
    *,
    state: JarvisState,
    tasks: list[dict[str, Any]],
    all_results: list[dict[str, Any]],
    strict_recovery: bool = False,
) -> str:
    observation_context = _build_observation_context(all_results)
    task_context = _task_ledger_context(tasks)
    tool_contracts = _relevant_tool_contracts(tasks)

    instruction = TOOL_RESPONSE_PROMPT
    if strict_recovery:
        eligible_ids = [str(task.get("id")) for task in _eligible_blocked_tasks(tasks)]
        instruction += (
            "\n\nTASK-LEDGER RECOVERY — MANDATORY:\n"
            "The request is NOT complete. One or more planned tasks remain unfinished even "
            "though their dependencies are satisfied. Do not answer the user yet. Return ONLY "
            "task_updates JSON that materializes the next executable task(s) with complete valid "
            "tool arguments derived from the observations. Eligible task IDs: "
            f"{eligible_ids}."
        )

    loop = asyncio.get_running_loop()
    reply = await loop.run_in_executor(
        None,
        lambda: generate_reply_with_history(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            messages=[{
                "role": "user",
                "content": (
                    f"Original request:\n{state.get('user_message', '')}\n\n"
                    f"Persistent task ledger:\n{task_context}\n\n"
                    f"Relevant live tool contracts:\n{tool_contracts}\n\n"
                    f"Completed tool observations ({len(all_results)}):\n{observation_context}\n\n"
                    "Use the task ledger as the source of truth for unfinished work. "
                    "Do not drop a requested outcome merely because it is not executable yet."
                ),
            }],
            system_instruction=instruction,
            max_output_tokens=6000,
        ),
    )
    return reply.text


async def tool_response_node(state: JarvisState) -> dict:
    await _emit_orb("processing")

    all_results = list(state.get("tool_results") or [])
    deferred = list(state.get("deferred_tool_calls") or [])
    planned_tasks = _mark_failed_dependencies(
        [dict(task) for task in (state.get("planned_tasks") or [])]
    )
    calls_made = int(state.get("tool_calls_made", 0) or 0)
    rounds_made = int(state.get("tool_rounds_made", 0) or 0)

    # Work already selected by the plan but deferred solely for concurrency/HIL
    # safety is executed before asking the model to reason again.
    if deferred and calls_made < MAX_TOTAL_TOOL_CALLS and rounds_made < MAX_TOOL_ROUNDS:
        remaining = MAX_TOTAL_TOOL_CALLS - calls_made
        next_batch = deferred[:remaining]
        still_deferred = deferred[remaining:]
        print(f"[ORCHESTRATOR] Scheduling {len(next_batch)} deferred call(s): {next_batch}")
        return {
            "planned_tasks": planned_tasks,
            "tool_calls": next_batch,
            "tool_call": next_batch[0] if next_batch else None,
            "deferred_tool_calls": still_deferred,
            "final_reply": None,
        }

    # Any ready task left in the ledger is executable without another LLM call.
    deterministic_ready = _ready_task_calls(planned_tasks)
    if deterministic_ready and calls_made < MAX_TOTAL_TOOL_CALLS and rounds_made < MAX_TOOL_ROUNDS:
        remaining = MAX_TOTAL_TOOL_CALLS - calls_made
        next_batch = deterministic_ready[:remaining]
        print(f"[ORCHESTRATOR] Scheduling {len(next_batch)} ready ledger task(s): {next_batch}")
        return {
            "planned_tasks": planned_tasks,
            "tool_calls": next_batch,
            "tool_call": next_batch[0] if next_batch else None,
            "deferred_tool_calls": [],
            "final_reply": None,
        }

    # Preserve direct passthrough for a genuinely standalone terminal specialist.
    if (
        len(planned_tasks) == 1
        and planned_tasks[0].get("status") == "completed"
        and len(all_results) == 1
        and _terminal_result(all_results[0])
    ):
        content = str(all_results[0].get("content", "")).strip()
        if content:
            print(f"[ORCHESTRATOR] Standalone terminal result from {all_results[0].get('tool')}; passthrough.")
            return {
                "planned_tasks": planned_tasks,
                "final_reply": content,
                "tool_calls": [],
                "tool_call": None,
                "deferred_tool_calls": [],
            }

    exhausted = calls_made >= MAX_TOTAL_TOOL_CALLS or rounds_made >= MAX_TOOL_ROUNDS
    if exhausted and not _all_tasks_terminal(planned_tasks):
        unresolved = [
            f"{task.get('id')}: {task.get('goal')} ({task.get('status')})"
            for task in planned_tasks
            if task.get("status") not in _TASK_TERMINAL
        ]
        return {
            "planned_tasks": planned_tasks,
            "final_reply": _best_effort_final(
                all_results,
                reason=(
                    "Some requested work remains incomplete because the bounded tool-execution "
                    f"limit was reached: {', '.join(unresolved)}"
                ),
            ),
            "tool_calls": [],
            "tool_call": None,
        }

    try:
        review_text = await _review_tasks_with_model(
            state=state,
            tasks=planned_tasks,
            all_results=all_results,
        )
    except GeminiClientError as exc:
        await _emit_orb("error")
        return {
            "planned_tasks": planned_tasks,
            "final_reply": _best_effort_final(
                all_results,
                reason=f"Final synthesis failed: {exc}",
            ),
            "tool_calls": [],
            "tool_call": None,
        }

    updates = try_parse_task_updates(review_text)

    # Backward compatibility: if the reviewer emits the old tool_calls shape,
    # attach each call to the matching unfinished ledger task instead of losing it.
    if not updates:
        legacy_calls = try_parse_tool_calls(review_text)
        if legacy_calls:
            updates = _legacy_calls_as_task_updates(legacy_calls, planned_tasks)
            if updates:
                print(f"[ORCHESTRATOR] Mapped legacy reviewer calls onto tasks: {updates}")

    if updates and not exhausted:
        planned_tasks, next_batch = _apply_task_updates(planned_tasks, updates)
        print(f"[ORCHESTRATOR] Reviewer task updates: {updates}")
        if next_batch:
            remaining = MAX_TOTAL_TOOL_CALLS - calls_made
            next_batch = next_batch[:remaining]
            print(f"[ORCHESTRATOR] Reviewer scheduled {len(next_batch)} call(s): {next_batch}")
            return {
                "planned_tasks": planned_tasks,
                "tool_calls": next_batch,
                "tool_call": next_batch[0] if next_batch else None,
                "final_reply": None,
            }

    # Never accept a premature prose answer while a known task is now eligible to
    # continue. One bounded recovery call focuses the model on that exact task.
    eligible_blocked = _eligible_blocked_tasks(planned_tasks)
    if eligible_blocked and not exhausted:
        print(
            "[ORCHESTRATOR] Reviewer returned no executable update for dependency-ready "
            f"task(s): {[task.get('id') for task in eligible_blocked]}. Retrying once."
        )
        try:
            recovery_text = await _review_tasks_with_model(
                state=state,
                tasks=planned_tasks,
                all_results=all_results,
                strict_recovery=True,
            )
        except GeminiClientError:
            recovery_text = ""

        recovery_updates = try_parse_task_updates(recovery_text)
        if not recovery_updates:
            legacy_calls = try_parse_tool_calls(recovery_text) if recovery_text else []
            recovery_updates = _legacy_calls_as_task_updates(legacy_calls, planned_tasks)

        if recovery_updates:
            planned_tasks, next_batch = _apply_task_updates(planned_tasks, recovery_updates)
            print(f"[ORCHESTRATOR] Recovery task updates: {recovery_updates}")
            if next_batch:
                remaining = MAX_TOTAL_TOOL_CALLS - calls_made
                next_batch = next_batch[:remaining]
                return {
                    "planned_tasks": planned_tasks,
                    "tool_calls": next_batch,
                    "tool_call": next_batch[0] if next_batch else None,
                    "final_reply": None,
                }

    # A final prose answer is valid only when the ledger has no runnable/eligible
    # unfinished work. Failed tasks are terminal and may be reported honestly.
    if _all_tasks_terminal(planned_tasks):
        return {
            "planned_tasks": planned_tasks,
            "final_reply": review_text,
            "tool_calls": [],
            "tool_call": None,
        }

    unresolved = [
        f"{task.get('id')}: {task.get('goal')}"
        for task in planned_tasks
        if task.get("status") not in _TASK_TERMINAL
    ]
    return {
        "planned_tasks": planned_tasks,
        "final_reply": _best_effort_final(
            all_results,
            reason=(
                "I could not safely materialize the remaining planned action(s): "
                + "; ".join(unresolved)
            ),
        ),
        "tool_calls": [],
        "tool_call": None,
    }


async def finalize_node(state: JarvisState) -> dict:
    await _emit_orb("speaking")

    # Keep tool_data/tool_results alive until main.py has emitted UI commands and
    # completed the response. personality_node clears them on the next user turn.
    final_reply = state.get("final_reply") or state.get("gemini_reply") or ""
    return {
        "final_reply": final_reply,
        "tool_call": None,
        "tool_calls": [],
        "deferred_tool_calls": [],
        "current_tool_results": [],
    }