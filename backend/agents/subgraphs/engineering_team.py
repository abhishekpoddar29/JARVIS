'''
from __future__ import annotations
import asyncio
import json
import re
from typing import TypedDict, Any, Literal
from langgraph.graph import END, START, StateGraph

from agents.gemini_client import generate_reply, GeminiClientError
from agents.graph_nodes import progress_emitter
from config import get_settings

settings = get_settings()

# MODEL ASSIGNMENT
REASONING_MODEL = "gemini-2.5-pro"
FAST_MODEL = "gemini-2.5-flash"

# Loop safety caps
# REMOVED: qa_engineer's role entirely, per the request -- QA was crashing runs
# (see the log: two failed QA attempts back-to-back, then the graph got stuck
# in Reviewer <-> QA diagnosis ping-pong before the run was manually killed).
# MAX_QA_ATTEMPTS and the reviewer<->QA combined ceiling are no longer needed;
# only the reviewer's own retry cap remains.
MAX_REVIEWER_ATTEMPTS = 4


class EngineeringState(TypedDict, total=False):
    requirements: str

    # Tech Lead output (folds in former market_researcher's value - general
    # Gradio/FastAPI patterns are now baked directly into this node's own
    # prompt instead of being sourced from a separate research step)
    tech_stack: str
    functional_requirements: str  # fixed reference spec - reviewer checks every cycle against this
    file_manifest: str            # always ["api.py", "app.py", "requirements.txt"]

    # Product Manager output
    backend_scope: str
    frontend_scope: str

    # Code
    backend_code: dict
    frontend_code: dict

    # Reviewer diagnosis (sole authority for build-quality failures now that
    # QA no longer exists -- reviewer's own precheck + LLM judgment is the
    # only gate before the graph ends)
    review_report: str
    review_passed: bool
    patch_target: str              # "backend" | "frontend" | "both" | ""
    backend_patch_instruction: str
    frontend_patch_instruction: str

    # REMOVED (qa_engineer role deleted entirely):
    #   test_results, last_qa_error, qa_attempts, cycle_attempts
    # These existed solely to carry QA's sandbox output back into the reviewer
    # and to cap the reviewer<->QA ping-pong loop. With QA gone, the reviewer
    # is the terminal gate -- there is no sandbox result to relay and no
    # second loop to cap.

    # Loop control
    reviewer_attempts: int


def strip_markdown(text: str) -> str:
    """
    Robustly strips Markdown code fences (e.g., ```python ... ```) from code strings
    using high-performance string manipulation instead of regex. This completely
    avoids Python's regex backtracking stack overflows on large files and handles 
    any custom language tag seamlessly.
    """
    text = text.strip()
    
    # Find the opening triple backticks
    start_idx = text.find("```")
    if start_idx != -1:
        # Find the end of the line containing the language specifier (e.g., ```python)
        newline_idx = text.find("\n", start_idx)
        if newline_idx != -1:
            content_start = newline_idx + 1
        else:
            content_start = start_idx + 3
            
        # Find the matching closing triple backticks from the end
        end_idx = text.rfind("```")
        if end_idx != -1 and end_idx > start_idx:
            return text[content_start:end_idx].strip()
        else:
            return text[content_start:].strip()
            
    return text


def try_parse_json_object(text: str):
    if not text:
        return None
    cleaned = strip_markdown(text)
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def _emit(message: str) -> None:
    """
    Sends a progress update to the same UI channel research_tool.py uses
    (via the shared progress_emitter ContextVar set up in main.py).
    """
    emit = progress_emitter.get()
    if emit:
        try:
            await emit(f"[Engineering Team] {message}")
        except Exception:
            pass


_GRADIO_PATTERNS = """
- Use gr.Blocks() as the top-level container; gr.Row()/gr.Column(scale=..., min_width=...) for responsive layout.
  Always set min_width (e.g. 280) on columns so layout doesn't collapse on narrow viewports.

- Import backend functions directly from api.py - NO HTTP requests between UI and backend.

- Use gr.themes.Base(primary_hue=..., neutral_hue=...) as the primary theme, plus a small inline CSS
  string via gr.Blocks(css=...) for minor touches only - a handful of purposeful rules, not a full
  design system. Cheap, high-value patterns worth using:

    - One `:root { --accent: ...; }` variable if 3+ elements share a color, so it's edited in one place.
    - `transition: all 0.2s ease;` + a `:hover` state on cards/buttons - responsive feel, no JS needed.
    - `border-radius` + subtle `border`/`box-shadow` on gr.Group/elem_classes containers, replacing
      Gradio's default flat panel look.
    - A conditional empty-state message when a list has zero items - never leave a panel blank.
    - **State-Driven Rendering**: Treat every UI frame as a pure function of state. The backend writes JSON state; the frontend reads it on a timer. This decouples business logic from UI layout, allowing agents to modify state without touching Gradio code. Use `gr.Timer(value=2)` (never below 1s) to poll and auto-refresh state. Use `elem_classes` on timer-updated components so CSS can target them precisely.
    - **Responsive Grid with `gr.Row` / `gr.Column`**: Use `scale` for relative width ratios (e.g., `scale=2` for main content vs `scale=1` for sidebars). Nest `Row` inside `Column` for complex dashboards. Always add `min_width` to prevent mobile collapse.
    - **Panel Pattern**: Every panel is a self-contained HTML block with a title bar, content area, and hover glow effects. Use CSS for subtle panel scanlines and hover transitions rather than heavy JS.
    - **CSS Variable System**: Define design tokens at `:root` for consistency (e.g., `--bg-deep`, `--bg-panel`, `--border`, `--accent`, `--text-hi`). Target Gradio wrapper classes (`.gradio-container`, `footer`, `.gr-button`) to remove default chrome with `!important` where necessary.
    - **Animation & Visual Effects**: Use pure CSS keyframes for GPU-accelerated animations. Apply `will-change: transform, opacity;` sparingly on animated elements (orb cores, voice bars) to prevent jank. Stagger list entries with `animation-delay` for organic feel.
    - **Conditional Empty States**: Never leave a panel blank. Always show a contextual empty state when lists have zero items.
    - **Progress Bars with Glow**: Use `transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1)` for smooth fills and subtle `box-shadow` glow animations on progress indicators.
    - **Native Component + Custom CSS**: Don't just use `gr.HTML`. Wrap native components (`gr.Plot`, `gr.Dataframe`, `gr.Chatbot`, `gr.File`, `gr.JSON`, `gr.Code`) in `gr.Row`/`gr.Column` with `elem_classes` for accessibility + functionality.
    - **Event Chain Pattern**: Chain events with `.submit()`, `.click()`, `.change()` handlers. Use `gr.Textbox` + `gr.HTML` for command-driven UIs where state updates trigger re-renders.
    - **Streaming / Real-time Updates**: Use Python generators with `yield` for step-by-step streaming feedback in `gr.Textbox` or `gr.Chatbot`.
    - **Performance**: Use `gr.Timer(value=5)` instead of `1` for heavy UIs to reduce CPU. Batch state writes and reads. Use SVG for icons and WebP for photos. Lazy-load panels only when `active`.
    - **Accessibility**: Ensure WCAG 2.1 AA contrast ratios (4.5:1 minimum). Add `:focus-visible` outlines for keyboard navigation. Respect `prefers-reduced-motion` by disabling animations for users who need it. Use semantic HTML (`<button>` not `<div onclick>`) and ARIA labels (`role="status"`, `aria-live="polite"`) for dynamic regions.

  Avoid unless explicitly requested: keyframe animations, background overlays, glow effects, or `js=`
  injection - these exceed "minor touches" and are out of scope by default.

- Use gr.Timer(value=2) to poll and auto-refresh state; don't go below 1s. Use elem_classes on
  timer-updated components so CSS can target them precisely.

- Mount Gradio onto FastAPI with gr.mount_gradio_app(app, demo, path="/").
"""


async def tech_lead(state: EngineeringState) -> dict:
    await _emit("Tech Lead is analyzing the request and defining scope...")
    print("\n[Engineering Team] Tech Lead analyzing request and defining scope...")
    requirements = state["requirements"]

    prompt = f"""You are a Principal Software Architect. A user has requested the following:

USER REQUEST:
{requirements}

First, think through what the user actually wants to build - the core purpose,
not just a literal restatement of their words.

Then define the build plan for a PURE PYTHON, TWO-FILE project:
- api.py: FastAPI backend. All business logic, state, endpoints.
- app.py: Gradio UI. Imports functions directly from api.py (no HTTP calls).
- requirements.txt: dependencies.

No other files will be generated. No subpackages, no __init__.py - api.py and
app.py sit in the same flat directory and import each other directly
(e.g. `from api import some_func`, never `from .api import some_func`).

GRADIO/FASTAPI PATTERNS TO FOLLOW:
{_GRADIO_PATTERNS}

Output ONLY raw JSON with these keys:
{{
  "tech_stack": "1-2 sentences naming the concrete libraries/approach.",
  "functional_requirements": "A clear, complete bullet-style list (as a single string) of every concrete behavior the finished app must have - this is the spec every later review cycle will check against, so be specific and complete."
}}
No markdown fences."""

    loop = asyncio.get_running_loop()
    tech_stack = "FastAPI backend + Gradio UI mounted via gr.mount_gradio_app."
    functional_requirements = requirements  # safe fallback: at minimum, check against raw ask

    try:
        reply = await loop.run_in_executor(None, lambda: generate_reply(
            api_key=settings.gemini_api_key, model=REASONING_MODEL, user_message=prompt,
            system_instruction="Principal Software Architect. Output ONLY raw JSON.",
            temperature=0.2, max_output_tokens=2000))

        data = try_parse_json_object(reply.text)
        if data:
            tech_stack = data.get("tech_stack", tech_stack)
            functional_requirements = data.get("functional_requirements", functional_requirements)
        else:
            print("[DEBUG-TechLead] Response was not valid JSON. Using defaults.")
    except Exception as e:
        print(f"[DEBUG-TechLead] LLM call failed: {e}. Using defaults.")

    print(f"[DEBUG-TechLead] Functional requirements defined ({len(functional_requirements)} chars).")

    manifest_text = json.dumps([
        {"path": "api.py", "desc": "Pure FastAPI. Exports `app`. Endpoints, models, business logic, state I/O."},
        {"path": "app.py", "desc": "Gradio UI (gr.Blocks). Imports from api.py. Mounts onto FastAPI."},
        {"path": "requirements.txt", "desc": "Python deps (fastapi, uvicorn, gradio, pydantic, python-multipart)"},
    ], indent=2)

    return {
        "tech_stack": tech_stack,
        "functional_requirements": functional_requirements,
        "file_manifest": manifest_text,
        "reviewer_attempts": 0,
        "patch_target": "",
        "backend_patch_instruction": "",
        "frontend_patch_instruction": "",
    }


# -- Node 2: Product Manager ---------------------------------------------------
async def product_manager(state: EngineeringState) -> dict:
    await _emit("Product Manager is splitting backend/frontend responsibilities...")
    print("\n[Engineering Team] Product Manager splitting work between backend and frontend...")
    requirements = state["requirements"]
    tech_stack = state.get("tech_stack", "")
    functional_requirements = state.get("functional_requirements", "")

    prompt = f"""You are a Technical Product Manager. Split this build into backend and frontend scopes.

USER REQUEST:
{requirements}

TECH STACK:
{tech_stack}

FUNCTIONAL REQUIREMENTS (the fixed spec both engineers must satisfy):
{functional_requirements}

Define:
1. backend_scope: exactly what api.py must implement - endpoints (path, method, purpose),
   data models, state handling. Be concrete enough that a developer needs no further
   clarification.
2. frontend_scope: exactly what app.py must implement - layout, components, what functions
   it imports from api.py and how it uses them, auto-refresh behavior. Do NOT invent
   specific function names here - the frontend must read the ACTUAL backend code once
   it exists, this is just the UX/behavior spec.

Output ONLY raw JSON:
{{"backend_scope": "...", "frontend_scope": "..."}}
Keep the total response under 3000 characters. No markdown fences."""

    loop = asyncio.get_running_loop()
    backend_scope = f"Implement the following as a FastAPI app: {functional_requirements}"
    frontend_scope = f"Implement a Gradio UI covering: {functional_requirements}"

    try:
        reply = await loop.run_in_executor(None, lambda: generate_reply(
            api_key=settings.gemini_api_key, model=REASONING_MODEL, user_message=prompt,
            system_instruction="Technical Product Manager. Output ONLY raw JSON.",
            temperature=0.2, max_output_tokens=3000))

        data = try_parse_json_object(reply.text)
        if data:
            backend_scope = data.get("backend_scope", backend_scope)
            frontend_scope = data.get("frontend_scope", frontend_scope)
        else:
            print("[DEBUG-PM] Response was not valid JSON. Using fallback scopes.")
    except Exception as e:
        print(f"[DEBUG-PM] LLM call failed: {e}. Using fallback scopes.")

    return {"backend_scope": backend_scope, "frontend_scope": frontend_scope}


# -- Node 3: Backend Engineer ---------------------------------------------------
async def backend_engineer(state: EngineeringState) -> dict:
    backend_scope = state.get("backend_scope", "")
    requirements = state.get("requirements", "")
    patch_instruction = state.get("backend_patch_instruction", "")
    existing_files = state.get("backend_code", {})
    existing_api_code = existing_files.get("api.py", "")

    loop = asyncio.get_running_loop()
    generated_files = dict(existing_files)

    if patch_instruction and existing_api_code:
        await _emit("Backend Engineer is patching api.py based on reviewer feedback...")
        print("\n[Engineering Team] Backend Engineer applying targeted patch to api.py...")
        prompt = f"""You are a Senior Python Developer patching an existing FastAPI file.

CURRENT api.py:
```python
{existing_api_code}
```

THE CODE REVIEWER FOUND THIS ISSUE AND PRESCRIBED THIS FIX:
{patch_instruction}

Apply ONLY this fix. Do not rewrite unrelated parts of the file. Keep everything
else exactly as it is unless the fix genuinely requires touching it.

CRITICAL - FLAT DIRECTORY, NO PACKAGE IMPORTS:
api.py and app.py live in the SAME flat folder, no __init__.py. Only plain
module-level imports are valid (e.g. `from api import X`), never relative
imports (`from .api import X`).

Output the COMPLETE corrected file. Output ONLY raw Python code."""
        system_instruction = "Senior Python Developer. Output ONLY the complete corrected raw code."
    else:
        await _emit("Backend Engineer is building api.py...")
        print("\n[Engineering Team] Backend Engineer building FastAPI backend (api.py)...")
        prompt = f"""Write 'api.py' - a complete FastAPI backend.

USER REQUEST:
{requirements}

BACKEND SCOPE:
{backend_scope}

RULES:
1. Export `app = FastAPI()`.
2. Define any Pydantic models directly in this file (no separate models.py).
3. Implement in-memory or JSON-file state handling directly in this file (no separate utils.py).
4. Implement ALL endpoints described in the backend scope. Use async def for I/O where relevant.
5. Add CORS middleware.
6. Add GET /health returning {{"status": "ok"}}.

CRITICAL - FLAT DIRECTORY, NO PACKAGE IMPORTS:
This project is a flat directory - api.py and app.py live in the SAME folder
with NO __init__.py and NO package structure. NEVER write a relative import
like `from . import x`.

Output ONLY raw Python code. No markdown fences."""
        system_instruction = "Senior Python Developer. Output ONLY raw code."

    max_retries = 2
    for attempt in range(max_retries):
        try:
            reply = await loop.run_in_executor(None, lambda: generate_reply(
                api_key=settings.gemini_api_key, model=REASONING_MODEL, user_message=prompt,
                system_instruction=system_instruction, temperature=0.2, max_output_tokens=8192))
            code = strip_markdown(reply.text)

            if len(code.strip()) < 50:
                print(f"[Engineering Team]   ! api.py too short. Retrying...")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue

            if re.search(r'^\s*from\s+\.\S*\s+import', code, re.MULTILINE):
                print(f"[Engineering Team]   ! api.py contains a relative import. Retrying...")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue

            generated_files["api.py"] = code
            print(f"[Engineering Team]   OK api.py ready ({len(code)} chars).")
            break
        except Exception as e:
            print(f"[Engineering Team]   ! api.py generation failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
                continue
            if "api.py" not in generated_files:
                generated_files["api.py"] = f"# Code generation failed after {max_retries} attempts: {e}"

    # requirements.txt - cheap, only (re)generate on first pass
    if "requirements.txt" not in generated_files:
        try:
            reply = await loop.run_in_executor(None, lambda: generate_reply(
                api_key=settings.gemini_api_key, model=FAST_MODEL,
                user_message="Write requirements.txt for a FastAPI + Gradio project. "
                              "Include: fastapi, uvicorn[standard], gradio, pydantic, python-multipart. "
                              "Output ONLY raw text.",
                system_instruction="DevOps-minded Python Developer. Output ONLY raw text.",
                temperature=0.1, max_output_tokens=300))
            generated_files["requirements.txt"] = strip_markdown(reply.text)
        except Exception as e:
            print(f"[Engineering Team]   ! requirements.txt failed: {e}")
            generated_files["requirements.txt"] = "fastapi\nuvicorn[standard]\ngradio\npydantic\npython-multipart\n"

    # Clear the patch instruction now that it's been applied
    return {"backend_code": generated_files, "backend_patch_instruction": ""}


# -- Node 4: Frontend Engineer ---------------------------------------------------
async def frontend_engineer(state: EngineeringState) -> dict:
    frontend_scope = state.get("frontend_scope", "")
    requirements = state.get("requirements", "")
    patch_instruction = state.get("frontend_patch_instruction", "")
    backend_code = state.get("backend_code", {}).get("api.py", "")
    existing_files = state.get("frontend_code", {})
    existing_app_code = existing_files.get("app.py", "")

    loop = asyncio.get_running_loop()
    generated_files = dict(existing_files)

    if patch_instruction and existing_app_code:
        await _emit("Frontend Engineer is patching app.py based on reviewer feedback...")
        print("\n[Engineering Team] Frontend Engineer applying targeted patch to app.py...")
        prompt = f"""You are an Elite Gradio Developer patching an existing UI file.

CURRENT app.py:
```python
{existing_app_code}
```

CURRENT api.py (for reference - your imports must match this):
```python
{backend_code[:4000]}
```

THE CODE REVIEWER FOUND THIS ISSUE AND PRESCRIBED THIS FIX:
{patch_instruction}

Apply ONLY this fix. Do not rewrite unrelated parts of the file.

CRITICAL - FLAT DIRECTORY, NO PACKAGE IMPORTS:
Only plain module-level imports are valid (e.g. `from api import X`), never
relative imports.

Output the COMPLETE corrected file. Output ONLY raw Python code."""
        system_instruction = "Elite Gradio Developer. Output ONLY the complete corrected raw code."
    else:
        await _emit("Frontend Engineer is building app.py...")
        print("\n[Engineering Team] Frontend Engineer building Gradio UI (app.py)...")
        prompt = f"""Write 'app.py' - a complete Gradio UI.

USER REQUEST:
{requirements}

FRONTEND SCOPE:
{frontend_scope}

ACTUAL BACKEND CODE (api.py) - import ONLY functions/objects that really exist here:
```python
{backend_code[:6000]}
```

MANDATORY RULES:
1. `from api import app` - import the FastAPI app object.
2. Import only functions that actually exist in the api.py shown above.
   NO requests.get()/requests.post() - direct imports only.
3. `with gr.Blocks(theme=gr.themes.Base(primary_hue="cyan", neutral_hue="slate")) as demo:`
   - optionally pass a small inline `css=` string for minor touches only.
4. Use `gr.Row()` / `gr.Column(scale=..., min_width=...)` for layout.
5. Use `gr.Timer(value=2)` to poll and refresh state where relevant.
6. Implement empty states for dynamic panels.
7. Use `gr.mount_gradio_app(app, demo, path="/")` at the end.

CRITICAL - FLAT DIRECTORY, NO PACKAGE IMPORTS:
Flat folder, no __init__.py. NEVER write a relative import like `from . import api`.

Output ONLY raw Python code. No markdown fences."""
        system_instruction = "Elite Gradio Developer. Output ONLY raw code."

    max_retries = 2
    for attempt in range(max_retries):
        try:
            reply = await loop.run_in_executor(None, lambda: generate_reply(
                api_key=settings.gemini_api_key, model=REASONING_MODEL, user_message=prompt,
                system_instruction=system_instruction, temperature=0.3, max_output_tokens=8192))
            code = strip_markdown(reply.text)

            has_relative_import = bool(re.search(r'^\s*from\s+\.\S*\s+import', code, re.MULTILINE))

            if len(code.strip()) > 100 and not has_relative_import:
                generated_files["app.py"] = code
                print(f"[Engineering Team]   OK app.py ready ({len(code)} chars).")
                break
            elif has_relative_import:
                print(f"[Engineering Team]   ! app.py contains a relative import. Retrying...")
        except Exception as e:
            print(f"[Engineering Team]   ! app.py generation failed: {e}")
        if attempt == max_retries - 1 and "app.py" not in generated_files:
            generated_files["app.py"] = f"# Code generation failed: relative import or generation error"
        else:
            await asyncio.sleep(1)

    return {"frontend_code": generated_files, "frontend_patch_instruction": ""}


# -- Node 5: Code Reviewer (LLM-based diagnosis + patch instruction authority) --
def _deterministic_precheck(api_code: str, app_code: str) -> list[str]:
    issues = []
    if not re.search(r'^\s*app\s*(:\s*FastAPI\s*)?=\s*FastAPI\s*\(', api_code, re.MULTILINE):
        issues.append("api.py missing: app = FastAPI() export")
    if '"/health"' not in api_code and "'/health'" not in api_code:
        issues.append("api.py missing: /health endpoint")
    if "from api import" not in app_code:
        issues.append("app.py missing: from api import ...")
    if "requests.get" in app_code or "requests.post" in app_code:
        issues.append("app.py using HTTP requests instead of direct imports (forbidden)")
    if "mount_gradio_app" not in app_code:
        issues.append("app.py missing: gr.mount_gradio_app()")
    for label, code in (("api.py", api_code), ("app.py", app_code)):
        if re.search(r'^\s*from\s+\.\S*\s+import', code, re.MULTILINE):
            issues.append(f"{label} contains a relative import (invalid in this flat directory)")
    return issues


async def code_reviewer(state: EngineeringState) -> dict:
    functional_requirements = state.get("functional_requirements", "")
    api_code = state.get("backend_code", {}).get("api.py", "")
    app_code = state.get("frontend_code", {}).get("app.py", "")
    reviewer_attempts = state.get("reviewer_attempts", 0) + 1

    await _emit(f"Code Reviewer is checking the build (attempt {reviewer_attempts})...")
    print("\n[Engineering Team] Code Reviewer diagnosing integration & functional fit...")

    base_result = {
        "reviewer_attempts": reviewer_attempts,
    }

    # REMOVED (qa_engineer role deleted entirely): this used to be a combined
    # reviewer<->QA cycle cap (MAX_CYCLE_ATTEMPTS). With QA gone, the reviewer
    # is the only gate and the only loop, so its own MAX_REVIEWER_ATTEMPTS cap
    # (checked in route_after_reviewer, same as before) is sufficient on its own.

    precheck_issues = _deterministic_precheck(api_code, app_code)

    loop = asyncio.get_running_loop()
    prompt = f"""You are a Lead Code Reviewer. Verify this project against its functional requirements.

FUNCTIONAL REQUIREMENTS (the spec both files must satisfy):
{functional_requirements}

api.py:
```python
{api_code[:6000]}
```

app.py:
```python
{app_code[:6000]}
```

DETERMINISTIC PRE-CHECK RESULTS (already found, treat as confirmed issues if any):
{json.dumps(precheck_issues) if precheck_issues else "None found."}

Decide:
1. Does the code, taken together, satisfy the functional requirements?
2. If not, which file(s) are at fault - backend (api.py), frontend (app.py), or both?
3. For each implicated file, write a SPECIFIC, actionable patch instruction - not a
   restatement of the problem, an actual instruction for what to change/add/fix.

Output ONLY raw JSON:
{{
  "passed": true or false,
  "summary": "1-2 sentence explanation of the verdict.",
  "patch_target": "backend" | "frontend" | "both" | "none",
  "backend_patch": "specific fix instruction, or empty string if backend not at fault",
  "frontend_patch": "specific fix instruction, or empty string if frontend not at fault"
}}
No markdown fences."""

    try:
        reply = await loop.run_in_executor(None, lambda: generate_reply(
            api_key=settings.gemini_api_key, model=REASONING_MODEL, user_message=prompt,
            system_instruction="Lead Code Reviewer. Output ONLY raw JSON.",
            temperature=0.1, max_output_tokens=1500))
        data = try_parse_json_object(reply.text)
    except Exception as e:
        print(f"[DEBUG-Reviewer] LLM call failed: {e}")
        data = None

    if data is None:
        if precheck_issues:
            print("[DEBUG-Reviewer] LLM diagnosis unavailable; routing on deterministic precheck only.")
            target = "backend" if any("api.py" in i for i in precheck_issues) else "frontend"
            return {
                **base_result,
                "review_passed": False,
                "review_report": "FAILED (precheck only, LLM diagnosis unavailable): " + "; ".join(precheck_issues),
                "patch_target": target,
                "backend_patch_instruction": "; ".join(i for i in precheck_issues if "api.py" in i),
                "frontend_patch_instruction": "; ".join(i for i in precheck_issues if "app.py" in i),
            }

        # REMOVED (qa_engineer role deleted entirely): this branch used to
        # handle the case where the LLM diagnosis call failed BUT a raw QA
        # sandbox error existed to route on instead. There is no longer a QA
        # error to fall back to, so with no precheck issues and no LLM
        # verdict available, the only sound default is to pass through --
        # same behavior as the branch below, now reached directly.
        print("[DEBUG-Reviewer] LLM diagnosis unavailable; no precheck issues found. Passing through.")
        return {
            **base_result, 
            "review_passed": True, 
            "review_report": "PASSED (LLM diagnosis unavailable, but deterministic precheck found no issues)", 
            "patch_target": ""
        }

    passed = bool(data.get("passed", False)) and not precheck_issues
    patch_target = data.get("patch_target", "none") if not passed else ""
    summary = data.get("summary", "")

    print(f"[DEBUG-Reviewer] passed={passed} target={patch_target!r} :: {summary}")

    if passed:
        return {
            **base_result,
            "review_passed": True,
            "review_report": f"PASSED: {summary}",
            "patch_target": "",
            "backend_patch_instruction": "",
            "frontend_patch_instruction": "",
        }

    backend_patch = data.get("backend_patch", "") or ("; ".join(i for i in precheck_issues if "api.py" in i))
    frontend_patch = data.get("frontend_patch", "") or ("; ".join(i for i in precheck_issues if "app.py" in i))

    if precheck_issues and patch_target == "none":
        patch_target = "both" if (backend_patch and frontend_patch) else ("backend" if backend_patch else "frontend")

    return {
        **base_result,
        "review_passed": False,
        "review_report": f"FAILED: {summary}",
        "patch_target": patch_target,
        "backend_patch_instruction": backend_patch if patch_target in ("backend", "both") else "",
        "frontend_patch_instruction": frontend_patch if patch_target in ("frontend", "both") else "",
    }



# REMOVED: qa_engineer node entirely. It ran the build in an E2B sandbox
# (pip install, import checks, structure check, health-endpoint smoke test)
# and fed failures back into code_reviewer for diagnosis. Per the request,
# this stage is gone -- code_reviewer's own deterministic precheck + LLM
# judgment is now the sole and final gate before the graph ends.

# -- Routing Functions ----------------------------------------------------------
def route_after_reviewer(state: EngineeringState) -> Literal["backend_engineer", "frontend_engineer", "__end__"]:
    # FIX (qa_engineer removed): the reviewer is now the terminal gate. A pass
    # used to hand off to "qa_engineer" for a sandbox run; there is no next
    # stage anymore, so a pass ends the graph directly.
    if state.get("review_passed", False):
        print("[DEBUG-Route] Reviewer passed. Ending graph.")
        return END

    # FIX (qa_engineer removed): cycle_attempts no longer exists (it tracked
    # the combined reviewer<->QA loop). Only the reviewer's own attempt cap
    # applies now.
    if state.get("reviewer_attempts", 0) > MAX_REVIEWER_ATTEMPTS:
        print("[DEBUG-Route] Reviewer attempt cap reached. Ending.")
        return END

    target = state.get("patch_target", "")
    print(f"[DEBUG-Route] Reviewer failed. patch_target={target!r}")

    if target in ("backend", "both"):
        return "backend_engineer"
    if target == "frontend":
        return "frontend_engineer"

    print("[DEBUG-Route] Unclear patch target. Defaulting to backend_engineer.")
    return "backend_engineer"


def route_after_backend_patch(state: EngineeringState) -> Literal["frontend_engineer", "code_reviewer"]:
    patch_target = state.get("patch_target", "")
    frontend_already_exists = bool(state.get("frontend_code", {}).get("app.py"))

    if not frontend_already_exists:
        print("[DEBUG-Route] First-time generation: backend done, routing to frontend.")
        return "frontend_engineer"

    if patch_target == "both" and state.get("frontend_patch_instruction"):
        print("[DEBUG-Route] Both files needed patching; backend done, routing to frontend.")
        return "frontend_engineer"

    print("[DEBUG-Route] Backend patch applied (frontend untouched). Routing to Code Reviewer.")
    return "code_reviewer"


# REMOVED (qa_engineer role deleted entirely): route_after_qa used to route a
# passing sandbox run to END and a failing one back to code_reviewer with the
# error attached. With qa_engineer gone, code_reviewer's own pass/fail routing
# above (route_after_reviewer) is the only routing decision left in the graph.


# -- Graph Builder ----------------------------------------------------------------
def build_engineering_graph():
    graph = StateGraph(EngineeringState)

    graph.add_node("tech_lead", tech_lead)
    graph.add_node("product_manager", product_manager)
    graph.add_node("backend_engineer", backend_engineer)
    graph.add_node("frontend_engineer", frontend_engineer)
    graph.add_node("code_reviewer", code_reviewer)
    # REMOVED: qa_engineer node (deleted entirely per request).

    graph.add_edge(START, "tech_lead")
    graph.add_edge("tech_lead", "product_manager")
    graph.add_edge("product_manager", "backend_engineer")

    graph.add_conditional_edges("backend_engineer", route_after_backend_patch)
    graph.add_edge("frontend_engineer", "code_reviewer")
    # FIX (qa_engineer removed): code_reviewer's conditional edge now routes
    # directly to END on pass (see route_after_reviewer) instead of to
    # qa_engineer. There is no longer a second conditional-edge node after it.
    graph.add_conditional_edges("code_reviewer", route_after_reviewer)

    return graph.compile()


engineering_graph = build_engineering_graph()

'''