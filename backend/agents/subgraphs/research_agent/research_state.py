from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Awaitable, Callable, TypedDict


class ResearchLevel(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    STRONG = "strong"


class ClaimStatus(StrEnum):
    UNKNOWN = "unknown"
    PARTIAL = "partial"
    SUPPORTED = "supported"
    CONTESTED = "contested"
    BLOCKED = "blocked"


class ActionType(StrEnum):
    SEARCH = "search"
    EXECUTE = "execute"
    COMPARE = "compare"
    SYNTHESIZE = "synthesize"


class ConflictType(StrEnum):
    TERMINOLOGY = "terminology_difference"
    ASSUMPTIONS = "different_assumptions"
    SCOPE = "different_scope"
    APPROXIMATION = "approximation_difference"
    MEASUREMENT = "measurement_disagreement"
    CONTRADICTION = "direct_contradiction"
    TOPIC_DRIFT = "topic_drift"
    NONE = "none"


class ResearchFailureMode(StrEnum):
    NONE = "none"
    RETRIEVAL = "retrieval_failure"
    EXTRACTION = "extraction_failure"
    EXECUTION = "execution_failure"
    SYSTEM = "system_failure"
    BUDGET = "budget_exhausted"


ProgressCallback = Callable[[str], Awaitable[None]]
research_progress_emitter: ContextVar[ProgressCallback | None] = ContextVar(
    "research_progress_emitter", default=None
)


class ResearchClaim(TypedDict, total=False):
    id: str
    title: str
    question: str
    importance: str
    kind: str
    status: str
    requires_current_data: bool
    requires_execution: bool
    required_source_roles: list[str]
    preferred_domains: list[str]
    search_terms: list[str]
    evidence_ids: list[str]
    source_ids: list[str]
    execution_ids: list[str]
    assumptions: list[str]
    conflicts: list[dict[str, Any]]
    missing_requirements: list[str]
    attempts: int
    search_attempts: int
    no_gain_streak: int


class ResearchAction(TypedDict, total=False):
    id: str
    action_type: str
    claim_id: str
    reason: str
    query: str
    search_role: str
    preferred_domains: list[str]
    code: str
    success_criteria: str
    source_gain: int
    evidence_gain: int


class SearchTask(TypedDict, total=False):
    id: str
    question_id: str
    question: str
    query: str
    search_role: str
    priority: str
    claim_type: str
    preferred_domains: list[str]
    excluded_domains: list[str]
    freshness: str
    phase: str
    time_mode: str


class SourcePassage(TypedDict, total=False):
    id: str
    text: str
    page: int


class ResearchSource(TypedDict, total=False):
    id: str
    task_id: str
    question_id: str
    question: str
    related_question_ids: list[str]
    focus_questions: list[str]
    priority: str
    claim_type: str
    search_role: str
    query: str
    preferred_domains: list[str]
    excluded_domains: list[str]
    phase: str
    time_mode: str
    title: str
    url: str
    final_url: str
    canonical_url: str
    domain: str
    snippet: str
    excerpt: str
    passages: list[SourcePassage]
    publication_date: str
    author: str
    publisher: str
    page_type: str
    source_role: str
    quality_tier: str
    quality_score: float
    rank_score: float
    retrieval_method: str
    fetch_status: str
    http_status: int
    content_type: str
    error: str


class EvidenceItem(TypedDict, total=False):
    id: str
    claim_id: str
    question_id: str
    claim: str
    claim_type: str
    source_id: str
    passage_ids: list[str]
    supporting_passage: str
    source_role: str
    source_domain: str
    evidence_kind: str
    confidence: str
    approved: bool
    rejection_reason: str
    extraction_method: str


class EvidenceDiagnostic(TypedDict, total=False):
    claim_id: str
    source_id: str
    passage_ids: list[str]
    reason: str
    raw_claim: str
    extraction_method: str


class ExecutionResult(TypedDict, total=False):
    id: str
    claim_id: str
    task: str
    code: str
    success: bool
    stdout: str
    stderr: str
    results: list[str]
    error: str
    created_at: str


class ResearchPlan(TypedDict, total=False):
    topic: str
    level: str
    audience: str
    target_words: int
    required_sections: list[str]
    claims: list[ResearchClaim]


class ResearchReview(TypedDict, total=False):
    verification_status: str
    research_status: str
    decision_safe: bool
    coverage_score: float
    supported_claim_ids: list[str]
    unresolved_claims: list[str]
    contradictions: list[str]
    system_limitations: list[str]
    failure_mode: str


class ResearchContext(TypedDict, total=False):
    topic: str
    original_request: str
    level: str
    report_summary: str
    claims: list[dict[str, Any]]
    key_findings: list[str]
    unresolved_gaps: list[str]
    queries_used: list[str]
    sources: list[dict[str, str]]
    created_at: str


class ResearchState(TypedDict, total=False):
    original_request: str
    topic_hint: str
    current_date: str
    prior_context: ResearchContext | None
    plan: ResearchPlan
    claim_ledger: list[ResearchClaim]
    current_action: ResearchAction | None
    action_history: list[ResearchAction]
    last_action_result: dict[str, Any]
    queries_used: list[str]
    sources: list[ResearchSource]
    evidence: list[EvidenceItem]
    evidence_diagnostics: list[EvidenceDiagnostic]
    execution_results: list[ExecutionResult]
    review: ResearchReview
    failure_mode: str
    iteration: int
    max_actions: int
    search_actions: int
    execution_actions: int
    final_report: str
    errors: list[str]
    created_at: str


LEVEL_LIMITS: dict[ResearchLevel, dict[str, int]] = {
    ResearchLevel.SHORT: {
        "max_claims": 5, "max_actions": 7, "max_search_actions": 6,
        "max_execution_actions": 1, "max_searches_per_claim": 3,
        "max_no_gain": 2, "max_sources": 24, "fetch_per_action": 5,
        "backfill_per_action": 2, "candidates_per_task": 7,
        "excerpt_chars": 3000, "target_words": 700, "report_tokens": 3200,
    },
    ResearchLevel.MEDIUM: {
        "max_claims": 8, "max_actions": 12, "max_search_actions": 10,
        "max_execution_actions": 2, "max_searches_per_claim": 3,
        "max_no_gain": 2, "max_sources": 42, "fetch_per_action": 7,
        "backfill_per_action": 3, "candidates_per_task": 9,
        "excerpt_chars": 3800, "target_words": 1600, "report_tokens": 6000,
    },
    ResearchLevel.STRONG: {
        "max_claims": 12, "max_actions": 18, "max_search_actions": 16,
        "max_execution_actions": 3, "max_searches_per_claim": 3,
        "max_no_gain": 2, "max_sources": 64, "fetch_per_action": 8,
        "backfill_per_action": 4, "candidates_per_task": 10,
        "excerpt_chars": 4400, "target_words": 3200, "report_tokens": 8192,
    },
}


def normalize_level(value: Any) -> ResearchLevel:
    try:
        return ResearchLevel(str(value).strip().lower())
    except ValueError:
        return ResearchLevel.MEDIUM


def level_limits(value: Any) -> dict[str, int]:
    return LEVEL_LIMITS[normalize_level(value)]


def build_research_context(state: ResearchState) -> ResearchContext:
    plan = state.get("plan", {})
    approved = [item for item in state.get("evidence", []) if item.get("approved")]
    review = state.get("review", {})
    return ResearchContext(
        topic=str(plan.get("topic") or state.get("topic_hint") or "").strip(),
        original_request=state.get("original_request", ""),
        level=normalize_level(plan.get("level")).value,
        report_summary=" ".join(state.get("final_report", "").split()[:260]),
        claims=[
            {"id": claim.get("id", ""), "title": claim.get("title", ""), "status": claim.get("status", "unknown")}
            for claim in state.get("claim_ledger", [])
        ],
        key_findings=[str(item.get("claim", "")) for item in approved][:24],
        unresolved_gaps=list(review.get("unresolved_claims", []))[:12],
        queries_used=list(state.get("queries_used", []))[:40],
        sources=[
            {
                "id": str(source.get("id", "")),
                "title": str(source.get("title", "")),
                "url": str(source.get("final_url") or source.get("url") or ""),
                "source_role": str(source.get("source_role", "")),
                "publication_date": str(source.get("publication_date", "")),
            }
            for source in state.get("sources", [])[:30]
        ],
        created_at=state.get("created_at") or datetime.now(UTC).isoformat(),
    )