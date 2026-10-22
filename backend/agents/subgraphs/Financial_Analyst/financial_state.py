from __future__ import annotations

from typing import Literal, TypedDict


# ============================================================
# Graph Configuration
# ============================================================

FAST_MODEL = "gemini-2.5-flash"

MAX_REVIEWER_ATTEMPTS = 4
MAX_ESCALATIONS = 1
MAX_CLAIM_REVIEW_ATTEMPTS = 3


# ============================================================
# Shared Literals
# ============================================================

ExecutionStatus = Literal[
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
]

ReviewStatus = Literal[
    "PENDING",
    "PASSED",
    "FAILED",
    "ESCALATED",
]

Recommendation = Literal[
    "BUY",
    "OUTPERFORM",
    "HOLD",
    "UNDERPERFORM",
    "SELL",
    "INSUFFICIENT_EVIDENCE",
]

AnalysisDepth = Literal[
    "quick",
    "standard",
    "deep",
]

InvestmentHorizon = Literal[
    "short_term",
    "medium_term",
    "long_term",
]

SourceType = Literal[
    "REGULATORY_FILING",
    "ANNUAL_REPORT",
    "QUARTERLY_REPORT",
    "EARNINGS_RELEASE",
    "INVESTOR_PRESENTATION",
    "EXCHANGE_DISCLOSURE",
    "COMPANY_WEBSITE",
    "MARKET_DATA_PROVIDER",
    "NEWS",
    "RESEARCH",
    "OTHER",
]


FinancialStatementType = Literal[
    "INCOME_STATEMENT",
    "BALANCE_SHEET",
    "CASH_FLOW",
    "SEGMENT",
    "NON_GAAP",
    "BANK_KPI",
    "OTHER",
]


FactStatus = Literal[
    "RAW",
    "NORMALIZED",
    "RECONCILED",
    "REJECTED",
]


ReconciliationStatus = Literal[
    "PENDING",
    "MATCHED",
    "RESOLVED",
    "UNRESOLVED",
    "ESCALATED",
]


AdjustmentType = Literal[
    "DISCONTINUED_OPERATION",
    "IMPAIRMENT",
    "RESTRUCTURING",
    "ONE_TIME_GAIN",
    "ONE_TIME_LOSS",
    "ACCOUNTING_CHANGE",
    "ACQUISITION",
    "DIVESTITURE",
    "OTHER",
]



# ============================================================
# Request Context
# ============================================================

class RequestContext(TypedDict):
    ticker: str
    user_company_name: str
    original_company_name: str

    analysis_type: str
    analysis_depth: AnalysisDepth
    investment_horizon: InvestmentHorizon
    compare_with: list[str]
    generate_excel: bool
    generate_ppt: bool
    report_language: str


# ============================================================
# Company Identity
# ============================================================

class CompanyIdentity(TypedDict):
    ticker: str
    company_name: str
    exchange: str
    isin: str
    sector: str
    industry: str
    currency: str
    fiscal_year_end: str

    analysis_framework: Literal[
        "NORMAL_COMPANY",
        "BANK",
        "FINANCIAL_SERVICES",
        "UNKNOWN",
    ]
    is_financial_institution: bool
    is_bank: bool

    identity_confidence: float
    status: ExecutionStatus


class IdentityValidationState(TypedDict):
    requested_ticker: str
    user_company_name: str
    resolved_company_name: str
    validated_company_name: str

    ticker_match: bool
    company_name_match: bool
    mismatch_detected: bool

    validation_reason: str
    confidence: float
    status: ExecutionStatus

# ============================================================
# Corporate Actions
# ============================================================

class CorporateActionContext(TypedDict):
    restructuring_detected: bool
    merger_detected: bool
    demerger_detected: bool
    discontinued_operations: bool
    accounting_change: bool
    comparability_broken: bool
    status: ExecutionStatus


# ============================================================
# Research Plan
# ============================================================

class ResearchPlan(TypedDict):
    mandatory_tracks: list[str]
    optional_tracks: list[str]
    specialist_agents: list[str]
    critical_metrics: list[str]
    status: ExecutionStatus


# ============================================================
# Execution State
# ============================================================

