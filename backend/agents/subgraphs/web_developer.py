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

# NEW: separate, tighter cap for the Tech Lead escalation path. Escalation means
# "the scope itself is contradictory," not "there's a code bug" -- it triggers a
# full scope repair (a bigger, costlier operation than an ordinary patch), so it
# gets its own small ceiling rather than sharing reviewer_attempts. Kept at 1:
# if a full scope rework still can't produce a passing build, further escalation
# is unlikely to help and the graph should stop and return its best effort.
MAX_ESCALATIONS = 1


# -- Shared helpers -----------------------------------------------------------
async def _emit(message: str) -> None:
    """Best-effort progress emission to the frontend orb/status bar. Never raises --
    progress reporting must never be able to break the graph itself."""
    try:
        await progress_emitter(message)
    except Exception:
        pass


def strip_markdown(text: str) -> str:
    pattern = r"```(?:python|json)?\s*\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return "\n\n".join(match.strip() for match in matches)
    return text.strip()


def try_parse_json_object(text: str) -> dict | None:
    """Parses the first {...} block found. Returns None (never raises) on any
    truncation or malformed-JSON issue, so callers can retry or fall back instead
    of silently propagating garbage -- this is the same discipline that fixed the
    PM contract truncation bug in engineering_team.py."""
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


class WebDeveloperState(TypedDict, total=False):
    requirements: str

    # Product Manager output -- plain-language intent capture only, zero
    # framework awareness (see web_developer.md section 2).
    build_summary: str

    # Tech Lead output -- the fixed anchor spec (functional_requirements) plus
    # the architecture/scope decisions. functional_requirements is written once
    # on the initial pass and never revised except during an escalation repair.
    tech_stack: str
    functional_requirements: str
    file_manifest: str
    backend_scope: str
    frontend_scope: str

    # Engineer outputs
    backend_code: dict
    frontend_code: dict

    # Code Reviewer output -- sole verification authority
    review_report: str
    review_passed: bool
    patch_target: str  # "backend" | "frontend" | "both" | "escalate" | "none"
    backend_patch_instruction: str
    frontend_patch_instruction: str
    escalation_reason: str  # populated only when patch_target == "escalate"

    # Append-only running history of what's been tried this run -- gives later
    # cycles (and a Tech Lead escalation repair) memory of prior rounds instead
    # of re-diagnosing from a cold start every time.
    decision_log: str

    # Loop control
    reviewer_attempts: int
    escalation_attempts: int


