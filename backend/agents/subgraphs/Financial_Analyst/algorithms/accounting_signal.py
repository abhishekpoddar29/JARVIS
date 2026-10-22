from __future__ import annotations

from copy import deepcopy
from typing import Any
from agents.subgraphs.Financial_Analyst.financial_state import (
    AccountingAdjustment,
    FinancialFact,
)


ADD_BACK_TYPES = frozenset({
    "IMPAIRMENT",
    "RESTRUCTURING",
    "ONE_TIME_LOSS",
})

REMOVE_GAIN_TYPES = frozenset({
    "ONE_TIME_GAIN",
})


ACCOUNTING_SIGNALS = frozenset({
    "ACCOUNTING_POLICY_CHANGE",
    "CONTINUING_OPERATIONS",
    "DISCONTINUED_OPERATIONS",
    "IMPAIRMENT",
    "NON_RECURRING_ITEM",
    "ONE_TIME_ITEM",
    "RESTATEMENT",
    "RESTRUCTURING",
})


def requires_accounting_review(
    conflict: dict[str, Any],
) -> bool:
    signals = conflict.get("accounting_signals", [])

    if not isinstance(signals, list):
        return False

    return any(
        str(signal).strip().upper() in ACCOUNTING_SIGNALS
        for signal in signals
    )

def apply_accounting_adjustments(
    facts: list[FinancialFact],
    adjustments: list[AccountingAdjustment],
) -> tuple[list[FinancialFact], list[AccountingAdjustment]]:
    fact_map = {
        fact["fact_id"]: fact
        for fact in facts
    }

    adjusted_facts = list(facts)
    processed_adjustments: list[AccountingAdjustment] = []

    for adjustment in adjustments:
        updated_adjustment = deepcopy(adjustment)
        updated_adjustment["applied"] = False
        updated_adjustment["adjusted_fact_id"] = None

        fact = fact_map.get(adjustment["fact_id"])
        amount = adjustment["amount"]

        if (
            fact is None
            or fact["value"] is None
            or amount is None
        ):
            processed_adjustments.append(updated_adjustment)
            continue

        adjustment_type = adjustment["adjustment_type"]

        if adjustment_type in ADD_BACK_TYPES:
            adjusted_value = fact["value"] + abs(amount)
        elif adjustment_type in REMOVE_GAIN_TYPES:
            adjusted_value = fact["value"] - abs(amount)
        else:
            processed_adjustments.append(updated_adjustment)
            continue

        adjusted_fact = deepcopy(fact)
        adjusted_fact_id = (
            f"{fact['fact_id']}-ADJ-{adjustment['adjustment_id']}"
        )

        adjusted_fact["fact_id"] = adjusted_fact_id
        adjusted_fact["value"] = adjusted_value
        adjusted_fact["evidence_ids"] = sorted({
            *fact["evidence_ids"],
            *adjustment["evidence_ids"],
        })
        adjusted_fact["confidence"] = min(
            fact["confidence"],
            adjustment["confidence"],
        )
        adjusted_fact["created_by"] = "AccountingAgent"
        adjusted_fact["version"] = fact["version"] + 1

        adjusted_facts.append(adjusted_fact)
        fact_map[adjusted_fact_id] = adjusted_fact

        updated_adjustment["adjusted_fact_id"] = adjusted_fact_id
        updated_adjustment["applied"] = True

        processed_adjustments.append(updated_adjustment)

    return adjusted_facts, processed_adjustments