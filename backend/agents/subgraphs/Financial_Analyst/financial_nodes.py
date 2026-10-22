from __future__ import annotations

import asyncio
import json
import re
import inspect
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from tools.web_search_tool import WebSearchTool
from tools.web_scraper_tool import WebScraperTool
from agents.gemini_client import generate_reply, GeminiClientError
from agents.graph_nodes import progress_emitter

from agents.subgraphs.Financial_Analyst.algorithms.metric_engine import (calculate_metrics,)
from agents.subgraphs.Financial_Analyst.algorithms.reconciliation import (reconcile_facts,)
from agents.subgraphs.Financial_Analyst.financial_prompts import (
    ACCOUNTING_PROMPT,
    COMPANY_IDENTITY_PROMPT,
    FINANCIAL_FACT_PROMPT,
    SOURCE_RESEARCH_PROMPT,
)
from agents.subgraphs.Financial_Analyst.financial_state import (FAST_MODEL,FinancialResearchState,)
from agents.subgraphs.Financial_Analyst.algorithms.accounting_signal import (apply_accounting_adjustments,)



from config import get_settings



MAX_SEARCH_RESULTS_TO_SCRAPE = 5
MAX_SOURCE_TEXT_CHARS = 20_000
MAX_TOTAL_SOURCE_TEXT_CHARS = 60_000

MISSING_METRIC_CATEGORY_MAP = {
    "current_ratio": "liquidity",
    "quick_ratio": "liquidity",

    "debt_to_equity": "solvency",
    "debt_to_assets": "solvency",
    "interest_coverage": "solvency",
    "net_debt": "solvency",

    "gross_margin": "profitability",
    "operating_margin": "profitability",
    "ebit_margin": "profitability",
    "ebitda_margin": "profitability",
    "net_margin": "profitability",
    "return_on_assets": "profitability",
    "return_on_equity": "profitability",

    "asset_turnover": "efficiency",

    "operating_cash_flow": "cash_flow",
    "free_cash_flow": "cash_flow",
    "free_cash_flow_margin": "cash_flow",

    "book_value_per_share": "per_share",
    "diluted_eps": "per_share",
    "basic_eps": "per_share",
    "graham_number": "valuation",
}


CATEGORY_IMPACT_MAP = {
    "valuation": (
        "Blocks valuation confidence because fair-value and market-based "
        "analysis need this metric."
    ),
    "per_share": (
        "Limits per-share valuation, book value analysis, and shareholder "
        "return analysis."
    ),
    "liquidity": (
        "Limits the assessment of short-term balance-sheet strength."
    ),
    "solvency": (
        "Limits the assessment of leverage, debt capacity, and financial risk."
    ),
    "profitability": (
        "Limits the assessment of margins, operating quality, and earnings "
        "strength."
    ),
    "cash_flow": (
        "Limits the assessment of cash conversion and reinvestment capacity."
    ),
    "efficiency": (
        "Limits the assessment of asset productivity and capital efficiency."
    ),
    "other": (
        "Limits part of the financial analysis because required inputs were "
        "not available."
    ),
}


FORMULA_IGNORE_TOKENS = {
    "abs",
    "sqrt",
    "min",
    "max",
    "pow",
    "round",
    "none",
    "null",
    "true",
    "false",
}


DERIVED_INPUT_REQUIREMENTS = {
    "average_total_assets": [
        "total_assets_current_period",
        "total_assets_prior_comparable_period",
    ],
    "average_shareholder_equity": [
        "shareholder_equity_current_period",
        "shareholder_equity_prior_comparable_period",
    ],
    "free_cash_flow": [
        "operating_cash_flow",
        "capital_expenditure",
    ],
    "book_value_per_share": [
        "shareholder_equity",
        "shares_outstanding",
    ],
}


def _metric_category(metric_key: str) -> str:
    key = str(metric_key or "").lower()

    if key in MISSING_METRIC_CATEGORY_MAP:
        return MISSING_METRIC_CATEGORY_MAP[key]

    if "margin" in key:
        return "profitability"

    if "debt" in key or "coverage" in key:
        return "solvency"

    if "cash_flow" in key or "fcf" in key:
        return "cash_flow"

    if "per_share" in key or "eps" in key:
        return "per_share"

    if "turnover" in key:
        return "efficiency"

    if key.startswith(("pe_", "pb_", "ps_", "ev_")):
        return "valuation"

    return "other"




def _extract_formula_inputs(formula: str) -> list[str]:
    tokens = re.findall(
        r"\b[a-zA-Z_][a-zA-Z0-9_]*\b",
        str(formula or ""),
    )

    cleaned = []

    for token in tokens:
        token = token.strip()

        if not token:
            continue

        if token.lower() in FORMULA_IGNORE_TOKENS:
            continue

        if token not in cleaned:
            cleaned.append(token)

    return cleaned


def _facts_by_id(
    state: FinancialResearchState,
) -> dict[str, dict[str, Any]]:
    return {
        str(fact.get("fact_id")): fact
        for fact in state["financial_facts"]["facts"]
        if fact.get("fact_id")
    }


def _available_fact_keys_by_period(
    state: FinancialResearchState,
) -> dict[str, set[str]]:
    available: dict[str, set[str]] = {}

    for fact in state["financial_facts"]["facts"]:
        period = str(fact.get("period", ""))
        metric_key = str(fact.get("metric_key", ""))
        value = fact.get("value")

        if not period or not metric_key:
            continue

        if fact.get("status") not in {"NORMALIZED", "RECONCILED"}:
            continue

        if not isinstance(value, (int, float)):
            continue

        available.setdefault(period, set()).add(metric_key)

    return available


def _available_metric_keys_by_period(
    metrics: list[dict[str, Any]],
) -> dict[str, set[str]]:
    available: dict[str, set[str]] = {}

    for metric in metrics:
        period = str(metric.get("period", ""))
        metric_key = str(metric.get("metric_key", ""))
        value = metric.get("value")

        if not period or not metric_key:
            continue

        if metric.get("status") != "COMPLETED":
            continue

        if not isinstance(value, (int, float)):
            continue

        available.setdefault(period, set()).add(metric_key)

    return available


def _available_input_labels(
    metric: dict[str, Any],
    fact_lookup: dict[str, dict[str, Any]],
) -> list[str]:
    labels: list[str] = []

    for input_id in metric.get("inputs", []):
        input_id = str(input_id)
        fact = fact_lookup.get(input_id)

        if fact:
            label = str(fact.get("metric_key", input_id))
        else:
            label = input_id

        if label not in labels:
            labels.append(label)

    return labels


def _missing_inputs_for_metric(
    metric: dict[str, Any],
    fact_keys_by_period: dict[str, set[str]],
    metric_keys_by_period: dict[str, set[str]],
) -> list[str]:
    formula_inputs = _extract_formula_inputs(
        str(metric.get("formula", "")),
    )
    period = str(metric.get("period", ""))

    available_keys = set()
    available_keys.update(fact_keys_by_period.get(period, set()))
    available_keys.update(metric_keys_by_period.get(period, set()))

    missing: list[str] = []

    for input_key in formula_inputs:
        if input_key in available_keys:
            continue

        expanded = DERIVED_INPUT_REQUIREMENTS.get(input_key)

        if expanded:
            for expanded_input in expanded:
                if expanded_input not in missing:
                    missing.append(expanded_input)
        else:
            if input_key not in missing:
                missing.append(input_key)

    return missing


def _missing_metric_reason(
    metric_key: str,
    category: str,
    period: str,
    missing_inputs: list[str],
) -> str:
    if category in {"valuation", "per_share"}:
        return (
            "Valuation or per-share metric could not be calculated because "
            "share-count, book-value, price, or market-data inputs were "
            "missing."
        )

    if str(period).startswith("Q"):
        return (
            "Quarterly metric could not be calculated because the required "
            "quarterly inputs or prior-period balance-sheet averages were "
            "incomplete."
        )

    if missing_inputs:
        return (
            "Metric could not be calculated because required inputs were "
            f"missing: {', '.join(missing_inputs[:5])}."
        )

    return (
        "Metric could not be calculated because one or more required inputs "
        "were missing, null, or invalid."
    )


def _build_missing_metrics(
    state: FinancialResearchState,
    metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []

    fact_lookup = _facts_by_id(state)
    fact_keys_by_period = _available_fact_keys_by_period(state)
    metric_keys_by_period = _available_metric_keys_by_period(metrics)

    for metric in metrics:
        if metric.get("status") != "FAILED":
            continue

        metric_key = str(metric.get("metric_key", ""))
        period = str(metric.get("period", ""))
        formula = str(metric.get("formula", ""))

        category = _metric_category(metric_key)

        available_inputs = _available_input_labels(
            metric,
            fact_lookup,
        )

        missing_inputs = _missing_inputs_for_metric(
            metric=metric,
            fact_keys_by_period=fact_keys_by_period,
            metric_keys_by_period=metric_keys_by_period,
        )

        severity = _missing_metric_severity(
            metric_key,
            category,
        )

        missing.append({
            "metric_key": metric_key,
            "period": period,
            "category": category,
            "formula": formula,
            "available_inputs": available_inputs,
            "missing_inputs": missing_inputs,
            "reason": _missing_metric_reason(
                metric_key=metric_key,
                category=category,
                period=period,
                missing_inputs=missing_inputs,
            ),
            "impact": CATEGORY_IMPACT_MAP.get(
                category,
                CATEGORY_IMPACT_MAP["other"],
            ),
            "severity": severity,
        })

    return missing

def _unique_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    output = []

    for value in values:
        if value in seen:
            continue

        seen.add(value)
        output.append(value)

    return output


def _build_missing_metric_summary(
    missing_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    if not missing_metrics:
        return {
            "total_missing": 0,
            "high_severity_count": 0,
            "medium_severity_count": 0,
            "low_severity_count": 0,
            "by_category": {},
            "unique_metric_keys": [],
            "high_impact_metric_keys": [],
            "blocked_analysis_areas": [],
            "explanation": (
                "No missing metric issues were detected by the metric engine."
            ),
            "status": "COMPLETED",
        }

    category_counts = Counter(
        metric.get("category", "other")
        for metric in missing_metrics
    )

    severity_counts = Counter(
        metric.get("severity", "LOW")
        for metric in missing_metrics
    )

    unique_metric_keys = _unique_preserve_order([
        str(metric.get("metric_key", ""))
        for metric in missing_metrics
        if metric.get("metric_key")
    ])

    high_impact_metric_keys = _unique_preserve_order([
        str(metric.get("metric_key", ""))
        for metric in missing_metrics
        if metric.get("severity") == "HIGH"
        and metric.get("metric_key")
    ])

    blocked_analysis_areas = []

    if category_counts.get("valuation", 0) or category_counts.get("per_share", 0):
        blocked_analysis_areas.append("valuation")

    if category_counts.get("liquidity", 0):
        blocked_analysis_areas.append("liquidity")

    if category_counts.get("solvency", 0):
        blocked_analysis_areas.append("solvency")

    if category_counts.get("cash_flow", 0):
        blocked_analysis_areas.append("cash_flow")

    if category_counts.get("profitability", 0):
        blocked_analysis_areas.append("profitability")

    top_categories = category_counts.most_common(3)

    top_category_text = ", ".join(
        f"{category} ({count})"
        for category, count in top_categories
    )

    explanation_parts = [
        f"{len(missing_metrics)} metrics could not be calculated.",
    ]

    if top_category_text:
        explanation_parts.append(
            f"The largest missing categories were {top_category_text}."
        )

    if high_impact_metric_keys:
        explanation_parts.append(
            "High-impact missing metrics included "
            f"{', '.join(high_impact_metric_keys[:5])}."
        )

    if "valuation" in blocked_analysis_areas:
        explanation_parts.append(
            "Valuation confidence is limited until market-data, share-count, "
            "enterprise-value, or per-share inputs are available."
        )

    quarterly_missing = [
        metric
        for metric in missing_metrics
        if str(metric.get("period", "")).startswith("Q")
    ]

    if quarterly_missing:
        explanation_parts.append(
            f"{len(quarterly_missing)} missing metrics were from quarterly "
            "periods, which often lack complete prior-period balance-sheet "
            "averages or cash-flow inputs."
        )

    return {
        "total_missing": len(missing_metrics),
        "high_severity_count": severity_counts.get("HIGH", 0),
        "medium_severity_count": severity_counts.get("MEDIUM", 0),
        "low_severity_count": severity_counts.get("LOW", 0),
        "by_category": dict(category_counts),
        "unique_metric_keys": unique_metric_keys,
        "high_impact_metric_keys": high_impact_metric_keys,
        "blocked_analysis_areas": blocked_analysis_areas,
        "explanation": " ".join(explanation_parts),
        "status": "COMPLETED",
    }

settings = get_settings()
web_search_tool = WebSearchTool()
web_scraper_tool = WebScraperTool()

async def _emit_progress(message: str,progress: int | None = None,) -> None:
    try:
        emitter = progress_emitter.get()
        if emitter is not None:
            await emitter(message)
    except Exception:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()



def _audit(
    state: FinancialResearchState,
    node: str,
    action: str,
    message: str,
) -> list[dict[str, str]]:
    return [
        *state["audit"]["events"],
        {
            "node": node,
            "action": action,
            "message": message,
        },
    ]


def _execution_update(
    state: FinancialResearchState,
    node: str,
    stage: str,
    progress: int,
    status: str,
) -> dict[str, Any]:
    execution = dict(state["execution"])

    execution["current_node"] = node
    execution["active_stage"] = stage
    execution["progress"] = progress
    execution["status"] = status

    if status == "COMPLETED":
        execution["completed_nodes"] = list(
            dict.fromkeys([
                *execution["completed_nodes"],
                node,
            ])
        )

    if status == "FAILED":
        execution["failed_nodes"] = list(
            dict.fromkeys([
                *execution["failed_nodes"],
                node,
            ])
        )

    return execution


def _parse_json(response: str) -> dict[str, Any]:
    text = response.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end <= start:
        raise ValueError("Agent returned no JSON object.")

    payload = json.loads(text[start:end + 1])

    if not isinstance(payload, dict):
        raise ValueError("Agent JSON must be an object.")

    return payload





async def _run_agent(
    prompt: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await asyncio.to_thread(
        generate_reply,
        api_key=settings.gemini_api_key,
        model=FAST_MODEL,
        user_message=json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        ),
        system_instruction=prompt,
        temperature=0.1,
        max_output_tokens=8192,
    )

    return _parse_json(response.text)


async def initialize_research(
    state: FinancialResearchState,
) -> dict[str, Any]:
    await _emit_progress(
        "Initializing financial research",
        2,
    )

    return {
        "execution": _execution_update(
            state,
            node="initialize_research",
            stage="INITIALIZATION",
            progress=2,
            status="COMPLETED",
        ),
        "audit": {
            "events": _audit(
                state,
                "initialize_research",
                "INITIALIZED",
                "Financial research pipeline initialized.",
            )
        },
    }

#Research and Scraping

def _source_type_from_result(
    title: str,
    url: str,
    snippet: str,
) -> str:
    text = f"{title} {url} {snippet}".lower()

    if "sec.gov" in text:
        return "REGULATORY_FILING"

    if "10-k" in text or "form 10-k" in text:
        return "ANNUAL_REPORT"

    if "10-q" in text or "10q" in text or "form 10-q" in text:
        return "QUARTERLY_REPORT"

    if "annual report" in text:
        return "ANNUAL_REPORT"

    if "investor relations" in text:
        return "COMPANY_WEBSITE"

    if "earnings" in text or "financial results" in text:
        return "EARNINGS_RELEASE"

    return "OTHER"


def _source_matches_company_identity(
    url: str,
    title: str,
    snippet: str,
    company_name: str,
    ticker: str,
) -> bool:
    text = f"{url} {title} {snippet}".lower()
    company_tokens = [
        token
        for token in re.findall(
            r"[a-z0-9]+",
            company_name.lower(),
        )
        if len(token) >= 4
    ]

    strong_company_hit = any(
        token in text
        for token in company_tokens
    )

    ticker_hit = ticker.lower() in text

    # Ticker alone is not enough for ambiguous tickers like LX.
    return strong_company_hit or (
        ticker_hit and strong_company_hit
    )

def _authority_rank_for_source_type(source_type: str) -> int:
    ranks = {
        "REGULATORY_FILING": 100,
        "ANNUAL_REPORT": 95,
        "EXCHANGE_DISCLOSURE": 90,
        "QUARTERLY_REPORT": 88,
        "EARNINGS_RELEASE": 85,
        "INVESTOR_PRESENTATION": 75,
        "COMPANY_WEBSITE": 70,
        "MARKET_DATA_PROVIDER": 50,
        "RESEARCH": 40,
        "NEWS": 30,
        "OTHER": 10,
    }

    return ranks.get(source_type, 10)


def _is_scrapable_url(url: str) -> bool:
    lowered = url.lower()

    if not lowered.startswith(("http://", "https://")):
        return False

    if lowered.endswith(".pdf") or ".pdf" in lowered:
        return False

    blocked_fragments = (
        "youtube.com",
        "youtu.be",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "linkedin.com",
    )

    return not any(fragment in lowered for fragment in blocked_fragments)



def _is_financial_report_link(link: dict[str, str]) -> bool:
    text = f"{link.get('text', '')} {link.get('href', '')}".lower()

    patterns = (
        "10-k",
        "10q",
        "10-q",
        "annual report",
        "financial statements",
        "form 10-k",
        "form 10-q",
    )

    return any(pattern in text for pattern in patterns)


def _is_blocked_or_useless_text(text: str) -> bool:
    lowered = text.lower()

    blocked_phrases = (
        "your request originates from an undeclared automated tool",
        "please declare your traffic",
        "sec reserves the right to limit requests",
        "access denied",
        "verify you are human",
    )

    if any(phrase in lowered for phrase in blocked_phrases):
        return True

    useful_terms = (
        "net sales",
        "total net sales",
        "revenue",
        "gross margin",
        "operating income",
        "net income",
        "total assets",
        "cash and cash equivalents",
        "consolidated statements",
        "consolidated balance sheets",
        "consolidated statements of operations",
    )

    return not any(term in lowered for term in useful_terms)




def _dedupe_search_results(
    results: list[dict[str, str]],
) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []

    for item in results:
        url = item.get("link", "").strip()

        if not url or url in seen:
            continue

        seen.add(url)
        deduped.append(item)

    return deduped


async def _collect_search_results(
    search_queries: list[str],
) -> list[dict[str, str]]:
    """
    Execute identity-locked financial search queries and return deduped results.

    The source_research_node is now responsible for building safer queries like:
    "LexinFintech Holdings Ltd." "LX" annual report

    This helper only executes those queries.
    """
    all_results: list[dict[str, str]] = []

    for query in search_queries:
        search_result = await web_search_tool.run(
            query=query,
        )

        if not search_result.ok:
            continue

        results = search_result.data.get(
            "results",
            [],
        )

        for result in results:
            url = (
                result.get("url")
                or result.get("link")
                or result.get("href")
                or ""
            )

            title = (
                result.get("title")
                or result.get("name")
                or ""
            )

            snippet = (
                result.get("snippet")
                or result.get("description")
                or result.get("summary")
                or ""
            )

            if not url:
                continue

            all_results.append({
                **result,
                "url": url,
                "link": url,
                "title": title,
                "snippet": snippet,
                "query": query,
            })

    return _dedupe_search_results(
        all_results,
    )


async def _scrape_single_url(
    item: dict[str, str],
    source_index: int,
    remaining_budget: int,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    url = item.get("link", item.get("href", "")).strip()
    title = item.get("title", item.get("text", "")).strip()
    snippet = item.get("snippet", "").strip()

    if not _is_scrapable_url(url):
        return None, None

    scrape_result = await web_scraper_tool.run(url=url)

    if not scrape_result.ok:
        return None, None

    text = scrape_result.data.get("text", "")

    if not text.strip():
        return None, None

    if _is_blocked_or_useless_text(text):
        return None, None

    clipped_text = text[:min(MAX_SOURCE_TEXT_CHARS, remaining_budget)]

    source_id = f"SOURCE-{source_index:04d}"

    source_type = _source_type_from_result(
        title=title,
        url=url,
        snippet=snippet,
    )

    source = {
        "source_id": source_id,
        "title": title,
        "url": url,
        "publisher": "",
        "source_type": source_type,
        "fiscal_period": "",
        "published_date": "",
        "retrieved_at": _now(),
        "authority_rank": _authority_rank_for_source_type(source_type),
        "primary_source": source_type in {
            "REGULATORY_FILING",
            "ANNUAL_REPORT",
            "QUARTERLY_REPORT",
            "EARNINGS_RELEASE",
            "COMPANY_WEBSITE",
        },
        "status": "COMPLETED",
    }

    source_text = {
        "source_id": source_id,
        "url": url,
        "title": title,
        "text": clipped_text,
        "retrieved_at": _now(),
    }

    return source, source_text







async def _scrape_financial_sources(
    search_results: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    sources: list[dict[str, Any]] = []
    source_texts: list[dict[str, str]] = []

    total_chars = 0
    source_index = 1

    for item in search_results:
        if len(source_texts) >= MAX_SEARCH_RESULTS_TO_SCRAPE:
            break

        url = item.get("link", "").strip()
        title = item.get("title", "").strip()
        snippet = item.get("snippet", "").strip()

        if not _is_scrapable_url(url):
            continue

        scrape_result = await web_scraper_tool.run(url=url)

        if not scrape_result.ok:
            continue

        text = scrape_result.data.get("text", "")

        if _is_blocked_or_useless_text(text):
            continue
        if not text.strip():
            continue

        remaining_budget = MAX_TOTAL_SOURCE_TEXT_CHARS - total_chars

        if remaining_budget <= 0:
            break

        clipped_text = text[:min(MAX_SOURCE_TEXT_CHARS, remaining_budget)]

        source_id = f"SOURCE-{source_index:04d}"
        source_type = _source_type_from_result(
            title=title,
            url=url,
            snippet=snippet,
        )

        sources.append({
            "source_id": source_id,
            "title": title,
            "url": url,
            "publisher": "",
            "source_type": source_type,
            "fiscal_period": "",
            "published_date": "",
            "retrieved_at": _now(),
            "authority_rank": _authority_rank_for_source_type(source_type),
            "primary_source": source_type in {
                "REGULATORY_FILING",
                "ANNUAL_REPORT",
                "QUARTERLY_REPORT",
                "EARNINGS_RELEASE",
                "COMPANY_WEBSITE",
            },
            "status": "COMPLETED",
        })

        source_texts.append({
            "source_id": source_id,
            "url": url,
            "title": title,
            "text": clipped_text,
            "retrieved_at": _now(),
        })

        total_chars += len(clipped_text)
        source_index += 1

    return sources, source_texts

def _identity_tokens(
    company_name: str,
) -> list[str]:
    """
    Extract useful identity tokens from company name.

    Example:
    LexinFintech Holdings Ltd. -> ["lexinfintech"]
    Honasa Consumer Limited -> ["honasa", "consumer"]
    ITC Limited -> ["itc"]

    Very generic suffixes are removed.
    """
    ignored_tokens = {
        "ltd",
        "limited",
        "inc",
        "corp",
        "corporation",
        "company",
        "co",
        "holdings",
        "holding",
        "plc",
        "group",
        "class",
        "adr",
        "ads",
    }

    tokens = re.findall(
        r"[a-z0-9]+",
        str(company_name or "").lower(),
    )

    return [
        token
        for token in tokens
        if token not in ignored_tokens
        and len(token) >= 3
    ]


def _source_matches_company_identity(
    url: str,
    title: str,
    snippet: str,
    company_name: str,
    ticker: str,
) -> bool:
    """
    Validate that a source appears related to the requested company.

    For ambiguous tickers, company-name evidence is required.
    """
    text = f"{url} {title} {snippet}".lower()

    tokens = _identity_tokens(
        company_name,
    )

    ticker_clean = str(ticker or "").lower()
    ticker_base = ticker_clean.split(".")[0]

    company_hits = [
        token
        for token in tokens
        if token in text
    ]

    ticker_hit = (
        ticker_clean in text
        or f" {ticker_base} " in f" {text} "
        or f"/{ticker_base}" in text
    )

    official_domain_hit = any(
        token in url.lower()
        for token in tokens
    )

    # Strong match: company identity appears.
    if company_hits:
        return True

    # Official/company-looking domain match.
    if official_domain_hit:
        return True

    # For non-ambiguous exchange tickers like HONASA.NS or ITC.NS,
    # ticker evidence can be acceptable.
    if "." in ticker_clean and ticker_hit:
        return True

    # For short US tickers like LX, ticker alone is unsafe.
    return False


COMMON_COMPANY_SUFFIXES = {
    "inc",
    "inc.",
    "ltd",
    "ltd.",
    "limited",
    "corp",
    "corp.",
    "corporation",
    "company",
    "co",
    "co.",
    "holdings",
    "holding",
    "plc",
    "sa",
    "ag",
    "nv",
    "ads",
    "adr",
}


def _normalize_company_name_for_match(name: str) -> set[str]:
    text = re.sub(
        r"[^a-zA-Z0-9\s]",
        " ",
        str(name or "").lower(),
    )

    tokens = {
        token
        for token in text.split()
        if token and token not in COMMON_COMPANY_SUFFIXES
    }

    return tokens


def _company_name_token_match(
    expected: str,
    resolved: str,
) -> bool:
    expected_tokens = _normalize_company_name_for_match(expected)
    resolved_tokens = _normalize_company_name_for_match(resolved)

    if not expected_tokens or not resolved_tokens:
        return True

    overlap = expected_tokens & resolved_tokens

    return bool(overlap)

def _analysis_framework(
    state: FinancialResearchState,
) -> str:
    return (
        state.get("company", {}).get("analysis_framework")
        or "UNKNOWN"
    )


def _is_bank_framework(
    state: FinancialResearchState,
) -> bool:
    company = state.get("company", {})

    return (
        company.get("analysis_framework") == "BANK"
        or company.get("is_bank") is True
    )




async def company_identity_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "company_identity_node"

    await _emit_progress(
        "Resolving company identity",
        8,
    )

    try:
        requested_ticker = state["request"]["ticker"].strip().upper()
        user_company_name = state["request"].get(
            "user_company_name",
            "",
        ).strip()

        result = await _run_agent(
            COMPANY_IDENTITY_PROMPT,
            {
                "request": state["request"],
                "identity_lock": {
                    "requested_ticker": requested_ticker,
                    "user_company_name": user_company_name,
                    "do_not_override_user_company_name": bool(
                        user_company_name
                    ),
                },
            },
        )

        company = result.get("company", {})

        normalized_company = {
            "ticker": state["request"]["ticker"],
            "company_name": (
                state.get("request", {}).get("user_company_name")
                or state.get("request", {}).get("original_company_name")
                or company.get("company_name", "")
            ),
            "exchange": company.get("exchange", ""),
            "isin": company.get("isin", ""),
            "sector": company.get("sector", ""),
            "industry": company.get("industry", ""),
            "currency": company.get("currency", ""),
            "fiscal_year_end": company.get("fiscal_year_end", ""),
            "analysis_framework": company.get("analysis_framework", "UNKNOWN"),
            "is_financial_institution": bool(
                company.get("is_financial_institution", False)
            ),
            "is_bank": bool(company.get("is_bank", False)),
            "identity_confidence": float(
                company.get("identity_confidence", 0.0)
            ),
            "status": company.get("status", "COMPLETED"),
        }

        company["ticker"] = requested_ticker

        resolved_company_name = str(
            company.get("company_name", "")
        ).strip()

        if user_company_name:
            company["company_name"] = user_company_name

        elif resolved_company_name:
            company["company_name"] = resolved_company_name

        else:
            company["company_name"] = requested_ticker

        company["status"] = "COMPLETED"

        return {
            "company": normalized_company,
            "execution": _execution_update(
                state,
                node,
                "IDENTITY",
                8,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "IDENTITY_RESOLVED",
                    (
                        f"Resolved requested ticker {requested_ticker} "
                        f"as {company['company_name']}. "
                        f"LLM resolved name was: "
                        f"{resolved_company_name or 'N/A'}."
                    ),
                )
            },
        }

    except (GeminiClientError, KeyError, ValueError) as exc:
        return {
            "execution": _execution_update(
                state,
                node,
                "IDENTITY",
                8,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "IDENTITY_FAILED",
                    str(exc),
                )
            },
        }


async def identity_validation_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "identity_validation_node"

    await _emit_progress(
        "Validating company identity",
        10,
    )

    try:
        requested_ticker = state["request"]["ticker"].strip().upper()
        user_company_name = state["request"].get(
            "user_company_name",
            "",
        ).strip()

        resolved_ticker = state["company"].get(
            "ticker",
            "",
        ).strip().upper()

        resolved_company_name = state["company"].get(
            "company_name",
            "",
        ).strip()

        ticker_match = requested_ticker == resolved_ticker

        if user_company_name:
            company_name_match = _company_name_token_match(
                expected=user_company_name,
                resolved=resolved_company_name,
            )
        else:
            company_name_match = bool(resolved_company_name)

        mismatch_detected = not ticker_match or not company_name_match

        if mismatch_detected:
            reason = (
                "Identity validation failed. "
                f"Requested ticker={requested_ticker}, "
                f"resolved ticker={resolved_ticker or 'N/A'}, "
                f"user company={user_company_name or 'N/A'}, "
                f"resolved company={resolved_company_name or 'N/A'}."
            )

            return {
                "identity_validation": {
                    "requested_ticker": requested_ticker,
                    "user_company_name": user_company_name,
                    "resolved_company_name": resolved_company_name,
                    "validated_company_name": "",
                    "ticker_match": ticker_match,
                    "company_name_match": company_name_match,
                    "mismatch_detected": True,
                    "validation_reason": reason,
                    "confidence": 0.0,
                    "status": "FAILED",
                },
                "execution": _execution_update(
                    state,
                    node,
                    "IDENTITY_VALIDATION",
                    10,
                    "FAILED",
                ),
                "audit": {
                    "events": _audit(
                        state,
                        node,
                        "IDENTITY_VALIDATION_FAILED",
                        reason,
                    )
                },
            }

        validated_company_name = (
            user_company_name
            or resolved_company_name
            or requested_ticker
        )

        company = dict(state["company"])
        company["ticker"] = requested_ticker
        company["company_name"] = validated_company_name
        company["status"] = "COMPLETED"

        return {
            "company": company,
            "identity_validation": {
                "requested_ticker": requested_ticker,
                "user_company_name": user_company_name,
                "resolved_company_name": resolved_company_name,
                "validated_company_name": validated_company_name,
                "ticker_match": True,
                "company_name_match": True,
                "mismatch_detected": False,
                "validation_reason": (
                    f"Identity validated for {validated_company_name} "
                    f"({requested_ticker})."
                ),
                "confidence": 0.95 if user_company_name else 0.75,
                "status": "COMPLETED",
            },
            "execution": _execution_update(
                state,
                node,
                "IDENTITY_VALIDATION",
                10,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "IDENTITY_VALIDATED",
                    (
                        f"Validated identity as {validated_company_name} "
                        f"({requested_ticker})."
                    ),
                )
            },
        }

    except (KeyError, ValueError, TypeError) as exc:
        return {
            "identity_validation": {
                "requested_ticker": state.get("request", {}).get(
                    "ticker",
                    "",
                ),
                "user_company_name": state.get("request", {}).get(
                    "user_company_name",
                    "",
                ),
                "resolved_company_name": state.get("company", {}).get(
                    "company_name",
                    "",
                ),
                "validated_company_name": "",
                "ticker_match": False,
                "company_name_match": False,
                "mismatch_detected": True,
                "validation_reason": str(exc),
                "confidence": 0.0,
                "status": "FAILED",
            },
            "execution": _execution_update(
                state,
                node,
                "IDENTITY_VALIDATION",
                10,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "IDENTITY_VALIDATION_FAILED",
                    str(exc),
                )
            },
        }