# -- NiceGUI pattern library ---------------------------------------------------
# FULL version: given to Tech Lead (to make architecture decisions) and Frontend
# Engineer (to write real code) directly and verbatim -- not paraphrased through
# an intermediate summary, same discipline already used for the actual backend
# code being handed to Frontend Engineer. Sourced from NiceGUI's own official
# LLM-facing reference (nicegui.io/llms.txt) plus the mounting/lifespan gotcha
# confirmed separately against a live upstream GitHub issue. Kept disciplined --
# a strict rulebook, not the full documentation site -- to avoid the same
# token-bloat mistake made once already with an earlier CSS pattern block.
_NICEGUI_PATTERNS = """
NICEGUI - STRICT MODERN USAGE RULES (mandatory, not optional):

ARCHITECTURE
- Define the UI inside `@ui.page('/')` (one page function per route). NEVER rely
  on script-mode top-level ui.* calls for a stateful app -- anything defined at
  module level outside a @ui.page function is SHARED ACROSS EVERY CONNECTED
  CLIENT, not per-user. All per-visitor state (selected filters, form values,
  active tab) must be created INSIDE the page function body, every time.
- Mount onto the existing FastAPI app with `ui.run_with(app, mount_path='/')`.
  Never call `ui.run()` directly -- that starts its own server; this project
  mounts onto the api.py app instead (the same role gr.mount_gradio_app used to
  play).
- CRITICAL: the FastAPI() instance in api.py must NOT use the `lifespan=` startup
  parameter -- it breaks NiceGUI's event handling once mounted via ui.run_with()
  (confirmed upstream bug). If startup logic is genuinely needed, use the
  deprecated-but-working `@app.on_event("startup")`, or nothing at all.
- Create any `ui.timer(interval, callback)` INSIDE the @ui.page function too, for
  the same per-client reason -- a module-level timer is shared and will not
  behave correctly across separate browser tabs.
- Use `@ui.refreshable` on any function that renders a dynamic section (a table,
  a card list, a stat panel) so a timer tick or a button click can call
  `my_section.refresh()` to re-render just that section -- never force a full
  page reload to reflect new data.

STYLING (strict order of preference -- do not skip ahead in this list)
1. Python API/kwargs first, e.g. `ui.button('Save', color='primary')`.
2. `.classes('...')` -- Tailwind utility classes -- the PRIMARY styling tool for
   spacing, layout, color, radius, shadow, hover/transition. Example:
   `.classes('rounded-xl shadow-lg p-4 bg-slate-800 hover:shadow-xl transition')`
3. `.props('...')` -- Quasar component props -- for component-specific behavior,
   e.g. `.props('rounded dense flat color=primary')`.
4. `.style('...')` -- raw CSS -- LAST RESORT ONLY, for the rare thing Tailwind
   and Quasar genuinely cannot express. Never write a large custom CSS block here.
- Set the theme ONCE, near the top of the page function:
  `ui.colors(primary='#...', secondary='#...', accent='#...')` and
  `ui.dark_mode().enable()` if a dark theme was requested. Do not hand-pick
  colors component by component after that.

COMPONENTS (use these -- do not hand-roll HTML for something NiceGUI ships)
- Layout: ui.row(), ui.column(), ui.grid(columns=...), ui.card(), ui.separator()
- Navigation: ui.tabs() + ui.tab_panels(), ui.left_drawer(), ui.header(), ui.footer()
- Data: ui.table() (sortable/paginated natively), ui.aggrid() for advanced grids
- Feedback: ui.notify(...) for toasts, ui.dialog() for modals, ui.spinner() /
  ui.linear_progress() for loading states, ui.badge() / ui.chip() for status tags
- Charts: ui.echart() or ui.plotly() -- never hand-roll SVG charts
- Forms: ui.input(), ui.select(), ui.number(), ui.switch(), ui.upload()

INTERACTION
- Prefer plain `on_click=` / `on_change=` handlers that call backend functions
  directly, then call `.refresh()` on the affected @ui.refreshable section. This
  is simpler and less error-prone than two-way `.bind_value()` binding for
  CRUD-style dashboards -- only reach for binding if the UI genuinely needs live
  two-way sync on a single value.
- Any fire-and-forget background work MUST use NiceGUI's `background_tasks.create()`,
  never a bare `asyncio.create_task()` -- a task from asyncio.create_task() has no
  owner holding a reference and can be silently garbage-collected mid-execution.

MINIMAL SHAPE TO FOLLOW (illustrative skeleton, not literal code to copy):
    from nicegui import ui, background_tasks
    from api import app, list_items, add_item

    @ui.page('/')
    def main_page():
        ui.dark_mode().enable()
        ui.colors(primary='#...')

        @ui.refreshable
        def item_list():
            for item in list_items():
                with ui.card().classes('rounded-xl shadow-lg p-4'):
                    ui.label(item['name'])

        item_list()
        ui.timer(2.0, item_list.refresh)

    ui.run_with(app, mount_path='/')

AVOID
- Raw `ui.html()`, injected `<script>` tags, or `.run_javascript()` unless the
  requirement explicitly demands something no native component covers.
- Module-level mutable state meant to represent one user's UI session.
- Reinventing a component NiceGUI already ships -- check the COMPONENTS list
  above before writing custom HTML/CSS for a table, tab, modal, or chart.
"""

