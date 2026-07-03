from __future__ import annotations

import json

from agents.research_agent import (
    ResearchAgent,
    ResearchDepth,
    context_from_dict,
    context_to_dict,
    detect_depth,
    report_to_context,
)
from memory.session_storage import session_store
from tools.base import BaseTool, ToolResult
# F14: Import the orb_emitter so we can stream progress to the frontend
from agents.graph_nodes import orb_emitter

_RESEARCH_CONTEXT_KEY = "research_context"


class ResearchTool(BaseTool):
    name: str = "research_tool"
    description: str = (
        "Deep research agent. Use when user says: research, deep dive, analyze, "
        "investigate, full report, comprehensive overview, or asks for structured "
        "multi-section reports on a topic. Returns a full Markdown research report "
        "with sources and citations. For simple one-line searches use web_search_tool."
    )

    async def run(self, topic: str, message: str = "") -> ToolResult:
        try:
            agent = ResearchAgent()
        except RuntimeError as exc:
            return ToolResult(ok=False, content=str(exc), data={})

        depth = detect_depth(message or topic)

        # Load prior context from Redis if exists
        prior_context = None
        try:
            raw = await session_store.get_value(_RESEARCH_CONTEXT_KEY)
            if raw:
                prior_context = context_from_dict(json.loads(raw))
        except Exception:
            pass

        # F14: Create a callback function that emits progress over the WebSocket
        async def _emit_progress(msg: str):
            emit_fn = orb_emitter.get()
            if emit_fn:
                try:
                    # We can use a specific orb state or just 'processing'
                    await emit_fn("processing")
                    # Note: To actually show the text in the UI, we'd need a custom 
                    # websocket payload. For now, this keeps the orb spinning.
                    print(f"[RESEARCH PROGRESS] {msg}")
                except Exception:
                    pass

        try:
            # Follow-up if same-session context exists and message is short/contextual
            if prior_context and _is_followup(message or topic):
                report = await agent.run_followup(
                    question=topic,
                    prior_context=prior_context,
                    progress_callback=_emit_progress # Pass callback here
                )
            else:
                report = await agent.run(
                    topic=topic,
                    depth=depth,
                    prior_context=prior_context,
                    progress_callback=_emit_progress # Pass callback here
                )

            # Persist context to Redis for follow-ups
            ctx = report_to_context(report)
            await session_store.set_value(
                _RESEARCH_CONTEXT_KEY,
                json.dumps(context_to_dict(ctx)),
            )

            sources_md = "\n".join(
                f"- [{s.title}]({s.link})" for s in report.sources[:10]
            )

            return ToolResult(
                ok=True,
                content=report.markdown,
                data={
                    "topic": report.topic,
                    "depth": report.depth,
                    "queries_used": report.queries_used,
                    "iterations": report.iterations, # New metadata
                    "source_count": len(report.sources),
                    "sources_md": sources_md,
                    "created_at": report.created_at,
                },
            )

        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"Research failed: {exc}",
                data={"topic": topic, "error": str(exc)},
            )


def _is_followup(message: str) -> bool:
    """Heuristic: short messages or ones using 'what about / tell me more' are follow-ups."""
    lower = message.lower().strip()
    followup_signals = [
        "what about", "tell me more", "and what", "how about",
        "what's their", "what are their", "expand on", "more on",
        "elaborate", "go deeper",
    ]
    return len(lower.split()) <= 6 or any(s in lower for s in followup_signals)