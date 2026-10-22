from __future__ import annotations

import time
from typing import Iterable

from e2b import AsyncSandbox

from .engineering_state import (
    GeneratedFile,
    SandboxCommandResult,
    SandboxResult,
    SandboxStage,
)


WORKSPACE = "/home/user/project"
OUTPUT_LIMIT = 8_000
COMMAND_TIMEOUT = 120
SANDBOX_TIMEOUT = 600


def _truncate(value: object) -> str:
    text = str(value or "")
    if len(text) <= OUTPUT_LIMIT:
        return text
    return text[:OUTPUT_LIMIT] + "\n...[truncated]"


async def _run_stage(
    sandbox: AsyncSandbox,
    stage: SandboxStage,
    commands: Iterable[str],
    results: list[SandboxCommandResult],
) -> SandboxCommandResult | None:
    for command in commands:
        started = time.perf_counter()
        result = await sandbox.commands.run(
            command,
            cwd=WORKSPACE,
            timeout=COMMAND_TIMEOUT,
        )
        command_result: SandboxCommandResult = {
            "stage": stage,
            "command": command,
            "exit_code": int(result.exit_code),
            "stdout": _truncate(result.stdout),
            "stderr": _truncate(result.stderr),
            "duration_seconds": round(
                time.perf_counter() - started,
                3,
            ),
        }
        results.append(command_result)

        if command_result["exit_code"] != 0:
            return command_result

    return None


async def run_project_in_sandbox(
    *,
    files: list[GeneratedFile],
    commands: dict[str, list[str]],
    allow_shell_commands: bool,
    request_id: str,
) -> SandboxResult:
    if not allow_shell_commands:
        return {
            "status": "FAILED",
            "failed_stage": "POLICY",
            "failed_command": "",
            "exit_code": 1,
            "stdout": "",
            "stderr": "Sandbox commands are disabled for this request.",
            "duration_seconds": 0.0,
            "command_results": [],
        }

    started = time.perf_counter()
    results: list[SandboxCommandResult] = []
    sandbox: AsyncSandbox | None = None

    try:
        sandbox = await AsyncSandbox.create(
            timeout=SANDBOX_TIMEOUT,
            metadata={"engineering_request_id": request_id},
        )

        for generated_file in files:
            await sandbox.files.write(
                f"{WORKSPACE}/{generated_file['path']}",
                generated_file["content"],
            )

        stage_commands: list[tuple[SandboxStage, list[str]]] = [
            ("INSTALL", commands.get("install", [])),
            ("BUILD", commands.get("build", [])),
            ("TEST", commands.get("test", [])),
            ("RUNTIME", commands.get("runtime", [])),
        ]

        for stage, values in stage_commands:
            failure = await _run_stage(
                sandbox,
                stage,
                values,
                results,
            )
            if failure is not None:
                return {
                    "status": "FAILED",
                    "failed_stage": stage,
                    "failed_command": failure["command"],
                    "exit_code": failure["exit_code"],
                    "stdout": failure["stdout"],
                    "stderr": failure["stderr"],
                    "duration_seconds": round(
                        time.perf_counter() - started,
                        3,
                    ),
                    "command_results": results,
                }

        return {
            "status": "COMPLETED",
            "failed_stage": "COMPLETE",
            "failed_command": "",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_seconds": round(
                time.perf_counter() - started,
                3,
            ),
            "command_results": results,
        }

    except Exception as exc:
        return {
            "status": "FAILED",
            "failed_stage": "MATERIALIZE",
            "failed_command": "",
            "exit_code": 1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "duration_seconds": round(
                time.perf_counter() - started,
                3,
            ),
            "command_results": results,
        }
    finally:
        if sandbox is not None:
            try:
                await sandbox.kill()
            except Exception:
                pass