# NARROW slice: given to Backend Engineer only. It never writes NiceGUI UI code,
# so it only needs to know the one thing that can break the mount from its side.
_NICEGUI_MOUNTING_NOTE = """
A NiceGUI frontend will be mounted onto this FastAPI app via
`ui.run_with(app, mount_path='/')` in app.py (not in this file). For that to work:
- Do NOT pass `lifespan=` to `FastAPI()` -- it breaks NiceGUI's event handling
  once mounted this way (confirmed upstream bug). Leave it out entirely, or use
  the deprecated `@app.on_event("startup")` if startup logic is truly needed.
- Keep `app = FastAPI()` a plain, simple instantiation so app.py can import and
  mount it without surprises.
"""


# -- Node 1: Product Manager ---------------------------------------------------
# Runs first, always, exactly once. Deliberately has NO framework knowledge --
# its only job is faithfully capturing intent before anyone starts solutioning
# (see web_developer.md section 2 for why this is the corrected role split).
async def product_manager(state: WebDeveloperState) -> dict:
    requirements = state["requirements"]
    await _emit("Product Manager is reviewing the request...")
    print("\n[Web Developer] Product Manager understanding user intent...")

    prompt = f"""You are a Product Manager. Read this software request and restate it as a
clear, plain-English build summary: what the user wants, who it's for, and what
"done" looks like from their perspective.

USER REQUEST:
{requirements}

Rules:
- Do NOT propose any technology, framework, endpoint, or UI component. That is
  not your job here -- someone else decides HOW to build it.
- Do NOT invent features the user didn't ask for or imply.
- Output ONLY the plain-English build summary. No JSON. No markdown fences.
No preamble like "Here is the summary" -- just the summary itself."""

    loop = asyncio.get_running_loop()
    try:
        reply = await loop.run_in_executor(None, lambda: generate_reply(
            api_key=settings.gemini_api_key, model=FAST_MODEL, user_message=prompt,
            system_instruction="Product Manager. Output plain English only, no formatting.",
            temperature=0.2, max_output_tokens=1000))
        build_summary = reply.text.strip()
        if len(build_summary) < 20:
            raise ValueError("build summary too short, likely a bad response")
    except Exception as e:
        print(f"[DEBUG-PM] Failed ({e}); falling back to raw requirements.")
        build_summary = requirements

    return {"build_summary": build_summary}


