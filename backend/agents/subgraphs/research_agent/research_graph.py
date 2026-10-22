from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from .research_nodes import (
    acquire_evidence_node,
    decide_next_action_node,
    evaluate_result_node,
    plan_research_node,
    synthesize_report_node,
)
from .research_router import route_after_decision
from .research_state import ProgressCallback, ResearchContext, ResearchState, research_progress_emitter


def _build_graph():
    graph = StateGraph(ResearchState)
    graph.add_node("plan", plan_research_node)
    graph.add_node("acquire", acquire_evidence_node)
    graph.add_node("evaluate", evaluate_result_node)
    graph.add_node("decide", decide_next_action_node)
    graph.add_node("synthesize", synthesize_report_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "acquire")
    graph.add_edge("acquire", "evaluate")
    graph.add_edge("evaluate", "decide")
    graph.add_conditional_edges("decide", route_after_decision, {"acquire": "acquire", "synthesize": "synthesize"})
    graph.add_edge("synthesize", END)
    return graph.compile()


research_graph = _build_graph()


async def run_research(
    *,
    original_request: str,
    topic_hint: str = "",
    prior_context: ResearchContext | None = None,
    progress_callback: ProgressCallback | None = None,
) -> ResearchState:
    initial: ResearchState = {
        "original_request": original_request.strip(),
        "topic_hint": topic_hint.strip(),
        "current_date": datetime.now(UTC).date().isoformat(),
        "prior_context": prior_context,
        "claim_ledger": [],
        "current_action": None,
        "action_history": [],
        "last_action_result": {},
        "queries_used": [],
        "sources": [],
        "evidence": [],
        "evidence_diagnostics": [],
        "execution_results": [],
        "failure_mode": "none",
        "iteration": 0,
        "search_actions": 0,
        "execution_actions": 0,
        "errors": [],
        "created_at": datetime.now(UTC).isoformat(),
    }
    token = research_progress_emitter.set(progress_callback)
    try:
        result: dict[str, Any] = await research_graph.ainvoke(initial, config={"recursion_limit": 80})
        return cast(ResearchState, result)
    finally:
        research_progress_emitter.reset(token)