class ExecutionState(TypedDict):
    current_node: str
    active_stage: str
    completed_nodes: list[str]
    failed_nodes: list[str]
    progress: int
    recommendation_allowed: bool
    status: ExecutionStatus


# ============================================================
# Review Loop
# ============================================================

class ReviewEvent(TypedDict):
    attempt: int
    reviewer: str
    verdict: ReviewStatus
    claim_ids: list[str]
    summary: str


class ReviewControl(TypedDict):
    active_cycle: str
    reviewer_attempts: int
    escalation_attempts: int
    claim_review_attempts: int
    escalation_reason: str
    history: list[ReviewEvent]


# ============================================================
# Audit Trail
# ============================================================

class AuditEvent(TypedDict):
    node: str
    action: str
    message: str


class AuditTrail(TypedDict):
    events: list[AuditEvent]


class MetricInput(TypedDict):
    fact_id: str
    metric_key: str
    value: float | None


class FinancialMetric(TypedDict):
    metric_key: str
    value: float | None
    formula: str
    inputs: list[str]
    period: str
    status: ExecutionStatus

class MetricsState(TypedDict):

    metrics: list[FinancialMetric]


class MissingMetric(TypedDict):
    metric_key: str
    period: str
    category: str
    formula: str
    available_inputs: list[str]
    missing_inputs: list[str]
    reason: str
    impact: str
    severity: Literal["LOW", "MEDIUM", "HIGH"]

class MissingMetricSummary(TypedDict):
    total_missing: int
    high_severity_count: int
    medium_severity_count: int
    low_severity_count: int

    by_category: dict[str, int]
    unique_metric_keys: list[str]
    high_impact_metric_keys: list[str]
    blocked_analysis_areas: list[str]

    explanation: str
    status: ExecutionStatus


class SourceText(TypedDict):
    source_id: str
    url: str
    title: str
    text: str
    retrieved_at: str


class SourceTextRegistry(TypedDict):
    texts: list[SourceText]
# ============================================================
# Root Financial Graph State
# (Remaining sections will be expanded incrementally)
# ============================================================

class FinancialResearchState(TypedDict):
    request: RequestContext

    company: CompanyIdentity
    identity_validation: IdentityValidationState
    corporate_actions: CorporateActionContext
    research_plan: ResearchPlan
    raw_financials: RawFinancialsState
    sources: SourceRegistry
    source_texts: SourceTextRegistry
    
    evidence: EvidenceRegistry
    financial_facts: FinancialFactRegistry
    reconciliation: ReconciliationState
    accounting: AccountingState
    

    metrics: MetricsState
    missing_metrics: list[MissingMetric]
    missing_metric_summary: MissingMetricSummary
    final_report: FinalReportState

    financial_health: FinancialHealthState
    market_data: MarketDataState
    earnings_quality: EarningsQualityState
    valuation: ValuationState

    business: BusinessState
    industry: IndustryState
    management: ManagementState
    competitive: CompetitiveState
    expectations: MarketExpectationState
    risks: RiskState

    debate: DebateState
    decision: DecisionState

    claims: ClaimRegistry
    repair: RepairState

    review_control: ReviewControl
    execution: ExecutionState
    audit: AuditTrail

    


# ============================================================
# Source Registry
# ============================================================

class SourceDocument(TypedDict):
    source_id: str
    title: str
    url: str
    publisher: str
    source_type: SourceType

    fiscal_period: str
    published_date: str
    retrieved_at: str

    authority_rank: int
    primary_source: bool

    status: ExecutionStatus


class SourceRegistry(TypedDict):
    sources: list[SourceDocument]
    
# ============================================================
# Structured Analysis Objects
# ============================================================

AgentName = Literal[
    "PlannerAgent",
    "CompanyIdentityAgent",
    "FinancialStatementAgent",
    "AccountingAgent",
    "FinancialHealthAgent",
    "EarningsQualityAgent",
    "ValuationAgent",
    "BusinessAgent",
    "IndustryAgent",
    "ManagementAgent",
    "CompetitiveAgent",
    "RiskAgent",
    "MarketExpectationAgent",
    "ScenarioAgent",
    "BullAnalyst",
    "BearAnalyst",
    "RiskOfficer",
    "InvestmentCommittee",
    "ClaimAuditor",
    "ThesisWriter",
]