# -- Node 2: Tech Lead ----------------------------------------------------------
# Runs second on the initial pass. May run AGAIN if Code Reviewer escalates --
# in that case it performs a scope repair instead of an initial design (branch
# detected below via patch_target == "escalate").
#
# Split into TWO focused calls rather than one mega-prompt, specifically to
# avoid the PM-contract-truncation failure mode from engineering_team.py: asking
# for a tight spec and a full architecture/scope decision in the same JSON
# response is exactly the kind of task-mixing that produced malformed output
# before. Call 1 is plain text (no JSON risk at all). Call 2 is the only JSON
# call in this node, and it's scoped to architecture decisions only.
async def tech_lead(state: WebDeveloperState) -> dict:
    is_escalation = state.get("patch_target") == "escalate" and bool(state.get("backend_code"))
    build_summary = state.get("build_summary", "") or state.get("requirements", "")
    requirements = state.get("requirements", "")

    if is_escalation:
        await _emit("Tech Lead is repairing the build scope...")
        print("\n[Web Developer] Tech Lead performing scope repair after escalation...")
    else:
        await _emit("Tech Lead is designing the architecture...")
        print("\n[Web Developer] Tech Lead defining spec & architecture...")

    loop = asyncio.get_running_loop()

    # -- Call 1: confirm/tighten the functional spec (skipped on escalation --
    # the spec doesn't need re-deriving, only the scope/architecture around it) --
    if is_escalation:
        functional_requirements = state.get("functional_requirements", requirements)
    else:
        spec_prompt = f"""You are a Principal Tech Lead. Turn this build summary into a clear,
testable functional requirements list -- what the finished app must actually do.

BUILD SUMMARY:
{build_summary}

ORIGINAL USER REQUEST (for anything the summary may have missed):
{requirements}

Output ONLY a bullet list (one requirement per line, starting with "- "). No JSON,
no headers, no preamble."""
        try:
            reply = await loop.run_in_executor(None, lambda: generate_reply(
                api_key=settings.gemini_api_key, model=FAST_MODEL, user_message=spec_prompt,
                system_instruction="Principal Tech Lead. Output a plain bullet list only.",
                temperature=0.2, max_output_tokens=1200))
            functional_requirements = reply.text.strip()
            if len(functional_requirements) < 20:
                raise ValueError("spec too short")
        except Exception as e:
            print(f"[DEBUG-TechLead] Spec call failed ({e}); using build_summary as spec.")
            functional_requirements = build_summary

    # -- Call 2: architecture / scope decision (the only JSON call in this node) --
    escalation_context = ""
    if is_escalation:
        escalation_context = f"""
THIS IS A SCOPE REPAIR, NOT AN INITIAL DESIGN. Code Reviewer escalated because the
previous scope was contradictory or unbuildable as specified:
ESCALATION REASON: {state.get('escalation_reason', 'N/A')}
RUN HISTORY SO FAR: {state.get('decision_log', 'N/A')}
Revise backend_scope / frontend_scope to resolve this specific problem. Do not
repeat the same scope that already failed."""

    arch_prompt = f"""You are a Principal Tech Lead scoping a two-file NiceGUI + FastAPI project.

FUNCTIONAL REQUIREMENTS:
{functional_requirements}
{escalation_context}

NICEGUI PATTERN LIBRARY (you own this -- decide what the frontend will look like
and how the backend must be shaped to support it):
{_NICEGUI_PATTERNS}

Decide:
1. tech_stack: one sentence (always "FastAPI backend (api.py) + NiceGUI frontend
   (app.py), mounted together via ui.run_with()").
2. backend_scope: concrete data models, in-memory/JSON state shape, and every
   function api.py must expose for the frontend to import directly.
3. frontend_scope: concrete page layout, which NiceGUI components to use for
   each part of the UI, and which backend functions each part calls.
4. file_manifest: the file list (always exactly api.py, app.py, requirements.txt
   for this project -- no other files).

Output ONLY raw JSON with these exact keys:
{{"tech_stack": "...", "backend_scope": "...", "frontend_scope": "...", "file_manifest": "api.py, app.py, requirements.txt"}}
No markdown fences."""

    async def _attempt(prompt: str) -> dict | None:
        reply = await loop.run_in_executor(None, lambda: generate_reply(
            api_key=settings.gemini_api_key, model=REASONING_MODEL, user_message=prompt,
            system_instruction="Principal Tech Lead. Output ONLY raw JSON.",
            temperature=0.25, max_output_tokens=8192))
        return try_parse_json_object(reply.text)

    data = None
    try:
        data = await _attempt(arch_prompt)
        if data is None:
            print("[DEBUG-TechLead] Architecture JSON malformed; retrying more concisely...")
            data = await _attempt(arch_prompt + "\n\nBe concise. Your last response was not valid JSON.")
    except Exception as e:
        print(f"[DEBUG-TechLead] Architecture call failed: {e}")

    if data is None:
        print("[DEBUG-TechLead] Falling back to minimal safe scope.")
        data = {
            "tech_stack": "FastAPI backend (api.py) + NiceGUI frontend (app.py), mounted via ui.run_with()",
            "backend_scope": functional_requirements,
            "frontend_scope": "Single-page NiceGUI UI reflecting the functional requirements directly.",
            "file_manifest": "api.py, app.py, requirements.txt",
        }

    result = {
        "tech_stack": data.get("tech_stack", ""),
        "functional_requirements": functional_requirements,
        "backend_scope": data.get("backend_scope", ""),
        "frontend_scope": data.get("frontend_scope", ""),
        "file_manifest": data.get("file_manifest", "api.py, app.py, requirements.txt"),
    }

    if not is_escalation:
        # First pass only -- initialize every loop-control field exactly once.
        result.update({
            "reviewer_attempts": 0,
            "escalation_attempts": 0,
            "decision_log": "",
            "patch_target": "",
            "backend_patch_instruction": "",
            "frontend_patch_instruction": "",
            "escalation_reason": "",
        })
    else:
        # Escalation repair -- clear the escalate signal and bump its own cap,
        # but leave reviewer_attempts/decision_log alone (they track history).
        result["patch_target"] = ""
        result["escalation_attempts"] = state.get("escalation_attempts", 0) + 1

    return result


