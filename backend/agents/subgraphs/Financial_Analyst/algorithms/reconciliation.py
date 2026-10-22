from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from agents.subgraphs.Financial_Analyst.financial_state import (
    ConflictRoute,
    ExecutionStatus,
    FinancialFact,
    MetricConflict,
    ReconciliationCandidate,
    ReconciliationState,
    ReconciliationStatus,
    SourceDocument,
)
from agents.subgraphs.Financial_Analyst.algorithms.source_priority import (
    source_authority_map,
)

from .accounting_signal import requires_accounting_review


RELATIVE_TOLERANCE = 0.005
ABSOLUTE_TOLERANCE = 1.0
MIN_RESOLUTION_SCORE = 0.60


ReconciliationKey = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
]


def _normalize_definition(definition: str) -> str:
    return " ".join(definition.lower().split())


def _reconciliation_key(
    fact: FinancialFact,
) -> ReconciliationKey:
    return (
        fact["metric_key"],
        _normalize_definition(fact["normalized_definition"]),
        fact["statement_type"],
        fact["period"],
        fact["scope"],
        fact["currency"],
        fact["unit"],
    )


def _values_match(
    value_a: float | None,
    value_b: float | None,
) -> bool:
    if value_a is None or value_b is None:
        return value_a is value_b

    difference = abs(value_a - value_b)

    if difference <= ABSOLUTE_TOLERANCE:
        return True

    scale = max(abs(value_a), abs(value_b))

    if scale == 0:
        return difference == 0

    return difference / scale <= RELATIVE_TOLERANCE


def _build_agreement_clusters(
    facts: list[FinancialFact],
) -> list[list[FinancialFact]]:
    parent = list(range(len(facts)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]

        return index

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)

        if root_left != root_right:
            parent[root_right] = root_left

    for left, right in combinations(range(len(facts)), 2):
        if _values_match(
            facts[left]["value"],
            facts[right]["value"],
        ):
            union(left, right)

    clusters: dict[int, list[FinancialFact]] = defaultdict(list)

    for index, fact in enumerate(facts):
        clusters[find(index)].append(fact)

    return list(clusters.values())


def _candidate_score(
    fact: FinancialFact,
    authority_map: dict[str, int],
) -> float:
    authority = max(
        (
            authority_map.get(source_id, 0)
            for source_id in fact["source_ids"]
        ),
        default=0,
    )

    return (authority / 100) * fact["confidence"]


def _cluster_score(
    cluster: list[FinancialFact],
    authority_map: dict[str, int],
    total_candidates: int,
) -> float:
    agreement_score = len(cluster) / total_candidates

    authority_confidence = max(
        _candidate_score(fact, authority_map)
        for fact in cluster
    )

    return agreement_score * authority_confidence


def _select_fact(
    cluster: list[FinancialFact],
    authority_map: dict[str, int],
) -> FinancialFact:
    return max(
        cluster,
        key=lambda fact: (
            _candidate_score(fact, authority_map),
            fact["confidence"],
            fact["version"],
        ),
    )


def _build_candidate(
    fact: FinancialFact,
    authority_map: dict[str, int],
) -> ReconciliationCandidate:
    source_id = max(
        fact["source_ids"],
        key=lambda item: authority_map.get(item, 0),
        default="",
    )

    return ReconciliationCandidate(
        fact_id=fact["fact_id"],
        source_id=source_id,
        value=fact["value"],
        authority_rank=authority_map.get(source_id, 0),
        confidence=fact["confidence"],
    )


def _determine_review_route(
    candidates: list[FinancialFact],
    status: ReconciliationStatus,
) -> ConflictRoute:
    if status != "UNRESOLVED":
        return "NONE"

    source_sets = {
        tuple(sorted(fact["source_ids"]))
        for fact in candidates
    }

    evidence_sets = {
        tuple(sorted(fact["evidence_ids"]))
        for fact in candidates
    }

    if len(source_sets) > 1:
        return "SOURCE_REVIEW"

    if len(evidence_sets) > 1:
        return "EVIDENCE_REVIEW"

    return "NONE"


