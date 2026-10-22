from __future__ import annotations

import re
from difflib import SequenceMatcher

from agents.subgraphs.Financial_Analyst.financial_state import (
    EvidenceItem,
    FinancialFact,
    FinancialStatementType,
)


# ============================================================
# Canonical Financial Metrics Ontology
# ============================================================

METRIC_ONTOLOGY: dict[str, tuple[FinancialStatementType, tuple[str, ...]]] = {
    # Income Statement
    "revenue": (
        "INCOME_STATEMENT",
        (
            "revenue",
            "total revenue",
            "revenue from operations",
            "sales",
            "net sales",
            "operating revenue",
        ),
    ),
    "cost_of_revenue": (
        "INCOME_STATEMENT",
        (
            "cost of revenue",
            "cost of sales",
            "cost of goods sold",
            "cogs",
        ),
    ),
    "gross_profit": (
        "INCOME_STATEMENT",
        (
            "gross profit",
            "gross income",
        ),
    ),
    "operating_income": (
        "INCOME_STATEMENT",
        (
            "operating income",
            "operating profit",
            "profit from operations",
            "operating profit loss",
        ),
    ),
    "ebit": (
        "INCOME_STATEMENT",
        (
            "ebit",
            "earnings before interest and tax",
            "earnings before interest and taxes",
        ),
    ),
    "ebitda": (
        "INCOME_STATEMENT",
        (
            "ebitda",
            "earnings before interest tax depreciation and amortization",
            "earnings before interest taxes depreciation and amortization",
        ),
    ),
    "interest_expense": (
        "INCOME_STATEMENT",
        (
            "interest expense",
            "finance costs",
            "finance cost",
            "borrowing costs",
        ),
    ),
    "pretax_income": (
        "INCOME_STATEMENT",
        (
            "pretax income",
            "pre tax income",
            "profit before tax",
            "profit loss before tax",
            "income before tax",
            "earnings before tax",
        ),
    ),
    "income_tax_expense": (
        "INCOME_STATEMENT",
        (
            "income tax expense",
            "tax expense",
            "taxation",
            "income taxes",
        ),
    ),
    "continuing_operations_income": (
        "INCOME_STATEMENT",
        (
            "income from continuing operations",
            "net income from continuing operations",
            "profit from continuing operations",
            "profit loss from continuing operations",
            "continuing operations income",
        ),
    ),
    "discontinued_operations_income": (
        "INCOME_STATEMENT",
        (
            "income from discontinued operations",
            "net income from discontinued operations",
            "profit from discontinued operations",
            "profit loss from discontinued operations",
            "discontinued operations income",
        ),
    ),
    "reported_net_income": (
        "INCOME_STATEMENT",
        (
            "net income",
            "net profit",
            "profit after tax",
            "profit loss for the period",
            "profit loss for the year",
            "net income attributable to shareholders",
        ),
    ),
    "diluted_eps": (
        "INCOME_STATEMENT",
        (
            "diluted eps",
            "diluted earnings per share",
            "earnings per share diluted",
        ),
    ),
    "basic_eps": (
        "INCOME_STATEMENT",
        (
            "basic eps",
            "basic earnings per share",
            "earnings per share basic",
        ),
    ),

    # Balance Sheet
    "cash_and_cash_equivalents": (
        "BALANCE_SHEET",
        (
            "cash and cash equivalents",
            "cash equivalents",
            "cash and equivalents",
        ),
    ),
    "total_current_assets": (
        "BALANCE_SHEET",
        (
            "total current assets",
            "current assets",
        ),
    ),
    "total_assets": (
        "BALANCE_SHEET",
        (
            "total assets",
        ),
    ),
    "total_current_liabilities": (
        "BALANCE_SHEET",
        (
            "total current liabilities",
            "current liabilities",
        ),
    ),
    "total_liabilities": (
        "BALANCE_SHEET",
        (
            "total liabilities",
        ),
    ),
    "short_term_debt": (
        "BALANCE_SHEET",
        (
            "short term debt",
            "short term borrowings",
            "current borrowings",
            "current debt",
        ),
    ),
    "long_term_debt": (
        "BALANCE_SHEET",
        (
            "long term debt",
            "long term borrowings",
            "non current borrowings",
            "non current debt",
        ),
    ),
    "total_debt": (
        "BALANCE_SHEET",
        (
            "total debt",
            "total borrowings",
            "borrowings",
        ),
    ),
    "shareholder_equity": (
        "BALANCE_SHEET",
        (
            "shareholder equity",
            "shareholders equity",
            "stockholders equity",
            "total equity",
            "equity attributable to owners",
            "equity attributable to shareholders",
        ),
    ),
    "retained_earnings": (
        "BALANCE_SHEET",
        (
            "retained earnings",
            "retained profits",
        ),
    ),
    "shares_outstanding": (
        "BALANCE_SHEET",
        (
            "shares outstanding",
            "ordinary shares outstanding",
            "number of shares outstanding",
        ),
    ),

    # Cash Flow
    "operating_cash_flow": (
        "CASH_FLOW",
        (
            "operating cash flow",
            "cash flow from operating activities",
            "net cash from operating activities",
            "net cash generated from operating activities",
        ),
    ),
    "investing_cash_flow": (
        "CASH_FLOW",
        (
            "investing cash flow",
            "cash flow from investing activities",
            "net cash from investing activities",
            "net cash used in investing activities",
        ),
    ),
    "financing_cash_flow": (
        "CASH_FLOW",
        (
            "financing cash flow",
            "cash flow from financing activities",
            "net cash from financing activities",
            "net cash used in financing activities",
        ),
    ),
    "capital_expenditure": (
        "CASH_FLOW",
        (
            "capital expenditure",
            "capital expenditures",
            "capex",
            "purchase of property plant and equipment",
            "payments for property plant and equipment",
        ),
    ),
    "free_cash_flow": (
        "CASH_FLOW",
        (
            "free cash flow",
            "fcf",
        ),
    ),
    "depreciation_and_amortization": (
        "CASH_FLOW",
        (
            "depreciation and amortization",
            "depreciation amortization",
            "depreciation and amortisation",
            "depreciation amortisation",
        ),
    ),
    "dividends_paid": (
        "CASH_FLOW",
        (
            "dividends paid",
            "dividend paid",
            "cash dividends paid",
        ),
    ),

    # Non-GAAP
    "normalized_ebitda": (
        "NON_GAAP",
        (
            "normalized ebitda",
            "normalised ebitda",
            "adjusted ebitda",
        ),
    ),
    "normalized_net_income": (
        "NON_GAAP",
        (
            "normalized net income",
            "normalised net income",
            "adjusted net income",
            "adjusted net profit",
        ),
    ),
}