#E2B data extraction

HISTORICAL_FINANCIALS_TIMEOUT = 180


def _historical_financials_code(
    ticker: str,
) -> str:
    return f"""
import json
import subprocess
import sys
from datetime import datetime, timezone

ticker = {ticker!r}

try:
    import pandas as pd
    import yfinance as yf
except Exception:
    subprocess.check_call([
        sys.executable,
        "-m",
        "pip",
        "install",
        "-q",
        "yfinance",
        "pandas",
    ])
    import pandas as pd
    import yfinance as yf


def df_to_records(df):
    if df is None or getattr(df, "empty", True):
        return []

    cleaned = df.copy()
    cleaned.index = cleaned.index.map(str)
    cleaned.columns = cleaned.columns.map(str)

    records = []

    for metric, row in cleaned.iterrows():
        values = {{}}

        for period, value in row.items():
            if pd.isna(value):
                values[str(period)] = None
            else:
                try:
                    values[str(period)] = float(value)
                except Exception:
                    values[str(period)] = str(value)

        records.append({{
            "metric": str(metric),
            "values": values,
        }})

    return records


stock = yf.Ticker(ticker)

info = {{}}

try:
    info = stock.get_info()
except Exception:
    try:
        info = stock.info or {{}}
    except Exception:
        info = {{}}

payload = {{
    "provider": "yfinance",
    "ticker": ticker,
    "company_name": info.get("longName") or info.get("shortName") or "",
    "currency": (
        info.get("financialCurrency")
        or info.get("currency")
        or ""
    ),
    "retrieved_at": datetime.now(timezone.utc).isoformat(),
    "annual": {{
        "income_statement": df_to_records(stock.financials),
        "balance_sheet": df_to_records(stock.balance_sheet),
        "cash_flow": df_to_records(stock.cashflow),
    }},
    "quarterly": {{
        "income_statement": df_to_records(stock.quarterly_financials),
        "balance_sheet": df_to_records(stock.quarterly_balance_sheet),
        "cash_flow": df_to_records(stock.quarterly_cashflow),
    }},
}}

print("__RAW_FINANCIALS_JSON_START__")
print(json.dumps(payload, ensure_ascii=False))
print("__RAW_FINANCIALS_JSON_END__")
"""


def _extract_raw_financials_json(
    text: str,
) -> str:
    start_marker = "__RAW_FINANCIALS_JSON_START__"
    end_marker = "__RAW_FINANCIALS_JSON_END__"

    start = text.find(start_marker)
    end = text.find(end_marker)

    if start == -1 or end == -1 or end <= start:
        raise ValueError(
            "Historical financials sandbox did not return JSON markers."
        )

    payload = text[start + len(start_marker):end].strip()

    json.loads(payload)

    return payload


async def _kill_sandbox(
    sandbox: Any,
) -> None:
    try:
        result = sandbox.kill()

        if inspect.isawaitable(result):
            await result

    except Exception:
        pass


async def _collect_raw_financials_with_e2b(
    ticker: str,
) -> dict[str, Any]:

    
    try:
        from e2b_code_interpreter import AsyncSandbox

    except ImportError as exc:
        raise ValueError(
            "Missing dependency: install e2b-code-interpreter."
        ) from exc

    sandbox = await AsyncSandbox.create(
        timeout=HISTORICAL_FINANCIALS_TIMEOUT,
    )

    try:
        code = _historical_financials_code(ticker)

        if hasattr(sandbox, "run_code"):
            execution = await sandbox.run_code(
                code,
                timeout=HISTORICAL_FINANCIALS_TIMEOUT,
                request_timeout=HISTORICAL_FINANCIALS_TIMEOUT,
            )
        elif hasattr(sandbox, "runCode"):
            execution = await sandbox.runCode(code)
        else:
            raise AttributeError(
                "E2B sandbox has neither run_code nor runCode. "
                "Install/upgrade e2b-code-interpreter and import "
                "AsyncSandbox from e2b_code_interpreter."
            )

        if execution.error is not None:
            raise ValueError(
                f"E2B historical financials execution failed: "
                f"{execution.error}"
            )

        logs = ""

        if execution.logs is not None:
            stdout = getattr(execution.logs, "stdout", []) or []
            stderr = getattr(execution.logs, "stderr", []) or []
            logs = "\\n".join([*stdout, *stderr])

        text = logs or execution.text or ""

        raw_json = _extract_raw_financials_json(text)
        payload = json.loads(raw_json)

        return {
            "provider": payload.get("provider", "yfinance"),
            "ticker": payload.get("ticker", ticker),
            "company_name": payload.get("company_name", ""),
            "currency": payload.get("currency", ""),
            "raw_financials": raw_json,
            "retrieved_at": payload.get("retrieved_at", _now()),
            "status": "COMPLETED",
            "error": "",
        }

    finally:
        await _kill_sandbox(sandbox)

async def historical_financials_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "historical_financials_node"

    await _emit_progress(
        "Collecting historical financial statements",
        14,
    )

    try:
        ticker = state["request"]["ticker"]

        raw_financials = await _collect_raw_financials_with_e2b(
            ticker=ticker,
        )

        if not raw_financials["raw_financials"]:
            raise ValueError(
                "Historical financials collector returned empty data."
            )

        return {
            "raw_financials": raw_financials,
            "execution": _execution_update(
                state,
                node,
                "HISTORICAL_FINANCIALS",
                14,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "HISTORICAL_FINANCIALS_COLLECTED",
                    (
                        f"Collected annual and quarterly historical financial statements "
                        f"from {raw_financials.get('provider', 'unknown')} for "
                        f"{raw_financials.get('ticker', ticker)} / "
                        f"{raw_financials.get('company_name', 'unknown company')} "
                        f"in {raw_financials.get('currency', 'unknown currency')}."
                    ),
                )
            },
        }

    except (KeyError, ValueError) as exc:
        return {
            "raw_financials": {
                "provider": "",
                "ticker": state["request"].get("ticker", ""),
                "company_name": "",
                "currency": "",
                "raw_financials": "",
                "retrieved_at": _now(),
                "status": "FAILED",
                "error": str(exc),
            },
            "execution": _execution_update(
                state,
                node,
                "HISTORICAL_FINANCIALS",
                14,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "HISTORICAL_FINANCIALS_FAILED",
                    str(exc),
                )
            },
        }



RAW_FINANCIALS_SOURCE_ID = "RAW-FINANCIALS-YFINANCE"


RAW_METRIC_MAP: dict[str, tuple[str, str, str]] = {
    # Income statement
    "total revenue": (
        "revenue",
        "INCOME_STATEMENT",
        "Total revenue as reported by historical financial data provider.",
    ),
    "operating revenue": (
        "revenue",
        "INCOME_STATEMENT",
        "Operating revenue as reported by historical financial data provider.",
    ),
    "cost of revenue": (
        "cost_of_revenue",
        "INCOME_STATEMENT",
        "Cost of revenue as reported by historical financial data provider.",
    ),
    "gross profit": (
        "gross_profit",
        "INCOME_STATEMENT",
        "Gross profit as reported by historical financial data provider.",
    ),
    "operating income": (
        "operating_income",
        "INCOME_STATEMENT",
        "Operating income as reported by historical financial data provider.",
    ),
    "total operating income as reported": (
        "operating_income",
        "INCOME_STATEMENT",
        "Operating income as reported by historical financial data provider.",
    ),
    "ebit": (
        "ebit",
        "INCOME_STATEMENT",
        "Earnings before interest and taxes.",
    ),
    "ebitda": (
        "ebitda",
        "INCOME_STATEMENT",
        "Earnings before interest, taxes, depreciation, and amortization.",
    ),
    "interest expense": (
        "interest_expense",
        "INCOME_STATEMENT",
        "Interest expense as reported by historical financial data provider.",
    ),
    "interest expense non operating": (
        "interest_expense",
        "INCOME_STATEMENT",
        "Non-operating interest expense as reported.",
    ),
    "pretax income": (
        "pretax_income",
        "INCOME_STATEMENT",
        "Pretax income as reported by historical financial data provider.",
    ),
    "tax provision": (
        "income_tax_expense",
        "INCOME_STATEMENT",
        "Income tax expense or tax provision as reported.",
    ),
    "net income": (
        "reported_net_income",
        "INCOME_STATEMENT",
        "Reported net income.",
    ),
    "net income common stockholders": (
        "reported_net_income",
        "INCOME_STATEMENT",
        "Net income attributable to common stockholders.",
    ),
    "diluted eps": (
        "diluted_eps",
        "INCOME_STATEMENT",
        "Diluted earnings per share.",
    ),
    "basic eps": (
        "basic_eps",
        "INCOME_STATEMENT",
        "Basic earnings per share.",
    ),
    "diluted average shares": (
        "shares_outstanding",
        "INCOME_STATEMENT",
        "Diluted weighted average shares outstanding.",
    ),
    "basic average shares": (
        "shares_outstanding",
        "INCOME_STATEMENT",
        "Basic weighted average shares outstanding.",
    ),

    # Balance sheet
    "cash and cash equivalents": (
        "cash_and_cash_equivalents",
        "BALANCE_SHEET",
        "Cash and cash equivalents.",
    ),
    "cash cash equivalents and short term investments": (
        "cash_and_cash_equivalents",
        "BALANCE_SHEET",
        "Cash, cash equivalents, and short-term investments.",
    ),
    "current assets": (
        "total_current_assets",
        "BALANCE_SHEET",
        "Total current assets.",
    ),
    "total assets": (
        "total_assets",
        "BALANCE_SHEET",
        "Total assets.",
    ),
    "current liabilities": (
        "total_current_liabilities",
        "BALANCE_SHEET",
        "Total current liabilities.",
    ),
    "total liabilities net minority interest": (
        "total_liabilities",
        "BALANCE_SHEET",
        "Total liabilities.",
    ),
    "total debt": (
        "total_debt",
        "BALANCE_SHEET",
        "Total debt.",
    ),
    "current debt": (
        "short_term_debt",
        "BALANCE_SHEET",
        "Current debt.",
    ),
    "current debt and capital lease obligation": (
        "short_term_debt",
        "BALANCE_SHEET",
        "Current debt and current lease obligations.",
    ),
    "long term debt": (
        "long_term_debt",
        "BALANCE_SHEET",
        "Long-term debt.",
    ),
    "long term debt and capital lease obligation": (
        "long_term_debt",
        "BALANCE_SHEET",
        "Long-term debt and lease obligations.",
    ),
    "stockholders equity": (
        "shareholder_equity",
        "BALANCE_SHEET",
        "Stockholders' equity.",
    ),
    "common stock equity": (
        "shareholder_equity",
        "BALANCE_SHEET",
        "Common stockholders' equity.",
    ),
    "ordinary shares number": (
        "shares_outstanding",
        "BALANCE_SHEET",
        "Ordinary shares outstanding.",
    ),
    "share issued": (
        "shares_outstanding",
        "BALANCE_SHEET",
        "Shares issued.",
    ),

    # Cash flow
    "operating cash flow": (
        "operating_cash_flow",
        "CASH_FLOW",
        "Operating cash flow.",
    ),
    "cash flow from continuing operating activities": (
        "operating_cash_flow",
        "CASH_FLOW",
        "Cash flow from continuing operating activities.",
    ),
    "capital expenditure": (
        "capital_expenditure",
        "CASH_FLOW",
        "Capital expenditure.",
    ),
    "purchase of ppe": (
        "capital_expenditure",
        "CASH_FLOW",
        "Purchase of property, plant, and equipment.",
    ),
    "free cash flow": (
        "free_cash_flow",
        "CASH_FLOW",
        "Free cash flow.",
    ),
    "cash dividends paid": (
        "dividends_paid",
        "CASH_FLOW",
        "Cash dividends paid.",
    ),
}