def _accounting_signals(
    candidates: list[FinancialFact],
) -> list[str]:
    return sorted({
        str(signal).strip().upper()
        for fact in candidates
        for signal in fact.get("accounting_signals", [])
        if str(signal).strip()
    })


def reconcile_facts(
    facts: list[FinancialFact],
    sources: list[SourceDocument],
) -> ReconciliationState:
    authority_map = source_authority_map(sources)

    grouped_facts: dict[
        ReconciliationKey,
        list[FinancialFact],
    ] = defaultdict(list)

    for fact in facts:
        if fact["status"] == "REJECTED":
            continue

        grouped_facts[_reconciliation_key(fact)].append(fact)

    conflicts: list[MetricConflict] = []
    resolved_ids: list[str] = []
    unresolved_ids: list[str] = []

    conflict_index = 1

    for key, candidates in grouped_facts.items():
        if len(candidates) == 1:
            continue

        clusters = _build_agreement_clusters(candidates)

        if len(clusters) == 1:
            selected = _select_fact(
                clusters[0],
                authority_map,
            )

            status: ReconciliationStatus = "MATCHED"
            resolution_reason = (
                "Candidate facts agree within reconciliation tolerance."
            )
        else:
            ranked_clusters = sorted(
                clusters,
                key=lambda cluster: _cluster_score(
                    cluster,
                    authority_map,
                    len(candidates),
                ),
                reverse=True,
            )

            best_cluster = ranked_clusters[0]

            best_score = _cluster_score(
                best_cluster,
                authority_map,
                len(candidates),
            )

            second_score = _cluster_score(
                ranked_clusters[1],
                authority_map,
                len(candidates),
            )

            if (
                best_score >= MIN_RESOLUTION_SCORE
                and best_score > second_score
            ):
                selected = _select_fact(
                    best_cluster,
                    authority_map,
                )

                status = "RESOLVED"
                resolution_reason = (
                    "Selected by authority-weighted candidate agreement."
                )
            else:
                selected = None
                status = "UNRESOLVED"
                resolution_reason = (
                    "Conflicting candidate facts lack sufficient "
                    "authority-weighted consensus."
                )

        conflict_id = f"CONFLICT-{conflict_index:04d}"
        conflict_index += 1

        signals = _accounting_signals(candidates)

        conflict = MetricConflict(
            conflict_id=conflict_id,
            metric_key=key[0],
            normalized_definition=key[1],
            statement_type=key[2],
            period=key[3],
            scope=key[4],
            currency=key[5],
            unit=key[6],
            candidates=[
                _build_candidate(
                    fact,
                    authority_map,
                )
                for fact in candidates
            ],
            selected_fact_id=(
                selected["fact_id"]
                if selected is not None
                else ""
            ),
            status=status,
            resolution_reason=resolution_reason,
            requires_accounting_review=False,
            accounting_signals=signals,
            review_route=_determine_review_route(
                candidates,
                status,
            ),
        )

        accounting_review = requires_accounting_review(conflict)

        conflict["requires_accounting_review"] = accounting_review

        if accounting_review:
            conflict["review_route"] = "ACCOUNTING_REVIEW"

        conflicts.append(conflict)

        if status in {"MATCHED", "RESOLVED"}:
            resolved_ids.append(conflict_id)
        else:
            unresolved_ids.append(conflict_id)

    graph_status: ExecutionStatus = (
        "FAILED"
        if unresolved_ids
        else "COMPLETED"
    )

    return ReconciliationState(
        conflicts=conflicts,
        unresolved_conflict_ids=unresolved_ids,
        resolved_conflict_ids=resolved_ids,
        critical_conflict=bool(unresolved_ids),
        status=graph_status,
    )