# -- Node 3: Backend Engineer ---------------------------------------------------
# Runs after Tech Lead (first pass or post-escalation); re-runs when Code
# Reviewer's patch_target is "backend" or "both". Never writes NiceGUI code, so
# it only gets the narrow mounting-note slice of the pattern library, not the
# full block.
async def backend_engineer(state: WebDeveloperState) -> dict:
    backend_scope = state.get("backend_scope", "")
    requirements = state.get("requirements", "")
    patch_instruction = state.get("backend_patch_instruction", "")
    existing_code = state.get("backend_code", {}).get("api.py", "")

    is_patch = bool(patch_instruction) and bool(existing_code)
    await _emit("Backend Engineer is writing api.py..." if not is_patch else "Backend Engineer is applying a fix to api.py...")
    print(f"\n[Web Developer] Backend Engineer {'patching' if is_patch else 'building'} api.py...")

    if is_patch:
        prompt = f"""You are a Senior Backend Engineer. Here is the CURRENT api.py:
```python
{existing_code}
```

Code Reviewer found this specific problem and instruction:
{patch_instruction}

Apply ONLY the fix described. Do not rewrite unrelated working code.

{_NICEGUI_MOUNTING_NOTE}

Output the FULL corrected api.py file. Output ONLY raw Python code, no markdown fences."""
    else:
        prompt = f"""You are a Senior Backend Engineer. Write api.py for this project.

SCOPE:
{backend_scope}

ORIGINAL REQUEST (for anything the scope may have missed):
{requirements}

{_NICEGUI_MOUNTING_NOTE}

RULES:
1. Export `app = FastAPI()` as a plain instantiation (see the mounting note above).
2. Implement every data model, state store, and function the scope describes,
   directly in this file (no separate models.py/utils.py -- flat, two-file project).
3. Add CORS middleware if the scope implies external API consumers; otherwise skip it.
4. Add a GET /health endpoint returning {{"status": "ok"}}.
5. All state (in-memory list/dict, or simple local JSON I/O) lives in this file.

Output ONLY raw Python code, no markdown fences."""

    loop = asyncio.get_running_loop()
    try:
        reply = await loop.run_in_executor(None, lambda: generate_reply(
            api_key=settings.gemini_api_key, model=REASONING_MODEL, user_message=prompt,
            system_instruction="Senior Backend Engineer. Output ONLY raw Python code.",
            temperature=0.2, max_output_tokens=8192))
        code = strip_markdown(reply.text)
        if len(code.strip()) < 50:
            raise ValueError("generated api.py suspiciously short")
    except Exception as e:
        print(f"[DEBUG-Backend] Failed: {e}")
        code = existing_code or f"# Backend generation failed: {e}"

    backend_files = dict(state.get("backend_code", {}))
    backend_files["api.py"] = code
    # requirements.txt is deterministic for this stack -- no need to spend an
    # LLM call generating the same handful of packages every single run.
    backend_files["requirements.txt"] = (
        "fastapi\nuvicorn[standard]\nnicegui\npydantic\npython-multipart\n"
    )
    return {"backend_code": backend_files}


