from __future__ import annotations

from collections import defaultdict
from math import sqrt

from agents.subgraphs.Financial_Analyst.financial_state import (
    FinancialFact,
    FinancialMetric,
    MetricsState,
    ReconciliationState,
)

from agents.subgraphs.Financial_Analyst.algorithms.period_alignment import (
    average_balance,
)

FactKey = tuple[str, str, str, str, str]
ContextKey = tuple[str, str, str, str]


def _safe_divide(
    numerator: float | None,
    denominator: float | None,
) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None

    return numerator / denominator


def _metric(
    metric_key: str,
    value: float | None,
    formula: str,
    inputs: list[str],
    period: str,
) -> FinancialMetric:
    return FinancialMetric(
        metric_key=metric_key,
        value=value,
        formula=formula,
        inputs=inputs,
        period=period,
        status="COMPLETED" if value is not None else "FAILED",
    )


def _canonical_facts(
    facts: list[FinancialFact],
    reconciliation: ReconciliationState,
) -> list[FinancialFact]:
    fact_map = {
        fact["fact_id"]: fact
        for fact in facts
        if fact["status"] != "REJECTED"
    }

    excluded_ids: set[str] = set()

    for conflict in reconciliation["conflicts"]:
        candidate_ids = {
            candidate["fact_id"]
            for candidate in conflict["candidates"]
        }

        if conflict["status"] == "UNRESOLVED":
            excluded_ids.update(candidate_ids)
            continue

        selected_id = conflict["selected_fact_id"]

        excluded_ids.update(
            candidate_id
            for candidate_id in candidate_ids
            if candidate_id != selected_id
        )

    return [
        fact
        for fact_id, fact in fact_map.items()
        if fact_id not in excluded_ids
    ]


def _index_facts(
    facts: list[FinancialFact],
) -> dict[FactKey, FinancialFact]:
    indexed: dict[FactKey, FinancialFact] = {}

    for fact in facts:
        key = (
            fact["metric_key"],
            fact["period"],
            fact["scope"],
            fact["currency"],
            fact["unit"],
        )

        existing = indexed.get(key)

        if existing is None or (
            fact["confidence"],
            fact["version"],
        ) > (
            existing["confidence"],
            existing["version"],
        ):
            indexed[key] = fact

    return indexed


def _fact(
    index: dict[FactKey, FinancialFact],
    metric_key: str,
    period: str,
    scope: str,
    currency: str,
    unit: str,
) -> FinancialFact | None:
    return index.get(
        (
            metric_key,
            period,
            scope,
            currency,
            unit,
        )
    )


def _calculate_ratio(
    metric_key: str,
    numerator: FinancialFact | None,
    denominator: FinancialFact | None,
    formula: str,
    period: str,
    multiplier: float = 1.0,
) -> FinancialMetric:
    inputs = [
        fact["fact_id"]
        for fact in (numerator, denominator)
        if fact is not None
    ]

    value = None

    if numerator is not None and denominator is not None:
        ratio = _safe_divide(
            numerator["value"],
            denominator["value"],
        )

        if ratio is not None:
            value = ratio * multiplier

    return _metric(
        metric_key=metric_key,
        value=value,
        formula=formula,
        inputs=inputs,
        period=period,
    )

def _calculate_average_balance_ratio(
    metric_key: str,
    numerator: FinancialFact | None,
    facts: list[FinancialFact],
    balance_metric_key: str,
    period: str,
    scope: str,
    currency: str,
    unit: str,
    formula: str,
    multiplier: float = 1.0,
) -> FinancialMetric:
    average_value, balance_inputs = average_balance(
        facts=facts,
        metric_key=balance_metric_key,
        period=period,
        scope=scope,
        currency=currency,
        unit=unit,
    )

    inputs = (
        [numerator["fact_id"]]
        if numerator is not None
        else []
    )
    inputs.extend(balance_inputs)

    value = None

    if numerator is not None and average_value is not None:
        ratio = _safe_divide(
            numerator["value"],
            average_value,
        )

        if ratio is not None:
            value = ratio * multiplier

    return _metric(
        metric_key=metric_key,
        value=value,
        formula=formula,
        inputs=list(dict.fromkeys(inputs)),
        period=period,
    )



    