ClaimCategory = Literal[
    "VALUATION",
    "FINANCIAL_HEALTH",
    "EARNINGS_QUALITY",
    "ACCOUNTING",
    "BUSINESS",
    "INDUSTRY",
    "MANAGEMENT",
    "COMPETITIVE",
    "RISK",
    "MARKET_EXPECTATION",
    "SCENARIO",
]


ClaimReviewStatus = Literal[
    "PENDING",
    "PASSED",
    "FAILED",
    "ESCALATED",
]

ConflictRoute = Literal[
    "NONE",
    "SOURCE_REVIEW",
    "EVIDENCE_REVIEW",
    "ACCOUNTING_REVIEW",
]

AccountingSignal = Literal[
    "ACCOUNTING_POLICY_CHANGE",
    "CONTINUING_OPERATIONS",
    "DISCONTINUED_OPERATIONS",
    "IMPAIRMENT",
    "NON_RECURRING_ITEM",
    "ONE_TIME_ITEM",
    "RESTATEMENT",
    "RESTRUCTURING",
]


class ClaimOwnership(TypedDict):
    owner_agent: AgentName
    created_by: AgentName
    last_modified_by: AgentName
    created_at: str
    updated_at: str


class ClaimReviewResult(TypedDict):
    reviewer: AgentName
    verdict: ClaimReviewStatus
    reason: str
    repair_instruction: str
    reviewed_at: str


class AnalysisClaim(TypedDict):
    claim_id: str
    title: str
    statement: str
    category: ClaimCategory

    evidence_ids: list[str]
    metric_ids: list[str]
    source_ids: list[str]

    confidence: float
    review_status: ClaimReviewStatus

    ownership: ClaimOwnership
    review_history: list[ClaimReviewResult]

    version: int

class ClaimRegistry(TypedDict):
    claim_ids: list[str]
    pending_review_ids: list[str]
    failed_claim_ids: list[str]
    approved_claim_ids: list[str]


class AnalysisFinding(TypedDict):
    title: str
    description: str
    severity: Literal[
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
    ]

class RepairRequest(TypedDict):
    claim_id: str
    owner_agent: AgentName
    reason: str
    repair_instruction: str
    requested_by: AgentName
    attempt: int


class RepairState(TypedDict):
    active: bool
    requests: list[RepairRequest]
    completed_claim_ids: list[str]
    failed_claim_ids: list[str]

class AgentAnalysis(TypedDict):
    summary: str
    strengths: list[str]
    weaknesses: list[str]
    findings: list[AnalysisFinding]
    claim_ids: list[str]
    unresolved_questions: list[str]
    confidence: float
    status: ExecutionStatus


# ============================================================
# Financial Health Analyst
# ============================================================
class HealthScore(TypedDict):
    score: float | None
    label: Literal[
        "UNKNOWN",
        "WEAK",
        "ADEQUATE",
        "STRONG",
        "EXCELLENT",
    ]
    reason: str
    supporting_metric_keys: list[str]
    missing_metric_keys: list[str]


class FinancialHealthState(TypedDict):
    latest_period: str

    profitability: HealthScore
    liquidity: HealthScore
    solvency: HealthScore
    cash_generation: HealthScore
    efficiency: HealthScore
    overall: HealthScore

    strengths: list[str]
    weaknesses: list[str]
    watch_items: list[str]

    summary: str
    status: ExecutionStatus


class EarningsQualityState(TypedDict):
    analysis: AgentAnalysis

class BusinessState(TypedDict):
    analysis: AgentAnalysis

class IndustryState(TypedDict):
    analysis: AgentAnalysis


class ManagementState(TypedDict):
    analysis: AgentAnalysis

class CompetitiveState(TypedDict):
    analysis: AgentAnalysis

class MarketExpectationState(TypedDict):
    analysis: AgentAnalysis

class RiskItem(TypedDict):
    title: str
    category: str
    probability: str
    severity: str
    description: str
    mitigation: str


class RiskState(TypedDict):
    risks: list[RiskItem]
    analysis: AgentAnalysis


# ============================================================
# Valuation Analyst
# ============================================================
class ValuationMethod(TypedDict):
    method: str
    fair_value: float | None
    upside_percent: float | None
    confidence: float