def _normalize_raw_label(label: str) -> str:
    value = label.replace("_", " ").replace("-", " ").strip().lower()
    return re.sub(r"\s+", " ", value)


def _fiscal_year_end_month(fiscal_year_end: str) -> int:
    text = str(fiscal_year_end or "").strip().lower()

    month_map = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }

    for key, month in month_map.items():
        if key in text:
            return month

    number_match = re.search(r"\b(1[0-2]|0?[1-9])\b", text)

    if number_match:
        return int(number_match.group(1))

    return 12


def _period_from_raw_date(
    raw_period: str,
    period_type: str,
    fiscal_year_end: str,
) -> str:
    text = str(raw_period)

    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)

    if not match:
        year_match = re.search(r"(\d{4})", text)

        if not year_match:
            return text

        year = int(year_match.group(1))

        if period_type == "quarterly":
            return f"Q? FY{year}"

        return f"FY{year}"

    year = int(match.group(1))
    month = int(match.group(2))

    if period_type != "quarterly":
        return f"FY{year}"

    fy_end_month = _fiscal_year_end_month(fiscal_year_end)
    fiscal_start_month = (fy_end_month % 12) + 1

    fiscal_year = year if month <= fy_end_month else year + 1
    month_offset = (month - fiscal_start_month) % 12
    fiscal_quarter = (month_offset // 3) + 1

    return f"Q{fiscal_quarter} FY{fiscal_year}"


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text or text.lower() in {"none", "null", "nan", "--"}:
        return None

    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _ensure_raw_financials_source(
    sources: list[dict[str, Any]],
    provider: str,
    retrieved_at: str,
) -> list[dict[str, Any]]:
    if any(source["source_id"] == RAW_FINANCIALS_SOURCE_ID for source in sources):
        return sources

    return [
        *sources,
        {
            "source_id": RAW_FINANCIALS_SOURCE_ID,
            "title": f"Historical financial statements from {provider}",
            "url": "",
            "publisher": provider,
            "source_type": "MARKET_DATA_PROVIDER",
            "fiscal_period": "",
            "published_date": "",
            "retrieved_at": retrieved_at or _now(),
            "authority_rank": 50,
            "primary_source": False,
            "status": "COMPLETED",
        },
    ]


def _iter_statement_records(
    raw_payload: dict[str, Any],
) -> list[tuple[str, str, dict[str, Any]]]:
    records: list[tuple[str, str, dict[str, Any]]] = []

    for period_type in ("annual", "quarterly"):
        section = raw_payload.get(period_type, {})

        if not isinstance(section, dict):
            continue

        for statement_name in (
            "income_statement",
            "balance_sheet",
            "cash_flow",
        ):
            statement_records = section.get(statement_name, [])

            if not isinstance(statement_records, list):
                continue

            for record in statement_records:
                if isinstance(record, dict):
                    records.append(
                        (
                            period_type,
                            statement_name,
                            record,
                        )
                    )

    return records


def _build_facts_from_raw_financials(
    state: FinancialResearchState,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    raw_state = state["raw_financials"]
    raw_text = raw_state["raw_financials"]

    if not raw_text:
        raise ValueError("No raw historical financial data available.")

    try:
        raw_payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"raw_financials is not valid JSON: {exc}"
        ) from exc

    provider = raw_payload.get("provider") or raw_state.get("provider") or "unknown"
    currency = (
        raw_payload.get("currency")
        or raw_state.get("currency")
        or state["company"].get("currency")
        or "USD"
    ).upper()

    retrieved_at = raw_payload.get("retrieved_at") or raw_state.get("retrieved_at") or _now()

    sources = _ensure_raw_financials_source(
        sources=state["sources"]["sources"],
        provider=provider,
        retrieved_at=retrieved_at,
    )

    evidence_items: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []

    seen_fact_keys: set[tuple[str, str, str, str, str, str]] = set()

    evidence_index = 1
    fact_index = 1

    for period_type, statement_name, record in _iter_statement_records(raw_payload):
        raw_label = str(record.get("metric", "")).strip()
        label_key = _normalize_raw_label(raw_label)

        mapping = RAW_METRIC_MAP.get(label_key)

        if mapping is None:
            continue

        metric_key, statement_type, definition = mapping

        values = record.get("values", {})

        if not isinstance(values, dict):
            continue

        for raw_period, raw_value in values.items():
            value = _safe_float(raw_value)

            if value is None:
                continue

            period = _period_from_raw_date(
                raw_period=raw_period,
                period_type=period_type,
                fiscal_year_end=state["company"].get("fiscal_year_end", ""),
            )

            fact_key = (
                metric_key,
                period,
                "CONSOLIDATED",
                currency,
                "BASE",
                statement_type,
            )

            if fact_key in seen_fact_keys:
                continue

            seen_fact_keys.add(fact_key)

            evidence_id = f"EVIDENCE-RAW-{evidence_index:05d}"
            fact_id = f"FACT-RAW-{fact_index:05d}"

            evidence_index += 1
            fact_index += 1

            evidence_items.append({
                "evidence_id": evidence_id,
                "source_id": RAW_FINANCIALS_SOURCE_ID,
                "extracted_by": "FinancialStatementAgent",
                "statement_type": statement_type,
                "metric_key": metric_key,
                "raw_label": raw_label,
                "raw_value": value,
                "unit": "BASE",
                "currency": currency,
                "period": period,
                "scope": "CONSOLIDATED",
                "definition": definition,
                "confidence": 0.92 if period_type == "annual" else 0.86,
                "created_at": _now(),
            })

            facts.append({
                "fact_id": fact_id,
                "metric_key": metric_key,
                "value": value,
                "unit": "BASE",
                "currency": currency,
                "period": period,
                "scope": "CONSOLIDATED",
                "statement_type": statement_type,
                "evidence_ids": [evidence_id],
                "source_ids": [RAW_FINANCIALS_SOURCE_ID],
                "normalized_definition": definition,
                "accounting_signals": [],
                "confidence": 0.92 if period_type == "annual" else 0.86,
                "status": "NORMALIZED",
                "created_by": "FinancialStatementAgent",
                "version": 1,
            })

    if not facts:
        raise ValueError(
            "No deterministic financial facts could be built from raw_financials."
        )

    return sources, evidence_items, facts


async def source_research_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "source_research_node"

    await _emit_progress(
        "Searching and scraping current financial context",
        18,
    )

    try:
        ticker = state["request"]["ticker"]

        company_name = (
            state.get("request", {}).get("user_company_name")
            or state.get("request", {}).get("original_company_name")
            or state.get("company", {}).get("company_name")
            or ticker
        )

        # Company-name locked queries reduce ambiguous ticker mistakes.
        # Example: LX alone can match unrelated companies, but
        # "LexinFintech Holdings Ltd." "LX" is much safer.
        search_queries = [
            (
                f'"{company_name}" "{ticker}" '
                "full year results annual report"
            ),
            (
                f'"{company_name}" "{ticker}" '
                "investor relations financial results"
            ),
            (
                f'"{company_name}" "{ticker}" '
                "SEC filings annual report"
            ),
            (
                f'"{company_name}" "{ticker}" '
                "quarterly results earnings release"
            ),
        ]

        search_results = await _collect_search_results(
            search_queries,
        )

        filtered_results: list[dict[str, Any]] = []
        rejected_results: list[str] = []

        for result in search_results:
            # Search tools may return either "url" or "link".
            # Normalize here so filtering and scraping use the same source.
            url = (
                result.get("url")
                or result.get("link")
                or result.get("href")
                or ""
            )
            title = (
                result.get("title")
                or result.get("name")
                or ""
            )
            snippet = (
                result.get("snippet")
                or result.get("description")
                or result.get("summary")
                or ""
            )

            if _source_matches_company_identity(
                url=url,
                title=title,
                snippet=snippet,
                company_name=company_name,
                ticker=ticker,
            ):
                normalized_result = {
                    **result,
                    "url": url,
                    "link": url,
                    "title": title,
                    "snippet": snippet,
                }
                filtered_results.append(normalized_result)
            else:
                rejected_results.append(url)

        # Important:
        # Do not scrape rejected sources. This prevents cases like
        # LX accidentally scraping Exelon investor pages.
        search_results = filtered_results

        sources, source_texts = await _scrape_financial_sources(
            search_results,
        )

        rejected_source_count = len(rejected_results)

        return {
            "sources": {
                "sources": [
                    *state["sources"]["sources"],
                    *sources,
                ],
            },
            "source_texts": {
                "texts": source_texts,
            },
            "execution": _execution_update(
                state,
                node,
                "SOURCE_RESEARCH",
                18,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "SOURCES_DISCOVERED",
                    (
                        f"Registered {len(sources)} current-context "
                        f"source documents and scraped {len(source_texts)} "
                        f"source texts. Rejected {rejected_source_count} "
                        "search results that did not match company identity."
                    ),
                )
            },
        }

    except Exception as exc:
        # Current web context is useful but not mandatory.
        # Historical structured financials remain the primary source of
        # deterministic facts, so this node should not crash the graph.
        return {
            "sources": state["sources"],
            "source_texts": {
                "texts": [],
            },
            "execution": _execution_update(
                state,
                node,
                "SOURCE_RESEARCH",
                18,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "SOURCE_RESEARCH_SKIPPED",
                    (
                        "Current web source research failed, returned weak "
                        "context, or produced no identity-safe sources. "
                        f"Historical financial data remains primary. {exc}"
                    ),
                )
            },
        }

BANK_PERCENT_KPI_PATTERNS: dict[str, list[str]] = {
    "net_interest_margin": [
        r"\bNIM\b[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
        r"net\s+interest\s+margin[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
    ],
    "gross_npa_ratio": [
        r"\bGNPA\b[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
        r"gross\s+NPA[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
        r"gross\s+non[-\s]?performing\s+assets[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
    ],
    "net_npa_ratio": [
        r"\bNNPA\b[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
        r"net\s+NPA[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
        r"net\s+non[-\s]?performing\s+assets[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
    ],
    "provision_coverage_ratio": [
        r"\bPCR\b[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
        r"provision\s+coverage\s+ratio[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
    ],
    "capital_adequacy_ratio": [
        r"\bCRAR\b[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
        r"capital\s+adequacy\s+ratio[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
        r"\bCAR\b[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
    ],
    "casa_ratio": [
        r"\bCASA\b[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
        r"current\s+account\s+savings\s+account[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
    ],
}

BANK_MONEY_KPI_PATTERNS: dict[str, list[str]] = {
    "deposits": [
        r"\bdeposits?\b[^₹$0-9]{0,60}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(lakh crore|lac crore|crore|crores|cr\.?|billion|million)",
        r"total\s+deposits?[^₹$0-9]{0,60}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(lakh crore|lac crore|crore|crores|cr\.?|billion|million)",
    ],
    "advances": [
        r"\badvances?\b[^₹$0-9]{0,60}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(lakh crore|lac crore|crore|crores|cr\.?|billion|million)",
        r"gross\s+advances?[^₹$0-9]{0,60}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(lakh crore|lac crore|crore|crores|cr\.?|billion|million)",
        r"loans?\s+and\s+advances?[^₹$0-9]{0,60}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(lakh crore|lac crore|crore|crores|cr\.?|billion|million)",
    ],
}

def _snippet_around_match(
    text: str,
    start: int,
    end: int,
    window: int = 240,
) -> str:
    left = max(
        0,
        start - window,
    )

    right = min(
        len(text),
        end + window,
    )

    return " ".join(
        text[left:right].split()
    )

def _parse_indian_money_to_crore(
    raw_value: str,
    raw_unit: str,
) -> float | None:
    value = _safe_float(
        str(raw_value).replace(",", "")
    )

    if value is None:
        return None

    unit = str(
        raw_unit or ""
    ).strip().lower()

    if "lakh crore" in unit:
        return value * 100000

    if "lac crore" in unit:
        return value * 100000

    if "crore" in unit:
        return value

    if unit in {"cr", "cr.", "crores"}:
        return value

    if "million" in unit:
        return value / 10

    if "billion" in unit:
        return value * 100

    return value

def _parse_percent_value(
    raw_value: str,
) -> float | None:
    value = _safe_float(
        str(raw_value).replace(",", "")
    )

    if value is None:
        return None

    return value


def _bank_kpi_fact(
    metric_key: str,
    value: float,
    unit: str,
    currency: str,
    period: str,
    evidence_id: str,
    source_id: str,
) -> dict[str, Any]:
    return {
        "fact_id": (
            f"bank_kpi::{metric_key}::{period}"
        ),
        "metric_key": metric_key,
        "value": value,
        "unit": unit,
        "currency": currency,
        "period": period,
        "scope": "CONSOLIDATED",
        "statement_type": "BANK_KPI",
        "evidence_ids": [
            evidence_id,
        ],
        "source_ids": [
            source_id,
        ],
        "normalized_definition": metric_key,
        "confidence": 0.78,
        "status": "COMPLETED",
        "created_by": "bank_kpi_regex_extractor",
        "version": 1,
    }

def _bank_kpi_evidence(
    evidence_id: str,
    source_id: str,
    metric_key: str,
    snippet: str,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source_id": source_id,
        "quote": snippet,
        "context": f"Regex extracted bank KPI: {metric_key}",
        "relevance": "BANK_KPI",
        "confidence": 0.78,
        "status": "COMPLETED",
    }

def _source_id_for_text_index(
    state: FinancialResearchState,
    index: int,
) -> str:
    sources = state.get(
        "sources",
        {},
    ).get(
        "sources",
        [],
    )

    if index < len(sources):
        return str(
            sources[index].get("source_id", f"source_text_{index}")
        )

    return f"source_text_{index}"





