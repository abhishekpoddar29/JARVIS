"""
backend/agents/graph.py

Wires graph_nodes into the actual LangGraph. This replaces the hand-rolled
control flow currently inside main.py's websocket handler:

    Gemini call -> if tool_call: execute tool -> second Gemini call -> reply
                -> else: reply directly

with the equivalent graph:

    START -> personality -> gemini --(tool_call?)--> tool -> tool_response -> finalize -> END
                                  \\----------------------------------------/
                                   (no tool_call)----------> finalize -> END
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

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


def build_graph():
    graph = StateGraph(JarvisState)


    graph.add_node("memory_retrieval",memory_retrieval_node)
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
        lambda state: "tool" if state.get("tool_call") else "finalize",
        {"tool":"tool" , "finalize":"finalize"}
    )
    graph.add_edge("finalize", END)
    checkpointer=MemorySaver()

    return graph.compile(checkpointer=checkpointer)


# Compiled once at import time — main.py imports `jarvis_graph` and calls
# `await jarvis_graph.ainvoke(initial_state)` per message.
jarvis_graph = build_graph()