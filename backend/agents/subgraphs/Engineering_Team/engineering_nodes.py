from __future__ import annotations

import asyncio
import hashlib
import json
import re
from functools import wraps
from typing import Any, Awaitable, Callable
from agents.gemini_client import generate_reply
from config import get_settings

from .budget import BudgetExceeded, budget_guard
from .engineering_prompts import (
    BACKEND_ENGINEER_SYSTEM, FILE_MANIFEST_SYSTEM, FRONTEND_ENGINEER_SYSTEM,
    PRODUCT_MANAGER_SYSTEM, REQUIREMENT_ARCHITECT_SYSTEM,
    REVIEW_REPAIR_SYSTEM, ROADMAP_SYSTEM, TEST_ENGINEER_SYSTEM,
)
from .engineering_state import (
    MAX_MANIFEST_REBUILDS,
    MAX_REPAIR_ATTEMPTS,
    MAX_ROADMAP_REBUILDS,
    EngineeringState,
    Owner,
    OwnerState,
)
from .manifest_utils import (
    files_for_owner, normalize_manifest_file, validate_file_bundle,
    validate_manifest,
)
from .project_writer import write_project
from .sandbox_runner import run_project_in_sandbox
from .tracing import emit_trace, trace_span


settings = get_settings()
FAST_MODEL = getattr(settings, "engineering_fast_model", "gemini-2.5-flash")
REASONING_MODEL = getattr(settings, "engineering_reasoning_model", "gemini-2.5-pro")
STACKS = {"STATIC_HTML_CSS_JS", "REACT_VITE", "FASTAPI_STATIC", "FASTAPI_REACT"}


def _control(state: EngineeringState, node: str, status: str, error: str = "") -> dict[str, Any]:
    value = dict(state["control"])
    value.update(current_node=node, status=status, error=error)
    return value


def _owner_failure(state: EngineeringState, key: str, error: str) -> OwnerState:
    value: OwnerState = dict(state[key])  # type: ignore[index]
    value.update(status="FAILED", error=error, attempt=value["attempt"] + 1)
    return value


def safe_node(node: str, owner_key: str = ""):
    def decorator(fn: Callable[[EngineeringState], Awaitable[dict[str, Any]]]):
        @wraps(fn)
        async def wrapped(state: EngineeringState) -> dict[str, Any]:
            try:
                return await fn(state)
            except BudgetExceeded as exc:
                return {"control": _control(state, node, "BUDGET_EXCEEDED", str(exc))}
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if owner_key:
                    return {owner_key: _owner_failure(state, owner_key, error)}
                return {"control": _control(state, node, "FAILED", error)}
        return wrapped
    return decorator


def _text(reply: Any) -> str:
    return str(getattr(reply, "text", reply) or "").strip()


def _usage(reply: Any) -> tuple[int, int]:
    value = getattr(reply, "usage_metadata", None) or getattr(reply, "usage", None) or {}
    if not isinstance(value, dict):
        return 0, 0
    return (
        int(value.get("prompt_token_count") or value.get("input_tokens") or 0),
        int(value.get("candidates_token_count") or value.get("output_tokens") or 0),
    )


def _json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = "\n".join(value.splitlines()[1:-1]).strip()
    start = value.find("{")
    if start < 0:
        raise ValueError("No JSON object in response.")
    depth, quoted, escaped = 0, False, False
    for index in range(start, len(value)):
        char = value[index]
        if escaped:
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted and char == "{":
            depth += 1
        elif not quoted and char == "}":
            depth -= 1
            if depth == 0:
                result = json.loads(value[start:index + 1])
                if not isinstance(result, dict):
                    raise ValueError("JSON output must be an object.")
                return result
    raise ValueError("Incomplete JSON response.")