async def financial_fact_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "financial_fact_node"

    await _emit_progress(
        "Building deterministic financial facts from historical statements",
        32,
    )

    try:
        if state["raw_financials"]["status"] != "COMPLETED":
            raise ValueError(
                "Historical financial data is missing or failed."
            )

        if not state["raw_financials"]["raw_financials"]:
            raise ValueError(
                "No raw historical financial data available."
            )

        sources, evidence, facts = _build_facts_from_raw_financials(
            state,
        )

        bank_kpi_facts: list[dict[str, Any]] = []
        bank_kpi_evidence: list[dict[str, Any]] = []

        if _is_bank_framework(state):
            source_texts = state.get(
                "source_texts",
                {},
            ).get(
                "texts",
                [],
            )

            existing_sources = state.get(
                "sources",
                {},
            ).get(
                "sources",
                [],
            )

            periods = list({
                str(fact.get("period", ""))
                for fact in facts
                if fact.get("period")
            })

            latest_annual = _latest_period(
                periods,
                annual_only=True,
            )

            reporting_currency = _reporting_currency(
                state,
            )

            percent_patterns: dict[str, list[str]] = {
                "net_interest_margin": [
                    r"\bNIM\b[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                    r"net\s+interest\s+margin[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                ],
                "gross_npa_ratio": [
                    r"\bGNPA\b[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                    r"gross\s+NPA[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                    r"gross\s+non[-\s]?performing\s+assets[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                ],
                "net_npa_ratio": [
                    r"\bNNPA\b[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                    r"net\s+NPA[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                    r"net\s+non[-\s]?performing\s+assets[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                ],
                "provision_coverage_ratio": [
                    r"\bPCR\b[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                    r"provision\s+coverage\s+ratio[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                ],
                "capital_adequacy_ratio": [
                    r"\bCRAR\b[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                    r"capital\s+adequacy\s+ratio[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                    r"\bCAR\b[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                ],
                "casa_ratio": [
                    r"\bCASA\b[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                    r"current\s+account\s+savings\s+account[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%",
                ],
            }

            money_patterns: dict[str, list[str]] = {
                "deposits": [
                    r"\bdeposits?\b[^₹$0-9]{0,80}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(lakh crore|lac crore|crore|crores|cr\.?|billion|million)",
                    r"total\s+deposits?[^₹$0-9]{0,80}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(lakh crore|lac crore|crore|crores|cr\.?|billion|million)",
                ],
                "advances": [
                    r"\badvances?\b[^₹$0-9]{0,80}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(lakh crore|lac crore|crore|crores|cr\.?|billion|million)",
                    r"gross\s+advances?[^₹$0-9]{0,80}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(lakh crore|lac crore|crore|crores|cr\.?|billion|million)",
                    r"loans?\s+and\s+advances?[^₹$0-9]{0,80}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(lakh crore|lac crore|crore|crores|cr\.?|billion|million)",
                ],
            }

            def _source_id_for_text_index(
                index: int,
            ) -> str:
                if index < len(existing_sources):
                    return str(
                        existing_sources[index].get(
                            "source_id",
                            f"source_text_{index}",
                        )
                    )

                return f"source_text_{index}"

            def _snippet(
                text: str,
                start: int,
                end: int,
                window: int = 220,
            ) -> str:
                left = max(
                    0,
                    start - window,
                )

                right = min(
                    len(text),
                    end + window,
                )

                return " ".join(
                    text[left:right].split()
                )

            def _parse_number(
                value: str,
            ) -> float | None:
                return _safe_float(
                    str(value).replace(",", "")
                )

            def _money_to_crore(
                value: str,
                unit: str,
            ) -> float | None:
                parsed_value = _parse_number(
                    value,
                )

                if parsed_value is None:
                    return None

                normalized_unit = str(
                    unit or ""
                ).strip().lower()

                if normalized_unit in {
                    "cr",
                    "cr.",
                    "crore",
                    "crores",
                }:
                    return parsed_value

                if "lakh crore" in normalized_unit:
                    return parsed_value * 100000

                if "lac crore" in normalized_unit:
                    return parsed_value * 100000

                if "billion" in normalized_unit:
                    return parsed_value * 100

                if "million" in normalized_unit:
                    return parsed_value / 10

                return parsed_value

            def _make_bank_kpi_evidence(
                metric_key: str,
                source_id: str,
                raw_value: float,
                unit: str,
                currency: str,
                period: str,
                snippet: str,
            ) -> dict[str, Any]:
                evidence_id = (
                    f"bank_kpi_evidence::{metric_key}::{period}"
                )

                return {
                    "evidence_id": evidence_id,
                    "source_id": source_id,
                    "extracted_by": "FinancialStatementAgent",
                    "statement_type": "BANK_KPI",
                    "metric_key": metric_key,
                    "raw_label": metric_key,
                    "raw_value": raw_value,
                    "unit": unit,
                    "currency": currency,
                    "period": period,
                    "scope": "CONSOLIDATED",
                    "definition": snippet,
                    "confidence": 0.78,
                    "created_at": _now(),
                }

            def _make_bank_kpi_fact(
                metric_key: str,
                value: float,
                unit: str,
                currency: str,
                evidence_id: str,
                source_id: str,
            ) -> dict[str, Any]:
                return {
                    "fact_id": (
                        f"bank_kpi::{metric_key}::{latest_annual}"
                    ),
                    "metric_key": metric_key,
                    "value": value,
                    "unit": unit,
                    "currency": currency,
                    "period": latest_annual,
                    "scope": "CONSOLIDATED",
                    "statement_type": "BANK_KPI",
                    "evidence_ids": [
                        evidence_id,
                    ],
                    "source_ids": [
                        source_id,
                    ],
                    "normalized_definition": metric_key,
                    "confidence": 0.78,
                    "status": "NORMALIZED",
                    "created_by": "FinancialStatementAgent",
                    "version": 1,
                }

            existing_fact_keys = {
                (
                    str(fact.get("metric_key", "")),
                    str(fact.get("period", "")),
                )
                for fact in facts
            }

            extracted_metric_keys: set[str] = set()

            if latest_annual:
                for text_index, source_text in enumerate(source_texts):
                    if isinstance(source_text, dict):
                        text = str(
                            source_text.get("text", "")
                        )

                        source_id = str(
                            source_text.get(
                                "source_id",
                                f"source_text_{text_index}",
                            )
                        )

                    else:
                        text = str(
                            source_text or ""
                        )

                        source_id = f"source_text_{text_index}"

                    if not text.strip():
                        continue

                    for metric_key, patterns in percent_patterns.items():
                        fact_key = (
                            metric_key,
                            latest_annual,
                        )

                        if (
                            metric_key in extracted_metric_keys
                            or fact_key in existing_fact_keys
                        ):
                            continue

                        for pattern in patterns:
                            match = re.search(
                                pattern,
                                text,
                                flags=re.IGNORECASE,
                            )

                            if not match:
                                continue

                            value = _parse_number(
                                match.group(1),
                            )

                            if value is None:
                                continue

                            evidence_item = _make_bank_kpi_evidence(
                                metric_key=metric_key,
                                source_id=source_id,
                                raw_value=value,
                                unit="%",
                                currency="",
                                period=latest_annual,
                                snippet=_snippet(
                                    text,
                                    match.start(),
                                    match.end(),
                                ),
                            )

                            fact_item = _make_bank_kpi_fact(
                                metric_key=metric_key,
                                value=value,
                                unit="%",
                                currency="",
                                evidence_id=evidence_item["evidence_id"],
                                source_id=source_id,
                            )

                            bank_kpi_evidence.append(
                                evidence_item,
                            )

                            bank_kpi_facts.append(
                                fact_item,
                            )

                            extracted_metric_keys.add(
                                metric_key,
                            )

                            break

                    for metric_key, patterns in money_patterns.items():
                        fact_key = (
                            metric_key,
                            latest_annual,
                        )

                        if (
                            metric_key in extracted_metric_keys
                            or fact_key in existing_fact_keys
                        ):
                            continue

                        for pattern in patterns:
                            match = re.search(
                                pattern,
                                text,
                                flags=re.IGNORECASE,
                            )

                            if not match:
                                continue

                            value = _money_to_crore(
                                match.group(1),
                                match.group(2),
                            )

                            if value is None:
                                continue

                            evidence_item = _make_bank_kpi_evidence(
                                metric_key=metric_key,
                                source_id=source_id,
                                raw_value=value,
                                unit="Cr",
                                currency=reporting_currency,
                                period=latest_annual,
                                snippet=_snippet(
                                    text,
                                    match.start(),
                                    match.end(),
                                ),
                            )

                            fact_item = _make_bank_kpi_fact(
                                metric_key=metric_key,
                                value=value,
                                unit="Cr",
                                currency=reporting_currency,
                                evidence_id=evidence_item["evidence_id"],
                                source_id=source_id,
                            )

                            bank_kpi_evidence.append(
                                evidence_item,
                            )

                            bank_kpi_facts.append(
                                fact_item,
                            )

                            extracted_metric_keys.add(
                                metric_key,
                            )

                            break

            facts = [
                *facts,
                *bank_kpi_facts,
            ]

            evidence = [
                *evidence,
                *bank_kpi_evidence,
            ]

        deduped_facts: list[dict[str, Any]] = []
        seen_fact_keys: set[tuple[str, str, str]] = set()

        for fact in facts:
            fact_key = (
                str(fact.get("metric_key", "")),
                str(fact.get("period", "")),
                str(fact.get("statement_type", "")),
            )

            if fact_key in seen_fact_keys:
                continue

            seen_fact_keys.add(
                fact_key,
            )

            deduped_facts.append(
                fact,
            )

        facts = deduped_facts

        normalized_fact_ids = [
            fact["fact_id"]
            for fact in facts
            if fact.get("status") == "NORMALIZED"
        ]

        rejected_fact_ids = [
            fact["fact_id"]
            for fact in facts
            if fact.get("status") == "REJECTED"
        ]

        combined_sources = [
            *state.get("sources", {}).get("sources", []),
            *sources,
        ]

        deduped_sources: list[dict[str, Any]] = []
        seen_source_ids: set[str] = set()

        for source in combined_sources:
            source_id = str(
                source.get("source_id", "")
            )

            if source_id and source_id in seen_source_ids:
                continue

            if source_id:
                seen_source_ids.add(
                    source_id,
                )

            deduped_sources.append(
                source,
            )

        return {
            "sources": {
                "sources": deduped_sources,
            },
            "financial_facts": {
                "facts": facts,
                "normalized_fact_ids": normalized_fact_ids,
                "reconciled_fact_ids": [],
                "rejected_fact_ids": rejected_fact_ids,
            },
            "evidence": {
                "evidence": evidence,
            },
            "execution": _execution_update(
                state,
                node,
                "FACT_EXTRACTION",
                32,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "FACTS_BUILT_DETERMINISTICALLY",
                    (
                        f"Built {len(facts)} normalized financial facts "
                        f"and {len(evidence)} evidence records from raw "
                        f"historical financial statements. Bank KPI extractor "
                        f"added {len(bank_kpi_facts)} facts and "
                        f"{len(bank_kpi_evidence)} evidence records."
                    ),
                )
            },
        }

    except (KeyError, ValueError, TypeError) as exc:
        return {
            "execution": _execution_update(
                state,
                node,
                "FACT_EXTRACTION",
                32,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "FACT_EXTRACTION_FAILED",
                    str(exc),
                )
            },
        }


async def reconciliation_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "reconciliation_node"

    await _emit_progress("Reconciling financial facts")

    try:
        if not state["financial_facts"]["facts"]:
            return {
                "reconciliation": {
                    "conflicts": [],
                    "unresolved_conflict_ids": [],
                    "resolved_conflict_ids": [],
                    "critical_conflict": True,
                    "status": "FAILED",
                },
                "execution": _execution_update(
                    state,
                    node,
                    "RECONCILIATION",
                    42,
                    "FAILED",
                ),
                "audit": {
                    "events": _audit(
                        state,
                        node,
                        "RECONCILIATION_FAILED",
                        "No financial facts available for reconciliation.",
                    )
                },
            }
        reconciliation = reconcile_facts(
            facts=state["financial_facts"]["facts"],
            sources=state["sources"]["sources"],
        )

        review_control = dict(state["review_control"])

        unresolved_count = len(
            reconciliation["unresolved_conflict_ids"]
        )

        if unresolved_count:
            review_control["reviewer_attempts"] += 1
            review_control["active_cycle"] = "RECONCILIATION"
        else:
            review_control["active_cycle"] = ""

        return {
            "reconciliation": reconciliation,
            "review_control": review_control,
            "execution": _execution_update(
                state,
                node,
                "RECONCILIATION",
                42,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "FACTS_RECONCILED",
                    (
                        f"Reconciliation completed with "
                        f"{unresolved_count} unresolved conflicts. "
                        f"Reviewer attempts: "
                        f"{review_control['reviewer_attempts']}."
                    ),
                )
            },
        }

    except (KeyError, ValueError, ArithmeticError) as exc:
        return {
            "execution": _execution_update(
                state,
                node,
                "RECONCILIATION",
                42,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "RECONCILIATION_FAILED",
                    str(exc),
                )
            },
        }


async def accounting_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "accounting_node"

    await _emit_progress(
        "Reviewing accounting classifications",
        52,
    )

    accounting_conflicts = [
        conflict
        for conflict in state["reconciliation"]["conflicts"]
        if conflict["requires_accounting_review"]
    ]

    if not accounting_conflicts:
        return {
            "accounting": {
                "classifications": [],
                "adjustments": [],
                "reviewed_conflict_ids": [],
                "adjusted_fact_ids": [],
                "unresolved_fact_ids": [],
                "review_required": False,
                "review_performed": False,
                "status_reason": "No accounting review signal detected.",
                "comparability_broken": False,
                "normalization_required": False,
                "reconciliation_required": False,
                "status": "COMPLETED",
            },
            "execution": _execution_update(
                state,
                node,
                "ACCOUNTING_REVIEW",
                52,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "ACCOUNTING_SKIPPED",
                    "No accounting review signal detected.",
                )
            },
        }

    try:
        result = await _run_agent(
            ACCOUNTING_PROMPT,
            {
                "request": state["request"],
                "company": state["company"],
                "corporate_actions": state["corporate_actions"],
                "sources": state["sources"],
                "evidence": state["evidence"],
                "financial_facts": state["financial_facts"],
                "conflicts": accounting_conflicts,
            },
        )

        classifications = result.get("classifications", [])
        adjustments = result.get("adjustments", [])
        unresolved_fact_ids = result.get(
            "unresolved_fact_ids",
            [],
        )

        accounting = {
            "classifications": classifications,
            "adjustments": adjustments,
            "reviewed_conflict_ids": [
                conflict["conflict_id"]
                for conflict in accounting_conflicts
            ],
            "adjusted_fact_ids": [],
            "unresolved_fact_ids": unresolved_fact_ids,
            "review_required": True,
            "review_performed": True,
            "status_reason": (
                f"Accounting review completed for "
                f"{len(accounting_conflicts)} accounting-sensitive conflicts."
            ),
            "comparability_broken": result.get(
                "comparability_broken",
                False,
            ),
            "normalization_required": result.get(
                "normalization_required",
                False,
            ),
            "reconciliation_required": bool(adjustments),
            "status": "COMPLETED",
        }

        return {
            "accounting": accounting,
            "execution": _execution_update(
                state,
                node,
                "ACCOUNTING_REVIEW",
                52,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "ACCOUNTING_REVIEWED",
                    (
                        f"Reviewed {len(accounting_conflicts)} "
                        f"accounting conflicts."
                    ),
                )
            },
        }

    except (GeminiClientError, KeyError, ValueError) as exc:
        return {
            "accounting": {
                **state["accounting"],
                "review_required": bool(accounting_conflicts),
                "review_performed": False,
                "status_reason": str(exc),
                "reconciliation_required": False,
                "status": "FAILED",
            },
            "execution": _execution_update(
                state,
                node,
                "ACCOUNTING_REVIEW",
                52,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "ACCOUNTING_REVIEW_FAILED",
                    str(exc),
                )
            },
        }


async def apply_accounting_adjustments_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "apply_accounting_adjustments_node"

    await _emit_progress(
        "Applying accounting adjustments",
        56,
    )

    try:
        facts, adjustments = apply_accounting_adjustments(
            facts=state["financial_facts"]["facts"],
            adjustments=state["accounting"]["adjustments"],
        )

        adjusted_fact_ids = [
            adjustment["adjusted_fact_id"]
            for adjustment in adjustments
            if (
                adjustment["applied"]
                and adjustment["adjusted_fact_id"] is not None
            )
        ]

        accounting = dict(state["accounting"])
        accounting["adjustments"] = adjustments
        accounting["adjusted_fact_ids"] = adjusted_fact_ids
        accounting["reconciliation_required"] = bool(
            adjusted_fact_ids
        )
        accounting["status_reason"] = (
            f"Applied {len(adjusted_fact_ids)} accounting adjustments."
            if adjusted_fact_ids
            else accounting.get(
                "status_reason",
                "Accounting review completed; no adjustments were applied.",
            )
        )
        accounting["status"] = "COMPLETED"

        return {
            "financial_facts": {
                "facts": facts,
                "normalized_fact_ids": [
                    fact["fact_id"]
                    for fact in facts
                    if fact.get("status") == "NORMALIZED"
                ],
                "reconciled_fact_ids": [],
                "rejected_fact_ids": [
                    fact["fact_id"]
                    for fact in facts
                    if fact.get("status") == "REJECTED"
                ],
            },
                        "accounting": accounting,
            "execution": _execution_update(
                state,
                node,
                "ACCOUNTING_ADJUSTMENT",
                56,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "ACCOUNTING_ADJUSTMENTS_APPLIED",
                    (
                        f"Applied {len(adjusted_fact_ids)} "
                        f"accounting adjustments."
                    ),
                )
            },
        }

    except (KeyError, ValueError, ArithmeticError) as exc:
        return {
            "execution": _execution_update(
                state,
                node,
                "ACCOUNTING_ADJUSTMENT",
                56,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "ACCOUNTING_ADJUSTMENT_FAILED",
                    str(exc),
                )
            },
        }




#Helper of metric engine node

VALUATION_METRIC_HINTS = (
    "pe",
    "p_e",
    "price",
    "market_cap",
    "enterprise_value",
    "ev",
    "dcf",
    "wacc",
    "fcf_yield",
    "earnings_yield",
)


def _missing_metric_severity(
    metric_key: str,
    category: str,
) -> str:
    key = str(metric_key or "").lower()
    category = str(category or "").lower()

    if category in {"valuation", "per_share"}:
        return "HIGH"

    if key in {
        "current_ratio",
        "debt_to_equity",
        "interest_coverage",
        "return_on_assets",
        "return_on_equity",
        "free_cash_flow",
        "free_cash_flow_margin",
    }:
        return "MEDIUM"

    if category in {
        "liquidity",
        "solvency",
        "profitability",
        "cash_flow",
    }:
        return "MEDIUM"

    return "LOW"



async def metric_engine_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "metric_engine_node"

    await _emit_progress(
        "Calculating deterministic financial metrics",
        62,
    )

    try:
        if not state["financial_facts"]["facts"]:
            return {
                "metrics": {
                    "metrics": [],
                },
                "execution": _execution_update(
                    state,
                    node,
                    "METRIC_CALCULATION",
                    62,
                    "FAILED",
                ),
                "audit": {
                    "events": _audit(
                        state,
                        node,
                        "METRIC_CALCULATION_FAILED",
                        "No financial facts available for metric calculation.",
                    )
                },
            }
        metrics = calculate_metrics(
            facts=state["financial_facts"]["facts"],
            reconciliation=state["reconciliation"],
        )

        completed = sum(
            metric["status"] == "COMPLETED"
            for metric in metrics["metrics"]
        )

        failed = sum(
            metric["status"] == "FAILED"
            for metric in metrics["metrics"]
        )

        missing_metrics = _build_missing_metrics(
            state,
            metrics["metrics"],
        )

        missing_metric_summary = _build_missing_metric_summary(
            missing_metrics,
        )

        return {
            "metrics": metrics,
            "missing_metrics": missing_metrics,
            "missing_metric_summary": missing_metric_summary,
            "execution": _execution_update(
                state,
                node,
                "METRIC_CALCULATION",
                62,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "METRICS_CALCULATED",
                    (
                        f"Calculated {completed} metrics; "
                        f"{failed} metrics lacked valid inputs. "
                        f"{missing_metric_summary['explanation']}"
                    ),
                )
            },
        }

    except (KeyError, ValueError, ArithmeticError) as exc:
        return {
            "execution": _execution_update(
                state,
                node,
                "METRIC_CALCULATION",
                62,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "METRIC_CALCULATION_FAILED",
                    str(exc),
                )
            },
        }

def _completed_metrics(
    state: FinancialResearchState,
) -> list[dict[str, Any]]:
    return [
        metric
        for metric in state["metrics"]["metrics"]
        if (
            metric.get("status") == "COMPLETED"
            and metric.get("value") is not None
        )
    ]


def _period_sort_key(period: str) -> tuple[int, int, int]:
    text = str(period)

    quarter_match = re.search(r"Q([1-4])\s+FY(\d{4})", text)

    if quarter_match:
        quarter = int(quarter_match.group(1))
        year = int(quarter_match.group(2))
        return year, quarter, 1

    year_match = re.search(r"FY(\d{4})", text)

    if year_match:
        return int(year_match.group(1)), 5, 0

    return 0, 0, 0


def _latest_period(
    periods: list[str],
    annual_only: bool = False,
    interim_only: bool = False,
) -> str:
    filtered = []

    for period in periods:
        text = str(period)

        if annual_only and text.startswith("Q"):
            continue

        if interim_only and not text.startswith("Q"):
            continue

        filtered.append(text)

    if not filtered:
        return ""

    return sorted(filtered, key=_period_sort_key)[-1]


def _metric_value(
    metrics: list[dict[str, Any]],
    metric_key: str,
    period: str,
) -> float | None:
    for metric in metrics:
        if (
            metric.get("metric_key") == metric_key
            and metric.get("period") == period
            and metric.get("status") == "COMPLETED"
        ):
            value = metric.get("value")

            if isinstance(value, (int, float)):
                return float(value)

    return None


def _fact_value(
    state: FinancialResearchState,
    metric_key: str,
    period: str,
) -> float | None:
    for fact in state["financial_facts"]["facts"]:
        if (
            fact.get("metric_key") == metric_key
            and fact.get("period") == period
            and fact.get("status") in {"NORMALIZED", "RECONCILED"}
        ):
            value = fact.get("value")

            if isinstance(value, (int, float)):
                return float(value)

    return None

def _bank_kpi_value(
    state: FinancialResearchState,
    metric_key: str,
    period: str,
) -> float | None:
    """
    Read a bank-specific KPI from financial facts.

    Examples:
    - net_interest_margin
    - gross_npa_ratio
    - net_npa_ratio
    - provision_coverage_ratio
    - capital_adequacy_ratio
    - casa_ratio
    - deposits
    - advances
    """
    value = _fact_value(
        state,
        metric_key,
        period,
    )

    if value is not None:
        return value

    facts = state.get(
        "financial_facts",
        {},
    ).get(
        "facts",
        [],
    )

    # Fallback: use latest available value if exact period is not matched.
    candidates = [
        fact
        for fact in facts
        if fact.get("metric_key") == metric_key
        and fact.get("value") is not None
    ]

    if not candidates:
        return None

    candidates = sorted(
        candidates,
        key=lambda fact: _period_sort_key(
            str(fact.get("period", ""))
        ),
    )

    return _safe_float(
        candidates[-1].get("value")
    )


def _normalize_currency(currency: str | None) -> str:
    return str(currency or "").strip().upper()


def _infer_currency_from_ticker(ticker: str | None) -> str:
    ticker = str(ticker or "").strip().upper()

    if ticker.endswith((".NS", ".BO")):
        return "INR"

    if ticker.endswith((".SS", ".SZ")):
        return "CNY"

    if ticker.endswith(".HK"):
        return "HKD"

    if ticker.endswith(".L"):
        return "GBP"

    if ticker.endswith(".TO"):
        return "CAD"

    if ticker.endswith(".AX"):
        return "AUD"

    return "USD"


def _reporting_currency(
    state: FinancialResearchState,
) -> str:
    raw_currency = _normalize_currency(
        state.get("raw_financials", {}).get("currency", "")
    )

    company_currency = _normalize_currency(
        state.get("company", {}).get("currency", "")
    )

    inferred_currency = _infer_currency_from_ticker(
        state.get("request", {}).get("ticker", "")
    )

    return raw_currency or company_currency or inferred_currency or "USD"


def _currency_display_name(currency: str) -> str:
    currency = _normalize_currency(currency)

    names = {
        "USD": "US dollars",
        "INR": "Indian rupees",
        "CNY": "Chinese yuan / RMB",
        "CNH": "Chinese yuan / RMB",
        "HKD": "Hong Kong dollars",
        "GBP": "British pounds",
        "EUR": "euros",
        "CAD": "Canadian dollars",
        "AUD": "Australian dollars",
        "JPY": "Japanese yen",
    }

    return names.get(currency, currency or "reported currency")


def _format_money(
    value: float | None,
    currency: str,
) -> str:
    if value is None:
        return "N/A"

    currency = _normalize_currency(currency)
    sign = "-" if value < 0 else ""
    value = abs(value)

    # Indian listed companies usually read better in crore.
    if currency == "INR":
        if value >= 10_000_000_000_000:
            return f"{sign}₹{value / 10_000_000_000_000:,.2f} lakh Cr"

        if value >= 10_000_000:
            return f"{sign}₹{value / 10_000_000:,.1f} Cr"

        if value >= 100_000:
            return f"{sign}₹{value / 100_000:,.1f} Lakh"

        return f"{sign}₹{value:,.0f}"

    # Chinese companies usually read better as RMB/CNY, not $.
    if currency in {"CNY", "CNH"}:
        if value >= 1_000_000_000:
            return f"{sign}RMB{value / 1_000_000_000:,.1f}B"

        if value >= 1_000_000:
            return f"{sign}RMB{value / 1_000_000:,.1f}M"

        return f"{sign}RMB{value:,.0f}"

    currency_prefix = {
        "USD": "$",
        "HKD": "HK$",
        "GBP": "£",
        "EUR": "€",
        "CAD": "C$",
        "AUD": "A$",
        "JPY": "¥",
    }.get(currency, f"{currency} ")

    if value >= 1_000_000_000:
        return f"{sign}{currency_prefix}{value / 1_000_000_000:,.1f}B"

    if value >= 1_000_000:
        return f"{sign}{currency_prefix}{value / 1_000_000:,.1f}M"

    return f"{sign}{currency_prefix}{value:,.0f}"


def _format_number(value: float | None) -> str:
    if value is None:
        return "N/A"

    return f"{value:,.2f}"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"

    adjusted = value * 100 if abs(value) <= 2 else value

    return f"{adjusted:.2f}%"


def _direction_word(
    latest: float | None,
    previous: float | None,
) -> str:
    if latest is None or previous is None:
        return "not comparable"

    if latest > previous:
        return "improved"

    if latest < previous:
        return "declined"

    return "was broadly stable"


def _accounting_quality_note(
    state: FinancialResearchState,
) -> str:
    accounting = state["accounting"]

    status = accounting.get("status", "PENDING")
    review_required = accounting.get("review_required", False)
    review_performed = accounting.get("review_performed", False)
    status_reason = accounting.get("status_reason", "")

    if status == "FAILED":
        return (
            "Accounting review failed before completion. "
            f"Reason: {status_reason or 'No failure reason provided.'}"
        )

    if status != "COMPLETED":
        return (
            "Accounting review was not completed. This should block a "
            "high-confidence final financial conclusion."
        )

    if not review_required:
        return (
            status_reason
            or "No accounting review signal was detected; accounting review "
            "was not required."
        )

    if review_required and review_performed:
        comparability = accounting.get(
            "comparability_broken",
            False,
        )
        normalization = accounting.get(
            "normalization_required",
            False,
        )
        adjustments = accounting.get("adjustments", [])

        return (
            f"{status_reason or 'Accounting review completed.'} "
            f"Comparability broken: {comparability}. "
            f"Normalization required: {normalization}. "
            f"Adjustments identified: {len(adjustments)}."
        )

    return (
        "Accounting review was required but was not performed. This should "
        "be treated as a limitation."
    )