class ValuationMultiple(TypedDict):
    metric_key: str
    value: float | None
    period: str
    formula: str
    interpretation: str
    status: ExecutionStatus


class ValuationState(TypedDict):
    latest_period: str

    financial_currency: str
    market_currency: str
    currency_mismatch: bool

    multiples: list[ValuationMultiple]

    valuation_label: Literal[
        "CHEAP",
        "OPTICALLY_CHEAP",
        "FAIR",
        "DEMANDING",
        "EXPENSIVE",
        "INCONCLUSIVE",
    ]

    valuation_summary: str
    limitations: list[str]

    status: ExecutionStatus




class DebateArgument(TypedDict):
    position: Literal[
        "BULL",
        "BEAR",
        "RISK",
    ]
    claim_ids: list[str]
    conclusion: str
    confidence: float

class DebateState(TypedDict):
    bull: DebateArgument
    bear: DebateArgument
    risk: DebateArgument

class DecisionState(TypedDict):
    recommendation: Recommendation

    confidence: float

    supporting_claims: list[str]

    opposing_claims: list[str]

    swing_factors: list[str]

    recommendation_allowed: bool


class ReportPackage(TypedDict):
    company: CompanyIdentity

    metrics: MetricsState

    financial_health: FinancialHealthState

    valuation: ValuationState

    business: BusinessState

    industry: IndustryState

    expectations: MarketExpectationState

    risks: RiskState

    debate: DebateState

    decision: DecisionState

# ============================================================
# Evidence Registry
# ============================================================

class EvidenceItem(TypedDict):
    evidence_id: str

    source_id: str
    extracted_by: AgentName

    statement_type: FinancialStatementType
    metric_key: str

    raw_label: str
    raw_value: float | str | None

    unit: str
    currency: str
    period: str
    scope: str

    definition: str

    confidence: float
    created_at: str


class EvidenceRegistry(TypedDict):
    evidence: list[EvidenceItem]



# ============================================================
# Financial Fact Registry
# ============================================================




class FinancialFact(TypedDict):
    fact_id: str
    metric_key: str

    value: float | None
    unit: str
    currency: str

    period: str
    scope: str
    statement_type: FinancialStatementType

    evidence_ids: list[str]
    source_ids: list[str]

    normalized_definition: str
    accounting_signals: list[AccountingSignal]

    confidence: float
    status: FactStatus

    created_by: AgentName
    version: int


class FinancialFactRegistry(TypedDict):
    facts: list[FinancialFact]

    normalized_fact_ids: list[str]
    reconciled_fact_ids: list[str]
    rejected_fact_ids: list[str]



# ============================================================
# Financial Reconciliation
# ============================================================

class ReconciliationCandidate(TypedDict):
    fact_id: str
    source_id: str
    value: float | None
    authority_rank: int
    confidence: float


class MetricConflict(TypedDict):
    conflict_id: str

    metric_key: str
    normalized_definition: str
    statement_type: str
    period: str
    scope: str
    currency: str
    unit: str

    candidates: list[ReconciliationCandidate]

    selected_fact_id: str | None

    status: ReconciliationStatus
    resolution_reason: str

    requires_accounting_review: bool
    accounting_signals: list[str]

    review_route: ConflictRoute


class ReconciliationState(TypedDict):
    conflicts: list[MetricConflict]

    unresolved_conflict_ids: list[str]
    resolved_conflict_ids: list[str]

    critical_conflict: bool
    status: ExecutionStatus

# ============================================================
# Accounting Classification
# ============================================================

class AccountingAdjustment(TypedDict):
    adjustment_id: str

    fact_id: str
    adjusted_fact_id: str | None

    adjustment_type: AdjustmentType

    description: str

    amount: float | None

    recurring: bool
    operating: bool
    cash: bool

    evidence_ids: list[str]

    confidence: float

    created_by: AgentName
    applied: bool


class AccountingClassification(TypedDict):
    fact_id: str

    recurring: bool
    operating: bool
    continuing_operation: bool

    classification_reason: str

    evidence_ids: list[str]

    confidence: float

    classified_by: AgentName