def _calculate_fcf(
    operating_cash_flow: FinancialFact | None,
    capital_expenditure: FinancialFact | None,
    period: str,
) -> FinancialMetric:
    inputs = [
        fact["fact_id"]
        for fact in (
            operating_cash_flow,
            capital_expenditure,
        )
        if fact is not None
    ]

    value = None

    if (
        operating_cash_flow is not None
        and capital_expenditure is not None
        and operating_cash_flow["value"] is not None
        and capital_expenditure["value"] is not None
    ):
        value = (
            operating_cash_flow["value"]
            - abs(capital_expenditure["value"])
        )

    return _metric(
        metric_key="free_cash_flow",
        value=value,
        formula="operating_cash_flow - abs(capital_expenditure)",
        inputs=inputs,
        period=period,
    )


def _calculate_graham_number(
    eps: FinancialFact | None,
    book_value_per_share: FinancialMetric | None,
    period: str,
) -> FinancialMetric:
    inputs: list[str] = []

    if eps is not None:
        inputs.append(eps["fact_id"])

    if book_value_per_share is not None:
        inputs.extend(book_value_per_share["inputs"])

    value = None

    if (
        eps is not None
        and eps["value"] is not None
        and eps["value"] > 0
        and book_value_per_share is not None
        and book_value_per_share["value"] is not None
        and book_value_per_share["value"] > 0
    ):
        value = sqrt(
            22.5
            * eps["value"]
            * book_value_per_share["value"]
        )

    return _metric(
        metric_key="graham_number",
        value=value,
        formula="sqrt(22.5 * diluted_eps * book_value_per_share)",
        inputs=list(dict.fromkeys(inputs)),
        period=period,
    )


