from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import uuid 
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from e2b import AsyncSandbox

from agents.gemini_client import GeminiClientError, generate_reply
from config import get_settings

settings = get_settings()


# ── Constants ──────────────────────────────────────────────────────────────────

SANDBOX_TIMEOUT = 30          # seconds per execution
MAX_RETRIES = 1               # retry once on error
MAX_OUTPUT_CHARS = 8000       # cap stdout/stderr to keep responses sane
EXPORTS_DIR = Path(__file__).resolve().parents[1] / "exports"

# Packages pre-installed in E2B sandbox (no install needed)
PREINSTALLED_PACKAGES = {
    "numpy", "pandas", "matplotlib", "requests", "json", "re",
    "math", "random", "datetime", "collections", "itertools",
    "statistics", "typing", "pathlib", "hashlib", "base64",
    "urllib", "string", "csv", "io", "sys", "os",
}


# ── Data models ──────────────────────────────────────────────────────────────────

@dataclass
class CodeArtifact:
    """A file generated inside the sandbox (image, CSV, JSON, etc.)."""
    name: str
    path: str           # sandbox path: /home/user/filename
    local_path: str     # backend path: backend/exports/filename
    content_type: str
    size_bytes: int


@dataclass
class CodeExecutionResult:
    """Structured result from a single sandbox execution."""
    success: bool
    code: str
    stdout: str
    stderr: str
    exit_code: int
    images: list[str] = field(default_factory=list)       # base64 PNG/JPG
    files: list[CodeArtifact] = field(default_factory=list)  # downloadable
    error: str | None = None
    execution_time_ms: int = 0
    retried: bool = False


# ── Prompts ──────────────────────────────────────────────────────────────────────

_CODE_GENERATION_PROMPT = """\
You are Jarvis's code execution assistant. Your job is to write clean, working Python 3
code that solves the user's task.

RULES — NO EXCEPTIONS:
1. Write COMPLETE, self-contained scripts. No placeholders. No "# TODO".
2. Save all output files (charts, data, images) to `/home/user/` with descriptive names.
   - Charts: `/home/user/output.png` or `/home/user/chart.png`
   - Data: `/home/user/data.csv` or `/home/user/results.json`
3. Print key results, summaries, and conclusions to stdout. The user sees stdout.
4. Use try/except for operations that might fail (network, file I/O).
5. Include comments for non-obvious logic. Keep code readable.
6. Do NOT use interactive features (input(), plt.show(), display()).
7. Do NOT write infinite loops or recursion without depth limits.
8. NEVER use blocking server commands (e.g., serve_forever() , app.run())
    if asked to write a web server , just define the app/routes and print a success message.
9. If the task needs packages beyond numpy/pandas/matplotlib/requests, list them.

RESPONSE FORMAT:
Return ONLY the Python code inside a single ```python ... ``` block.
No explanation outside the code block. No markdown before or after.
"""

_CODE_RETRY_PROMPT = """\
The previous code failed with this error:

```
{error}
```

Original task: {task}

Failed code:
```python
{failed_code}
```

Fix the code. Address the root cause of the error.
Return ONLY the corrected Python code inside ```python ... ```.
No explanation outside the code block.
"""