class AccountingState(TypedDict):
    classifications: list[AccountingClassification]
    adjustments: list[AccountingAdjustment]

    reviewed_conflict_ids: list[str]
    adjusted_fact_ids: list[str]
    unresolved_fact_ids: list[str]

    review_required: bool
    review_performed: bool
    status_reason: str

    comparability_broken: bool
    normalization_required: bool
    reconciliation_required: bool

    status: ExecutionStatus

class RawFinancialsState(TypedDict):
    provider: str
    ticker: str
    company_name: str
    currency: str
    raw_financials: str
    retrieved_at: str
    status: ExecutionStatus
    error: str


#Final Report state 

class FinalReportState(TypedDict):
    executive_summary: str
    financial_health_verdict: str
    profitability_analysis: str
    growth_analysis: str
    balance_sheet_analysis: str
    cash_flow_analysis: str
    valuation_note: str
    accounting_quality_note: str
    risk_note: str
    missing_data_note: str
    final_view: str
    report_text: str
    status: ExecutionStatus

class MarketDataState(TypedDict):
    ticker: str
    price: float | None
    previous_close: float | None
    market_cap: float | None
    enterprise_value: float | None
    shares_outstanding: float | None

    # Currency for price, market cap, and enterprise value.
    # Example: LX ADR trades in USD.
    currency: str

    # yfinance financialCurrency hint.
    # Example: LX statements are reported in CNY.
    financial_currency_hint: str

    exchange: str
    provider: str
    retrieved_at: str
    status: ExecutionStatus
    error: str