# -- Node 4: Frontend Engineer ---------------------------------------------------
# Runs after Backend Engineer (first pass, or when both files need a patch);
# re-runs when patch_target is "frontend". Reads the ACTUAL generated api.py
# code -- never a description of it -- and gets the FULL NiceGUI pattern
# library directly, not a paraphrase relayed through Tech Lead.
async def frontend_engineer(state: WebDeveloperState) -> dict:
    frontend_scope = state.get("frontend_scope", "")
    requirements = state.get("requirements", "")
    backend_code = state.get("backend_code", {}).get("api.py", "")
    patch_instruction = state.get("frontend_patch_instruction", "")
    existing_code = state.get("frontend_code", {}).get("app.py", "")

    is_patch = bool(patch_instruction) and bool(existing_code)
    await _emit("Frontend Engineer is writing app.py..." if not is_patch else "Frontend Engineer is applying a fix to app.py...")
    print(f"\n[Web Developer] Frontend Engineer {'patching' if is_patch else 'building'} app.py...")

    if is_patch:
        prompt = f"""You are a Senior Frontend Engineer. Here is the CURRENT app.py:
```python
{existing_code}
```

The ACTUAL api.py it must import from (do not import anything not defined here):
```python
{backend_code}
```

Code Reviewer found this specific problem and instruction:
{patch_instruction}

Apply ONLY the fix described. Do not rewrite unrelated working code.

{_NICEGUI_PATTERNS}

Output the FULL corrected app.py file. Output ONLY raw Python code, no markdown fences."""
    else:
        prompt = f"""You are a Senior Frontend Engineer. Write app.py for this project using NiceGUI.

SCOPE:
{frontend_scope}

ORIGINAL REQUEST (for anything the scope may have missed):
{requirements}

THE ACTUAL api.py YOU MUST IMPORT FROM (import ONLY functions/objects that really
exist below -- do not invent or assume anything not defined here):
```python
{backend_code}
```

{_NICEGUI_PATTERNS}

RULES:
1. `from api import app, <only functions that exist above>` -- no HTTP requests
   between UI and backend, ever.
2. Mount with `ui.run_with(app, mount_path='/')` at module level, at the end of
   the file (this call itself is fine outside @ui.page -- it is the mount
   operation, not per-user UI state).
3. All page content and any timers/refreshables live INSIDE a `@ui.page('/')`
   function, per the pattern library above.

Output ONLY raw Python code, no markdown fences."""

    loop = asyncio.get_running_loop()
    try:
        reply = await loop.run_in_executor(None, lambda: generate_reply(
            api_key=settings.gemini_api_key, model=REASONING_MODEL, user_message=prompt,
            system_instruction="Senior Frontend Engineer, NiceGUI specialist. Output ONLY raw Python code.",
            temperature=0.25, max_output_tokens=8192))
        code = strip_markdown(reply.text)
        if len(code.strip()) < 50:
            raise ValueError("generated app.py suspiciously short")
    except Exception as e:
        print(f"[DEBUG-Frontend] Failed: {e}")
        code = existing_code or f"# Frontend generation failed: {e}"

    frontend_files = dict(state.get("frontend_code", {}))
    frontend_files["app.py"] = code
    return {"frontend_code": frontend_files}


# -- Node 5: Code Reviewer -------------------------------------------------------
# Sole verification authority -- no separate Tech Lead sanity check duplicates
# this job (see web_developer.md section 5). Deterministic precheck first
# (updated for NiceGUI: ui.run_with instead of mount_gradio_app, plus the
# lifespan= gotcha), then an LLM holistic check against functional_requirements.
def _deterministic_precheck(api_code: str, app_code: str) -> list[str]:
    issues: list[str] = []

    if "app = FastAPI()" not in api_code and "app=FastAPI()" not in api_code:
        issues.append("api.py missing: app = FastAPI()")

    if '"/health"' not in api_code and "'/health'" not in api_code:
        issues.append("api.py missing: /health endpoint")

    if "lifespan=" in api_code:
        issues.append(
            "api.py uses lifespan= on FastAPI() -- this breaks NiceGUI's event "
            "handling once mounted via ui.run_with(). Remove lifespan= entirely."
        )

    if "from api import" not in app_code:
        issues.append("app.py missing: from api import ...")

    if "requests.get" in app_code or "requests.post" in app_code:
        issues.append("app.py using HTTP requests instead of direct imports")

    if "ui.run_with" not in app_code:
        issues.append("app.py missing: ui.run_with(app, ...) -- NiceGUI is not mounted")

    if "@ui.page" not in app_code:
        issues.append(
            "app.py has no @ui.page(...) function -- UI content must live inside "
            "a page function, not at module level, or state will leak across users"
        )

    for path, code in (("api.py", api_code), ("app.py", app_code)):
        if re.search(r'^\s*from\s+\.\S*\s+import', code, re.MULTILINE):
            issues.append(f"{path} contains a relative import (invalid in this flat, non-package directory)")

    return issues


