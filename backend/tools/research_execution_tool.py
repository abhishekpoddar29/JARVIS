from __future__ import annotations

import ast
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tools.base import BaseTool, ToolResult


_ALLOWED_IMPORT_ROOTS = {
    "math", "statistics", "decimal", "fractions", "itertools", "functools",
    "collections", "sympy", "numpy", "pandas", "scipy",
}
_BLOCKED_CALLS = {"open", "eval", "exec", "compile", "input", "__import__"}


def _validate_code(code: str) -> str | None:
    if not code.strip():
        return "Execution code is empty."
    if len(code) > 8_000 or len(code.splitlines()) > 80:
        return "Execution code exceeds the 80-line or 8,000-character limit."
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Invalid Python syntax: {exc}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if name.split(".", 1)[0] not in _ALLOWED_IMPORT_ROOTS:
                    return f"Import '{name}' is not allowed in research verification."
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
            return f"Call '{node.func.id}' is not allowed in research verification."
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return "Dunder attribute access is not allowed."
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    for field in ("text", "value", "data"):
        candidate = getattr(value, field, None)
        if isinstance(candidate, str):
            return candidate
    return str(value)


class ResearchExecutionTool(BaseTool):
    name: str = "research_execution_tool"
    description: str = (
        "Run deterministic Python in an isolated E2B sandbox to verify mathematics, "
        "statistics, simulations, or data transformations already grounded in research sources."
    )

    async def run(self, task: str, code: str, claim_id: str = "") -> ToolResult:
        validation_error = _validate_code(code)
        if validation_error:
            return ToolResult(ok=False, content=validation_error, error=validation_error, data={})
        if not os.getenv("E2B_API_KEY"):
            message = "E2B_API_KEY is not configured; computational verification was skipped."
            return ToolResult(ok=False, content=message, error=message, data={})

        try:
            from e2b_code_interpreter import AsyncSandbox
        except ImportError:
            message = "Install `e2b-code-interpreter` to enable research execution."
            return ToolResult(ok=False, content=message, error=message, data={})

        sandbox = None
        execution_id = f"X{uuid4().hex[:10]}"
        try:
            sandbox = await AsyncSandbox.create(timeout=120)
            execution = await sandbox.run_code(code, timeout=45)
            logs = getattr(execution, "logs", None)
            stdout = "\n".join(_text(item) for item in getattr(logs, "stdout", [])).strip()
            stderr = "\n".join(_text(item) for item in getattr(logs, "stderr", [])).strip()
            results = [_text(item) for item in getattr(execution, "results", []) if _text(item)]
            errors = [_text(item) for item in getattr(execution, "error", [])] if isinstance(getattr(execution, "error", None), list) else []
            error = "\n".join(errors).strip() or _text(getattr(execution, "error", None)).strip()
            success = not error
            payload = {
                "id": execution_id,
                "claim_id": claim_id,
                "task": task.strip(),
                "code": code,
                "success": success,
                "stdout": stdout[-8_000:],
                "stderr": stderr[-4_000:],
                "results": results[:20],
                "error": error[-4_000:],
                "created_at": datetime.now(UTC).isoformat(),
            }
            summary = stdout or (results[0] if results else error or "Execution completed without textual output.")
            return ToolResult(
                ok=success,
                content=f"Research verification {'completed' if success else 'failed'}: {summary[:1000]}",
                error=error or None,
                data={"execution_result": payload},
            )
        except Exception as exc:
            message = f"E2B verification failed: {type(exc).__name__}: {exc}"
            return ToolResult(ok=False, content=message, error=message, data={})
        finally:
            if sandbox is not None:
                try:
                    await sandbox.kill()
                except Exception:
                    pass


research_execution_tool = ResearchExecutionTool()