def _missing_data_note(
    state: FinancialResearchState,
    missing_metrics: list[dict[str, Any]],
    missing_metric_summary: dict[str, Any],
    source_text_count: int,
) -> str:
    total_missing = missing_metric_summary.get(
        "total_missing",
        len(missing_metrics),
    )

    by_category = missing_metric_summary.get(
        "by_category",
        {},
    )

    high_impact_metric_keys = missing_metric_summary.get(
        "high_impact_metric_keys",
        [],
    )

    blocked_analysis_areas = missing_metric_summary.get(
        "blocked_analysis_areas",
        [],
    )

    largest_categories = sorted(
        by_category.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:3]

    largest_category_text = (
        ", ".join(
            f"{category} ({count})"
            for category, count in largest_categories
        )
        if largest_categories
        else "none"
    )

    high_impact_text = (
        ", ".join(high_impact_metric_keys)
        if high_impact_metric_keys
        else "none"
    )

    blocked_text = (
        ", ".join(blocked_analysis_areas)
        if blocked_analysis_areas
        else "none"
    )

    quarterly_missing_count = len([
        metric
        for metric in missing_metrics
        if str(metric.get("period", "")).upper().startswith("Q")
    ])

    if _is_bank_framework(state):
        latest_period = state.get(
            "financial_health",
            {},
        ).get(
            "latest_period",
            "",
        )

        required_bank_metrics = {
            "net_interest_margin": "NIM",
            "gross_npa_ratio": "GNPA",
            "net_npa_ratio": "NNPA",
            "provision_coverage_ratio": "PCR",
            "capital_adequacy_ratio": "CRAR",
            "casa_ratio": "CASA",
            "deposits": "deposits",
            "advances": "advances",
        }

        missing_bank_metrics = []

        for metric_key, display_name in required_bank_metrics.items():
            if _bank_kpi_value(
                state,
                metric_key,
                latest_period,
            ) is None:
                missing_bank_metrics.append(
                    display_name,
                )

        bank_gap_text = (
            ", ".join(missing_bank_metrics)
            if missing_bank_metrics
            else "none"
        )

        return (
            f"{total_missing} metrics could not be calculated. "
            f"The largest missing categories were {largest_category_text}. "
            f"High-impact missing metrics included {high_impact_text}. "
            f"Bank-specific remaining data gaps: {bank_gap_text}. "
            "For banks, NIM, GNPA, NNPA, PCR, credit cost, CASA, deposits, "
            "advances, liquidity coverage, and CRAR matter more than generic "
            "industrial-company metrics such as gross margin, free-cash-flow "
            "margin, or asset turnover. "
            f"{quarterly_missing_count} missing metrics were from quarterly "
            "periods, which often lack complete prior-period balance-sheet "
            "averages or cash-flow inputs. "
            f"Blocked or limited analysis areas: {blocked_text}. "
            f"Source-text coverage: {source_text_count} scraped source texts."
        )

    valuation = state.get(
        "valuation",
        {},
    )

    valuation_multiples = valuation.get(
        "multiples",
        [],
    )

    completed_valuation_multiples = _completed_valuation_multiples(
        valuation_multiples,
    )

    valuation_has_market_data = (
        valuation.get("status") == "COMPLETED"
        and len(completed_valuation_multiples) >= 3
        and not valuation.get("currency_mismatch")
    )

    if valuation_has_market_data:
        valuation_limitation = (
            "Valuation market data was available and preliminary multiples "
            "were calculated. Valuation confidence is still limited by missing "
            "peer comparison, historical valuation range, growth forecasts, "
            "margin assumptions, and DCF assumptions."
        )
    else:
        valuation_limitation = (
            "Valuation confidence is limited until market data, share count, "
            "enterprise value, per-share inputs, or currency-compatible market "
            "data are available."
        )

    return (
        f"{total_missing} metrics could not be calculated. "
        f"The largest missing categories were {largest_category_text}. "
        f"High-impact missing metrics included {high_impact_text}. "
        f"{valuation_limitation} "
        f"{quarterly_missing_count} missing metrics were from quarterly "
        "periods, which often lack complete prior-period balance-sheet "
        "averages or cash-flow inputs. "
        f"Blocked or limited analysis areas: {blocked_text}. "
        f"Source-text coverage: {source_text_count} scraped source texts."
    )


BANK_KPI_METRIC_KEYS = {
    "net_interest_margin",
    "gross_npa_ratio",
    "net_npa_ratio",
    "provision_coverage_ratio",
    "capital_adequacy_ratio",
    "casa_ratio",
    "deposits",
    "advances",
}

def _display_company_name(
    state: FinancialResearchState,
) -> str:
    identity = state.get("identity_validation", {})
    request = state.get("request", {})
    company = state.get("company", {})

    return (
        identity.get("validated_company_name")
        or request.get("user_company_name")
        or request.get("original_company_name")
        or company.get("company_name")
        or request.get("ticker")
        or "Unknown Company"
    )


def _build_final_report_text(
    state: FinancialResearchState,
    report: dict[str, str],
) -> str:
    company = state["company"]
    request = state["request"]
    sources = state["sources"]["sources"]
    source_texts = state["source_texts"]["texts"]
    facts = state["financial_facts"]["facts"]
    evidence = state["evidence"]["evidence"]
    metrics = state["metrics"]["metrics"]
    framework = _analysis_framework(state)
    completed_metrics = _completed_metrics(state)
    missing_metrics = state.get("missing_metrics", [])
    missing_metric_summary = state.get(
        "missing_metric_summary",
        {},
    )
    missing_categories = missing_metric_summary.get(
        "by_category",
        {},
    )

    missing_category_text = ", ".join(
        f"{category}: {count}"
        for category, count in missing_categories.items()
    )
    missing_metric_summary = state.get(
            "missing_metric_summary",
            {
                "total_missing": len(missing_metrics),
                "high_severity_count": 0,
                "medium_severity_count": 0,
                "low_severity_count": 0,
                "by_category": {},
                "unique_metric_keys": [],
                "high_impact_metric_keys": [],
                "blocked_analysis_areas": [],
                "explanation": "",
                "status": "PENDING",
            },
        )
    reporting_currency = _reporting_currency(state)
    currency_name = _currency_display_name(reporting_currency)

    company_name = _display_company_name(state)
    financial_health = state.get("financial_health", {})
    overall_health = financial_health.get("overall", {})
    health_score = overall_health.get("score")
    health_label = overall_health.get("label", "UNKNOWN")

    health_score_text = (
        f"{health_score:.1f}/100"
        if isinstance(health_score, (int, float))
        else "not scored"
    )
    valuation = state.get("valuation", {})
    valuation_label = valuation.get(
        "valuation_label",
        "INCONCLUSIVE",
    )

    return (
        f"# Financial Analysis: {company_name} ({request['ticker']})\n\n"
        f"**Currency:** {reporting_currency} ({currency_name}). "
        f"Financial statement values are shown in reporting currency, "
        f"not converted to USD.\n\n"
        f"**Analysis framework:** {framework}.\n\n"
        f"**Financial health score:** {health_score_text} "
        f"({health_label}).\n\n"
        f"**Valuation status:** {valuation_label}.\n\n"
        f"## Executive Summary\n"
        f"{report['executive_summary']}\n\n"
        f"## Financial Health Verdict\n"
        f"{report['financial_health_verdict']}\n\n"
        f"## Profitability\n"
        f"{report['profitability_analysis']}\n\n"
        f"## Growth\n"
        f"{report['growth_analysis']}\n\n"
        f"## Balance Sheet\n"
        f"{report['balance_sheet_analysis']}\n\n"
        f"## Cash Flow\n"
        f"{report['cash_flow_analysis']}\n\n"
        f"## Valuation Status\n"
        f"{report['valuation_note']}\n\n"
        f"## Accounting Quality\n"
        f"{report['accounting_quality_note']}\n\n"
        f"## Risk Note\n"
        f"{report['risk_note']}\n\n"
        f"## Missing Data and Limitations\n"
        f"{report['missing_data_note']}\n\n"
        f"{'Missing categories: ' + missing_category_text if missing_category_text else ''}\n\n"
        f"## Final View\n"
        f"{report['final_view']}\n\n"
        f"---\n"
        f"Pipeline summary: {len(sources)} sources registered, "
        f"{len(source_texts)} sources scraped, {len(evidence)} evidence "
        f"records, {len(facts)} financial facts, "
        f"{len(completed_metrics)} completed metrics, "
        f"{len(missing_metrics)} missing metrics out of {len(metrics)} "
        f"attempted metrics."
        f"Missing metric summary status: "
        f"{missing_metric_summary.get('status', 'UNKNOWN')}."
    )


def _health_label(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"

    if score >= 85:
        return "EXCELLENT"

    if score >= 70:
        return "STRONG"

    if score >= 50:
        return "ADEQUATE"

    return "WEAK"


def _average_scores(scores: list[float | None]) -> float | None:
    valid_scores = [
        score
        for score in scores
        if isinstance(score, (int, float))
    ]

    if not valid_scores:
        return None

    return sum(valid_scores) / len(valid_scores)


def _score_gross_margin(value: float | None) -> float | None:
    if value is None:
        return None

    if value >= 60:
        return 90

    if value >= 40:
        return 75

    if value >= 25:
        return 60

    if value >= 10:
        return 45

    return 25


def _score_operating_margin(value: float | None) -> float | None:
    if value is None:
        return None

    if value >= 20:
        return 90

    if value >= 15:
        return 80

    if value >= 8:
        return 65

    if value >= 3:
        return 50

    if value >= 0:
        return 40

    return 20


def _score_net_margin(value: float | None) -> float | None:
    if value is None:
        return None

    if value >= 15:
        return 85

    if value >= 10:
        return 75

    if value >= 5:
        return 60

    if value >= 0:
        return 45

    return 20


def _score_current_ratio(value: float | None) -> float | None:
    if value is None:
        return None

    if value >= 2.0:
        return 85

    if value >= 1.5:
        return 70

    if value >= 1.0:
        return 55

    return 30


def _score_debt_to_equity(value: float | None) -> float | None:
    if value is None:
        return None

    if value <= 0.25:
        return 90

    if value <= 0.50:
        return 80

    if value <= 1.00:
        return 65

    if value <= 2.00:
        return 45

    return 25


def _score_debt_to_assets(value: float | None) -> float | None:
    if value is None:
        return None

    if value <= 0.15:
        return 90

    if value <= 0.30:
        return 75

    if value <= 0.50:
        return 55

    return 30


def _score_interest_coverage(value: float | None) -> float | None:
    if value is None:
        return None

    if value >= 8:
        return 90

    if value >= 4:
        return 75

    if value >= 2:
        return 55

    if value >= 1:
        return 35

    return 20


def _score_fcf_margin(value: float | None) -> float | None:
    if value is None:
        return None

    if value >= 15:
        return 85

    if value >= 8:
        return 70

    if value >= 3:
        return 55

    if value >= 0:
        return 40

    return 20


def _score_asset_turnover(value: float | None) -> float | None:
    if value is None:
        return None

    if value >= 1.5:
        return 85

    if value >= 1.0:
        return 75

    if value >= 0.5:
        return 60

    if value >= 0.25:
        return 45

    return 30


def _health_score(
    score: float | None,
    reason: str,
    supporting_metric_keys: list[str],
    missing_metric_keys: list[str],
) -> dict[str, Any]:
    return {
        "score": round(score, 1) if score is not None else None,
        "label": _health_label(score),
        "reason": reason,
        "supporting_metric_keys": supporting_metric_keys,
        "missing_metric_keys": missing_metric_keys,
    }


def _metric_missing_keys(
    metrics: list[dict[str, Any]],
    period: str,
    metric_keys: list[str],
) -> list[str]:
    missing = []

    for metric_key in metric_keys:
        value = _metric_value(
            metrics,
            metric_key,
            period,
        )

        if value is None:
            missing.append(metric_key)

    return missing


def _health_periods_from_state(
    state: FinancialResearchState,
    metrics: list[dict[str, Any]],
) -> list[str]:
    periods = {
        str(metric.get("period", ""))
        for metric in metrics
        if metric.get("period")
    }

    for fact in state.get("financial_facts", {}).get("facts", []):
        period = str(fact.get("period", ""))

        if period:
            periods.add(period)

    return [
        period
        for period in periods
        if period
    ]


def _build_profitability_health(
    metrics: list[dict[str, Any]],
    period: str,
) -> dict[str, Any]:
    gross_margin = _metric_value(
        metrics,
        "gross_margin",
        period,
    )
    operating_margin = _metric_value(
        metrics,
        "operating_margin",
        period,
    )
    net_margin = _metric_value(
        metrics,
        "net_margin",
        period,
    )

    score = _average_scores([
        _score_gross_margin(gross_margin),
        _score_operating_margin(operating_margin),
        _score_net_margin(net_margin),
    ])

    reason_parts = []

    if gross_margin is not None:
        reason_parts.append(
            f"gross margin was {_format_percent(gross_margin)}"
        )

    if operating_margin is not None:
        reason_parts.append(
            f"operating margin was {_format_percent(operating_margin)}"
        )

    if net_margin is not None:
        reason_parts.append(
            f"net margin was {_format_percent(net_margin)}"
        )

    if reason_parts:
        reason = (
            "Profitability assessment is based on "
            + ", ".join(reason_parts)
            + "."
        )
    else:
        reason = (
            "Profitability could not be fully assessed because margin "
            "metrics were unavailable."
        )

    return _health_score(
        score=score,
        reason=reason,
        supporting_metric_keys=[
            "gross_margin",
            "operating_margin",
            "net_margin",
        ],
        missing_metric_keys=_metric_missing_keys(
            metrics,
            period,
            [
                "gross_margin",
                "operating_margin",
                "net_margin",
            ],
        ),
    )


def _build_liquidity_health(
    metrics: list[dict[str, Any]],
    period: str,
) -> dict[str, Any]:
    current_ratio = _metric_value(
        metrics,
        "current_ratio",
        period,
    )

    score = _score_current_ratio(current_ratio)

    if current_ratio is None:
        reason = (
            "Liquidity could not be fully assessed because current ratio "
            "was unavailable."
        )
    else:
        reason = (
            f"Liquidity assessment is based on current ratio of "
            f"{_format_number(current_ratio)}."
        )

    return _health_score(
        score=score,
        reason=reason,
        supporting_metric_keys=[
            "current_ratio",
        ],
        missing_metric_keys=_metric_missing_keys(
            metrics,
            period,
            [
                "current_ratio",
            ],
        ),
    )


def _build_solvency_health(
    metrics: list[dict[str, Any]],
    period: str,
) -> dict[str, Any]:
    debt_to_equity = _metric_value(
        metrics,
        "debt_to_equity",
        period,
    )
    debt_to_assets = _metric_value(
        metrics,
        "debt_to_assets",
        period,
    )
    interest_coverage = _metric_value(
        metrics,
        "interest_coverage",
        period,
    )

    score = _average_scores([
        _score_debt_to_equity(debt_to_equity),
        _score_debt_to_assets(debt_to_assets),
        _score_interest_coverage(interest_coverage),
    ])

    reason_parts = []

    if debt_to_equity is not None:
        reason_parts.append(
            f"debt-to-equity was {_format_number(debt_to_equity)}"
        )

    if debt_to_assets is not None:
        reason_parts.append(
            f"debt-to-assets was {_format_number(debt_to_assets)}"
        )

    if interest_coverage is not None:
        reason_parts.append(
            f"interest coverage was {_format_number(interest_coverage)}"
        )

    if reason_parts:
        reason = (
            "Solvency assessment is based on "
            + ", ".join(reason_parts)
            + "."
        )
    else:
        reason = (
            "Solvency could not be fully assessed because leverage and "
            "coverage metrics were unavailable."
        )

    return _health_score(
        score=score,
        reason=reason,
        supporting_metric_keys=[
            "debt_to_equity",
            "debt_to_assets",
            "interest_coverage",
        ],
        missing_metric_keys=_metric_missing_keys(
            metrics,
            period,
            [
                "debt_to_equity",
                "debt_to_assets",
                "interest_coverage",
            ],
        ),
    )


def _build_cash_generation_health(
    state: FinancialResearchState,
    metrics: list[dict[str, Any]],
    period: str,
) -> dict[str, Any]:
    fcf_margin = _metric_value(
        metrics,
        "free_cash_flow_margin",
        period,
    )

    free_cash_flow = _fact_value(
        state,
        "free_cash_flow",
        period,
    )

    if free_cash_flow is None:
        free_cash_flow = _metric_value(
            metrics,
            "free_cash_flow",
            period,
        )

    score = _score_fcf_margin(fcf_margin)

    if score is not None and isinstance(free_cash_flow, (int, float)):
        if free_cash_flow > 0:
            score = min(score + 5, 100)
        elif free_cash_flow < 0:
            score = max(score - 15, 0)

    reason_parts = []

    if fcf_margin is not None:
        reason_parts.append(
            f"free-cash-flow margin was {_format_percent(fcf_margin)}"
        )

    if free_cash_flow is not None:
        reporting_currency = _reporting_currency(state)
        reason_parts.append(
            f"free cash flow was {_format_money(free_cash_flow, reporting_currency)}"
        )

    if reason_parts:
        reason = (
            "Cash-generation assessment is based on "
            + ", ".join(reason_parts)
            + "."
        )
    else:
        reason = (
            "Cash generation could not be fully assessed because free cash "
            "flow and FCF margin were unavailable."
        )

    return _health_score(
        score=score,
        reason=reason,
        supporting_metric_keys=[
            "free_cash_flow",
            "free_cash_flow_margin",
        ],
        missing_metric_keys=_metric_missing_keys(
            metrics,
            period,
            [
                "free_cash_flow",
                "free_cash_flow_margin",
            ],
        ),
    )


def _build_efficiency_health(
    metrics: list[dict[str, Any]],
    period: str,
) -> dict[str, Any]:
    asset_turnover = _metric_value(
        metrics,
        "asset_turnover",
        period,
    )

    score = _score_asset_turnover(asset_turnover)

    if asset_turnover is None:
        reason = (
            "Efficiency could not be fully assessed because asset turnover "
            "was unavailable."
        )
    else:
        reason = (
            f"Efficiency assessment is based on asset turnover of "
            f"{_format_number(asset_turnover)}."
        )

    return _health_score(
        score=score,
        reason=reason,
        supporting_metric_keys=[
            "asset_turnover",
        ],
        missing_metric_keys=_metric_missing_keys(
            metrics,
            period,
            [
                "asset_turnover",
            ],
        ),
    )


def _weighted_overall_health_score(
    profitability: dict[str, Any],
    liquidity: dict[str, Any],
    solvency: dict[str, Any],
    cash_generation: dict[str, Any],
    efficiency: dict[str, Any],
) -> float | None:
    weighted_scores = [
        (profitability.get("score"), 0.30,),
        (solvency.get("score"),0.25,),
        (cash_generation.get("score"),0.20,),
        (liquidity.get("score"),0.15,),
        (efficiency.get("score"),0.10,),
    ]

    numerator = 0.0
    denominator = 0.0

    for score, weight in weighted_scores:
        if not isinstance(score, (int, float)):
            continue

        numerator += score * weight
        denominator += weight

    if denominator == 0:
        return None

    return numerator / denominator


def _health_strengths_and_watch_items(
    profitability: dict[str, Any],
    liquidity: dict[str, Any],
    solvency: dict[str, Any],
    cash_generation: dict[str, Any],
    efficiency: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    category_map = {
        "profitability": profitability,
        "liquidity": liquidity,
        "solvency": solvency,
        "cash generation": cash_generation,
        "efficiency": efficiency,
    }

    strengths = []
    weaknesses = []
    watch_items = []

    for category, score_obj in category_map.items():
        label = score_obj.get("label", "UNKNOWN")

        if label in {"STRONG", "EXCELLENT"}:
            strengths.append(category)

        elif label == "WEAK":
            weaknesses.append(category)

        elif label in {"ADEQUATE", "UNKNOWN"}:
            watch_items.append(category)

    return strengths, weaknesses, watch_items


def _financial_health_summary(
    company_name: str,
    period: str,
    overall: dict[str, Any],
    strengths: list[str],
    weaknesses: list[str],
    watch_items: list[str],
) -> str:
    label = overall.get("label", "UNKNOWN")
    score = overall.get("score")

    score_text = (
        f"{score:.1f}/100"
        if isinstance(score, (int, float))
        else "not scored"
    )

    if label == "UNKNOWN":
        return (
            f"Financial health for {company_name} could not be fully scored "
            f"for {period or 'the latest period'} because too many required "
            "metrics were unavailable."
        )

    parts = [
        f"{company_name}'s financial health is {label.lower()} for "
        f"{period or 'the latest period'} with an overall score of "
        f"{score_text}."
    ]

    if strengths:
        parts.append(
            "Strength areas: "
            f"{', '.join(strengths)}."
        )

    if weaknesses:
        parts.append(
            "Weak areas: "
            f"{', '.join(weaknesses)}."
        )

    if watch_items:
        parts.append(
            "Watch areas: "
            f"{', '.join(watch_items)}."
        )

    return " ".join(parts)


def _ratio_percent_from_facts(
    numerator: float | None,
    denominator: float | None,
) -> float | None:
    """
    Return percentage points from two already-normalized fact values.

    Example:
    net_income = 1,223.6
    total_assets = 211,123.7

    ROA = 1,223.6 / 211,123.7 * 100 = 0.58
    Meaning: 0.58%.
    """
    ratio = _safe_divide(
        numerator,
        denominator,
    )

    if ratio is None:
        return None

    return ratio * 100


def _bank_roa_roe_from_facts(
    state: FinancialResearchState,
    period: str,
) -> tuple[float | None, float | None]:
    """
    Recompute bank ROA and ROE directly from facts.

    This avoids generic metric-engine scaling mistakes where ROA can become
    60.78% instead of 0.58%.
    """
    net_income = _fact_value(
        state,
        "reported_net_income",
        period,
    )

    total_assets = _fact_value(
        state,
        "total_assets",
        period,
    )

    shareholder_equity = _fact_value(
        state,
        "shareholder_equity",
        period,
    )

    roa = _ratio_percent_from_facts(
        net_income,
        total_assets,
    )

    roe = _ratio_percent_from_facts(
        net_income,
        shareholder_equity,
    )

    return roa, roe




async def financial_health_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "financial_health_node"

    await _emit_progress(
        "Assessing financial health",
        70,
    )

    try:
        metrics = _completed_metrics(state)

        periods = _health_periods_from_state(
            state,
            metrics,
        )

        latest_annual = _latest_period(
            periods,
            annual_only=True,
        )

        if not latest_annual:
            financial_health = {
                "latest_period": "",
                "profitability": _health_score(
                    None,
                    "Profitability could not be assessed because no annual period was available.",
                    [],
                    [
                        "gross_margin",
                        "operating_margin",
                        "net_margin",
                    ],
                ),
                "liquidity": _health_score(
                    None,
                    "Liquidity could not be assessed because no annual period was available.",
                    [],
                    [
                        "current_ratio",
                    ],
                ),
                "solvency": _health_score(
                    None,
                    "Solvency could not be assessed because no annual period was available.",
                    [],
                    [
                        "debt_to_equity",
                        "debt_to_assets",
                        "interest_coverage",
                    ],
                ),
                "cash_generation": _health_score(
                    None,
                    "Cash generation could not be assessed because no annual period was available.",
                    [],
                    [
                        "free_cash_flow",
                        "free_cash_flow_margin",
                    ],
                ),
                "efficiency": _health_score(
                    None,
                    "Efficiency could not be assessed because no annual period was available.",
                    [],
                    [
                        "asset_turnover",
                    ],
                ),
                "overall": _health_score(
                    None,
                    "Overall financial health could not be scored because no annual period was available.",
                    [],
                    [],
                ),
                "strengths": [],
                "weaknesses": [],
                "watch_items": [
                    "annual financial data availability",
                ],
                "summary": (
                    "Financial health could not be fully assessed because no "
                    "annual period was available."
                ),
                "status": "COMPLETED",
            }

            return {
                "financial_health": financial_health,
                "execution": _execution_update(
                    state,
                    node,
                    "FINANCIAL_HEALTH",
                    70,
                    "COMPLETED",
                ),
                "audit": {
                    "events": _audit(
                        state,
                        node,
                        "FINANCIAL_HEALTH_LIMITED",
                        "Financial health assessment completed with limited annual data.",
                    )
                },
            }

        if _is_bank_framework(state):
            def _score_bank_net_margin(
                value: float | None,
            ) -> float | None:
                if value is None:
                    return None

                if value >= 20:
                    return 85

                if value >= 12:
                    return 70

                if value >= 6:
                    return 55

                if value >= 0:
                    return 40

                return 20

            def _score_bank_roa(
                value: float | None,
            ) -> float | None:
                if value is None:
                    return None

                if value >= 1.5:
                    return 85

                if value >= 1.0:
                    return 70

                if value >= 0.5:
                    return 55

                if value >= 0:
                    return 40

                return 20

            def _score_bank_roe(
                value: float | None,
            ) -> float | None:
                if value is None:
                    return None

                if value >= 15:
                    return 85

                if value >= 10:
                    return 70

                if value >= 5:
                    return 55

                if value >= 0:
                    return 40

                return 20

            def _score_bank_equity_to_assets(
                value: float | None,
            ) -> float | None:
                if value is None:
                    return None

                if value >= 12:
                    return 80

                if value >= 8:
                    return 65

                if value >= 5:
                    return 45

                return 25

            net_margin = _metric_value(
                metrics,
                "net_margin",
                latest_annual,
            )

            roa, roe = _bank_roa_roe_from_facts(
                state,
                latest_annual,
            )

            if roa is None:
                roa = _metric_value(
                    metrics,
                    "return_on_assets",
                    latest_annual,
                )

            if roe is None:
                roe = _metric_value(
                    metrics,
                    "return_on_equity",
                    latest_annual,
                )

            nim = _bank_kpi_value(
                state,
                "net_interest_margin",
                latest_annual,
            )

            gnpa = _bank_kpi_value(
                state,
                "gross_npa_ratio",
                latest_annual,
            )

            nnpa = _bank_kpi_value(
                state,
                "net_npa_ratio",
                latest_annual,
            )

            pcr = _bank_kpi_value(
                state,
                "provision_coverage_ratio",
                latest_annual,
            )

            crar = _bank_kpi_value(
                state,
                "capital_adequacy_ratio",
                latest_annual,
            )

            bank_profitability_missing_keys = []

            if net_margin is None:
                bank_profitability_missing_keys.append(
                    "net_margin",
                )

            if roa is None:
                bank_profitability_missing_keys.append(
                    "return_on_assets",
                )

            if roe is None:
                bank_profitability_missing_keys.append(
                    "return_on_equity",
                )

            bank_solvency_missing_keys = []

            if crar is None:
                bank_solvency_missing_keys.append(
                    "CRAR",
                )

            if gnpa is None:
                bank_solvency_missing_keys.append(
                    "GNPA",
                )

            if nnpa is None:
                bank_solvency_missing_keys.append(
                    "NNPA",
                )

            bank_overall_missing_keys = []

            if nim is None:
                bank_overall_missing_keys.append(
                    "NIM",
                )

            if gnpa is None:
                bank_overall_missing_keys.append(
                    "GNPA",
                )

            if nnpa is None:
                bank_overall_missing_keys.append(
                    "NNPA",
                )

            if pcr is None:
                bank_overall_missing_keys.append(
                    "PCR",
                )

            if crar is None:
                bank_overall_missing_keys.append(
                    "CRAR",
                )

            bank_overall_missing_keys.extend([
                "credit_cost",
                "CASA",
            ])

            total_assets = _fact_value(
                state,
                "total_assets",
                latest_annual,
            )

            shareholder_equity = _fact_value(
                state,
                "shareholder_equity",
                latest_annual,
            )

            equity_to_assets = _safe_divide(
                shareholder_equity,
                total_assets,
            )

            if equity_to_assets is not None:
                equity_to_assets *= 100

            profitability_score = _average_scores([
                _score_bank_net_margin(net_margin),
                _score_bank_roa(roa),
                _score_bank_roe(roe),
            ])

            capital_score = _score_bank_equity_to_assets(
                equity_to_assets,
            )

            overall_score = _average_scores([
                profitability_score,
                capital_score,
            ])

            profitability = _health_score(
                score=profitability_score,
                reason=(
                    f"Bank profitability assessment uses net margin "
                    f"{_format_percent(net_margin)}, ROA "
                    f"{_format_percent(roa)}, and ROE "
                    f"{_format_percent(roe)}. Gross margin and operating "
                    "margin are not primary bank metrics."
                ),
                supporting_metric_keys=[
                    "net_margin",
                    "return_on_assets",
                    "return_on_equity",
                ],
                
                missing_metric_keys=bank_profitability_missing_keys,
            )

            liquidity = _health_score(
                score=None,
                reason=(
                    "Bank liquidity cannot be reliably scored from the current "
                    "generic metric set. Deposit mix, CASA, LCR, and liquidity "
                    "coverage data should be extracted for a full bank assessment."
                ),
                supporting_metric_keys=[],
                missing_metric_keys=[
                    "CASA",
                    "LCR",
                    "deposit_growth",
                    "liquidity_coverage_ratio",
                ],
            )

            solvency = _health_score(
                score=capital_score,
                reason=(
                    f"Bank capital strength is approximated using "
                    f"equity-to-assets of {_format_percent(equity_to_assets)}. "
                    "A complete bank solvency view should use CRAR, CET1, "
                    "Tier-1 capital, leverage ratio, and asset-quality metrics."
                ),
                supporting_metric_keys=[
                    "shareholder_equity",
                    "total_assets",
                ],
                missing_metric_keys=bank_solvency_missing_keys,
            )

            cash_generation = _health_score(
                score=None,
                reason=(
                    "Free cash flow is not a primary financial-health metric "
                    "for banks because deposits, advances, investments, and "
                    "borrowings are operating balance-sheet items."
                ),
                supporting_metric_keys=[],
                missing_metric_keys=[
                    "net_interest_income",
                    "net_interest_margin",
                    "fee_income",
                    "credit_cost",
                ],
            )

            efficiency = _health_score(
                score=None,
                reason=(
                    "Bank efficiency should be measured using cost-to-income "
                    "ratio and operating expense trends, not asset turnover."
                ),
                supporting_metric_keys=[],
                missing_metric_keys=[
                    "cost_to_income",
                    "operating_expense_ratio",
                ],
            )

            overall = _health_score(
                score=overall_score,
                reason=(
                    "Overall bank financial health is based on first-pass "
                    "profitability and capital-strength proxies. A complete "
                    "bank score still requires NIM, GNPA, NNPA, PCR, credit "
                    "cost, deposit quality, CASA, and CRAR."
                ),
                supporting_metric_keys=[
                    "net_margin",
                    "return_on_assets",
                    "return_on_equity",
                    "shareholder_equity",
                    "total_assets",
                ],
                missing_metric_keys=bank_overall_missing_keys,
            )

            strengths: list[str] = []
            weaknesses: list[str] = []
            watch_items: list[str] = []

            if nim is None:
                watch_items.append("NIM")

            if gnpa is None or nnpa is None:
                watch_items.append("asset quality")

            if pcr is None:
                watch_items.append("provision coverage")

            if crar is None:
                watch_items.append("capital adequacy")

            watch_items.extend([
                "credit cost",
                "deposit franchise",
            ])

            if solvency["label"] in {"STRONG", "EXCELLENT"}:
                strengths.append("capital position")

            if profitability["label"] in {"STRONG", "EXCELLENT"}:
                strengths.append("profitability")

            if profitability["label"] == "WEAK":
                weaknesses.append("profitability")

            if profitability["label"] in {"ADEQUATE", "UNKNOWN"}:
                watch_items.append("profitability")

            if solvency["label"] == "WEAK":
                weaknesses.append("capital position")

            company_name = _display_company_name(state)

            score_text = (
                f"{overall['score']}/100"
                if overall.get("score") is not None
                else "not scored"
            )

            financial_health = {
                "latest_period": latest_annual,
                "profitability": profitability,
                "liquidity": liquidity,
                "solvency": solvency,
                "cash_generation": cash_generation,
                "efficiency": efficiency,
                "overall": overall,
                "strengths": strengths,
                "weaknesses": weaknesses,
                "watch_items": watch_items,
                "summary": (
                    f"{company_name}'s bank financial health is "
                    f"{overall['label'].lower()} for {latest_annual} with "
                    f"an overall score of {score_text}. This is a bank-specific "
                    "first-pass score based on profitability and capital proxies. "
                    f"ROA was {_format_percent(roa)}, ROE was {_format_percent(roe)}, "
                    f"NIM was {_format_percent(nim)}, GNPA was {_format_percent(gnpa)}, "
                    f"NNPA was {_format_percent(nnpa)}, PCR was {_format_percent(pcr)}, "
                    f"and CRAR was {_format_percent(crar)}. A fuller bank assessment "
                    "still requires credit cost, CASA, deposit quality, loan-book mix, "
                    "liquidity coverage, and management commentary."
                ),
                "status": "COMPLETED",
            }

            return {
                "financial_health": financial_health,
                "execution": _execution_update(
                    state,
                    node,
                    "FINANCIAL_HEALTH",
                    70,
                    "COMPLETED",
                ),
                "audit": {
                    "events": _audit(
                        state,
                        node,
                        "BANK_FINANCIAL_HEALTH_ASSESSED",
                        (
                            f"Bank financial health assessed for "
                            f"{latest_annual}: {overall['label']} with "
                            f"score {overall['score']}."
                        ),
                    )
                },
            }

        profitability = _build_profitability_health(
            metrics,
            latest_annual,
        )

        liquidity = _build_liquidity_health(
            metrics,
            latest_annual,
        )

        solvency = _build_solvency_health(
            metrics,
            latest_annual,
        )

        cash_generation = _build_cash_generation_health(
            state,
            metrics,
            latest_annual,
        )

        efficiency = _build_efficiency_health(
            metrics,
            latest_annual,
        )

        overall_score = _weighted_overall_health_score(
            profitability=profitability,
            liquidity=liquidity,
            solvency=solvency,
            cash_generation=cash_generation,
            efficiency=efficiency,
        )

        strengths, weaknesses, watch_items = _health_strengths_and_watch_items(
            profitability=profitability,
            liquidity=liquidity,
            solvency=solvency,
            cash_generation=cash_generation,
            efficiency=efficiency,
        )

        company_name = _display_company_name(state)

        overall = _health_score(
            score=overall_score,
            reason=(
                "Overall financial health score is a weighted blend of "
                "profitability, solvency, cash generation, liquidity, and "
                "efficiency."
            ),
            supporting_metric_keys=[
                "gross_margin",
                "operating_margin",
                "net_margin",
                "current_ratio",
                "debt_to_equity",
                "debt_to_assets",
                "interest_coverage",
                "free_cash_flow_margin",
                "asset_turnover",
            ],
            missing_metric_keys=[],
        )

        financial_health = {
            "latest_period": latest_annual,
            "profitability": profitability,
            "liquidity": liquidity,
            "solvency": solvency,
            "cash_generation": cash_generation,
            "efficiency": efficiency,
            "overall": overall,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "watch_items": watch_items,
            "summary": _financial_health_summary(
                company_name=company_name,
                period=latest_annual,
                overall=overall,
                strengths=strengths,
                weaknesses=weaknesses,
                watch_items=watch_items,
            ),
            "status": "COMPLETED",
        }

        return {
            "financial_health": financial_health,
            "execution": _execution_update(
                state,
                node,
                "FINANCIAL_HEALTH",
                70,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "FINANCIAL_HEALTH_ASSESSED",
                    (
                        f"Financial health assessed for {latest_annual}: "
                        f"{overall['label']} with score "
                        f"{overall['score']}."
                    ),
                )
            },
        }

    except Exception as exc:
        return {
            "financial_health": {
                **state.get("financial_health", {}),
                "summary": str(exc),
                "status": "FAILED",
            },
            "execution": _execution_update(
                state,
                node,
                "FINANCIAL_HEALTH",
                70,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "FINANCIAL_HEALTH_FAILED",
                    str(exc),
                )
            },
        }