async def code_reviewer(state: WebDeveloperState) -> dict:
    functional_requirements = state.get("functional_requirements", "")
    api_code = state.get("backend_code", {}).get("api.py", "")
    app_code = state.get("frontend_code", {}).get("app.py", "")
    decision_log = state.get("decision_log", "")
    reviewer_attempts = state.get("reviewer_attempts", 0) + 1

    await _emit(f"Code Reviewer is checking the build (attempt {reviewer_attempts})...")
    print("\n[Web Developer] Code Reviewer verifying integration & functional fit...")

    base_result = {"reviewer_attempts": reviewer_attempts}

    precheck_issues = _deterministic_precheck(api_code, app_code)

    prompt = f"""You are a Lead Code Reviewer. Verify this project against its functional requirements.

FUNCTIONAL REQUIREMENTS (the fixed spec both files must satisfy):
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

RUN HISTORY SO FAR (what's already been tried this run -- do not re-propose a fix
already attempted and rejected):
{decision_log or "This is the first review cycle."}

Decide:
1. Does the code, taken together, satisfy the functional requirements?
2. If not, is this an ORDINARY CODE BUG (missing endpoint, wrong import, a
   component referencing something that doesn't exist, a NiceGUI anti-pattern
   like module-level per-user state) -- route to "backend", "frontend", or "both".
   OR is this a genuinely ARCHITECTURAL problem -- the scope itself asks for two
   things that contradict each other, or is unbuildable as specified, not fixable
   by patching a file -- route to "escalate". Use "escalate" RARELY; almost every
   real failure is an ordinary code bug and should NOT escalate.
3. For "backend"/"frontend"/"both", write a SPECIFIC, actionable patch instruction.
   For "escalate", write a clear escalation_reason explaining the scope conflict.

Output ONLY raw JSON:
{{
  "passed": true or false,
  "summary": "1-2 sentence explanation of the verdict.",
  "patch_target": "backend" | "frontend" | "both" | "escalate" | "none",
  "backend_patch": "specific fix instruction, or empty string",
  "frontend_patch": "specific fix instruction, or empty string",
  "escalation_reason": "specific scope-conflict explanation, or empty string"
}}
No markdown fences."""

    loop = asyncio.get_running_loop()
    data = None
    try:
        reply = await loop.run_in_executor(None, lambda: generate_reply(
            api_key=settings.gemini_api_key, model=REASONING_MODEL, user_message=prompt,
            system_instruction="Lead Code Reviewer. Output ONLY raw JSON.",
            temperature=0.1, max_output_tokens=1500))
        data = try_parse_json_object(reply.text)
    except Exception as e:
        print(f"[DEBUG-Reviewer] LLM call failed: {e}")

    if data is None:
        if precheck_issues:
            print("[DEBUG-Reviewer] LLM diagnosis unavailable; routing on deterministic precheck only.")
            target = "backend" if any("api.py" in i for i in precheck_issues) else "frontend"
            log_line = f"Round {reviewer_attempts}: precheck-only failure -- {'; '.join(precheck_issues)}"
            return {
                **base_result,
                "review_passed": False,
                "review_report": "FAILED (precheck only, LLM diagnosis unavailable): " + "; ".join(precheck_issues),
                "patch_target": target,
                "backend_patch_instruction": "; ".join(i for i in precheck_issues if "api.py" in i),
                "frontend_patch_instruction": "; ".join(i for i in precheck_issues if "app.py" in i),
                "escalation_reason": "",
                "decision_log": (decision_log + "\n" + log_line).strip(),
            }
        print("[DEBUG-Reviewer] LLM diagnosis unavailable; no precheck issues found. Passing through.")
        return {
            **base_result,
            "review_passed": True,
            "review_report": "PASSED (LLM diagnosis unavailable, but deterministic precheck found no issues)",
            "patch_target": "",
            "decision_log": (decision_log + f"\nRound {reviewer_attempts}: passed (precheck only).").strip(),
        }

    passed = bool(data.get("passed", False)) and not precheck_issues
    patch_target = data.get("patch_target", "none") if not passed else ""
    summary = data.get("summary", "")
    backend_patch = data.get("backend_patch", "")
    frontend_patch = data.get("frontend_patch", "")
    escalation_reason = data.get("escalation_reason", "") if patch_target == "escalate" else ""

    print(f"[DEBUG-Reviewer] passed={passed} target={patch_target!r} :: {summary}")

    log_line = f"Round {reviewer_attempts}: " + (
        "passed" if passed else f"target={patch_target} -- {summary}"
    )
    new_log = (decision_log + "\n" + log_line).strip()

    if passed:
        return {
            **base_result,
            "review_passed": True,
            "review_report": f"PASSED: {summary}",
            "patch_target": "",
            "decision_log": new_log,
        }

    return {
        **base_result,
        "review_passed": False,
        "review_report": f"FAILED: {summary}",
        "patch_target": patch_target,
        "backend_patch_instruction": backend_patch if patch_target in ("backend", "both") else "",
        "frontend_patch_instruction": frontend_patch if patch_target in ("frontend", "both") else "",
        "escalation_reason": escalation_reason,
        "decision_log": new_log,
    }


