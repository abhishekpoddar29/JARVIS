from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, TypedDict
from uuid import uuid4


Status = Literal[
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "SKIPPED",
    "INTERRUPTED",
    "CANCELLED",
    "BUDGET_EXCEEDED",
]
Stack = Literal[
    "STATIC_HTML_CSS_JS",
    "REACT_VITE",
    "FASTAPI_STATIC",
    "FASTAPI_REACT",
    "UNKNOWN",
]
Owner = Literal["FRONTEND", "BACKEND", "TEST"]
RepairTarget = Literal[
    "FRONTEND",
    "BACKEND",
    "TEST",
    "MANIFEST",
    "ROADMAP",
    "MULTIPLE",
    "STOP",
    "NONE",
]
SandboxStage = Literal[
    "NONE",
    "POLICY",
    "MATERIALIZE",
    "INSTALL",
    "BUILD",
    "TEST",
    "RUNTIME",
    "COMPLETE",
]
MAX_REPAIR_ATTEMPTS = 2
MAX_ROADMAP_REBUILDS = 1
MAX_MANIFEST_REBUILDS = 1


class RequestState(TypedDict):
    request_id: str
    raw_prompt: str
    target_stack_hint: str
    allow_shell_commands: bool
    created_at: str


class GeneratedFile(TypedDict):
    path: str
    content: str


class ManifestFile(TypedDict):
    path: str
    owner: Owner
    purpose: str
    depends_on: list[str]
    required: bool


class ManifestState(TypedDict):
    files: list[ManifestFile]
    frontend_instruction: str
    backend_instruction: str
    test_instruction: str
    errors: list[str]
    status: Status


class OwnerState(TypedDict):
    status: Status
    instruction: str
    files: list[GeneratedFile]
    commands: dict[str, list[str]]
    attempt: int
    error: str


class SandboxCommandResult(TypedDict):
    stage: SandboxStage
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class SandboxResult(TypedDict):
    status: Status
    failed_stage: SandboxStage
    failed_command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    command_results: list[SandboxCommandResult]


class ReviewResult(TypedDict):
    status: Status
    failure_summary: str
    root_cause: str
    target_owner: RepairTarget
    target_files: list[str]
    repair_instruction: str
    rerun_tests: bool
    confidence: float
    error_signature: str


class RepairRecord(TypedDict):
    attempt: int
    target_owner: RepairTarget
    target_files: list[str]
    error_signature: str
    repair_instruction: str


class ControlState(TypedDict):
    current_node: str
    status: Status
    repair_attempts: int
    roadmap_rebuilds: int
    manifest_rebuilds: int
    trace_id: str
    error: str


class PackageResult(TypedDict):
    status: Status
    project_directory: str
    archive_path: str
    written_files: list[str]
    error: str


class EngineeringState(TypedDict):
    request: RequestState
    pm_report: str
    engineering_spec: str
    roadmap: str
    manifest: ManifestState
    selected_stack: Stack
    frontend_required: bool
    backend_required: bool
    frontend_work: OwnerState
    backend_work: OwnerState
    test_work: OwnerState
    sandbox_result: SandboxResult
    review_result: ReviewResult
    repair_history: list[RepairRecord]
    control: ControlState
    package_result: PackageResult


def _owner() -> OwnerState:
    return {
        "status": "PENDING",
        "instruction": "",
        "files": [],
        "commands": {
            "install": [],
            "build": [],
            "test": [],
            "runtime": [],
        },
        "attempt": 0,
        "error": "",
    }


def build_initial_engineering_state(
    raw_prompt: str,
    *,
    target_stack_hint: str = "",
    allow_shell_commands: bool = True,
) -> EngineeringState:
    request_id = f"engineering-{uuid4().hex}"

    return {
        "request": {
            "request_id": request_id,
            "raw_prompt": str(raw_prompt or "").strip(),
            "target_stack_hint": str(target_stack_hint or "").strip(),
            "allow_shell_commands": bool(allow_shell_commands),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "pm_report": "",
        "engineering_spec": "",
        "roadmap": "",
        "manifest": {
            "files": [],
            "frontend_instruction": "",
            "backend_instruction": "",
            "test_instruction": "",
            "errors": [],
            "status": "PENDING",
        },
        "selected_stack": "UNKNOWN",
        "frontend_required": False,
        "backend_required": False,
        "frontend_work": _owner(),
        "backend_work": _owner(),
        "test_work": _owner(),
        "sandbox_result": {
            "status": "PENDING",
            "failed_stage": "NONE",
            "failed_command": "",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_seconds": 0.0,
            "command_results": [],
        },
        "review_result": {
            "status": "PENDING",
            "failure_summary": "",
            "root_cause": "",
            "target_owner": "NONE",
            "target_files": [],
            "repair_instruction": "",
            "rerun_tests": False,
            "confidence": 0.0,
            "error_signature": "",
        },
        "repair_history": [],
        "control": {
            "current_node": "",
            "status": "PENDING",
            "repair_attempts": 0,
            "roadmap_rebuilds": 0,
            "manifest_rebuilds": 0,
            "trace_id": request_id,
            "error": "",
        },
        "package_result": {
            "status": "PENDING",
            "project_directory": "",
            "archive_path": "",
            "written_files": [],
            "error": "",
        },
    }