def _financial_health_verdict_from_state(
    state: FinancialResearchState,
) -> str:
    financial_health = state.get("financial_health", {})

    if financial_health.get("status") == "COMPLETED":
        summary = financial_health.get("summary", "")

        if summary:
            return summary

    if financial_health.get("status") == "FAILED":
        return (
            "Financial health scoring failed, so this report relies on raw "
            "financial metrics rather than a scored health model."
        )

    return (
        "Financial health has not yet been scored. This report therefore "
        "uses raw financial metrics only."
    )


def _financial_health_final_view(
    state: FinancialResearchState,
) -> str:
    financial_health = state.get("financial_health",{},)

    overall = financial_health.get("overall",{},)

    health_label = overall.get("label","UNKNOWN",)

    strengths = financial_health.get("strengths",[],)

    weaknesses = financial_health.get("weaknesses",[],)

    watch_items = financial_health.get("watch_items",[],)

    valuation = state.get("valuation",{},)

    valuation_label = valuation.get("valuation_label","INCONCLUSIVE",)

    if health_label in {"EXCELLENT", "STRONG"}:
        base = (
            "The company appears financially healthy based on the scored "
            "financial-health model."
        )
    elif health_label == "ADEQUATE":
        base = (
            "The company appears financially adequate, but not exceptional, "
            "based on the scored financial-health model."
        )
    elif health_label == "WEAK":
        base = (
            "The company shows weak financial health based on the scored "
            "financial-health model."
        )
    else:
        base = (
            "The company does not yet have enough scored health data for a "
            "firm financial-health conclusion."
        )

    details = []

    if strengths:
        details.append(
            f"Strengths: {', '.join(strengths)}."
        )

    if weaknesses:
        details.append(
            f"Weaknesses: {', '.join(weaknesses)}."
        )

    if watch_items:
        details.append(
            f"Watch items: {', '.join(watch_items)}."
        )

    if valuation_label == "OPTICALLY_CHEAP":
        details.append(
            "Valuation appears optically attractive on first-pass multiples, "
            "but this should be validated against peer multiples, historical "
            "valuation range, growth expectations, and DCF assumptions."
        )
    elif valuation_label in {"CHEAP", "FAIR", "DEMANDING", "EXPENSIVE"}:
        details.append(
            f"Preliminary valuation label: {valuation_label.lower()}."
        )
    else:
        details.append(
            "Valuation remains inconclusive or incomplete."
        )

    details.append(
        "This remains a financial-health and preliminary-valuation conclusion, "
        "not a full investment recommendation, until peer comparison, market "
        "expectations, risk analysis, and thesis review are added."
    )

    return " ".join(
        [base] + details
    )


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        if value != value:
            return None

        return float(value)

    except (TypeError, ValueError):
        return None


def _fast_info_get(
    fast_info: Any,
    key: str,
) -> Any:
    try:
        if hasattr(fast_info, "get"):
            return fast_info.get(key)

        return getattr(fast_info, key)

    except Exception:
        return None


def _market_info_value(
    info: dict[str, Any],
    fast_info: Any,
    info_keys: list[str],
    fast_keys: list[str],
) -> Any:
    for key in info_keys:
        value = info.get(key)

        if value is not None:
            return value

    for key in fast_keys:
        value = _fast_info_get(
            fast_info,
            key,
        )

        if value is not None:
            return value

    return None


def _fetch_market_data_sync(
    ticker: str,
) -> dict[str, Any]:
    try:
        import yfinance as yf

        ticker_obj = yf.Ticker(ticker)

        try:
            info = ticker_obj.get_info()
        except Exception:
            info = getattr(ticker_obj, "info", {}) or {}

        fast_info = getattr(ticker_obj, "fast_info", {}) or {}

        price = _safe_float(
            _market_info_value(
                info,
                fast_info,
                [
                    "currentPrice",
                    "regularMarketPrice",
                    "previousClose",
                ],
                [
                    "last_price",
                    "lastPrice",
                    "regular_market_price",
                    "previous_close",
                ],
            )
        )

        previous_close = _safe_float(
            _market_info_value(
                info,
                fast_info,
                [
                    "previousClose",
                    "regularMarketPreviousClose",
                ],
                [
                    "previous_close",
                    "regular_market_previous_close",
                ],
            )
        )

        market_cap = _safe_float(
            _market_info_value(
                info,
                fast_info,
                [
                    "marketCap",
                ],
                [
                    "market_cap",
                ],
            )
        )

        enterprise_value = _safe_float(
            _market_info_value(
                info,
                fast_info,
                [
                    "enterpriseValue",
                ],
                [
                    "enterprise_value",
                ],
            )
        )

        shares_outstanding = _safe_float(
            _market_info_value(
                info,
                fast_info,
                [
                    "sharesOutstanding",
                    "impliedSharesOutstanding",
                    "floatShares",
                ],
                [
                    "shares",
                    "shares_outstanding",
                ],
            )
        )

        # Market data currency must be the trading / quote currency.
        # Do not use financialCurrency here because ADRs can trade in USD
        # while statements are reported in a different currency.
        currency = _market_currency_from_info(
            ticker,
            info,
            fast_info,
        )

        financial_currency_hint = _financial_currency_hint_from_info(
            info,
        )

        exchange = str(
            info.get("exchange")
            or info.get("fullExchangeName")
            or ""
        )

        return {
            "ticker": ticker,
            "price": price,
            "previous_close": previous_close,
            "market_cap": market_cap,
            "enterprise_value": enterprise_value,
            "shares_outstanding": shares_outstanding,
            "currency": currency,
            "financial_currency_hint": financial_currency_hint,
            "exchange": exchange,
            "provider": "yfinance",
            "retrieved_at": datetime.now(
                timezone.utc,
            ).isoformat(),
            "status": "COMPLETED",
            "error": "",
        }

    except Exception as exc:
        return {
            "ticker": ticker,
            "price": None,
            "previous_close": None,
            "market_cap": None,
            "enterprise_value": None,
            "shares_outstanding": None,
            "currency": "",
            "financial_currency_hint": "",
            "exchange": "",
            "provider": "yfinance",
            "retrieved_at": datetime.now(
                timezone.utc,
            ).isoformat(),
            "status": "FAILED",
            "error": str(exc),
        }

def _market_currency_from_info(
    ticker: str,
    info: dict[str, Any],
    fast_info: Any,
) -> str:
    """
    Return the trading / quote currency for market data.

    Important:
    - info["currency"] is the quote currency.
    - info["financialCurrency"] is the reporting currency.
    - For ADRs like LX, quote currency may be USD while financial
      statements are CNY.
    """
    quote_currency = _market_info_value(
        info,
        fast_info,
        [
            "currency",
        ],
        [
            "currency",
        ],
    )

    quote_currency = _normalize_currency(
        quote_currency,
    )

    if quote_currency:
        return quote_currency

    return _infer_currency_from_ticker(
        ticker,
    )


def _financial_currency_hint_from_info(
    info: dict[str, Any],
) -> str:
    """
    Return yfinance's statement/reporting currency hint when available.
    This must not be used as market-data currency.
    """
    return _normalize_currency(
        info.get("financialCurrency", "")
    )

async def _collect_market_data(
    ticker: str,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _fetch_market_data_sync,
        ticker,
    )

async def market_data_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "market_data_node"

    await _emit_progress(
        "Collecting market data",
        73,
    )

    ticker = state["request"]["ticker"]

    market_data = await _collect_market_data(
        ticker,
    )

    status = market_data.get(
        "status",
        "FAILED",
    )

    if status == "COMPLETED":
        message = (
            f"Collected market data from "
            f"{market_data.get('provider', 'unknown')} for "
            f"{ticker}: price={market_data.get('price')}, "
            f"market_cap={market_data.get('market_cap')}, "
            f"enterprise_value={market_data.get('enterprise_value')}, "
            f"market_currency={market_data.get('currency')}, "
            f"financial_currency_hint="
            f"{market_data.get('financial_currency_hint')}."
        )
    else:
        message = (
            f"Market data collection failed for {ticker}: "
            f"{market_data.get('error', 'unknown error')}"
        )

    return {
        "market_data": market_data,
        "execution": _execution_update(
            state,
            node,
            "MARKET_DATA",
            73,
            status,
        ),
        "audit": {
            "events": _audit(
                state,
                node,
                (
                    "MARKET_DATA_COLLECTED"
                    if status == "COMPLETED"
                    else "MARKET_DATA_FAILED"
                ),
                message,
            )
        },
    }

def _safe_divide(
    numerator: float | None,
    denominator: float | None,
) -> float | None:
    if numerator is None or denominator is None:
        return None

    if denominator == 0:
        return None

    return numerator / denominator