def calculate_metrics(
    facts: list[FinancialFact],
    reconciliation: ReconciliationState,
) -> MetricsState:
    canonical_facts = _canonical_facts(
        facts,
        reconciliation,
    )

    index = _index_facts(canonical_facts)

    contexts: set[ContextKey] = {
    (
        fact["period"],
        fact["scope"],
        fact["currency"],
        fact["unit"],
    )
    for fact in canonical_facts
}

    metrics: list[FinancialMetric] = []

    for period, scope, currency, unit in sorted(contexts):
        revenue = _fact(
        index, "revenue", period, scope, currency, unit
        )
        gross_profit = _fact(
            index, "gross_profit", period, scope, currency, unit
        )
        operating_income = _fact(
            index, "operating_income", period, scope, currency, unit
        )
        ebit = _fact(
            index, "ebit", period, scope, currency, unit
        )
        ebitda = _fact(
            index, "ebitda", period, scope, currency, unit
        )
        interest_expense = _fact(
            index, "interest_expense", period, scope, currency, unit
        )
        reported_net_income = _fact(
            index,
            "reported_net_income",
            period, scope, currency, unit
        )

        current_assets = _fact(
            index,
            "total_current_assets",
            period, scope, currency, unit
        )
        current_liabilities = _fact(
            index,
            "total_current_liabilities",
            period, scope, currency, unit
        )
        total_assets = _fact(index, "total_assets", period , scope, currency, unit)
        total_liabilities = _fact(
            index,
            "total_liabilities",
            period, scope, currency, unit
        )
        total_debt = _fact(index, "total_debt", period, scope, currency, unit)
        shareholder_equity = _fact(
            index,
            "shareholder_equity",
            period, scope, currency, unit
        )
        cash = _fact(
            index,
            "cash_and_cash_equivalents",
            period, scope, currency, unit
        )
        shares_outstanding = _fact(
            index,
            "shares_outstanding",
            period, scope, currency, unit
        )

        operating_cash_flow = _fact(
            index,
            "operating_cash_flow",
            period, scope, currency, unit
        )
        capital_expenditure = _fact(
            index,
            "capital_expenditure",
            period, scope, currency, unit
        )
        diluted_eps = _fact(index, "diluted_eps", period,scope, currency, unit)

        current_ratio = _calculate_ratio(
            "current_ratio",
            current_assets,
            current_liabilities,
            "total_current_assets / total_current_liabilities",
            period,
        )

        debt_to_equity = _calculate_ratio(
            "debt_to_equity",
            total_debt,
            shareholder_equity,
            "total_debt / shareholder_equity",
            period,
        )

        debt_to_assets = _calculate_ratio(
            "debt_to_assets",
            total_debt,
            total_assets,
            "total_debt / total_assets",
            period,
        )

        interest_coverage = _calculate_ratio(
            "interest_coverage",
            ebit,
            interest_expense,
            "ebit / abs(interest_expense)",
            period,
        )

        if (
            interest_coverage["value"] is not None
            and interest_expense is not None
            and interest_expense["value"] is not None
        ):
            interest_coverage["value"] = _safe_divide(
                ebit["value"] if ebit else None,
                abs(interest_expense["value"]),
            )

        gross_margin = _calculate_ratio(
            "gross_margin",
            gross_profit,
            revenue,
            "gross_profit / revenue * 100",
            period,
            multiplier=100.0,
        )

        operating_margin = _calculate_ratio(
            "operating_margin",
            operating_income,
            revenue,
            "operating_income / revenue * 100",
            period,
            multiplier=100.0,
        )

        ebit_margin = _calculate_ratio(
            "ebit_margin",
            ebit,
            revenue,
            "ebit / revenue * 100",
            period,
            multiplier=100.0,
        )

        ebitda_margin = _calculate_ratio(
            "ebitda_margin",
            ebitda,
            revenue,
            "ebitda / revenue * 100",
            period,
            multiplier=100.0,
        )

        net_margin = _calculate_ratio(
            "net_margin",
            reported_net_income,
            revenue,
            "reported_net_income / revenue * 100",
            period,
            multiplier=100.0,
        )

        return_on_assets = _calculate_average_balance_ratio(
            metric_key="return_on_assets",
            numerator=reported_net_income,
            facts=canonical_facts,
            balance_metric_key="total_assets",
            period=period,
            scope=scope,
            currency=currency,
            unit=unit,
            formula="reported_net_income / average_total_assets * 100",
            multiplier=100.0,
        )

        return_on_equity = _calculate_average_balance_ratio(
            metric_key="return_on_equity",
            numerator=reported_net_income,
            facts=canonical_facts,
            balance_metric_key="shareholder_equity",
            period=period,
            scope=scope,
            currency=currency,
            unit=unit,
            formula="reported_net_income / average_shareholder_equity * 100",
            multiplier=100.0,
        )

        asset_turnover = _calculate_average_balance_ratio(
            metric_key="asset_turnover",
            numerator=revenue,
            facts=canonical_facts,
            balance_metric_key="total_assets",
            period=period,
            scope=scope,
            currency=currency,
            unit=unit,
            formula="revenue / average_total_assets",
        )

        free_cash_flow = _calculate_fcf(
            operating_cash_flow,
            capital_expenditure,
            period,
        )

        fcf_margin = _calculate_ratio(
            "free_cash_flow_margin",
            None,
            revenue,
            "free_cash_flow / revenue * 100",
            period,
            multiplier=100.0,
        )

        if (
            free_cash_flow["value"] is not None
            and revenue is not None
        ):
            ratio = _safe_divide(
                free_cash_flow["value"],
                revenue["value"],
            )

            fcf_margin["value"] = (
                ratio * 100.0
                if ratio is not None
                else None
            )
            fcf_margin["inputs"] = list(
                dict.fromkeys(
                    free_cash_flow["inputs"]
                    + [revenue["fact_id"]]
                )
            )
            fcf_margin["status"] = (
                "COMPLETED"
                if fcf_margin["value"] is not None
                else "FAILED"
            )

        book_value_per_share = _calculate_ratio(
            "book_value_per_share",
            shareholder_equity,
            shares_outstanding,
            "shareholder_equity / shares_outstanding",
            period,
        )

        net_debt = _metric(
            metric_key="net_debt",
            value=(
                total_debt["value"] - cash["value"]
                if (
                    total_debt is not None
                    and cash is not None
                    and total_debt["value"] is not None
                    and cash["value"] is not None
                )
                else None
            ),
            formula="total_debt - cash_and_cash_equivalents",
            inputs=[
                fact["fact_id"]
                for fact in (total_debt, cash)
                if fact is not None
            ],
            period=period,
        )

        graham_number = _calculate_graham_number(
            diluted_eps,
            book_value_per_share,
            period,
        )

        metrics.extend([
            current_ratio,
            debt_to_equity,
            debt_to_assets,
            interest_coverage,
            gross_margin,
            operating_margin,
            ebit_margin,
            ebitda_margin,
            net_margin,
            return_on_assets,
            return_on_equity,
            asset_turnover,
            free_cash_flow,
            fcf_margin,
            book_value_per_share,
            net_debt,
            graham_number,
        ])

    return MetricsState(
        metrics=metrics,
    )