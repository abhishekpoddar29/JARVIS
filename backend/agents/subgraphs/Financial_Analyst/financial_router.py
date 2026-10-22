from __future__ import annotations

from typing import Literal

from agents.subgraphs.Financial_Analyst.financial_state import (
    FinancialResearchState,
    MAX_CLAIM_REVIEW_ATTEMPTS,
    MAX_ESCALATIONS,
    MAX_REVIEWER_ATTEMPTS,
)
from langgraph.graph import END


ReconciliationRoute = Literal[
    "source_research",
    "financial_fact_extraction",
    "accounting_review",
    "apply_accounting_adjustments",
    "calculate_metrics",
    "escalate",
]

ClaimReviewRoute = Literal[
    "targeted_repair",
    "investment_debate",
    "escalate",
]

DecisionRoute = Literal[
    "thesis_writer",
    "insufficient_evidence",
]

FactExtractionRoute = Literal[
    "reconcile_facts",
    "end",
]


def route_after_identity_validation(state: FinancialResearchState) -> str:
    if state["identity_validation"]["status"] != "COMPLETED":
        return "end"

    if state["execution"]["status"] == "FAILED":
        return "end"

    return "historical_financials"

def route_after_fact_extraction(
    state: FinancialResearchState,
) -> FactExtractionRoute:
    failed_nodes = state["execution"]["failed_nodes"]

    if "financial_fact_node" in failed_nodes:
        return "end"

    if not state["financial_facts"]["facts"]:
        return "end"

    return "reconcile_facts"


def route_after_reconciliation(
    state: FinancialResearchState,
) -> str:
    execution = state.get("execution", {})
    reconciliation = state.get("reconciliation", {})
    review_control = state.get("review_control", {})

    if execution.get("status") == "FAILED":
        return "end"

    if reconciliation.get("status") == "FAILED":
        return "end"

    unresolved_conflict_ids = reconciliation.get(
        "unresolved_conflict_ids",
        [],
    )

    if unresolved_conflict_ids:
        reviewer_attempts = review_control.get(
            "reviewer_attempts",
            0,
        )

        if reviewer_attempts < MAX_REVIEWER_ATTEMPTS:
            return "financial_fact_extraction"

        return "escalate"

    # Important:
    # Do not return calculate_metrics here.
    # Accounting must always be resolved before metrics.
    return "accounting_review"


def _reconciliation_escalation_route(
    state: FinancialResearchState,
) -> ReconciliationRoute:
    review = state["review_control"]

    if review["escalation_attempts"] < MAX_ESCALATIONS:
        return "escalate"

    return "calculate_metrics"

#route_after_accounting_review() currently returns financial_fact_extraction. 
# When we build the Accounting node, we should likely add a dedicated apply_accounting_adjustments / fact patch stage, 
# then route directly back to reconcile_facts. Otherwise the extraction agent may unnecessarily regenerate every financial fact.

def route_after_accounting_review(
    state: FinancialResearchState,
) -> str:
    accounting = state["accounting"]
    review_control = state["review_control"]

    if accounting["status"] == "FAILED":
        return "escalate"

    if accounting.get("reconciliation_required"):
        return "apply_accounting_adjustments"

    return "calculate_metrics"


def route_after_claim_review(
    state: FinancialResearchState,
) -> ClaimReviewRoute:
    claims = state["claims"]
    repair = state["repair"]
    review = state["review_control"]

    if not claims["failed_claim_ids"]:
        return "investment_debate"

    if (
        repair["active"]
        and review["claim_review_attempts"]
        < MAX_CLAIM_REVIEW_ATTEMPTS
    ):
        return "targeted_repair"

    if review["escalation_attempts"] < MAX_ESCALATIONS:
        return "escalate"

    return "investment_debate"


def route_after_targeted_repair(
    state: FinancialResearchState,
) -> Literal[
    "claim_review",
    "escalate",
]:
    repair = state["repair"]
    review = state["review_control"]

    if repair["failed_claim_ids"]:
        if review["escalation_attempts"] < MAX_ESCALATIONS:
            return "escalate"

    return "claim_review"


def route_after_decision(
    state: FinancialResearchState,
) -> DecisionRoute:
    decision = state["decision"]
    execution = state["execution"]

    if (
        decision["recommendation_allowed"]
        and execution["recommendation_allowed"]
    ):
        return "thesis_writer"

    return "insufficient_evidence"