async def _call(
    state: EngineeringState,
    node: str,
    system: str,
    prompt: str,
    *,
    reasoning: bool,
    tokens: int,
    json_output: bool = False,
) -> str | dict[str, Any]:
    request_id = state["request"]["request_id"]
    model = REASONING_MODEL if reasoning else FAST_MODEL
    last_error = ""

    for attempt in range(2 if json_output else 1):
        await budget_guard.register(request_id)
        await budget_guard.reserve_llm_call(request_id, reasoning=reasoning)
        user_prompt = prompt if not attempt else prompt + "\n\nReturn one complete raw JSON object only."
        async with trace_span(
            request_id=request_id,
            node=node,
            operation="llm.generate",
            metadata={"model": model, "prompt_chars": len(user_prompt)},
        ):
            reply = await asyncio.to_thread(
                generate_reply,
                api_key=settings.gemini_api_key,
                model=model,
                user_message=user_prompt,
                system_instruction=system,
                temperature=0.1,
                max_output_tokens=tokens,
            )
        input_tokens, output_tokens = _usage(reply)
        await budget_guard.record_usage(
            request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        result = _text(reply)
        if not json_output:
            return result
        try:
            return _json(result)
        except Exception as exc:
            last_error = str(exc)

    raise ValueError(last_error or "Invalid JSON response.")


@safe_node("product_manager")
async def product_manager_node(
    state: EngineeringState,
) -> dict[str, Any]:
    prompt = f"""RAW USER REQUEST:
{state['request']['raw_prompt']}

SUPPORTED STACKS:
STATIC_HTML_CSS_JS, REACT_VITE, FASTAPI_STATIC, FASTAPI_REACT

SUPPORTED STORAGE:
none, in-memory, local JSON

Infer non-critical prototype details. Do not ask the user questions.
"""

    report = str(
        await _call(
            state,
            "product_manager",
            PRODUCT_MANAGER_SYSTEM,
            prompt,
            reasoning=False,
            tokens=2_500,
        )
    )

    if not report:
        raise ValueError("Product brief is empty.")

    return {
        "pm_report": report,
        "control": _control(
            state,
            "product_manager",
            "COMPLETED",
        ),
    }


@safe_node("requirement_architect")

async def requirement_architect_node(state: EngineeringState) -> dict[str, Any]:
    prompt = (
        f"RAW REQUEST:\n{state['request']['raw_prompt']}\n\n"
        f"PRODUCT BRIEF:\n{state['pm_report']}\n\n"
        f"STACK HINT:\n{state['request']['target_stack_hint'] or 'None'}"
    )
    data = await _call(state, "requirement_architect", REQUIREMENT_ARCHITECT_SYSTEM, prompt, reasoning=True, tokens=5000, json_output=True)
    assert isinstance(data, dict)
    stack = str(data.get("selected_stack", "")).upper()
    spec = str(data.get("engineering_spec_markdown", "")).strip()
    if stack not in STACKS or not spec:
        raise ValueError("Invalid engineering specification.")
    return {
        "engineering_spec": spec,
        "selected_stack": stack,
        "frontend_required": bool(data.get("frontend_required", True)),
        "backend_required": bool(data.get("backend_required", False)),
        "control": _control(state, "requirement_architect", "COMPLETED"),
    }


@safe_node("roadmap")
async def roadmap_node(state: EngineeringState) -> dict[str, Any]:
    prompt = f"SELECTED STACK:\n{state['selected_stack']}\n\nFROZEN SPECIFICATION:\n{state['engineering_spec']}"
    roadmap = str(await _call(state, "roadmap", ROADMAP_SYSTEM, prompt, reasoning=True, tokens=4000))
    if not roadmap:
        raise ValueError("Empty roadmap.")
    return {"roadmap": roadmap, "control": _control(state, "roadmap", "COMPLETED")}


@safe_node("file_manifest")
async def file_manifest_node(state: EngineeringState) -> dict[str, Any]:
    prompt = (
        f"SELECTED STACK:\n{state['selected_stack']}\n\n"
        f"SPECIFICATION:\n{state['engineering_spec']}\n\nROADMAP:\n{state['roadmap']}"
    )
    data = await _call(state, "file_manifest", FILE_MANIFEST_SYSTEM, prompt, reasoning=False, tokens=3500, json_output=True)
    assert isinstance(data, dict)
    manifest_files = [item for raw in data.get("files", []) or [] if (item := normalize_manifest_file(raw))]
    errors = validate_manifest(manifest_files, state["selected_stack"])
    manifest = {
        "files": manifest_files,
        "frontend_instruction": str(data.get("frontend_instruction", "")).strip(),
        "backend_instruction": str(data.get("backend_instruction", "")).strip(),
        "test_instruction": str(data.get("test_instruction", "")).strip(),
        "errors": errors,
        "status": "FAILED" if errors else "COMPLETED",
    }
    updates: dict[str, Any] = {"manifest": manifest, "control": _control(state, "file_manifest", manifest["status"], "; ".join(errors))}
    for key, instruction in (
        ("frontend_work", manifest["frontend_instruction"]),
        ("backend_work", manifest["backend_instruction"]),
        ("test_work", manifest["test_instruction"]),
    ):
        updates[key] = {**state[key], "instruction": instruction}
    return updates


async def _owner(state: EngineeringState, owner: Owner, repair: bool) -> OwnerState:
    key = "frontend_work" if owner == "FRONTEND" else "backend_work"
    current: OwnerState = dict(state[key])  # type: ignore[index]
    required = state["frontend_required"] if owner == "FRONTEND" else state["backend_required"]
    if not required:
        return {**current, "status": "SKIPPED", "error": ""}

    assigned = files_for_owner(state["manifest"]["files"], owner)
    if not assigned:
        return {**current, "status": "FAILED", "error": f"No files assigned to {owner}.", "attempt": current["attempt"] + 1}

    node = f"{owner.lower()}_{'repair' if repair else 'engineer'}"
    system = FRONTEND_ENGINEER_SYSTEM if owner == "FRONTEND" else BACKEND_ENGINEER_SYSTEM
    prompt = (
        f"SPECIFICATION:\n{state['engineering_spec']}\n\nROADMAP:\n{state['roadmap']}\n\n"
        f"ASSIGNED FILES:\n{json.dumps(assigned, ensure_ascii=False, indent=2)}\n\n"
        f"OWNER INSTRUCTION:\n{current['instruction']}"
    )
    if repair:
        prompt += (
            f"\n\nEXISTING FILES:\n{json.dumps(current['files'], ensure_ascii=False)}\n\n"
            f"REPAIR VERDICT:\n{json.dumps(state['review_result'], ensure_ascii=False)}\n\n"
            f"SANDBOX RESULT:\n{json.dumps(state['sandbox_result'], ensure_ascii=False)}"
        )

    data = await _call(state, node, system, prompt, reasoning=False, tokens=8000, json_output=True)
    assert isinstance(data, dict)
    generated, errors = validate_file_bundle(data.get("files", []) or [], assigned)
    return {
        **current,
        "files": generated,
        "attempt": current["attempt"] + 1,
        "status": "FAILED" if errors else "COMPLETED",
        "error": "; ".join(errors),
    }


@safe_node("frontend_engineer", "frontend_work")
async def frontend_engineer_node(state: EngineeringState) -> dict[str, Any]:
    return {"frontend_work": await _owner(state, "FRONTEND", False)}


@safe_node("backend_engineer", "backend_work")
async def backend_engineer_node(state: EngineeringState) -> dict[str, Any]:
    return {"backend_work": await _owner(state, "BACKEND", False)}


@safe_node("frontend_repair", "frontend_work")
async def frontend_repair_node(state: EngineeringState) -> dict[str, Any]:
    return {"frontend_work": await _owner(state, "FRONTEND", True)}


@safe_node("backend_repair", "backend_work")
async def backend_repair_node(state: EngineeringState) -> dict[str, Any]:
    return {"backend_work": await _owner(state, "BACKEND", True)}


async def _test(state: EngineeringState, repair: bool) -> OwnerState:
    current: OwnerState = dict(state["test_work"])
    assigned = files_for_owner(state["manifest"]["files"], "TEST")
    node = "test_repair" if repair else "test_engineer"
    prompt = (
        f"SPECIFICATION:\n{state['engineering_spec']}\n\nROADMAP:\n{state['roadmap']}\n\n"
        f"ASSIGNED TEST FILES:\n{json.dumps(assigned, ensure_ascii=False)}\n\n"
        f"FRONTEND FILES:\n{json.dumps(state['frontend_work']['files'], ensure_ascii=False)}\n\n"
        f"BACKEND FILES:\n{json.dumps(state['backend_work']['files'], ensure_ascii=False)}"
    )
    if repair:
        prompt += (
            f"\n\nEXISTING TESTS:\n{json.dumps(current['files'], ensure_ascii=False)}\n\n"
            f"REPAIR VERDICT:\n{json.dumps(state['review_result'], ensure_ascii=False)}\n\n"
            f"SANDBOX RESULT:\n{json.dumps(state['sandbox_result'], ensure_ascii=False)}"
        )

    data = await _call(state, node, TEST_ENGINEER_SYSTEM, prompt, reasoning=False, tokens=6000, json_output=True)
    assert isinstance(data, dict)
    generated, errors = validate_file_bundle(data.get("files", []) or [], assigned)
    commands = data.get("commands", {})
    commands = commands if isinstance(commands, dict) else {}
    return {
        **current,
        "files": generated,
        "commands": {name: [str(v) for v in commands.get(name, []) or []] for name in ("install", "build", "test", "runtime")},
        "attempt": current["attempt"] + 1,
        "status": "FAILED" if errors else "COMPLETED",
        "error": "; ".join(errors),
    }


@safe_node("test_engineer", "test_work")
async def test_engineer_node(state: EngineeringState) -> dict[str, Any]:
    owner_failed = (
        state["frontend_required"] and state["frontend_work"]["status"] != "COMPLETED"
    ) or (
        state["backend_required"] and state["backend_work"]["status"] != "COMPLETED"
    )
    if owner_failed:
        return {"test_work": _owner_failure(state, "test_work", "Required implementation owner failed.")}
    work = await _test(state, False)
    return {"test_work": work, "control": _control(state, "test_engineer", work["status"], work["error"])}


@safe_node("test_repair", "test_work")
async def test_repair_node(state: EngineeringState) -> dict[str, Any]:
    work = await _test(state, True)
    return {"test_work": work, "control": _control(state, "test_repair", work["status"], work["error"])}


def _merged(state: EngineeringState) -> list[dict[str, str]]:
    files: dict[str, dict[str, str]] = {}
    for key in ("frontend_work", "backend_work", "test_work"):
        for item in state[key]["files"]:  # type: ignore[index]
            files[item["path"]] = item
    return list(files.values())


@safe_node("sandbox_test")
async def sandbox_test_node(state: EngineeringState) -> dict[str, Any]:
    result = await run_project_in_sandbox(
        files=_merged(state),
        commands=state["test_work"]["commands"],
        allow_shell_commands=state["request"]["allow_shell_commands"],
        request_id=state["request"]["request_id"],
    )
    return {"sandbox_result": result, "control": _control(state, "sandbox_test", result["status"], result.get("stderr", ""))}


def _signature(state: EngineeringState) -> str:
    result = state["sandbox_result"]
    source = f"{result.get('failed_stage')}|{result.get('failed_command')}|{result.get('exit_code')}|{str(result.get('stderr', ''))[-2000:]}"
    if result["status"] == "PENDING":
        source += f"|{state['frontend_work']['error']}|{state['backend_work']['error']}|{state['test_work']['error']}"
    return hashlib.sha256(source.encode()).hexdigest()[:20]


@safe_node("review_and_repair")
async def review_and_repair_node(state: EngineeringState) -> dict[str, Any]:
    control = dict(state["control"])
    signature = _signature(state)
    repeated = any(record["error_signature"] == signature for record in state["repair_history"])
    if repeated or control["repair_attempts"] >= MAX_REPAIR_ATTEMPTS:
        return {"review_result": {**state["review_result"], "status": "FAILED", "target_owner": "STOP", "error_signature": signature}}

    prompt = (
        f"SPECIFICATION:\n{state['engineering_spec']}\n\nROADMAP:\n{state['roadmap']}\n\n"
        f"MANIFEST:\n{json.dumps(state['manifest'], ensure_ascii=False)}\n\n"
        f"OWNER ERRORS:\nfrontend={state['frontend_work']['error']}\nbackend={state['backend_work']['error']}\ntest={state['test_work']['error']}\n\n"
        f"SANDBOX:\n{json.dumps(state['sandbox_result'], ensure_ascii=False, indent=2)}\n\n"
        f"REPAIR HISTORY:\n{json.dumps(state['repair_history'], ensure_ascii=False)}"
    )
    data = await _call(state, "review_and_repair", REVIEW_REPAIR_SYSTEM, prompt, reasoning=True, tokens=2500, json_output=True)
    assert isinstance(data, dict)
    target = str(data.get("target_owner", "STOP")).upper()
    allowed = {"FRONTEND", "BACKEND", "TEST", "MANIFEST", "ROADMAP", "MULTIPLE", "STOP"}
    target = target if target in allowed else "STOP"
    if target == "ROADMAP" and control["roadmap_rebuilds"] >= MAX_ROADMAP_REBUILDS:
        target = "STOP"
    if target == "MANIFEST" and control["manifest_rebuilds"] >= MAX_MANIFEST_REBUILDS:
        target = "STOP"

    review = {
        "status": "COMPLETED" if target != "STOP" else "FAILED",
        "failure_summary": str(data.get("failure_summary", "")).strip(),
        "root_cause": str(data.get("root_cause", "")).strip(),
        "target_owner": target,
        "target_files": [str(v) for v in data.get("target_files", []) or []],
        "repair_instruction": str(data.get("repair_instruction", "")).strip(),
        "rerun_tests": bool(data.get("rerun_tests", False)),
        "confidence": max(0.0, min(float(data.get("confidence", 0.0)), 1.0)),
        "error_signature": signature,
    }
    control.update(current_node="review_and_repair", repair_attempts=control["repair_attempts"] + 1, status=review["status"])
    control["roadmap_rebuilds"] += int(target == "ROADMAP")
    control["manifest_rebuilds"] += int(target == "MANIFEST")
    history = [
        *state["repair_history"],
        {
            "attempt": control["repair_attempts"],
            "target_owner": target,
            "target_files": review["target_files"],
            "error_signature": signature,
            "repair_instruction": review["repair_instruction"],
        },
    ]
    await emit_trace({
        "type": "agent_trace",
        "event": "ROUTE_DECISION",
        "trace_id": state["request"]["request_id"],
        "node": "review_and_repair",
        "selected_route": target,
        "error_signature": signature,
    })
    return {"review_result": review, "repair_history": history, "control": control}


@safe_node("multi_repair")
async def multi_repair_node(state: EngineeringState) -> dict[str, Any]:
    frontend, backend = await asyncio.gather(
        _owner(state, "FRONTEND", True),
        _owner(state, "BACKEND", True),
    )
    return {"frontend_work": frontend, "backend_work": backend}


@safe_node("package_and_save")
async def package_and_save_node(state: EngineeringState) -> dict[str, Any]:
    if state["sandbox_result"]["status"] != "COMPLETED":
        raise ValueError("Sandbox did not pass.")
    goal = re.search(r"(?is)##\s*Product Goal\s*(.*?)(?=\n##\s|\Z)", state["pm_report"])
    name = goal.group(1).strip().splitlines()[0][:60] if goal else "generated-project"
    result = write_project(
        project_name=name,
        files=_merged(state),
        engineering_spec=state["engineering_spec"],
        roadmap=state["roadmap"],
        sandbox_summary=json.dumps(state["sandbox_result"], ensure_ascii=False, indent=2),
    )
    return {"package_result": result, "control": _control(state, "package_and_save", result["status"], result["error"])}