def _valuation_status(
    value: float | None,
) -> str:
    return "COMPLETED" if value is not None else "FAILED"

def _valuation_currency_mismatch_reason(
    financial_currency: str,
    market_currency: str,
    financial_currency_hint: str,
) -> str:
    hint_text = (
        f" yfinance financialCurrency hint is {financial_currency_hint}."
        if financial_currency_hint
        else ""
    )

    return (
        f"Market data currency is {market_currency}, while financial "
        f"statements are reported in {financial_currency}."
        f"{hint_text} Direct valuation multiples are blocked until FX "
        "conversion is added."
    )


def _valuation_multiples_look_implausible(
    multiples: list[dict[str, Any]],
) -> bool:
    """
    Defensive sanity check.

    This catches cases where currency mismatch or scale mismatch slips
    through and produces impossible-looking multiples.
    """
    lookup = {
        multiple.get("metric_key"): multiple.get("value")
        for multiple in multiples
        if multiple.get("status") == "COMPLETED"
    }

    pe = lookup.get("price_to_earnings")
    ps = lookup.get("price_to_sales")
    pfcf = lookup.get("price_to_free_cash_flow")
    fcf_yield = lookup.get("fcf_yield")

    if isinstance(pe, (int, float)) and pe > 0 and pe < 0.5:
        return True

    if isinstance(ps, (int, float)) and ps > 0 and ps < 0.05:
        return True

    if isinstance(pfcf, (int, float)) and pfcf > 0 and pfcf < 0.5:
        return True

    if isinstance(fcf_yield, (int, float)) and fcf_yield > 100:
        return True

    return False

def _multiple_value_text(
    metric_key: str,
    value: float | None,
) -> str:
    if value is None:
        return "N/A"

    if metric_key.endswith("_yield"):
        return _format_percent(value)

    return f"{value:,.2f}x"


def _multiple_interpretation(
    metric_key: str,
    value: float | None,
) -> str:
    if value is None:
        return "Metric could not be calculated because required inputs were unavailable."

    if metric_key == "price_to_earnings":
        if value >= 60:
            return "Very high earnings multiple; valuation appears demanding."
        if value >= 35:
            return "High earnings multiple; valuation requires strong growth."
        if value >= 20:
            return "Moderate-to-high earnings multiple."
        if value >= 10:
            return "Moderate earnings multiple."
        return "Low earnings multiple."

    if metric_key == "price_to_sales":
        if value >= 10:
            return "Very high sales multiple."
        if value >= 5:
            return "High sales multiple."
        if value >= 2:
            return "Moderate sales multiple."
        return "Low sales multiple."

    if metric_key == "price_to_book":
        if value >= 8:
            return "Very high book multiple."
        if value >= 4:
            return "High book multiple."
        if value >= 1.5:
            return "Moderate book multiple."
        return "Low book multiple."

    if metric_key == "price_to_free_cash_flow":
        if value >= 60:
            return "Very high free-cash-flow multiple."
        if value >= 35:
            return "High free-cash-flow multiple."
        if value >= 20:
            return "Moderate-to-high free-cash-flow multiple."
        if value >= 10:
            return "Moderate free-cash-flow multiple."
        return "Low free-cash-flow multiple."

    if metric_key == "fcf_yield":
        if value >= 6:
            return "Strong free-cash-flow yield."
        if value >= 3:
            return "Moderate free-cash-flow yield."
        if value >= 1:
            return "Low free-cash-flow yield."
        return "Very low free-cash-flow yield."

    if metric_key == "earnings_yield":
        if value >= 6:
            return "Strong earnings yield."
        if value >= 3:
            return "Moderate earnings yield."
        if value >= 1:
            return "Low earnings yield."
        return "Very low earnings yield."

    if metric_key.startswith("ev_to_"):
        if value >= 40:
            return "Very high enterprise-value multiple."
        if value >= 25:
            return "High enterprise-value multiple."
        if value >= 10:
            return "Moderate enterprise-value multiple."
        return "Low enterprise-value multiple."

    return "Valuation metric calculated."


def _valuation_multiple(
    metric_key: str,
    value: float | None,
    period: str,
    formula: str,
) -> dict[str, Any]:
    return {
        "metric_key": metric_key,
        "value": round(value, 4) if value is not None else None,
        "period": period,
        "formula": formula,
        "interpretation": _multiple_interpretation(
            metric_key,
            value,
        ),
        "status": _valuation_status(value),
    }


def _completed_valuation_multiples(
    multiples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        multiple
        for multiple in multiples
        if multiple.get("status") == "COMPLETED"
        and isinstance(multiple.get("value"), (int, float))
    ]


def _valuation_label_from_multiples(
    multiples: list[dict[str, Any]],
) -> str:
    """
    First-pass valuation label for normal operating companies.

    This is intentionally conservative:
    - Do not call a high-quality compounder simply CHEAP because P/E is low.
    - High P/B or missing growth context should moderate the conclusion.
    - Final recommendation still requires peer comparison, historical range,
      growth expectations, DCF, and risk analysis.
    """
    lookup = {
        multiple["metric_key"]: multiple.get("value")
        for multiple in _completed_valuation_multiples(multiples)
    }

    pe = lookup.get("price_to_earnings")
    ps = lookup.get("price_to_sales")
    pb = lookup.get("price_to_book")
    pfcf = lookup.get("price_to_free_cash_flow")
    fcf_yield = lookup.get("fcf_yield")

    if pe is None and ps is None and pfcf is None:
        return "INCONCLUSIVE"

    attractive_signals = 0
    expensive_signals = 0

    # Earnings multiple.
    if pe is not None:
        if pe <= 15:
            attractive_signals += 2
        elif pe <= 22:
            attractive_signals += 1
        elif pe >= 45:
            expensive_signals += 2
        elif pe >= 30:
            expensive_signals += 1

    # Sales multiple.
    if ps is not None:
        if ps <= 2:
            attractive_signals += 1
        elif ps >= 8:
            expensive_signals += 2
        elif ps >= 5:
            expensive_signals += 1

    # Book multiple.
    # For asset-light companies, high P/B is not automatically bad,
    # but it prevents us from calling the stock plainly cheap.
    if pb is not None:
        if pb >= 8:
            expensive_signals += 2
        elif pb >= 5:
            expensive_signals += 1
        elif pb <= 2:
            attractive_signals += 1

    # Free-cash-flow multiple.
    if pfcf is not None:
        if pfcf <= 15:
            attractive_signals += 2
        elif pfcf <= 22:
            attractive_signals += 1
        elif pfcf >= 45:
            expensive_signals += 2
        elif pfcf >= 30:
            expensive_signals += 1

    # FCF yield is stored as percentage points.
    # Example: 5.84 means 5.84%.
    if fcf_yield is not None:
        if fcf_yield >= 6:
            attractive_signals += 2
        elif fcf_yield >= 4:
            attractive_signals += 1
        elif fcf_yield <= 2:
            expensive_signals += 1

    if expensive_signals >= 3:
        return "EXPENSIVE"

    if expensive_signals >= 2 and attractive_signals <= 1:
        return "DEMANDING"

    if attractive_signals >= 3 and expensive_signals == 0:
        return "CHEAP"

    if attractive_signals >= 2 and expensive_signals >= 1:
        return "OPTICALLY_CHEAP"

    if attractive_signals >= 2:
        return "OPTICALLY_CHEAP"

    if expensive_signals >= 1 and attractive_signals == 0:
        return "DEMANDING"

    return "FAIR"


def _valuation_label_phrase(
    valuation_label: str,
) -> str:
    """
    Convert internal valuation labels into user-facing language.
    """
    if valuation_label == "OPTICALLY_CHEAP":
        return "optically attractive"

    if valuation_label == "CHEAP":
        return "cheap"

    if valuation_label == "FAIR":
        return "fair"

    if valuation_label == "DEMANDING":
        return "demanding"

    if valuation_label == "EXPENSIVE":
        return "expensive"

    return "inconclusive"

def _valuation_summary_from_state(
    company_name: str,
    valuation_label: str,
    multiples: list[dict[str, Any]],
    limitations: list[str],
) -> str:
    completed = _completed_valuation_multiples(
        multiples,
    )

    if valuation_label == "INCONCLUSIVE":
        if limitations:
            return (
                "Valuation is inconclusive. "
                + " ".join(limitations)
            )

        return (
            "Valuation is inconclusive because too few market-data inputs "
            "were available."
        )

    key_metrics = []

    for metric_key in [
        "price_to_earnings",
        "price_to_sales",
        "price_to_book",
        "price_to_free_cash_flow",
        "ev_to_revenue",
        "fcf_yield",
    ]:
        match = next(
            (
                multiple
                for multiple in completed
                if multiple.get("metric_key") == metric_key
            ),
            None,
        )

        if not match:
            continue

        key_metrics.append(
            f"{metric_key}={_multiple_value_text(metric_key, match.get('value'))}"
        )

    metric_text = (
        ", ".join(key_metrics)
        if key_metrics
        else "available valuation multiples"
    )

    label_phrase = _valuation_label_phrase(
    valuation_label,
    )

    if valuation_label == "OPTICALLY_CHEAP":
        label_sentence = (
            f"{company_name}'s preliminary valuation appears "
            f"{label_phrase} based on {metric_text}, but it should not be "
            "treated as definitively cheap without peer comparison, historical "
            "valuation range, growth expectations, and DCF assumptions."
        )
    else:
        label_sentence = (
            f"{company_name}'s preliminary valuation appears "
            f"{label_phrase} based on {metric_text}."
        )

    return (
        f"{label_sentence} "
        "This is a multiple-based valuation view only; peer comparison, "
        "growth expectations, historical multiple range, and DCF assumptions "
        "are still required before forming an investment recommendation."
    )

async def valuation_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "valuation_node"

    await _emit_progress(
        "Calculating valuation multiples",
        75,
    )

    try:
        metrics = _completed_metrics(state)

        periods = _health_periods_from_state(
            state,
            metrics,
        )

        latest_annual = _latest_period(
            periods,
            annual_only=True,
        )

        financial_currency = _normalize_currency(
            _reporting_currency(state)
        )

        market_data = state.get(
            "market_data",
            {},
        )

        # This must be quote / trading currency, not financialCurrency.
        # Example:
        # LX market currency = USD, financial statement currency = CNY.
        market_currency = _normalize_currency(
            market_data.get("currency", "")
        )

        if not market_currency:
            market_currency = _infer_currency_from_ticker(
                state["request"]["ticker"],
            )

        financial_currency_hint = _normalize_currency(
            market_data.get("financial_currency_hint", "")
        )

        currency_mismatch = (
            bool(financial_currency)
            and bool(market_currency)
            and financial_currency != market_currency
        )

        limitations: list[str] = []

        if market_data.get("status") != "COMPLETED":
            limitations.append(
                "Market data was unavailable, so valuation multiples could "
                "not be calculated."
            )

        if currency_mismatch:
            limitations.append(
                _valuation_currency_mismatch_reason(
                    financial_currency=financial_currency,
                    market_currency=market_currency,
                    financial_currency_hint=financial_currency_hint,
                )
            )

        market_cap = _safe_float(
            market_data.get("market_cap")
        )

        enterprise_value = _safe_float(
            market_data.get("enterprise_value")
        )

        shares_outstanding = _safe_float(
            market_data.get("shares_outstanding")
        )

        price = _safe_float(
            market_data.get("price")
        )

        if market_cap is None:
            limitations.append(
                "Market capitalization was unavailable."
            )

        if enterprise_value is None:
            limitations.append(
                "Enterprise value was unavailable, so EV multiples may be "
                "missing."
            )

        if not latest_annual:
            limitations.append(
                "No annual financial period was available for valuation."
            )

        if limitations and (
            market_data.get("status") != "COMPLETED"
            or currency_mismatch
            or market_cap is None
            or not latest_annual
        ):
            valuation = {
                "latest_period": latest_annual or "",
                "financial_currency": financial_currency,
                "market_currency": market_currency,
                "currency_mismatch": currency_mismatch,
                "multiples": [],
                "valuation_label": "INCONCLUSIVE",
                "valuation_summary": _valuation_summary_from_state(
                    company_name=_display_company_name(state),
                    valuation_label="INCONCLUSIVE",
                    multiples=[],
                    limitations=limitations,
                ),
                "limitations": limitations,
                "status": "COMPLETED",
            }

            return {
                "valuation": valuation,
                "execution": _execution_update(
                    state,
                    node,
                    "VALUATION",
                    75,
                    "COMPLETED",
                ),
                "audit": {
                    "events": _audit(
                        state,
                        node,
                        "VALUATION_INCONCLUSIVE",
                        valuation["valuation_summary"],
                    )
                },
            }

        revenue = _fact_value(
            state,
            "revenue",
            latest_annual,
        )

        net_income = _fact_value(
            state,
            "reported_net_income",
            latest_annual,
        )

        shareholder_equity = _fact_value(
            state,
            "shareholder_equity",
            latest_annual,
        )

        operating_income = _fact_value(
            state,
            "operating_income",
            latest_annual,
        )

        ebitda = _fact_value(
            state,
            "ebitda",
            latest_annual,
        )

        free_cash_flow = _fact_value(
            state,
            "free_cash_flow",
            latest_annual,
        )

        if free_cash_flow is None:
            free_cash_flow = _metric_value(
                metrics,
                "free_cash_flow",
                latest_annual,
            )

        if shares_outstanding is None and price and market_cap:
            shares_outstanding = _safe_divide(
                market_cap,
                price,
            )

        def _yield_from_multiple(
            multiple: float | None,
        ) -> float | None:
            """
            Return yield in percentage points from a valuation multiple.

            Example:
            P/FCF = 55.96x
            FCF yield = 100 / 55.96 = 1.79%
            """
            if multiple is None:
                return None

            if multiple == 0:
                return None

            return 100 / multiple

        def _bank_valuation_label(
            price_to_book: float | None,
            price_to_earnings: float | None,
            roe: float | None,
            roa: float | None,
        ) -> str:
            """
            Bank valuation should be judged mainly by P/B relative to ROE,
            supported by P/E and ROA.
            """
            if price_to_book is None and price_to_earnings is None:
                return "INCONCLUSIVE"

            expensive_signals = 0
            cheap_signals = 0

            if price_to_book is not None:
                if price_to_book >= 1.2 and (
                    roe is None
                    or roe < 8
                ):
                    expensive_signals += 2
                elif price_to_book <= 0.8 and (
                    roe is not None
                    and roe >= 10
                ):
                    cheap_signals += 2
                elif price_to_book >= 1.5:
                    expensive_signals += 1

            if price_to_earnings is not None:
                if price_to_earnings >= 20:
                    expensive_signals += 1
                elif price_to_earnings <= 10:
                    cheap_signals += 1

            if roa is not None and roa < 1:
                expensive_signals += 1

            if expensive_signals >= 2:
                return "EXPENSIVE"

            if cheap_signals >= 2:
                return "CHEAP"

            return "FAIR"

        if _is_bank_framework(state):
            price_to_earnings = _safe_divide(
                market_cap,
                net_income,
            )

            price_to_book = _safe_divide(
                market_cap,
                shareholder_equity,
            )

            roa, roe = _bank_roa_roe_from_facts(
                state,
                latest_annual,
            )

            if roa is None:
                roa = _metric_value(
                    metrics,
                    "return_on_assets",
                    latest_annual,
                )

            if roe is None:
                roe = _metric_value(
                    metrics,
                    "return_on_equity",
                    latest_annual,
                )

            bank_multiples = [
                _valuation_multiple(
                    "price_to_earnings",
                    price_to_earnings,
                    latest_annual,
                    "market_cap / reported_net_income",
                ),
                _valuation_multiple(
                    "price_to_book",
                    price_to_book,
                    latest_annual,
                    "market_cap / shareholder_equity",
                ),
                _valuation_multiple(
                    "earnings_yield",
                    _yield_from_multiple(price_to_earnings),
                    latest_annual,
                    "100 / price_to_earnings",
                ),
            ]

            completed_count = len(
                _completed_valuation_multiples(
                    bank_multiples,
                )
            )

            if completed_count < 2:
                limitations.append(
                    "Fewer than two bank valuation multiples were available, "
                    "so valuation confidence is limited."
                )

            limitations.append(
                "Bank valuation uses P/B, P/E, ROE, and ROA. EV/Revenue, "
                "P/FCF, and FCF yield are not primary bank valuation metrics."
            )

            valuation_label = _bank_valuation_label(
                price_to_book=price_to_book,
                price_to_earnings=price_to_earnings,
                roe=roe,
                roa=roa,
            )

            company_name = _display_company_name(state)

            metric_parts = []

            if price_to_book is not None:
                metric_parts.append(
                    f"P/B={price_to_book:,.2f}x"
                )

            if price_to_earnings is not None:
                metric_parts.append(
                    f"P/E={price_to_earnings:,.2f}x"
                )

            if roe is not None:
                metric_parts.append(
                    f"ROE={_format_percent(roe)}"
                )

            if roa is not None:
                metric_parts.append(
                    f"ROA={_format_percent(roa)}"
                )

            metric_text = (
                ", ".join(metric_parts)
                if metric_parts
                else "available bank valuation indicators"
            )

            valuation_summary = (
                f"{company_name}'s preliminary bank valuation appears "
                f"{valuation_label.lower()} based on {metric_text}. "
                "For banks, P/B should be interpreted against ROE, ROA, "
                "asset quality, credit cost, deposit franchise, and capital "
                "adequacy. This is not a full investment recommendation."
            )

            valuation = {
                "latest_period": latest_annual,
                "financial_currency": financial_currency,
                "market_currency": market_currency,
                "currency_mismatch": currency_mismatch,
                "multiples": bank_multiples,
                "valuation_label": valuation_label,
                "valuation_summary": valuation_summary,
                "limitations": limitations,
                "status": "COMPLETED",
            }

            return {
                "valuation": valuation,
                "execution": _execution_update(
                    state,
                    node,
                    "VALUATION",
                    75,
                    "COMPLETED",
                ),
                "audit": {
                    "events": _audit(
                        state,
                        node,
                        "BANK_VALUATION_COMPLETED",
                        (
                            f"Bank valuation completed for {latest_annual}: "
                            f"{valuation_label} with {completed_count} "
                            "completed bank multiples."
                        ),
                    )
                },
            }

        price_to_earnings = _safe_divide(
            market_cap,
            net_income,
        )

        price_to_sales = _safe_divide(
            market_cap,
            revenue,
        )

        price_to_book = _safe_divide(
            market_cap,
            shareholder_equity,
        )

        price_to_free_cash_flow = _safe_divide(
            market_cap,
            free_cash_flow,
        )

        ev_to_revenue = _safe_divide(
            enterprise_value,
            revenue,
        )

        ev_to_ebitda = _safe_divide(
            enterprise_value,
            ebitda,
        )

        ev_to_operating_income = _safe_divide(
            enterprise_value,
            operating_income,
        )

        multiples = [
            _valuation_multiple(
                "price_to_earnings",
                price_to_earnings,
                latest_annual,
                "market_cap / reported_net_income",
            ),
            _valuation_multiple(
                "price_to_sales",
                price_to_sales,
                latest_annual,
                "market_cap / revenue",
            ),
            _valuation_multiple(
                "price_to_book",
                price_to_book,
                latest_annual,
                "market_cap / shareholder_equity",
            ),
            _valuation_multiple(
                "price_to_free_cash_flow",
                price_to_free_cash_flow,
                latest_annual,
                "market_cap / free_cash_flow",
            ),
            _valuation_multiple(
                "ev_to_revenue",
                ev_to_revenue,
                latest_annual,
                "enterprise_value / revenue",
            ),
            _valuation_multiple(
                "ev_to_ebitda",
                ev_to_ebitda,
                latest_annual,
                "enterprise_value / ebitda",
            ),
            _valuation_multiple(
                "ev_to_operating_income",
                ev_to_operating_income,
                latest_annual,
                "enterprise_value / operating_income",
            ),
            _valuation_multiple(
                "earnings_yield",
                _yield_from_multiple(price_to_earnings),
                latest_annual,
                "100 / price_to_earnings",
            ),
            _valuation_multiple(
                "fcf_yield",
                _yield_from_multiple(price_to_free_cash_flow),
                latest_annual,
                "100 / price_to_free_cash_flow",
            ),
        ]

        completed_count = len(
            _completed_valuation_multiples(
                multiples,
            )
        )

        if _valuation_multiples_look_implausible(
            multiples,
        ):
            limitations.append(
                "Valuation multiples look implausible, which usually "
                "indicates a currency mismatch, ADR scale mismatch, or "
                "market-data unit mismatch. Valuation has been marked "
                "inconclusive for safety."
            )

            valuation = {
                "latest_period": latest_annual,
                "financial_currency": financial_currency,
                "market_currency": market_currency,
                "currency_mismatch": True,
                "multiples": multiples,
                "valuation_label": "INCONCLUSIVE",
                "valuation_summary": _valuation_summary_from_state(
                    company_name=_display_company_name(state),
                    valuation_label="INCONCLUSIVE",
                    multiples=multiples,
                    limitations=limitations,
                ),
                "limitations": limitations,
                "status": "COMPLETED",
            }

            return {
                "valuation": valuation,
                "execution": _execution_update(
                    state,
                    node,
                    "VALUATION",
                    75,
                    "COMPLETED",
                ),
                "audit": {
                    "events": _audit(
                        state,
                        node,
                        "VALUATION_INCONCLUSIVE",
                        valuation["valuation_summary"],
                    )
                },
            }

        if completed_count < 3:
            limitations.append(
                "Fewer than three valuation multiples were available, so "
                "valuation confidence is limited."
            )

        valuation_label = _valuation_label_from_multiples(
            multiples,
        )

        if completed_count < 3:
            valuation_label = "INCONCLUSIVE"

        company_name = _display_company_name(state)

        valuation = {
            "latest_period": latest_annual,
            "financial_currency": financial_currency,
            "market_currency": market_currency,
            "currency_mismatch": currency_mismatch,
            "multiples": multiples,
            "valuation_label": valuation_label,
            "valuation_summary": _valuation_summary_from_state(
                company_name=company_name,
                valuation_label=valuation_label,
                multiples=multiples,
                limitations=limitations,
            ),
            "limitations": limitations,
            "status": "COMPLETED",
        }

        return {
            "valuation": valuation,
            "execution": _execution_update(
                state,
                node,
                "VALUATION",
                75,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "VALUATION_COMPLETED",
                    (
                        f"Valuation completed for {latest_annual}: "
                        f"{valuation_label} with {completed_count} "
                        "completed multiples."
                    ),
                )
            },
        }

    except Exception as exc:
        return {
            "valuation": {
                **state.get("valuation", {}),
                "valuation_label": "INCONCLUSIVE",
                "valuation_summary": str(exc),
                "limitations": [
                    str(exc),
                ],
                "status": "FAILED",
            },
            "execution": _execution_update(
                state,
                node,
                "VALUATION",
                75,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "VALUATION_FAILED",
                    str(exc),
                )
            },
        }


