from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .engineering_nodes import (
    backend_engineer_node, backend_repair_node, file_manifest_node,
    frontend_engineer_node, frontend_repair_node, multi_repair_node,
    package_and_save_node, product_manager_node,
    requirement_architect_node,
    review_and_repair_node, roadmap_node, sandbox_test_node,
    test_engineer_node, test_repair_node,
)
from .engineering_router import (
    after_manifest, after_owner_repair, after_pm, after_review,
    after_roadmap, after_sandbox, after_spec, after_test,
)
from .engineering_state import EngineeringState


def build_engineering_graph(*, checkpointer=None):
    graph = StateGraph(EngineeringState)

    nodes = {
        "product_manager": product_manager_node,
        "requirement_architect": requirement_architect_node,
        "roadmap": roadmap_node,
        "file_manifest": file_manifest_node,
        "frontend_engineer": frontend_engineer_node,
        "backend_engineer": backend_engineer_node,
        "test_engineer": test_engineer_node,
        "sandbox_test": sandbox_test_node,
        "review_and_repair": review_and_repair_node,
        "frontend_repair": frontend_repair_node,
        "backend_repair": backend_repair_node,
        "test_repair": test_repair_node,
        "multi_repair": multi_repair_node,
        "package_and_save": package_and_save_node,
    }
    for name, node in nodes.items():
        graph.add_node(name, node)

    graph.add_edge(START, "product_manager")
    graph.add_conditional_edges("product_manager", after_pm)
    graph.add_conditional_edges("requirement_architect", after_spec)
    graph.add_conditional_edges("roadmap", after_roadmap)
    graph.add_conditional_edges("file_manifest", after_manifest)

    # Explicit parallel barrier. Non-required owners return SKIPPED.
    graph.add_edge(["frontend_engineer", "backend_engineer"], "test_engineer")

    graph.add_conditional_edges("test_engineer", after_test)
    graph.add_conditional_edges("sandbox_test", after_sandbox)
    graph.add_conditional_edges("review_and_repair", after_review)
    graph.add_conditional_edges("frontend_repair", after_owner_repair)
    graph.add_conditional_edges("backend_repair", after_owner_repair)
    graph.add_conditional_edges("multi_repair", after_owner_repair)
    graph.add_edge("test_repair", "sandbox_test")
    graph.add_edge("package_and_save", END)

    return graph.compile(checkpointer=checkpointer or InMemorySaver())


engineering_graph = build_engineering_graph()