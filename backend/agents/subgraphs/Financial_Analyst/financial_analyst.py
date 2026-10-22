from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.subgraphs.Financial_Analyst.financial_nodes import (
    accounting_node,
    apply_accounting_adjustments_node,
    company_identity_node,
    final_report_node,
    financial_fact_node,
    identity_validation_node,
    initialize_research,
    metric_engine_node,
    reconciliation_node,
    source_research_node,
    historical_financials_node,
    financial_health_node,
    market_data_node,
    valuation_node,
)
from agents.subgraphs.Financial_Analyst.financial_router import (
    route_after_accounting_review,
    route_after_fact_extraction,
    route_after_identity_validation,
    route_after_reconciliation,
)
from agents.subgraphs.Financial_Analyst.financial_state import (
    FinancialResearchState,
)





graph = StateGraph(FinancialResearchState)


# ============================================================
# Nodes
# ============================================================

graph.add_node("initialize_research",initialize_research,)
graph.add_node("company_identity",company_identity_node,)
graph.add_node("identity_validation", identity_validation_node,)
graph.add_node("source_research",source_research_node,)
graph.add_node("financial_fact_extraction",financial_fact_node,)
graph.add_node("reconcile_facts",reconciliation_node,)
graph.add_node("accounting_review",accounting_node,)
graph.add_node("apply_accounting_adjustments",apply_accounting_adjustments_node)
graph.add_node("calculate_metrics", metric_engine_node,)
graph.add_node("financial_health",financial_health_node,)
graph.add_node("market_data",market_data_node,)
graph.add_node("valuation",valuation_node,)
graph.add_node("final_report", final_report_node,)
graph.add_node("historical_financials",historical_financials_node,)



# ============================================================
# Primary Flow
# ============================================================

graph.add_edge(START,"initialize_research",)
graph.add_edge("initialize_research","company_identity",)
graph.add_edge("company_identity","identity_validation",)

graph.add_conditional_edges(
    "identity_validation",
    route_after_identity_validation,
    {
        "historical_financials": "historical_financials",
        "end": END,
    },
)

graph.add_edge("historical_financials","source_research",)
graph.add_edge("source_research","financial_fact_extraction",)

graph.add_conditional_edges(
    "financial_fact_extraction",
    route_after_fact_extraction,
    {
        "reconcile_facts": "reconcile_facts",
        "end": END,
    },
)


# ============================================================
# Reconciliation Routing
# ============================================================

graph.add_conditional_edges(
    "reconcile_facts",
    route_after_reconciliation,
    {
        "source_research": "source_research",
        "financial_fact_extraction": "financial_fact_extraction",
        "accounting_review": "accounting_review",
        

        # Escalation node is not implemented yet.
        # End safely instead of creating an invalid graph edge.
        "escalate": END,
        "end": END,
    },
)


# ============================================================
# Accounting Routing
# ============================================================

graph.add_conditional_edges(
    "accounting_review",
    route_after_accounting_review,
    {
        "apply_accounting_adjustments": "apply_accounting_adjustments",
        "calculate_metrics": "calculate_metrics",
        "escalate": END,
    },
)


# Accounting adjustments create versioned facts.
# Reconciliation must validate the updated fact registry again.
graph.add_edge("apply_accounting_adjustments","reconcile_facts",)


# ============================================================
# Current Phase Completion
# ============================================================

graph.add_edge("calculate_metrics","financial_health",)
graph.add_edge("financial_health","market_data",)
graph.add_edge("market_data","valuation",)
graph.add_edge("valuation","final_report",)
graph.add_edge("final_report", END)


financial_analyst_graph = graph.compile()