from __future__ import annotations

import hashlib
import json
import re
import time
import traceback
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agents.graph_nodes import orb_emitter
from agents.subgraphs.research_agent import ResearchContext, build_research_context, run_research
from config import get_settings
from memory.session_storage import session_store
from tools.base import BaseTool, ToolResult

try:
    from agents.graph_nodes import progress_emitter
except ImportError:
    progress_emitter = None


settings = get_settings()


def _context_value(variable: Any) -> Any:
    try:
        return variable.get() if variable is not None else None
    except (LookupError, AttributeError):
        return None


def _context_key(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.casefold()).strip("-")[:70] or "topic"
    digest = hashlib.sha256(topic.casefold().encode()).hexdigest()[:10]
    return f"research_context:{slug}:{digest}"


def _log(run_id: str, event: str, **fields: Any) -> None:
    rendered = " ".join(f"{key}={json.dumps(value, default=str)[:500]}" for key, value in fields.items())
    print(f"[RESEARCH TOOL][{run_id}] {datetime.now(UTC).isoformat(timespec='milliseconds')} {event} {rendered}", flush=True)


class ResearchTool(BaseTool):
    name: str = "research_tool"
    description: str = (
        "Claim-driven evidence research for theses, investigations, comparisons, and deep reports. "
        "It dynamically chooses search/scrape, evidence comparison, or E2B computation for the "
        "highest-priority incomplete claim, then synthesizes only validated evidence."
    )

    async def run(self, topic: str, message: str) -> ToolResult:
        run_id = uuid4().hex[:8]
        started = time.perf_counter()
        topic = topic.strip()
        request = message.strip()
        if not topic or not request:
            return ToolResult(
                ok=False,
                content="Both topic and the complete original research request are required.",
                error="topic and message are required",
                data={"terminal": True, "passthrough": True, "retryable": False, "research_status": "failed"},
            )
        if not settings.gemini_api_key or not settings.serper_api_key:
            missing = "GEMINI_API_KEY" if not settings.gemini_api_key else "SERPER_API_KEY"
            return ToolResult(
                ok=False, content=f"{missing} is not configured.", error=f"{missing} is not configured.",
                data={"terminal": True, "passthrough": True, "retryable": False, "research_status": "failed"},
            )

        key = _context_key(topic)
        prior: ResearchContext | None = None
        try:
            raw = await session_store.get_value(key)
            decoded = json.loads(raw) if raw else None
            if isinstance(decoded, dict) and str(decoded.get("topic", "")).casefold() == topic.casefold():
                prior = ResearchContext(**decoded)
        except Exception as exc:
            _log(run_id, "context.load.failed", error=str(exc))

        async def progress(text: str) -> None:
            print(f"[RESEARCH][{run_id}] {text}", flush=True)
            orb = _context_value(orb_emitter)
            if orb:
                try:
                    await orb("processing")
                except Exception:
                    pass
            callback = _context_value(progress_emitter)
            if callback:
                try:
                    await callback(text)
                except Exception:
                    pass

        try:
            _log(run_id, "graph.started", topic=topic, prior_context=bool(prior))
            state = await run_research(
                original_request=request,
                topic_hint=topic,
                prior_context=prior,
                progress_callback=progress,
            )
            report = str(state.get("final_report", "")).strip()
            if not report:
                raise RuntimeError("Research completed without a report.")

            plan = state.get("plan", {})
            review = state.get("review", {})
            ledger = state.get("claim_ledger", [])
            sources = state.get("sources", [])
            evidence = state.get("evidence", [])
            approved = [item for item in evidence if item.get("approved")]
            executions = state.get("execution_results", [])
            cited_source_ids = {str(item.get("source_id")) for item in approved}
            usable_sources = [item for item in sources if item.get("passages")]
            failure_mode = str(review.get("failure_mode") or state.get("failure_mode") or "none")

            # Persist only useful, topic-isolated research. Failed runs cannot poison later requests.
            context_persisted = False
            if approved and failure_mode not in {"extraction_failure", "system_failure"}:
                try:
                    await session_store.set_value(key, json.dumps(build_research_context(state), ensure_ascii=False))
                    context_persisted = True
                except Exception as exc:
                    _log(run_id, "context.persist.failed", error=str(exc))

            if review.get("verification_status") != "verified":
                note = (
                    "> **Research status: partial.** The report contains validated findings; "
                    "remaining knowledge gaps and any internal limitations are separated explicitly."
                )
                report = f"{note}\n\n{report}"

            status_counts = Counter(str(item.get("status", "unknown")) for item in ledger)
            retrieval_counts = Counter(str(item.get("fetch_status", "unknown")) for item in sources)
            elapsed = round(time.perf_counter() - started, 2)
            _log(
                run_id, "result.ready", elapsed=elapsed, claims=dict(status_counts),
                sources=len(sources), usable_sources=len(usable_sources), approved_evidence=len(approved),
                actions=len(state.get("action_history", [])), executions=len(executions), failure_mode=failure_mode,
            )

            return ToolResult(
                ok=True,
                content=report,
                data={
                    "terminal": True,
                    "passthrough": True,
                    "retryable": False,
                    "debug_run_id": run_id,
                    "elapsed_seconds": elapsed,
                    "topic": plan.get("topic") or topic,
                    "level": plan.get("level", "medium"),
                    "research_status": review.get("research_status", "partial"),
                    "verification_status": review.get("verification_status", "incomplete"),
                    "decision_safe": bool(review.get("decision_safe", False)),
                    "failure_mode": failure_mode,
                    "coverage_score": review.get("coverage_score", 0.0),
                    "claim_status_counts": dict(status_counts),
                    "claims": ledger,
                    "actions": state.get("action_history", []),
                    "queries_used": state.get("queries_used", []),
                    "source_count": len(sources),
                    "usable_source_count": len(usable_sources),
                    "retrieval_status_counts": dict(retrieval_counts),
                    "verified_source_count": len(cited_source_ids),
                    "approved_evidence_count": len(approved),
                    "evidence_diagnostics": state.get("evidence_diagnostics", [])[:30],
                    "execution_results": executions,
                    "unresolved_claims": review.get("unresolved_claims", []),
                    "contradictions": review.get("contradictions", []),
                    "system_limitations": review.get("system_limitations", []),
                    "context_persisted": context_persisted,
                    "created_at": state.get("created_at"),
                    "warnings": state.get("errors", []),
                },
            )
        except Exception as exc:
            elapsed = round(time.perf_counter() - started, 2)
            _log(run_id, "result.failed", elapsed=elapsed, error=f"{type(exc).__name__}: {exc}")
            print(traceback.format_exc(), flush=True)
            return ToolResult(
                ok=False,
                content=f"Research failed: {exc}",
                error=str(exc),
                data={
                    "terminal": True, "passthrough": True, "retryable": False,
                    "debug_run_id": run_id, "elapsed_seconds": elapsed,
                    "research_status": "failed", "decision_safe": False,
                    "failure_mode": "system_failure", "topic": topic,
                },
            )


research_tool = ResearchTool()