_PACKAGE_CHECK_PROMPT = """\
Given this Python code, list any pip packages that need to be installed
which are NOT in the standard library: numpy, pandas, matplotlib, requests.

Standard library modules that do NOT need installation:
json, re, math, random, datetime, collections, itertools, statistics, typing,
pathlib, hashlib, base64, urllib, string, csv, io, sys, os, time, inspect.

Respond ONLY with a JSON array of package names. Empty array if none needed.
No markdown fences. Example: ["yfinance", "beautifulsoup4"]

Code:
```python
{code}
```
"""
class CodeAgent:
    """
    Writes Python via Gemini, executes via E2B sandbox, captures all outputs,
    retries once on error. Returns structured result for frontend rendering.
    """


    def __init__(self) -> None:
        if settings.e2b_api_key:
            # E2B v1.0 SDK automatically reads the E2B_API_KEY environment variable.
            os.environ["E2B_API_KEY"] = settings.e2b_api_key

        self._ensure_exports_dir()

    # ── Public API ─────────────────────────────────────────────────────────────
    async def execute(
        self,
        task: str,
        retry_on_error: bool = True,
        file_ref: str = None,  # <-- ADDED: Accept the uploaded file reference
    ) -> CodeExecutionResult:
        """
        Full pipeline: generate code -> detect packages -> execute -> capture -> retry if needed.
        Wrapped in try-except to ensure graceful failure without crashing the graph.
        """
        start_time = datetime.now(UTC)
        code = ""

        try:
            if not settings.enable_code_execution:
                raise RuntimeError("Code execution is disabled by ENABLE_CODE_EXECUTION.")
            if not settings.e2b_api_key:
                raise RuntimeError("E2B_API_KEY not configured")

            # Step 1: Generate code
            code = await self._generate_code(task)

            # Step 2: Detect non-standard packages
            packages = self._detect_packages(code)

            # Step 3: Execute (pass file_ref)
            result = await self._run_in_sandbox(code, packages, file_ref)
            result.code = code

            # Step 4: Retry once if failed
            if not result.success and retry_on_error and result.stderr:
                print(f"[CodeAgent] Retrying after error: {result.stderr[:200]}")
                fixed_code = await self._generate_code(task, error_context=result.stderr, failed_code=code)
                # Pass file_ref on retry as well
                result = await self._run_in_sandbox(fixed_code, packages, file_ref)
                result.code = fixed_code
                result.retried = True

            result.execution_time_ms = int(
                (datetime.now(UTC) - start_time).total_seconds() * 1000
            )

            return result

        except Exception as exc:
            # Graceful Error Return: Catch Gemini failures or unexpected graph errors
            print(f"[CodeAgent] Pipeline execution failed: {exc}")
            return CodeExecutionResult(
                success=False,
                code=code,
                stdout="",
                stderr=str(exc),
                exit_code=-1,
                error=f"Agent pipeline error: {exc}",
                execution_time_ms=int(
                    (datetime.now(UTC) - start_time).total_seconds() * 1000
                )
            )

    # ── Step 1: Code generation ────────────────────────────────────────────────

    async def _generate_code(
        self,
        task: str,
        error_context: str | None = None,
        failed_code: str | None = None,
    ) -> str:
        """Ask Gemini to write or fix Python code."""

        if error_context and failed_code:
            system_prompt = _CODE_RETRY_PROMPT.format(
                error=error_context,
                task=task,
                failed_code=failed_code,
            )
            user_message = f"Fix this code. Task: {task}"
        else:
            system_prompt = _CODE_GENERATION_PROMPT
            user_message = f"Task: {task}\n\nWrite the complete Python code:"

        loop = asyncio.get_running_loop()
        try:
            reply = await loop.run_in_executor(
                None,
                lambda: generate_reply(
                    api_key=settings.gemini_api_key,
                    model=settings.gemini_model,
                    user_message=user_message,
                    system_instruction=system_prompt,
                    temperature=0.2,
                    max_output_tokens=8192,
                ),
            )
            print(f"[CODE GEN] Raw length: {len(reply.text)} chars")
            return self._extract_code(reply.text)

        except Exception as exc:
            print(f"[CODE GEN] Failed: {exc}")
            raise

    # ── Step 2: Package detection ────────────────────────────────────────────

    def _detect_packages(self, code: str) -> list[str]:
        """Detect third-party packages using Python's ast module."""
        import ast
        
        third_party_packages = {
            "numpy", "pandas", "matplotlib", "requests", "seaborn", 
            "scipy", "scikit-learn", "sklearn", "tensorflow", "torch",
            "beautifulsoup4", "bs4", "yfinance", "pillow", "PIL",
            "openpyxl", "xlsxwriter", "faker", "pyspark",
            "fpdf", "fpdf2", "reportlab",
            "plotly", "kaleido", "bokeh", "dash",
            "pptx"  # <-- ADDED THIS
        }
        
        mapping = {
            "cv2": "opencv-python",
            "sklearn": "scikit-learn",
            "PIL": "pillow",
            "bs4": "beautifulsoup4",
            "yaml": "pyyaml",
            "fpdf": "fpdf2",
            "pptx": "python-pptx"  # <-- ADDED THIS MAPPING
        }
        
        # ... rest of the method remains exactly the same ...
        
        found_packages = set()
        
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_level = alias.name.split('.')[0]
                        if top_level in third_party_packages or top_level in mapping:
                            found_packages.add(mapping.get(top_level, top_level))
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top_level = node.module.split('.')[0]
                        if top_level in third_party_packages or top_level in mapping:
                            found_packages.add(mapping.get(top_level, top_level))
                            
            print(f"[PACKAGES] AST Detected: {list(found_packages)}")
            return list(found_packages)
            
        except SyntaxError:
            print("[PACKAGES] AST parse failed (syntax error), falling back to regex")
            # Fallback regex if Gemini wrote slightly invalid Python
            matches = re.findall(r'^\s*(?:import|from)\s+([\w.]+)', code, re.MULTILINE)
            return [m.split('.')[0] for m in matches if m.split('.')[0] in third_party_packages]

    # ── Step 3: Sandbox execution ────────────────────────────────────────────

    async def _run_in_sandbox(
        self,
        code: str,
        packages: list[str],
        file_ref: str = None  # <-- FIX: Added file_ref parameter
    ) -> CodeExecutionResult:
        """Execute code in E2B async sandbox, capture stdout/stderr/files/images."""
        
        sandbox = None
        try:
            # E2B v1.0 uses AsyncSandbox.create() 
            sandbox = await AsyncSandbox.create()

            # F14: If a file was uploaded, send it to the sandbox
            if file_ref:
                local_file_path = Path("uploads") / file_ref
                if local_file_path.exists():
                    print(f"[SANDBOX] Uploading {file_ref} to sandbox...")
                    file_bytes = local_file_path.read_bytes()
                    # Write the file to the sandbox's /home/user/ directory
                    await sandbox.files.write(f"/home/user/{file_ref}", file_bytes)
                else:
                    return CodeExecutionResult(
                        success=False, code="", stdout="", stderr="",
                        exit_code=-1, error=f"Uploaded file not found: {file_ref}"
                    )

            # Install extra packages if needed
            if packages:
                install_cmd = f"pip install --quiet {' '.join(packages)}"
                print(f"[SANDBOX] Installing: {packages}")
                install_proc = await sandbox.commands.run(
                    install_cmd,
                    timeout=SANDBOX_TIMEOUT,
                )
                if install_proc.exit_code != 0:
                    print(f"[SANDBOX] Install stderr: {install_proc.stderr[:500]}")

            # Write code to file
            await sandbox.files.write("/home/user/script.py", code)
            print(f"[SANDBOX] Code written ({len(code)} chars)")

            # Execute
            process = await sandbox.commands.run(
                "python /home/user/script.py",
                timeout=SANDBOX_TIMEOUT,
            )

            stdout = (process.stdout or "")[:MAX_OUTPUT_CHARS]
            stderr = (process.stderr or "")[:MAX_OUTPUT_CHARS]
            exit_code = process.exit_code

            print(f"[SANDBOX] Exit code: {exit_code}")
            print(f"[SANDBOX] Stdout: {stdout[:200]}...")
            if stderr:
                print(f"[SANDBOX] Stderr: {stderr[:200]}...")

            # Collect artifacts
            import uuid
            images: list[str] = []
            files: list[CodeArtifact] = []

            run_id = datetime.now(UTC).strftime("%H%M%S") + "_" + uuid.uuid4().hex[:4]
            run_export_dir = EXPORTS_DIR / run_id
            run_export_dir.mkdir(parents=True, exist_ok=True)

            async def _scan_and_download(path: str):
                """Recursively scans the sandbox and downloads files, skipping hidden system files."""
                try:
                    entries = await sandbox.files.list(path)
                    for entry in entries:
                        # FIX 1: Skip hidden files and folders (.bashrc, .cache, etc.)
                        if entry.name.startswith('.'):
                            continue
                            
                        full_path = f"{path.rstrip('/')}/{entry.name}"
                        if entry.name == "script.py":
                            continue

                        try:
                            # Try to read as a file
                            content = await sandbox.files.read(full_path, format="bytes")
                            size = len(content)

                            if entry.name.endswith((".png", ".jpg", ".jpeg")):
                                b64 = base64.b64encode(content).decode("utf-8")
                                images.append(b64)
                                print(f"[ARTIFACT] Image: {entry.name} ({size} bytes)")
                            else:
                                local_path = run_export_dir / entry.name
                                local_path.write_bytes(content)
                                
                                relative_path = local_path.relative_to(EXPORTS_DIR).as_posix()
                                
                                files.append(CodeArtifact(
                                    name=entry.name,
                                    path=full_path,
                                    local_path=relative_path, 
                                    content_type=self._detect_content_type(entry.name),
                                    size_bytes=size,
                                ))
                                print(f"[ARTIFACT] File: {entry.name} ({size} bytes) -> {local_path}")

                        except Exception:
                            # FIX 2: If reading fails, it's a directory. Recurse into it.
                            await _scan_and_download(full_path)
                            
                except Exception as e:
                    print(f"[ARTIFACT] Scan error at {path}: {e}")

            # Start recursive scan
            await _scan_and_download("/home/user")

            return CodeExecutionResult(
                success=exit_code == 0,
                code="",  # Filled by execute()
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                images=images,
                files=files,
            )

        except Exception as exc:
            print(f"[SANDBOX] Fatal error: {exc}")
            return CodeExecutionResult(
                success=False,
                code="",
                stdout="",
                stderr="",
                exit_code=-1,
                error=str(exc),
            )
            
        finally:
            # ALWAYS kill the sandbox to prevent zombie VMs
            if sandbox:
                try:
                    await sandbox.kill()
                except Exception as close_exc:
                    print(f"[SANDBOX] Failed to close cleanly: {close_exc}")


    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_code(text: str) -> str:
        """Extract Python code from ```python ... ``` blocks."""
        text = text.strip()

        # Try fenced code block
        match = re.search(r"```python\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Try generic code block
        match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Fallback: return whole text if it looks like code
        if "def " in text or "import " in text or "print(" in text:
            return text

        raise ValueError("No valid Python code block found in response")

    @staticmethod
    def _detect_content_type(filename: str) -> str:
        """Map file extension to MIME type."""
        ext = filename.split(".")[-1].lower()
        types = {
            "csv": "text/csv",
            "json": "application/json",
            "pdf": "application/pdf",
            "txt": "text/plain",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "html": "text/html",
            "md": "text/markdown",
        }
        return types.get(ext, "application/octet-stream")

    def _ensure_exports_dir(self) -> None:
        """Create exports/ directory if missing."""
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def _save_to_exports(self, filename: str, content: bytes) -> Path:
        """Save artifact to backend/exports/ for FastAPI file serving."""
        timestamp = datetime.now(UTC).strftime("%H%M%S")
        safe_name = f"{timestamp}_{filename}"
        path = EXPORTS_DIR / safe_name
        path.write_bytes(content)
        return path


# ── Singleton instance ───────────────────────────────────────────────────────────

code_agent = CodeAgent()
