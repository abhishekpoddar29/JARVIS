'''
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, UTC
from pathlib import Path
from tools.base import BaseTool, ToolResult
from agents.subgraphs.web_developer import web_developer_graph, MAX_REVIEWER_ATTEMPTS, MAX_ESCALATIONS


logger = logging.getLogger(__name__)
EXPORTS_DIR = Path(__file__).resolve().parents[1] / "web_developer_work"

class WebDeveloperTool(BaseTool):
    name: str = "web_developer_tool"
    description: str = (
        "Use this tool to build a complete, production-ready web app prototype (NiceGUI UI + FastAPI "
        "Backend, pure Python, two files). Pass the exact requirements. The team captures intent, designs "
        "the architecture, implements, and reviews the project end-to-end -- patching only the specific "
        "file(s) responsible when something fails, and escalating to a scope redesign only if the problem "
        "is architectural rather than a routine bug."
    )

    async def run(self, requirements: str, **kwargs) -> ToolResult:
        logger.info(f"[WebDeveloperTool] Starting multi-agent workflow for: {requirements[:50]}")

        try:
            result = await web_developer_graph.ainvoke({
                "requirements": requirements
            })

            run_id = datetime.now(UTC).strftime("%H%M%S") + "_" + uuid.uuid4().hex[:4]
            run_export_dir = EXPORTS_DIR / run_id
            run_export_dir.mkdir(parents=True, exist_ok=True)

            def _write_files(files_dict: dict):
                if not files_dict: return
                for path, code in files_dict.items():
                    filepath = run_export_dir / path
                    filepath.parent.mkdir(parents=True, exist_ok=True)
                    filepath.write_text(str(code), encoding="utf-8")

            # Write all generated code files (backend_code holds api.py +
            # requirements.txt; frontend_code holds app.py -- same flat,
            # three-file manifest as before, just NiceGUI instead of Gradio)
            _write_files(result.get("backend_code", {}))
            _write_files(result.get("frontend_code", {}))

            # Create .gitignore
            (run_export_dir / ".gitignore").write_text("__pycache__/\n*.pyc\n.env\n", encoding="utf-8")

            # Save architectural artifacts and reports. Numbering follows the
            # actual pipeline order: Product Manager now runs BEFORE Tech Lead
            # (see web_developer.md for why this role order was corrected), so
            # 00_BUILD_SUMMARY.md (new -- didn't exist in engineering_team_tool)
            # comes first, followed by Tech Lead's outputs, then the review and
            # the new decision log.
            (run_export_dir / "00_BUILD_SUMMARY.md").write_text(str(result.get("build_summary", "N/A")), encoding="utf-8")
            (run_export_dir / "01_TECH_STACK.md").write_text(str(result.get("tech_stack", "N/A")), encoding="utf-8")
            (run_export_dir / "02_FUNCTIONAL_REQUIREMENTS.md").write_text(str(result.get("functional_requirements", "N/A")), encoding="utf-8")
            # NOTE: file_manifest is a plain descriptive string in this graph
            # (e.g. "api.py, app.py, requirements.txt"), not a JSON array like
            # the old tool assumed -- saved as .txt to match what it actually is.
            (run_export_dir / "03_FILE_MANIFEST.txt").write_text(str(result.get("file_manifest", "api.py, app.py, requirements.txt")), encoding="utf-8")
            (run_export_dir / "04_BACKEND_SCOPE.md").write_text(str(result.get("backend_scope", "N/A")), encoding="utf-8")
            (run_export_dir / "05_FRONTEND_SCOPE.md").write_text(str(result.get("frontend_scope", "N/A")), encoding="utf-8")
            (run_export_dir / "06_REVIEW_REPORT.md").write_text(str(result.get("review_report", "N/A")), encoding="utf-8")
            # NEW: the append-only round-by-round history is genuinely useful
            # to persist -- it's the clearest record of what was tried and why,
            # especially on a run that needed multiple patch cycles or escalated.
            (run_export_dir / "07_DECISION_LOG.md").write_text(str(result.get("decision_log", "N/A")), encoding="utf-8")

            absolute_path = run_export_dir.resolve()
            logger.info(f"[WebDeveloperTool] Project successfully saved to: {absolute_path}")

            tech_stack_res = str(result.get('tech_stack', 'N/A'))
            functional_res = str(result.get('functional_requirements', 'N/A'))
            review_res = str(result.get('review_report', 'N/A'))
            reviewer_attempts = result.get('reviewer_attempts', 0)
            escalation_attempts = result.get('escalation_attempts', 0)
            review_passed = result.get('review_passed', False)

            # Be explicit about WHY a run stopped without passing -- ordinary
            # reviewer-cap exhaustion vs. escalation-cap exhaustion are different
            # situations (a routine bug that couldn't be patched in time, vs. a
            # scope conflict that couldn't be fully resolved), and the person
            # reading this report should know which one happened.
            stop_reason = ""
            if not review_passed:
                if escalation_attempts >= MAX_ESCALATIONS and result.get("patch_target") == "escalate":
                    stop_reason = " (stopped: escalation cap reached -- the scope conflict could not be fully resolved)"
                elif reviewer_attempts > MAX_REVIEWER_ATTEMPTS:
                    stop_reason = " (stopped: reviewer attempt cap reached without a clean pass)"

            final_report = (
                f"Web developer team finished.\n\n"
                f"**Project Directory:** {absolute_path}\n\n"
                f"**Tech Stack:**\n{tech_stack_res[:500] if tech_stack_res else 'N/A'}\n\n"
                f"**Functional Requirements:**\n{functional_res[:500] if functional_res else 'N/A'}\n\n"
                f"**Code Review (final diagnosis){stop_reason}:**\n{review_res[:500] if review_res else 'N/A'}\n\n"
                f"**Cycle Stats:** {reviewer_attempts} reviewer pass(es), {escalation_attempts} escalation(s) to Tech Lead\n\n"
                f"**Files Generated:**\n"
                f"- api.py (FastAPI backend - models, state, and endpoints in one file)\n"
                f"- app.py (NiceGUI UI, Tailwind/Quasar styling, mounted on FastAPI via ui.run_with)\n"
                f"- requirements.txt\n\n"
                f"**How to run:**\n"
                f"1. `cd {absolute_path}`\n"
                f"2. `pip install -r requirements.txt`\n"
                f"3. `uvicorn app:app --reload --port 8080`\n"
                f"4. Open `http://localhost:8080` in your browser.\n\n"
                f"Note: this code has not been executed in a sandbox -- the Code Reviewer's checks are "
                f"static. Run it yourself before relying on it, especially if any cycle stats above are high."
            )

            return ToolResult(
                ok=True,
                content=final_report,
                data={
                    "files_path": str(absolute_path),
                    "tech_stack": tech_stack_res,
                    "functional_requirements": functional_res,
                    "review_report": review_res,
                    "review_passed": review_passed,
                    "reviewer_attempts": reviewer_attempts,
                    "escalation_attempts": escalation_attempts,
                }
            )
        except Exception as e:
            logger.error(f"[WebDeveloperTool] Failed: {e}")
            return ToolResult(ok=False, content=f"Web developer team failed: {e}")

web_developer_tool = WebDeveloperTool()

'''