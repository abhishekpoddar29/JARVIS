from __future__ import annotations

import re

from agents.subgraphs.Financial_Analyst.financial_state import (
    FinancialFact,
)


def _fiscal_year(period: str) -> int | None:
    match = re.fullmatch(r"FY(\d{4})", period)

    if match is None:
        return None

    return int(match.group(1))


def previous_period(period: str) -> str | None:
    year = _fiscal_year(period)

    if year is None:
        return None

    return f"FY{year - 1}"


def find_fact(
    facts: list[FinancialFact],
    metric_key: str,
    period: str,
    scope: str,
    currency: str,
    unit: str,
) -> FinancialFact | None:
    matches = [
        fact
        for fact in facts
        if fact["metric_key"] == metric_key
        and fact["period"] == period
        and fact["scope"] == scope
        and fact["currency"] == currency
        and fact["unit"] == unit
        and fact["status"] != "REJECTED"
    ]

    if not matches:
        return None

    return max(
        matches,
        key=lambda fact: (
            fact["confidence"],
            fact["version"],
        ),
    )


def average_balance(
    facts: list[FinancialFact],
    metric_key: str,
    period: str,
    scope: str,
    currency: str,
    unit: str,
) -> tuple[float | None, list[str]]:
    prior_period = previous_period(period)

    if prior_period is None:
        return None, []

    current = find_fact(
        facts,
        metric_key,
        period,
        scope,
        currency,
        unit,
    )

    prior = find_fact(
        facts,
        metric_key,
        prior_period,
        scope,
        currency,
        unit,
    )

    if (
        current is None
        or prior is None
        or current["value"] is None
        or prior["value"] is None
    ):
        return None, []

    return (
        (current["value"] + prior["value"]) / 2,
        [
            current["fact_id"],
            prior["fact_id"],
        ],
    )