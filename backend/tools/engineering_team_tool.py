"""
tools/engineering_team_tool.py

Start-only Jarvis wrapper for Engineering Team v2.

Engineering Team v2 does not pause for project clarification. The Product
Manager infers non-critical prototype details and records them as assumptions.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.subgraphs.Engineering_Team.budget import (
    budget_guard,
)
from agents.subgraphs.Engineering_Team.engineering_graph import (
    engineering_graph,
)
from agents.subgraphs.Engineering_Team.engineering_state import (
    build_initial_engineering_state,
)
from tools.base import BaseTool, ToolResult


logger = logging.getLogger(__name__)

ENGINEERING_RECURSION_LIMIT = 50
MAX_REQUIREMENTS_CHARACTERS = 30_000

SUPPORTED_STACK_HINTS = {
    "STATIC_HTML_CSS_JS",
    "REACT_VITE",
    "FASTAPI_STATIC",
    "FASTAPI_REACT",
}


def _normalize_stack_hint(value: Any) -> str:
    normalized = str(value or "").strip().upper()

    if (
        normalized
        and normalized not in SUPPORTED_STACK_HINTS
    ):
        supported = ", ".join(
            sorted(SUPPORTED_STACK_HINTS)
        )
        raise ValueError(
            "Unsupported Engineering Team stack hint. "
            f"Supported values: {supported}."
        )

    return normalized


def _failure_reason(
    result: dict[str, Any],
) -> str:
    package = result.get(
        "package_result",
        {},
    )
    control = result.get(
        "control",
        {},
    )
    review = result.get(
        "review_result",
        {},
    )
    sandbox = result.get(
        "sandbox_result",
        {},
    )
    manifest = result.get(
        "manifest",
        {},
    )

    candidates: list[Any] = [
        package.get("error"),
        control.get("error"),
        review.get("failure_summary"),
        review.get("root_cause"),
        sandbox.get("stderr"),
    ]

    manifest_errors = manifest.get(
        "errors",
        [],
    )
    if (
        isinstance(manifest_errors, list)
        and manifest_errors
    ):
        candidates.append(
            "Manifest errors: "
            + "; ".join(
                str(value)
                for value in manifest_errors
            )
        )

    for key, label in (
        ("frontend_work", "Frontend"),
        ("backend_work", "Backend"),
        ("test_work", "Test"),
    ):
        owner = result.get(key, {})
        error = str(
            owner.get("error", "")
        ).strip()
        if error:
            candidates.append(
                f"{label} error: {error}"
            )

    for candidate in candidates:
        text = str(
            candidate or ""
        ).strip()
        if text:
            return text

    return (
        "The Engineering Team workflow ended before "
        "a tested project package was saved."
    )


class EngineeringTeamTool(BaseTool):
    name: str = "engineering_team_tool"

    description: str = (
        "Build a new complete software project. "
        "Engineering Team v2 creates the product brief, "
        "freezes the engineering specification, plans and "
        "generates coordinated files, creates tests, runs "
        "the project in an isolated sandbox, performs "
        "bounded targeted repairs, and packages the tested "
        "project locally. Supported stacks: static HTML/CSS/"
        "JavaScript, React with Vite, FastAPI with a static "
        "frontend, and FastAPI with React."
    )

    async def run(
        self,
        requirements: str = "",
        target_stack_hint: str = "",
        allow_shell_commands: bool = True,
    ) -> ToolResult:
        prompt = str(
            requirements or ""
        ).strip()

        if not prompt:
            return ToolResult(
                ok=False,
                content=(
                    "Engineering project requirements "
                    "are required."
                ),
                data={"status": "FAILED"},
            )

        if (
            len(prompt)
            > MAX_REQUIREMENTS_CHARACTERS
        ):
            return ToolResult(
                ok=False,
                content=(
                    "Engineering project requirements "
                    "are too long."
                ),
                data={"status": "FAILED"},
            )

        try:
            stack_hint = _normalize_stack_hint(
                target_stack_hint
            )
        except ValueError as exc:
            return ToolResult(
                ok=False,
                content=str(exc),
                data={"status": "FAILED"},
            )

        initial_state = (
            build_initial_engineering_state(
                prompt,
                target_stack_hint=stack_hint,
                allow_shell_commands=bool(
                    allow_shell_commands
                ),
            )
        )
        thread_id = initial_state[
            "request"
        ]["request_id"]

        config = {
            "configurable": {
                "thread_id": thread_id,
            },
            "run_name": (
                "engineering_team.start"
            ),
            "tags": [
                "engineering-team-v2",
                "start",
            ],
            "metadata": {
                "tool": self.name,
                "engineering_thread_id": (
                    thread_id
                ),
            },
            "recursion_limit": (
                ENGINEERING_RECURSION_LIMIT
            ),
        }

        logger.info(
            "[EngineeringTeamTool:v2] "
            "start thread=%s",
            thread_id,
        )

        try:
            result = (
                await engineering_graph.ainvoke(
                    initial_state,
                    config=config,
                )
            )

            if not isinstance(result, dict):
                raise ValueError(
                    "Engineering graph returned "
                    "an invalid state."
                )

            usage = await budget_guard.snapshot(
                thread_id
            )

            package = result.get(
                "package_result",
                {},
            )
            sandbox = result.get(
                "sandbox_result",
                {},
            )
            control = result.get(
                "control",
                {},
            )
            repair_history = result.get(
                "repair_history",
                [],
            )

            repair_attempts = (
                len(repair_history)
                if isinstance(
                    repair_history,
                    list,
                )
                else int(
                    control.get(
                        "repair_attempts",
                        0,
                    )
                )
            )

            if (
                package.get("status")
                == "COMPLETED"
            ):
                project_directory = str(
                    package.get(
                        "project_directory",
                        "",
                    )
                )
                archive_path = str(
                    package.get(
                        "archive_path",
                        "",
                    )
                )

                tool_result = ToolResult(
                    ok=True,
                    content=(
                        "Engineering Team completed "
                        "and sandbox-tested the project."
                        "\n\n"
                        f"Project directory: "
                        f"{project_directory}\n"
                        f"Archive: {archive_path}\n"
                        f"Stack: "
                        f"{result.get('selected_stack', 'UNKNOWN')}\n"
                        f"Repair attempts: "
                        f"{repair_attempts}"
                    ),
                    data={
                        "engineering_thread_id": (
                            thread_id
                        ),
                        "trace_id": (
                            control.get(
                                "trace_id",
                                thread_id,
                            )
                        ),
                        "status": "COMPLETED",
                        "project_directory": (
                            project_directory
                        ),
                        "archive_path": (
                            archive_path
                        ),
                        "selected_stack": (
                            result.get(
                                "selected_stack",
                                "UNKNOWN",
                            )
                        ),
                        "sandbox_passed": (
                            sandbox.get("status")
                            == "COMPLETED"
                        ),
                        "repair_attempts": (
                            repair_attempts
                        ),
                        "written_files": (
                            package.get(
                                "written_files",
                                [],
                            )
                        ),
                        "usage": usage,
                    },
                )
            else:
                reason = _failure_reason(
                    result
                )
                status = str(
                    control.get(
                        "status",
                        "FAILED",
                    )
                ).upper()
                if status not in {
                    "FAILED",
                    "BUDGET_EXCEEDED",
                    "CANCELLED",
                }:
                    status = "FAILED"

                tool_result = ToolResult(
                    ok=False,
                    content=(
                        "Engineering Team could not "
                        "complete the project. "
                        f"{reason}"
                    ),
                    data={
                        "engineering_thread_id": (
                            thread_id
                        ),
                        "trace_id": (
                            control.get(
                                "trace_id",
                                thread_id,
                            )
                        ),
                        "status": status,
                        "reason": reason,
                        "current_node": (
                            control.get(
                                "current_node",
                                "",
                            )
                        ),
                        "selected_stack": (
                            result.get(
                                "selected_stack",
                                "UNKNOWN",
                            )
                        ),
                        "repair_attempts": (
                            repair_attempts
                        ),
                        "failed_stage": (
                            sandbox.get(
                                "failed_stage",
                                "NONE",
                            )
                        ),
                        "failed_command": (
                            sandbox.get(
                                "failed_command",
                                "",
                            )
                        ),
                        "usage": usage,
                    },
                )

            await budget_guard.release(
                thread_id
            )
            return tool_result

        except Exception as exc:
            logger.exception(
                "[EngineeringTeamTool:v2] "
                "Workflow failed"
            )

            usage = (
                await budget_guard.snapshot(
                    thread_id
                )
            )
            await budget_guard.release(
                thread_id
            )

            return ToolResult(
                ok=False,
                content=(
                    "Engineering Team failed with "
                    "an internal error."
                ),
                data={
                    "engineering_thread_id": (
                        thread_id
                    ),
                    "trace_id": thread_id,
                    "status": "FAILED",
                    "error": (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                    "usage": usage,
                },
            )


engineering_team_tool = EngineeringTeamTool()