def _valuation_note_from_state(
    state: FinancialResearchState,
) -> str:
    valuation = state.get(
        "valuation",
        {},
    )

    status = valuation.get(
        "status",
        "PENDING",
    )

    if status == "FAILED":
        return (
            "Valuation failed before completion. "
            f"Reason: {valuation.get('valuation_summary', 'Unknown error')}"
        )

    if status != "COMPLETED":
        return (
            "Valuation has not yet been completed. Market data, enterprise "
            "value, peer multiples, and DCF assumptions are still required "
            "before issuing an investment recommendation."
        )

    summary = valuation.get(
        "valuation_summary",
        "",
    )

    if valuation.get("currency_mismatch"):
        return summary or (
            "Valuation is inconclusive because market data and financial "
            "statements are reported in different currencies."
        )

    multiples = valuation.get(
        "multiples",
        [],
    )

    completed = _completed_valuation_multiples(
        multiples,
    )

    if not completed:
        return summary or (
            "Valuation is inconclusive because no valuation multiples were "
            "available."
        )

    display_rows = []

    for metric_key in [
        "price_to_earnings",
        "price_to_sales",
        "price_to_book",
        "price_to_free_cash_flow",
        "ev_to_revenue",
        "ev_to_ebitda",
        "fcf_yield",
        "earnings_yield",
    ]:
        multiple = next(
            (
                item
                for item in completed
                if item.get("metric_key") == metric_key
            ),
            None,
        )

        if not multiple:
            continue

        display_rows.append(
            f"{metric_key}: "
            f"{_multiple_value_text(metric_key, multiple.get('value'))}"
        )

    multiples_text = (
        " Key multiples: "
        + "; ".join(display_rows[:6])
        + "."
        if display_rows
        else ""
    )

    return (
        f"{summary}{multiples_text}"
    )

def _bank_profitability_analysis(
    state: FinancialResearchState,
    metrics: list[dict[str, Any]],
    period: str,
) -> str:
    net_margin = _metric_value(
        metrics,
        "net_margin",
        period,
    )

    roa, roe = _bank_roa_roe_from_facts(
        state,
        period,
    )

    return (
        f"In {period or 'the latest annual period'}, net margin was "
        f"{_format_percent(net_margin)}, ROA was {_format_percent(roa)}, "
        f"and ROE was {_format_percent(roe)}. For a bank, these are more "
        "relevant than gross margin or operating margin. Profitability should "
        "be interpreted alongside net interest margin, provisions, credit "
        "cost, asset quality, and operating efficiency."
    )


def _bank_balance_sheet_analysis(
    state: FinancialResearchState,
    period: str,
) -> str:
    reporting_currency = _reporting_currency(state)

    total_assets = _fact_value(
        state,
        "total_assets",
        period,
    )

    shareholder_equity = _fact_value(
        state,
        "shareholder_equity",
        period,
    )

    cash = _fact_value(
        state,
        "cash_and_cash_equivalents",
        period,
    )

    equity_to_assets = _safe_divide(
        shareholder_equity,
        total_assets,
    )

    if equity_to_assets is not None:
        equity_to_assets *= 100

    return (
        f"At {period or 'the latest annual period'}, total assets were "
        f"{_format_money(total_assets, reporting_currency)}, shareholder "
        f"equity was {_format_money(shareholder_equity, reporting_currency)}, "
        f"and cash plus short-term cash resources were "
        f"{_format_money(cash, reporting_currency)}. Equity-to-assets was "
        f"{_format_percent(equity_to_assets)}. For banks, capital adequacy, "
        "deposit quality, loan-book quality, asset quality, and liquidity "
        "coverage are more important than industrial debt-to-equity ratios."
    )

def _bank_cash_flow_analysis() -> str:
    return (
        "Free cash flow is not a primary bank-analysis metric because "
        "customer deposits, loans, investments, and borrowings are operating "
        "balance-sheet items for banks. Bank cash generation should instead "
        "be assessed through net interest income, net interest margin, fee "
        "income, credit costs, capital generation, deposit growth, and advance "
        "growth."
    )


def _bank_risk_note() -> str:
    return (
        "This Step 1 bank report covers only first-pass financial statement "
        "risk. A full bank risk review should analyze asset quality, GNPA, "
        "NNPA, provision coverage ratio, credit cost, NIM sustainability, "
        "deposit franchise, CASA mix, regulatory capital, liquidity coverage, "
        "and loan-book concentration."
    )


def _normal_company_balance_sheet_analysis(
    state: FinancialResearchState,
    period: str,
    total_assets: float | None,
    shareholder_equity: float | None,
    total_debt: float | None,
    cash: float | None,
    debt_to_equity: float | None,
    debt_to_assets: float | None,
) -> str:
    reporting_currency = _reporting_currency(state)

    net_cash = None

    if isinstance(cash, (int, float)) and isinstance(total_debt, (int, float)):
        net_cash = cash - total_debt

    base = (
        f"At {period or 'the latest annual period'}, total assets were "
        f"{_format_money(total_assets, reporting_currency)}, shareholder "
        f"equity was {_format_money(shareholder_equity, reporting_currency)}, "
        f"total debt was {_format_money(total_debt, reporting_currency)}, "
        f"and cash plus short-term cash resources were "
        f"{_format_money(cash, reporting_currency)}. "
    )

    if net_cash is not None and net_cash > 0:
        return (
            f"{base}"
            f"The company had a net cash position of "
            f"{_format_money(net_cash, reporting_currency)}. "
            f"Debt-to-equity was {_format_number(debt_to_equity)} and "
            f"debt-to-assets was {_format_number(debt_to_assets)}, indicating "
            "low balance-sheet leverage."
        )

    if net_cash is not None and net_cash < 0:
        return (
            f"{base}"
            f"The company had net debt of "
            f"{_format_money(abs(net_cash), reporting_currency)}. "
            f"Debt-to-equity was {_format_number(debt_to_equity)} and "
            f"debt-to-assets was {_format_number(debt_to_assets)}, so leverage "
            "should be interpreted against cash generation, earnings stability, "
            "and business cyclicality."
        )

    return (
        f"{base}"
        f"Debt-to-equity was {_format_number(debt_to_equity)} and "
        f"debt-to-assets was {_format_number(debt_to_assets)}, suggesting "
        "leverage should be interpreted against the company's cash generation "
        "and business model."
    )

async def final_report_node(
    state: FinancialResearchState,
) -> dict[str, Any]:
    node = "final_report_node"

    await _emit_progress(
        "Preparing final analyst report",
        78,
    )

    try:
        metrics = _completed_metrics(state)

        periods = list({
            str(metric.get("period", ""))
            for metric in metrics
            if metric.get("period")
        })

        latest_annual = _latest_period(
            periods,
            annual_only=True,
        )

        previous_annual_periods = sorted(
            [
                period
                for period in periods
                if period.startswith("FY")
                and period != latest_annual
            ],
            key=_period_sort_key,
        )

        previous_annual = (
            previous_annual_periods[-1]
            if previous_annual_periods
            else ""
        )

        latest_interim = _latest_period(
            periods,
            interim_only=True,
        )

        revenue = _fact_value(
            state,
            "revenue",
            latest_annual,
        )

        previous_revenue = _fact_value(
            state,
            "revenue",
            previous_annual,
        )

        net_income = _fact_value(
            state,
            "reported_net_income",
            latest_annual,
        )

        operating_cash_flow = _fact_value(
            state,
            "operating_cash_flow",
            latest_annual,
        )

        free_cash_flow = _fact_value(
            state,
            "free_cash_flow",
            latest_annual,
        )

        total_debt = _fact_value(
            state,
            "total_debt",
            latest_annual,
        )

        cash = _fact_value(
            state,
            "cash_and_cash_equivalents",
            latest_annual,
        )

        total_assets = _fact_value(
            state,
            "total_assets",
            latest_annual,
        )

        shareholder_equity = _fact_value(
            state,
            "shareholder_equity",
            latest_annual,
        )

        gross_margin = _metric_value(
            metrics,
            "gross_margin",
            latest_annual,
        )

        operating_margin = _metric_value(
            metrics,
            "operating_margin",
            latest_annual,
        )

        net_margin = _metric_value(
            metrics,
            "net_margin",
            latest_annual,
        )

        current_ratio = _metric_value(
            metrics,
            "current_ratio",
            latest_annual,
        )

        debt_to_equity = _metric_value(
            metrics,
            "debt_to_equity",
            latest_annual,
        )

        debt_to_assets = _metric_value(
            metrics,
            "debt_to_assets",
            latest_annual,
        )

        fcf_margin = _metric_value(
            metrics,
            "free_cash_flow_margin",
            latest_annual,
        )

        source_text_count = len(
            state.get("source_texts", {}).get("texts", [])
        )

        missing_metrics = state.get(
            "missing_metrics",
            [],
        )

        missing_metric_summary = state.get(
            "missing_metric_summary",
            {
                "total_missing": len(missing_metrics),
                "high_severity_count": 0,
                "medium_severity_count": 0,
                "low_severity_count": 0,
                "by_category": {},
                "unique_metric_keys": [],
                "high_impact_metric_keys": [],
                "blocked_analysis_areas": [],
                "explanation": "",
                "status": "PENDING",
            },
        )

        reporting_currency = _reporting_currency(state)
        currency_name = _currency_display_name(reporting_currency)

        revenue_direction = _direction_word(
            revenue,
            previous_revenue,
        )

        company_name = _display_company_name(state)

        financial_health_state = state.get(
            "financial_health",
            {},
        )

        overall_health = financial_health_state.get(
            "overall",
            {},
        )

        health_label = overall_health.get(
            "label",
            "UNKNOWN",
        )

        health_score = overall_health.get(
            "score",
        )

        health_score_text = (
            f"{health_score}/100"
            if isinstance(health_score, (int, float))
            else "not scored"
        )

        valuation_state = state.get(
            "valuation",
            {},
        )

        valuation_label = valuation_state.get(
            "valuation_label",
            "INCONCLUSIVE",
        )

        valuation_multiples = valuation_state.get(
            "multiples",
            [],
        )

        completed_valuation_multiples = _completed_valuation_multiples(
            valuation_multiples,
        )

        valuation_has_market_data = (
            valuation_state.get("status") == "COMPLETED"
            and len(completed_valuation_multiples) >= 2
            and not valuation_state.get("currency_mismatch")
        )

        if _is_bank_framework(state):
            nim = _bank_kpi_value(
                state,
                "net_interest_margin",
                latest_annual,
            )

            gnpa = _bank_kpi_value(
                state,
                "gross_npa_ratio",
                latest_annual,
            )

            nnpa = _bank_kpi_value(
                state,
                "net_npa_ratio",
                latest_annual,
            )

            pcr = _bank_kpi_value(
                state,
                "provision_coverage_ratio",
                latest_annual,
            )

            crar = _bank_kpi_value(
                state,
                "capital_adequacy_ratio",
                latest_annual,
            )

            deposits = _bank_kpi_value(
                state,
                "deposits",
                latest_annual,
            )

            advances = _bank_kpi_value(
                state,
                "advances",
                latest_annual,
            )

            roa, roe = _bank_roa_roe_from_facts(
                state,
                latest_annual,
            )

            executive_summary = (
                f"{company_name} appears financially {health_label.lower()} "
                f"on a first-pass bank framework based on "
                f"{latest_annual or 'the latest annual period'} results. "
                f"Figures are reported in {currency_name}. "
                f"Revenue was {_format_money(revenue, reporting_currency)} and "
                f"net income was {_format_money(net_income, reporting_currency)}. "
                f"Revenue {revenue_direction} versus "
                f"{previous_annual or 'the prior annual period'}. "
                f"Bank profitability indicators were ROA {_format_percent(roa)}, "
                f"ROE {_format_percent(roe)}, and NIM {_format_percent(nim)}. "
                f"Asset-quality indicators were GNPA {_format_percent(gnpa)}, "
                f"NNPA {_format_percent(nnpa)}, and PCR {_format_percent(pcr)}. "
                f"Capital adequacy was {_format_percent(crar)}. "
                f"Deposits were {_format_money(deposits, reporting_currency)} and "
                f"advances were {_format_money(advances, reporting_currency)}. "
                "For banks, these indicators are more relevant than free cash flow."
            )

        else:
            if health_label in {"EXCELLENT", "STRONG"}:
                health_phrase = "financially strong"
            elif health_label == "ADEQUATE":
                health_phrase = "financially adequate"
            elif health_label == "WEAK":
                health_phrase = "financially weak"
            else:
                health_phrase = "not fully scored"

            executive_summary = (
                f"{company_name} appears {health_phrase} based on "
                f"{latest_annual or 'the latest annual period'} results. "
                f"Figures are reported in {currency_name}. "
                f"Revenue was {_format_money(revenue, reporting_currency)}, "
                f"net income was {_format_money(net_income, reporting_currency)}, "
                f"and free cash flow was "
                f"{_format_money(free_cash_flow, reporting_currency)}. "
                f"Revenue {revenue_direction} versus "
                f"{previous_annual or 'the prior annual period'}, while "
                "profitability and cash generation remain key financial-health "
                "drivers."
            )

        financial_health_verdict = _financial_health_verdict_from_state(
            state,
        )

        if _is_bank_framework(state):
            profitability_analysis = _bank_profitability_analysis(
                state=state,
                metrics=metrics,
                period=latest_annual,
            )

            balance_sheet_analysis = _bank_balance_sheet_analysis(
                state=state,
                period=latest_annual,
            )

            cash_flow_analysis = _bank_cash_flow_analysis()

            risk_note = _bank_risk_note()

        else:
            profitability_analysis = (
                f"In {latest_annual or 'the latest annual period'}, gross margin "
                f"was {_format_percent(gross_margin)}, operating margin was "
                f"{_format_percent(operating_margin)}, and net margin was "
                f"{_format_percent(net_margin)}. These levels indicate pricing "
                "power, scale efficiency, and earnings conversion quality."
            )


            
            balance_sheet_analysis = _normal_company_balance_sheet_analysis(
            state=state,
            period=latest_annual,
            total_assets=total_assets,
            shareholder_equity=shareholder_equity,
            total_debt=total_debt,
            cash=cash,
            debt_to_equity=debt_to_equity,
            debt_to_assets=debt_to_assets,
            )

            cash_flow_analysis = (
                f"Operating cash flow was "
                f"{_format_money(operating_cash_flow, reporting_currency)} "
                f"and free cash flow was "
                f"{_format_money(free_cash_flow, reporting_currency)} in "
                f"{latest_annual or 'the latest annual period'}. "
                f"Free-cash-flow margin was {_format_percent(fcf_margin)}. "
                "This supports reinvestment, balance-sheet flexibility, and "
                "potential shareholder returns."
            )

            risk_note = (
                "This Step 1 report covers financial-statement risk only. "
                "Business, regulatory, competitive, product mix, execution, "
                "industry, and valuation risks should be handled by the future "
                "risk_analysis_node and investment_thesis_node."
            )

        growth_analysis = (
            f"Revenue in {latest_annual or 'the latest annual period'} was "
            f"{_format_money(revenue, reporting_currency)} versus "
            f"{_format_money(previous_revenue, reporting_currency)} in "
            f"{previous_annual or 'the prior annual period'}, so annual "
            f"revenue {revenue_direction}. Interim figures such as "
            f"{latest_interim or 'the latest quarter'} should be interpreted "
            "separately from full-year results."
        )

        valuation_note = _valuation_note_from_state(
            state,
        )

        accounting_quality_note = _accounting_quality_note(
            state,
        )

        total_missing = missing_metric_summary.get(
            "total_missing",
            len(missing_metrics),
        )

        by_category = missing_metric_summary.get(
            "by_category",
            {},
        )

        high_impact_metric_keys = missing_metric_summary.get(
            "high_impact_metric_keys",
            [],
        )

        blocked_analysis_areas = missing_metric_summary.get(
            "blocked_analysis_areas",
            [],
        )

        largest_categories = sorted(
            by_category.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:3]

        largest_category_text = (
            ", ".join(
                f"{category} ({count})"
                for category, count in largest_categories
            )
            if largest_categories
            else "none"
        )

        high_impact_text = (
            ", ".join(high_impact_metric_keys)
            if high_impact_metric_keys
            else "none"
        )

        blocked_text = (
            ", ".join(blocked_analysis_areas)
            if blocked_analysis_areas
            else "none"
        )

        quarterly_missing_count = len([
            metric
            for metric in missing_metrics
            if str(metric.get("period", "")).upper().startswith("Q")
        ])

        if _is_bank_framework(state):
            missing_data_note = (
                f"{total_missing} metrics could not be calculated. "
                f"The largest missing categories were {largest_category_text}. "
                f"High-impact missing metrics included {high_impact_text}. "
                "For a bank, the most important remaining data gaps are "
                "NIM, GNPA, NNPA, provision coverage ratio, credit cost, CASA, "
                "deposit growth, advance growth, liquidity coverage, and CRAR. "
                f"{quarterly_missing_count} missing metrics were from quarterly "
                "periods, which often lack complete prior-period balance-sheet "
                "averages or cash-flow inputs. "
                f"Blocked or limited analysis areas: {blocked_text}. "
                f"Source-text coverage: {source_text_count} scraped source texts."
            )

        elif valuation_has_market_data:
            missing_data_note = (
                f"{total_missing} metrics could not be calculated. "
                f"The largest missing categories were {largest_category_text}. "
                f"High-impact missing metrics included {high_impact_text}. "
                "Valuation market data was available and preliminary multiples "
                "were calculated. Valuation confidence is still limited by "
                "missing peer comparison, historical valuation range, growth "
                "forecasts, margin assumptions, and DCF assumptions. "
                f"{quarterly_missing_count} missing metrics were from quarterly "
                "periods, which often lack complete prior-period balance-sheet "
                "averages or cash-flow inputs. "
                f"Blocked or limited analysis areas: {blocked_text}. "
                f"Source-text coverage: {source_text_count} scraped source texts."
            )

        else:
            missing_data_note = (
                f"{total_missing} metrics could not be calculated. "
                f"The largest missing categories were {largest_category_text}. "
                f"High-impact missing metrics included {high_impact_text}. "
                "Valuation confidence is limited until market data, share count, "
                "enterprise value, per-share inputs, or currency-compatible "
                "market data are available. "
                f"{quarterly_missing_count} missing metrics were from quarterly "
                "periods, which often lack complete prior-period balance-sheet "
                "averages or cash-flow inputs. "
                f"Blocked or limited analysis areas: {blocked_text}. "
                f"Source-text coverage: {source_text_count} scraped source texts."
            )

        final_view = _financial_health_final_view(
            state,
        )

        if (
            not _is_bank_framework(state)
            and valuation_label == "OPTICALLY_CHEAP"
        ):
            final_view = (
                f"{final_view} Valuation appears optically attractive on "
                "first-pass multiples, but this should be validated against "
                "peer multiples, historical valuation range, growth "
                "expectations, and DCF assumptions."
            )

        report = {
            "executive_summary": executive_summary,
            "financial_health_verdict": financial_health_verdict,
            "profitability_analysis": profitability_analysis,
            "growth_analysis": growth_analysis,
            "balance_sheet_analysis": balance_sheet_analysis,
            "cash_flow_analysis": cash_flow_analysis,
            "valuation_note": valuation_note,
            "accounting_quality_note": accounting_quality_note,
            "risk_note": risk_note,
            "missing_data_note": missing_data_note,
            "final_view": final_view,
        }

        report_text = _build_final_report_text(
            state,
            report,
        )

        return {
            "final_report": {
                **report,
                "report_text": report_text,
                "status": "COMPLETED",
            },
            "execution": _execution_update(
                state,
                node,
                "FINAL_REPORT",
                78,
                "COMPLETED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "FINAL_REPORT_CREATED",
                    "Final analyst report generated from completed graph state.",
                )
            },
        }

    except Exception as exc:
        return {
            "final_report": {
                "executive_summary": "",
                "financial_health_verdict": "",
                "profitability_analysis": "",
                "growth_analysis": "",
                "balance_sheet_analysis": "",
                "cash_flow_analysis": "",
                "valuation_note": "",
                "accounting_quality_note": "",
                "risk_note": "",
                "missing_data_note": "",
                "final_view": "",
                "report_text": "",
                "status": "FAILED",
            },
            "execution": _execution_update(
                state,
                node,
                "FINAL_REPORT",
                78,
                "FAILED",
            ),
            "audit": {
                "events": _audit(
                    state,
                    node,
                    "FINAL_REPORT_FAILED",
                    str(exc),
                )
            },
        }