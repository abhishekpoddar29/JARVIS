from __future__ import annotations

from agents.subgraphs.Financial_Analyst.financial_state import (
    SourceDocument,
    SourceType,
)


SOURCE_AUTHORITY: dict[SourceType, int] = {
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


PRIMARY_SOURCE_BONUS = 5
MAX_AUTHORITY_SCORE = 100


def get_base_authority(source_type: SourceType) -> int:
    return SOURCE_AUTHORITY[source_type]


def calculate_source_authority(
    source_type: SourceType,
    primary_source: bool,
) -> int:
    score = get_base_authority(source_type)

    if primary_source:
        score += PRIMARY_SOURCE_BONUS

    return min(score, MAX_AUTHORITY_SCORE)


def score_source(source: SourceDocument) -> int:
    return calculate_source_authority(
        source_type=source["source_type"],
        primary_source=source["primary_source"],
    )


def rank_sources(
    sources: list[SourceDocument],
) -> list[SourceDocument]:
    return sorted(
        sources,
        key=score_source,
        reverse=True,
    )


def source_authority_map(
    sources: list[SourceDocument],
) -> dict[str, int]:
    return {
        source["source_id"]: score_source(source)
        for source in sources
    }