# -- Routing Functions ------------------------------------------------------------
def route_after_reviewer(state: WebDeveloperState) -> Literal["backend_engineer", "frontend_engineer", "tech_lead", "__end__"]:
    if state.get("review_passed", False):
        print("[DEBUG-Route] Reviewer passed. Ending graph.")
        return END

    if state.get("reviewer_attempts", 0) > MAX_REVIEWER_ATTEMPTS:
        print("[DEBUG-Route] Reviewer attempt cap reached. Ending with best effort.")
        return END

    target = state.get("patch_target", "")
    print(f"[DEBUG-Route] Reviewer failed. patch_target={target!r}")

    if target == "escalate":
        if state.get("escalation_attempts", 0) >= MAX_ESCALATIONS:
            print("[DEBUG-Route] Escalation cap reached. Ending with best effort -- cannot escalate further.")
            return END
        print("[DEBUG-Route] Architectural conflict. Escalating to Tech Lead.")
        return "tech_lead"

    if target in ("backend", "both"):
        return "backend_engineer"
    if target == "frontend":
        return "frontend_engineer"

    print("[DEBUG-Route] Unclear patch target. Defaulting to backend_engineer.")
    return "backend_engineer"


def route_after_backend_patch(state: WebDeveloperState) -> Literal["frontend_engineer", "code_reviewer"]:
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


# -- Graph Builder ------------------------------------------------------------------
def build_web_developer_graph():
    graph = StateGraph(WebDeveloperState)

    graph.add_node("product_manager", product_manager)
    graph.add_node("tech_lead", tech_lead)
    graph.add_node("backend_engineer", backend_engineer)
    graph.add_node("frontend_engineer", frontend_engineer)
    graph.add_node("code_reviewer", code_reviewer)

    graph.add_edge(START, "product_manager")
    graph.add_edge("product_manager", "tech_lead")
    # Unconditional, on purpose: tech_lead always routes to backend_engineer next,
    # whether this is the initial pass (from product_manager) or a scope-repair
    # pass (from code_reviewer's escalate route) -- the two entry paths differ,
    # the exit does not.
    graph.add_edge("tech_lead", "backend_engineer")

    graph.add_conditional_edges("backend_engineer", route_after_backend_patch)
    graph.add_edge("frontend_engineer", "code_reviewer")
    graph.add_conditional_edges("code_reviewer", route_after_reviewer)

    return graph.compile()


web_developer_graph = build_web_developer_graph()
'''