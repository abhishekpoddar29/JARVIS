from __future__ import annotations

from pathlib import Path

from agents.code_agent import code_agent
from tools.base import BaseTool, ToolResult


class CodeExecutionTool(BaseTool):
    name: str = "code_execution_tool"
    description: str = (
        "Writes, executes, and tests Python code in a secure E2B cloud sandbox. "
        "Use when the user asks to write code, run scripts, plot charts (matplotlib), "
        "analyze data (pandas/numpy), generate files (CSV/JSON), or perform complex "
        "calculations. Returns stdout, stderr, generated images, and downloadable files."
    )

    async def run(self, task: str) -> ToolResult:  # type: ignore[override]
        try:
            # Execute the code agent pipeline
            result = await code_agent.execute(task=task)

            # Serialize file artifacts for frontend JSON transport
            # The frontend will prepend the API base URL to these download paths
            files_data = [
                {
                    "name": f.name,
                    "download_path": f"/api/files/{Path(f.local_path).name}",
                    "content_type": f.content_type,
                    "size_bytes": f.size_bytes,
                }
                for f in result.files
            ]

            # Construct the structured payload for the frontend's CodeBlock component
            tool_data = {
                "code_result": {
                    "code": result.code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "success": result.success,
                    "images": result.images,  # list of base64 strings
                    "files": files_data,
                    "error": result.error,
                    "execution_time_ms": result.execution_time_ms,
                    "retried": result.retried,
                }
            }

            if result.success:
                # Summarize the output for the main agent's conversational response
                summary_parts = ["Code executed successfully."]
                if result.stdout.strip():
                    summary_parts.append(f"Stdout:\n{result.stdout.strip()}")
                if files_data:
                    summary_parts.append(f"Generated files: {', '.join(f['name'] for f in files_data)}")
                if result.images:
                    summary_parts.append(f"Generated {len(result.images)} image(s) inline.")
                
                summary = "\n".join(summary_parts)

                return ToolResult(
                    ok=True,
                    content=summary,
                    data=tool_data,
                )
            else:
                error_msg = result.error or result.stderr or "Unknown execution error"
                return ToolResult(
                    ok=False,
                    content=f"Code execution failed. Error: {error_msg}",
                    data=tool_data,
                )

        except Exception as exc:
            # Catch unexpected tool-level errors (e.g. missing API keys at runtime)
            return ToolResult(
                ok=False,
                content=f"Code execution tool crashed: {exc}",
                data={},
            )