# ============================================================
# Unit Ontology
# ============================================================

UNIT_MULTIPLIERS: dict[str, float] = {
    "": 1.0,
    "unit": 1.0,
    "units": 1.0,
    "ones": 1.0,
    "thousand": 1_000.0,
    "thousands": 1_000.0,
    "k": 1_000.0,
    "lakh": 100_000.0,
    "lakhs": 100_000.0,
    "million": 1_000_000.0,
    "millions": 1_000_000.0,
    "mn": 1_000_000.0,
    "m": 1_000_000.0,
    "crore": 10_000_000.0,
    "crores": 10_000_000.0,
    "cr": 10_000_000.0,
    "billion": 1_000_000_000.0,
    "billions": 1_000_000_000.0,
    "bn": 1_000_000_000.0,
    "b": 1_000_000_000.0,
}


MIN_LABEL_SIMILARITY = 0.92


# ============================================================
# Text Normalization
# ============================================================

def normalize_text(value: str) -> str:
    value = value.lower().strip()
    value = value.replace("&", " and ")
    value = re.sub(r"[/\\_-]+", " ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return " ".join(value.split())


# ============================================================
# Metric Resolution
# ============================================================

def resolve_metric_key(
    raw_label: str,
    statement_type: FinancialStatementType,
) -> str | None:
    normalized_label = normalize_text(raw_label)

    exact_matches: list[str] = []

    for metric_key, (expected_statement, aliases) in METRIC_ONTOLOGY.items():
        if expected_statement != statement_type:
            continue

        if normalized_label in {
            normalize_text(alias)
            for alias in aliases
        }:
            exact_matches.append(metric_key)

    if len(exact_matches) == 1:
        return exact_matches[0]

    if exact_matches:
        return None

    best_metric: str | None = None
    best_score = 0.0
    ambiguous = False

    for metric_key, (expected_statement, aliases) in METRIC_ONTOLOGY.items():
        if expected_statement != statement_type:
            continue

        score = max(
            SequenceMatcher(
                None,
                normalized_label,
                normalize_text(alias),
            ).ratio()
            for alias in aliases
        )

        if score > best_score:
            best_metric = metric_key
            best_score = score
            ambiguous = False
        elif score == best_score and score >= MIN_LABEL_SIMILARITY:
            ambiguous = True

    if best_score < MIN_LABEL_SIMILARITY or ambiguous:
        return None

    return best_metric


# ============================================================
# Numeric Normalization
# ============================================================

def parse_numeric_value(
    raw_value: float | str | None,
) -> float | None:
    if raw_value is None:
        return None

    if isinstance(raw_value, (int, float)):
        return float(raw_value)

    value = raw_value.strip()

    if not value or value in {"-", "—", "–"}:
        return None

    negative = value.startswith("(") and value.endswith(")")

    value = value.strip("()")
    value = value.replace(",", "")
    value = re.sub(r"[^\d.+\-]", "", value)

    if not value:
        return None

    try:
        parsed = float(value)
    except ValueError:
        return None

    return -abs(parsed) if negative else parsed


def normalize_unit(unit: str) -> str:
    return normalize_text(unit)


def normalize_value(
    raw_value: float | str | None,
    unit: str,
) -> float | None:
    value = parse_numeric_value(raw_value)

    if value is None:
        return None

    multiplier = UNIT_MULTIPLIERS.get(normalize_unit(unit))

    if multiplier is None:
        return None

    return value * multiplier


# ============================================================
# Period Normalization
# ============================================================

def normalize_period(period: str) -> str:
    value = period.upper().strip()
    value = re.sub(r"\s+", "", value)

    fy_match = re.fullmatch(r"FY[-_]?(\d{4})", value)
    if fy_match:
        return f"FY{fy_match.group(1)}"

    quarter_match = re.fullmatch(
        r"(Q[1-4])[-_]?FY[-_]?(\d{4})",
        value,
    )
    if quarter_match:
        return f"{quarter_match.group(1)}FY{quarter_match.group(2)}"

    quarter_year_match = re.fullmatch(
        r"(Q[1-4])[-_]?(\d{4})",
        value,
    )
    if quarter_year_match:
        return f"{quarter_year_match.group(1)}FY{quarter_year_match.group(2)}"

    return value


# ============================================================
# Scope Normalization
# ============================================================

SCOPE_ALIASES: dict[str, str] = {
    "consolidated": "CONSOLIDATED",
    "group": "CONSOLIDATED",
    "standalone": "STANDALONE",
    "company": "STANDALONE",
    "parent": "STANDALONE",
}


def normalize_scope(scope: str) -> str:
    normalized = normalize_text(scope)
    return SCOPE_ALIASES.get(normalized, normalized.upper())


# ============================================================
# Evidence → Financial Fact
# ============================================================

def normalize_evidence(
    evidence: EvidenceItem,
    fact_id: str,
) -> FinancialFact | None:
    metric_key = resolve_metric_key(
        raw_label=evidence["raw_label"],
        statement_type=evidence["statement_type"],
    )

    if metric_key is None:
        return None

    value = normalize_value(
        raw_value=evidence["raw_value"],
        unit=evidence["unit"],
    )

    if evidence["raw_value"] is not None and value is None:
        return None

    return FinancialFact(
        fact_id=fact_id,
        metric_key=metric_key,
        value=value,
        unit="BASE",
        currency=evidence["currency"].upper().strip(),
        period=normalize_period(evidence["period"]),
        scope=normalize_scope(evidence["scope"]),
        statement_type=evidence["statement_type"],
        evidence_ids=[evidence["evidence_id"]],
        source_ids=[evidence["source_id"]],
        normalized_definition=evidence["definition"],
        accounting_signals=[],
        confidence=evidence["confidence"],
        status="NORMALIZED",
        created_by=evidence["extracted_by"],
        version=1,
    )