def build_initial_financial_state(
    request: RequestContext,
) -> FinancialResearchState:
    ticker = str(
        request.get("ticker", "")
    ).strip().upper()

    company_name = str(
        request.get("company_name")
        or request.get("user_company_name")
        or request.get("original_company_name")
        or ""
    ).strip()

    user_company_name = str(
        request.get("user_company_name")
        or company_name
        or ""
    ).strip()

    original_company_name = str(
        request.get("original_company_name")
        or company_name
        or ""
    ).strip()

    analysis_type = str(
        request.get(
            "analysis_type",
            "Valuation and Financial Health",
        )
    ).strip()

    analysis_depth = request.get(
        "analysis_depth",
        "standard",
    )

    investment_horizon = request.get(
        "investment_horizon",
        "long_term",
    )

    compare_with = request.get(
        "compare_with",
        [],
    )

    if compare_with is None:
        compare_with = []

    generate_excel = bool(
        request.get(
            "generate_excel",
            False,
        )
    )

    generate_ppt = bool(
        request.get(
            "generate_ppt",
            False,
        )
    )

    report_language = str(
        request.get(
            "report_language",
            "English",
        )
    ).strip()

    normalized_request = {
        **request,
        "ticker": ticker,
        "company_name": company_name,
        "user_company_name": user_company_name,
        "original_company_name": original_company_name,
        "analysis_type": analysis_type,
        "analysis_depth": analysis_depth,
        "investment_horizon": investment_horizon,
        "compare_with": compare_with,
        "generate_excel": generate_excel,
        "generate_ppt": generate_ppt,
        "report_language": report_language,
    }

    return {
        "request": normalized_request,

        "company": {
            "ticker": ticker,
            "company_name": company_name or "",
            "exchange": "",
            "isin": "",
            "sector": "",
            "industry": "",
            "currency": "",
            "fiscal_year_end": "",
            "analysis_framework": "UNKNOWN",
            "is_financial_institution": False,
            "is_bank": False,
            "identity_confidence": 0.0,
            "status": "PENDING",
        },

        "identity_validation": {
            "requested_ticker": ticker,
            "user_company_name": user_company_name,
            "resolved_company_name": "",
            "validated_company_name": "",
            "ticker_match": False,
            "company_name_match": False,
            "mismatch_detected": False,
            "validation_reason": "",
            "confidence": 0.0,
            "status": "PENDING",
        },

        "corporate_actions": {
            "restructuring_detected": False,
            "merger_detected": False,
            "demerger_detected": False,
            "discontinued_operations": False,
            "accounting_change": False,
            "comparability_broken": False,
            "status": "PENDING",
        },

        "research_plan": {
            "mandatory_tracks": [],
            "optional_tracks": [],
            "specialist_agents": [],
            "critical_metrics": [],
            "status": "PENDING",
        },

        "raw_financials": {
            "provider": "",
            "ticker": ticker,
            "company_name": company_name or "",
            "currency": "",
            "raw_financials": "",
            "retrieved_at": "",
            "status": "PENDING",
            "error": "",
        },

        "sources": {
            "sources": [],
        },

        "source_texts": {
            "texts": [],
        },

        "evidence": {
            "evidence": [],
        },

        "financial_facts": {
            "facts": [],
            "normalized_fact_ids": [],
            "reconciled_fact_ids": [],
            "rejected_fact_ids": [],
        },

        "reconciliation": {
            "conflicts": [],
            "resolved_conflict_ids": [],
            "unresolved_conflict_ids": [],
            "critical_conflict": False,
            "status": "PENDING",
        },

        "accounting": {
            "classifications": [],
            "adjustments": [],
            "reviewed_conflict_ids": [],
            "adjusted_fact_ids": [],
            "unresolved_fact_ids": [],
            "review_required": False,
            "review_performed": False,
            "status_reason": "",
            "comparability_broken": False,
            "normalization_required": False,
            "reconciliation_required": False,
            "status": "PENDING",
        },

        "metrics": {
            "metrics": [],
        },

        "missing_metrics": [],

        "missing_metric_summary": {
            "total_missing": 0,
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
            "status": "PENDING",
        },

        "financial_health": {
            "latest_period": "",
            "profitability": {
                "score": None,
                "label": "UNKNOWN",
                "reason": "",
                "supporting_metric_keys": [],
                "missing_metric_keys": [],
            },
            "liquidity": {
                "score": None,
                "label": "UNKNOWN",
                "reason": "",
                "supporting_metric_keys": [],
                "missing_metric_keys": [],
            },
            "solvency": {
                "score": None,
                "label": "UNKNOWN",
                "reason": "",
                "supporting_metric_keys": [],
                "missing_metric_keys": [],
            },
            "cash_generation": {
                "score": None,
                "label": "UNKNOWN",
                "reason": "",
                "supporting_metric_keys": [],
                "missing_metric_keys": [],
            },
            "efficiency": {
                "score": None,
                "label": "UNKNOWN",
                "reason": "",
                "supporting_metric_keys": [],
                "missing_metric_keys": [],
            },
            "overall": {
                "score": None,
                "label": "UNKNOWN",
                "reason": "",
                "supporting_metric_keys": [],
                "missing_metric_keys": [],
            },
            "strengths": [],
            "weaknesses": [],
            "watch_items": [],
            "summary": "",
            "status": "PENDING",
        },

        "market_data": {
            "ticker": ticker,
            "price": None,
            "previous_close": None,
            "market_cap": None,
            "enterprise_value": None,
            "shares_outstanding": None,
            "currency": "",
            "financial_currency_hint": "",
            "exchange": "",
            "provider": "",
            "retrieved_at": "",
            "status": "PENDING",
            "error": "",
        },

        "earnings_quality": {},

        "valuation": {
            "latest_period": "",
            "financial_currency": "",
            "market_currency": "",
            "currency_mismatch": False,
            "multiples": [],
            "valuation_label": "INCONCLUSIVE",
            "valuation_summary": "",
            "limitations": [],
            "status": "PENDING",
        },

        "business": {},
        "industry": {},
        "management": {},
        "competitive": {},
        "expectations": {},
        "risks": {},
        "debate": {},
        "decision": {},

        "claims": {
            "claim_ids": [],
            "pending_review_ids": [],
            "failed_claim_ids": [],
            "approved_claim_ids": [],
        },

        "repair": {
            "active": False,
            "requests": [],
            "completed_claim_ids": [],
            "failed_claim_ids": [],
        },

        "review_control": {
            "active_cycle": "",
            "reviewer_attempts": 0,
            "escalation_attempts": 0,
            "claim_review_attempts": 0,
            "escalation_reason": "",
            "history": [],
        },

        "execution": {
            "current_node": "",
            "active_stage": "",
            "completed_nodes": [],
            "failed_nodes": [],
            "progress": 0,
            "recommendation_allowed": False,
            "status": "PENDING",
        },

        "audit": {
            "events": [],
        },
    }


