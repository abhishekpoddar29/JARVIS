"""
backend/agents/graph.py

Jarvis main orchestration graph.

Flow:

    START
      -> memory_retrieval
      -> personality
      -> gemini                  planner / direct responder
          -> finalize            when no tool work is required
          -> tool                batched dispatcher
              -> tool_response   aggregate + review
                  -> tool        when another batch is required
                  -> finalize    when the request is complete

The graph stays intentionally small. Parallelism, duplicate protection,
approval safety, and bounded multi-round execution live inside graph_nodes.py.
Existing tools and specialist subgraphs remain unchanged.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.graph_nodes import (
    finalize_node,
    gemini_node,
    memory_retrieval_node,
    personality_node,
    route_after_gemini,
    tool_node,
    tool_response_node,
)
from agents.graph_state import JarvisState


def route_after_tool_response(state: JarvisState) -> str:
    """Continue only when the reviewer scheduled another tool batch."""
    if state.get("final_reply"):
        return "finalize"

    pending = state.get("tool_calls") or []
    if not pending and state.get("tool_call"):
        pending = [state["tool_call"]]

    return "tool" if pending else "finalize"


def build_graph():
    graph = StateGraph(JarvisState)

    graph.add_node("memory_retrieval", memory_retrieval_node)
    graph.add_node("personality", personality_node)
    graph.add_node("gemini", gemini_node)
    graph.add_node("tool", tool_node)
    graph.add_node("tool_response", tool_response_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "memory_retrieval")
    graph.add_edge("memory_retrieval", "personality")
    graph.add_edge("personality", "gemini")

    graph.add_conditional_edges(
        "gemini",
        route_after_gemini,
        {"tool": "tool", "done": "finalize"},
    )

    graph.add_edge("tool", "tool_response")

    graph.add_conditional_edges(
        "tool_response",
        route_after_tool_response,
        {"tool": "tool", "finalize": "finalize"},
    )

    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=MemorySaver())


jarvis_graph = build_graph()