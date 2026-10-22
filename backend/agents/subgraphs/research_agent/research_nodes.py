from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urlsplit

from agents.gemini_client import generate_reply
from config import get_settings
from tools.research_execution_tool import research_execution_tool

from .research_prompts import (
    CLAIM_PLANNER_PROMPT,
    CONFLICT_CLASSIFIER_PROMPT,
    EVIDENCE_EVALUATOR_PROMPT,
    NEXT_ACTION_PROMPT,
    REPORT_OVERVIEW_PROMPT,
    REPORT_SECTION_PROMPT,
)
from .research_sources import ResearchSourceClient
from .research_state import (
    ActionType,
    EvidenceDiagnostic,
    EvidenceItem,
    ExecutionResult,
    ResearchAction,
    ResearchClaim,
    ResearchFailureMode,
    ResearchLevel,
    ResearchPlan,
    ResearchReview,
    ResearchSource,
    ResearchState,
    SearchTask,
    level_limits,
    normalize_level,
    research_progress_emitter,
)

settings = get_settings()
_WORD_RE = re.compile(r"[a-zA-Z0-9]{3,}")
_EXECUTION_INTENT_RE = re.compile(
    r"\b(deriv(?:e|ation)|calculat(?:e|ion)|comput(?:e|ation)|simulat(?:e|ion)|"
    r"numerical(?:ly)?|symbolic(?:ally)?|reproduc(?:e|ibility)|statistical analysis|"
    r"analy[sz]e (?:the |this )?(?:data|dataset)|verify (?:the )?(?:math|numbers|calculation))\b",
    re.I,
)
_OFFICIAL_GITHUB_ORGS = {
    "openai", "anthropics", "google", "microsoft", "aws", "aws-samples",
    "princeton-nlp", "swe-bench", "all-hands-ai", "langchain-ai",
}


async def _emit(message: str) -> None:
    callback = research_progress_emitter.get()
    if callback:
        try:
            await callback(message)
        except Exception:
            pass


async def _generate_json(prompt: str, payload: dict[str, Any], tokens: int = 5000) -> dict[str, Any]:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    reply = await asyncio.to_thread(
        generate_reply,
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        user_message=json.dumps(payload, ensure_ascii=False, default=str),
        system_instruction=prompt,
        temperature=0.1,
        max_output_tokens=tokens,
        response_mime_type="application/json",
    )
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", reply.text.strip(), flags=re.I)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(text[index:])
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
    raise ValueError("Model did not return a JSON object.")


async def _generate_text(prompt: str, payload: dict[str, Any], tokens: int) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    reply = await asyncio.to_thread(
        generate_reply,
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        user_message=json.dumps(payload, ensure_ascii=False, default=str),
        system_instruction=prompt,
        temperature=0.12,
        max_output_tokens=tokens,
    )
    return reply.text.strip()


def _unique(values: Any, limit: int = 12) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        clean = str(value).strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
        if len(output) >= limit:
            break
    return output


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value or ""))
        return int(match.group()) if match else default


def _infer_level(request: str) -> ResearchLevel:
    lower = request.casefold()
    if any(term in lower for term in ("thesis", "deep research", "in depth", "comprehensive")):
        return ResearchLevel.STRONG
    dimensions = lower.count(",") + lower.count(" and ")
    return ResearchLevel.MEDIUM if dimensions >= 2 or len(request.split()) > 18 else ResearchLevel.SHORT


def _execution_requested(request: str, question: str, proposed: Any) -> bool:
    return bool(proposed) and bool(_EXECUTION_INTENT_RE.search(f"{request} {question}"))


def _fallback_claims(topic: str, request: str, level: ResearchLevel) -> list[ResearchClaim]:
    parts = [part.strip(" .") for part in re.split(r",|\band\b", request, flags=re.I) if len(part.split()) > 1]
    questions = [f"What is {topic}, and which concepts are essential to understand it?"]
    questions.extend(f"What does reliable evidence show about {part}?" for part in parts[: level_limits(level)["max_claims"] - 1])
    return [
        ResearchClaim(
            id=f"C{index}", title=question[:80], question=question,
            importance="must", kind="other", status="unknown",
            requires_current_data=False,
            requires_execution=_execution_requested(request, question, True),
            required_source_roles=["primary", "independent"], preferred_domains=[],
            search_terms=_unique(_WORD_RE.findall(question), 6), evidence_ids=[],
            source_ids=[], execution_ids=[], assumptions=[], conflicts=[],
            missing_requirements=[], attempts=0, search_attempts=0, no_gain_streak=0,
        )
        for index, question in enumerate(questions, 1)
    ]


def _normalize_claim(raw: Any, index: int, request: str) -> ResearchClaim | None:
    if not isinstance(raw, dict):
        return None
    question = str(raw.get("question", "")).strip()
    if not question:
        return None
    return ResearchClaim(
        id=f"C{index}", title=str(raw.get("title") or question).strip()[:100],
        question=question[:600],
        importance="must" if str(raw.get("importance", "should")).lower() == "must" else "should",
        kind=str(raw.get("kind", "other")).strip().lower()[:40], status="unknown",
        requires_current_data=bool(raw.get("requires_current_data", False)),
        requires_execution=_execution_requested(request, question, raw.get("requires_execution")),
        required_source_roles=_unique(raw.get("required_source_roles"), 4),
        preferred_domains=_unique(raw.get("preferred_domains"), 6),
        search_terms=_unique(raw.get("search_terms"), 8), evidence_ids=[], source_ids=[],
        execution_ids=[], assumptions=[], conflicts=[], missing_requirements=[],
        attempts=0, search_attempts=0, no_gain_streak=0,
    )


def _claim(state: ResearchState, claim_id: str) -> ResearchClaim | None:
    return next((item for item in state.get("claim_ledger", []) if item.get("id") == claim_id), None)


def _compact_query(topic: str, claim: ResearchClaim) -> str:
    missing = " ".join(claim.get("missing_requirements", [])[:1])
    terms = " ".join(claim.get("search_terms", [])[:5])
    return " ".join(f"{topic} {terms} {missing or claim.get('question', '')}".split())[:220]


def _search_action(state: ResearchState, claim: ResearchClaim, reason: str) -> ResearchAction:
    topic = str(state.get("plan", {}).get("topic") or state.get("topic_hint") or "")
    role = "primary" if claim.get("kind") in {"technical", "mathematical", "scientific", "historical", "legal"} else "independent"
    return ResearchAction(
        id=f"A{len(state.get('action_history', [])) + 1}", action_type="search",
        claim_id=str(claim.get("id")), reason=reason, query=_compact_query(topic, claim),
        search_role=role, preferred_domains=list(claim.get("preferred_domains", [])),
        code="", success_criteria="Retrieve direct evidence for the active claim.",
    )


def _source_packet(sources: list[ResearchSource], limit: int = 6) -> list[dict[str, Any]]:
    packet: list[dict[str, Any]] = []
    for source in sources[:limit]:
        passages = [
            {"id": passage.get("id"), "text": str(passage.get("text", ""))[:1400]}
            for passage in source.get("passages", [])[:6]
            if str(passage.get("text", "")).strip()
        ]
        if passages:
            packet.append({
                "id": source.get("id"), "title": source.get("title"),
                "domain": source.get("domain"), "source_role": source.get("source_role"),
                "publication_date": source.get("publication_date"), "passages": passages,
            })
    return packet


def _update_claim(ledger: list[ResearchClaim], updated: ResearchClaim) -> list[ResearchClaim]:
    return [updated if item.get("id") == updated.get("id") else item for item in ledger]


def _source_key(source: ResearchSource) -> str:
    title = re.sub(r"\W+", " ", str(source.get("title", "")).casefold()).strip()
    if len(title) >= 24:
        return f"title:{title}"
    url = str(source.get("canonical_url") or source.get("final_url") or source.get("url") or "").lower().rstrip("/")
    return f"url:{url}" if url else title


def _correct_source_role(source: ResearchSource) -> None:
    domain = str(source.get("domain", "")).lower()
    if domain == "github.com":
        path = urlsplit(str(source.get("final_url") or source.get("url") or "")).path.strip("/")
        owner = path.split("/", 1)[0].casefold() if path else ""
        source["source_role"] = "primary_technical" if owner in _OFFICIAL_GITHUB_ORGS else "independent_repository"
    elif source.get("page_type") == "listing" and source.get("source_role") == "primary_official":
        source["source_role"] = "reference_only"


def _merge_sources(existing: list[ResearchSource], incoming: list[ResearchSource]) -> tuple[list[ResearchSource], list[str], int]:
    merged = list(existing)
    by_key = {_source_key(item): item for item in merged if _source_key(item)}
    action_ids: list[str] = []
    added = 0
    for source in incoming:
        _correct_source_role(source)
        key = _source_key(source)
        duplicate = by_key.get(key) if key else None
        if duplicate:
            action_ids.append(str(duplicate.get("id")))
            continue
        merged.append(source)
        action_ids.append(str(source.get("id")))
        if key:
            by_key[key] = source
        added += 1
    return merged, _unique(action_ids, 50), added


async def plan_research_node(state: ResearchState) -> dict[str, Any]:
    await _emit("Decomposing the request into researchable claims...")
    request = state["original_request"]
    fallback_level = _infer_level(request)
    try:
        raw = await _generate_json(CLAIM_PLANNER_PROMPT, {
            "request": request, "topic_hint": state.get("topic_hint", ""),
            "current_date": state.get("current_date"), "prior_context": state.get("prior_context"),
        })
        errors = list(state.get("errors", []))
    except Exception as exc:
        raw = {}
        errors = [*state.get("errors", []), f"Planner fallback used: {type(exc).__name__}: {exc}"]

    level = normalize_level(raw.get("level") or fallback_level.value)
    limits = level_limits(level)
    topic = str(raw.get("topic") or state.get("topic_hint") or request).strip()[:300]
    claims = [
        claim for index, item in enumerate(raw.get("claims", []), 1)
        if (claim := _normalize_claim(item, index, request))
    ][: limits["max_claims"]]
    if not claims:
        claims = _fallback_claims(topic, request, level)
    if not any(claim.get("importance") == "must" for claim in claims):
        claims[0]["importance"] = "must"

    plan = ResearchPlan(
        topic=topic, level=level.value, audience=str(raw.get("audience", "general")),
        target_words=_safe_int(raw.get("target_words"), limits["target_words"]),
        required_sections=_unique(raw.get("required_sections"), 12) or ["Executive Summary", "Analysis", "Conclusion"],
        claims=claims,
    )
    initial = ResearchState(plan=plan, claim_ledger=claims, action_history=[])
    return {
        "plan": plan, "claim_ledger": claims,
        "current_action": _search_action(initial, claims[0], "Establish the first requested claim."),
        "max_actions": limits["max_actions"], "errors": errors,
    }


async def acquire_evidence_node(state: ResearchState) -> dict[str, Any]:
    action = state.get("current_action") or {}
    action_type = str(action.get("action_type", ""))
    claim = _claim(state, str(action.get("claim_id", "")))
    if not claim:
        return {"failure_mode": ResearchFailureMode.SYSTEM.value, "errors": [*state.get("errors", []), "Action referenced an unknown claim."]}

    await _emit(f"Working on {claim.get('title', claim.get('id'))}: {action_type}...")
    history = [*state.get("action_history", []), ResearchAction(**action)]
    result: dict[str, Any] = {"action_type": action_type, "claim_id": claim.get("id")}
    failure_mode = ResearchFailureMode.NONE.value
    queries = list(state.get("queries_used", []))
    sources = list(state.get("sources", []))
    executions = list(state.get("execution_results", []))
    search_actions = int(state.get("search_actions", 0))
    execution_actions = int(state.get("execution_actions", 0))

    if action_type == ActionType.SEARCH.value:
        limits = level_limits(state.get("plan", {}).get("level"))
        remaining = max(0, limits["max_sources"] - len(sources))
        if remaining == 0:
            result.update(source_ids=[], usable_source_ids=[], source_gain=0)
            failure_mode = ResearchFailureMode.RETRIEVAL.value
        else:
            task = SearchTask(
                id=str(action.get("id")), question_id=str(claim.get("id")),
                question=str(claim.get("question")), query=str(action.get("query")),
                search_role=str(action.get("search_role", "discovery")),
                priority=str(claim.get("importance", "should")), claim_type=str(claim.get("kind", "other")),
                preferred_domains=list(action.get("preferred_domains", [])), excluded_domains=[],
                freshness="latest" if claim.get("requires_current_data") else "mixed",
                phase="claim", time_mode="latest" if claim.get("requires_current_data") else "mixed",
            )
            client = ResearchSourceClient(settings.serper_api_key)
            new_sources, executed_queries = await client.search_and_fetch(
                tasks=[task],
                existing_urls={str(item.get("final_url") or item.get("url") or "") for item in sources},
                start_index=len(sources) + 1,
                candidates_per_task=min(limits["candidates_per_task"], remaining + 2),
                fetch_limit=min(limits["fetch_per_action"], remaining),
                backfill_limit=min(limits["backfill_per_action"], remaining),
                excerpt_chars=limits["excerpt_chars"],
            )
            sources, action_ids, source_gain = _merge_sources(sources, new_sources)
            queries.extend(query for query in executed_queries if query not in queries)
            selected = [item for item in sources if str(item.get("id")) in set(action_ids)]
            usable = [item for item in selected if item.get("passages")]
            result.update(
                source_ids=action_ids,
                usable_source_ids=[item.get("id") for item in usable],
                source_gain=source_gain,
            )
            if not usable:
                failure_mode = ResearchFailureMode.RETRIEVAL.value
        search_actions += 1

    elif action_type == ActionType.EXECUTE.value:
        tool_result = await research_execution_tool.run(
            task=str(action.get("reason") or action.get("success_criteria") or claim.get("question")),
            code=str(action.get("code", "")), claim_id=str(claim.get("id")),
        )
        execution = (tool_result.data or {}).get("execution_result") if isinstance(tool_result.data, dict) else None
        if isinstance(execution, dict):
            executions.append(ExecutionResult(**execution))
            result["execution_id"] = execution.get("id")
        else:
            result["error"] = tool_result.error or tool_result.content
            failure_mode = ResearchFailureMode.EXECUTION.value
        execution_actions += 1

    elif action_type == ActionType.COMPARE.value:
        result["compare_ready"] = True
    else:
        failure_mode = ResearchFailureMode.SYSTEM.value
        result["error"] = f"Unsupported action type: {action_type}"

    return {
        "sources": sources, "queries_used": queries, "execution_results": executions,
        "action_history": history, "last_action_result": result,
        "failure_mode": failure_mode, "search_actions": search_actions,
        "execution_actions": execution_actions, "iteration": int(state.get("iteration", 0)) + 1,
    }


async def _evaluate_packet(claim: ResearchClaim, packet: list[dict[str, Any]], retry: bool = False) -> dict[str, Any]:
    return await _generate_json(EVIDENCE_EVALUATOR_PROMPT, {
        "active_claim": claim,
        "sources": packet[:3] if retry else packet,
        "retry_instruction": "Use exact IDs; return empty evidence when unsupported." if retry else "",
    }, 2800 if retry else 4800)


def _validate_evidence(
    state: ResearchState,
    claim: ResearchClaim,
    raw_items: Any,
    packet_sources: list[ResearchSource],
    method: str,
) -> tuple[list[EvidenceItem], list[EvidenceDiagnostic]]:
    source_map = {str(source.get("id")): source for source in packet_sources}
    valid: list[EvidenceItem] = []
    diagnostics: list[EvidenceDiagnostic] = []
    next_index = len(state.get("evidence", [])) + 1
    existing = {
        (str(item.get("claim_id")), str(item.get("source_id")), tuple(item.get("passage_ids", [])), str(item.get("claim", "")).casefold())
        for item in state.get("evidence", [])
    }
    for raw in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw, dict):
            continue
        source_id = str(raw.get("source_id", ""))
        source = source_map.get(source_id)
        passage_ids = _unique(raw.get("passage_ids"), 6)
        allowed = {str(item.get("id")): str(item.get("text", "")) for item in (source or {}).get("passages", [])}
        raw_claim = str(raw.get("claim", "")).strip()
        reason = (
            "invalid_source_id" if not source else
            "missing_claim" if not raw_claim else
            "invalid_passage_id" if not passage_ids or any(pid not in allowed for pid in passage_ids) else ""
        )
        if reason:
            diagnostics.append(EvidenceDiagnostic(
                claim_id=str(claim.get("id")), source_id=source_id, passage_ids=passage_ids,
                reason=reason, raw_claim=raw_claim, extraction_method=method,
            ))
            continue
        confidence = str(raw.get("confidence", "medium")).lower()
        key = (str(claim.get("id")), source_id, tuple(passage_ids), raw_claim.casefold())
        if key in existing:
            continue
        existing.add(key)
        approved = confidence in {"high", "medium"}
        valid.append(EvidenceItem(
            id=f"E{next_index}", claim_id=str(claim.get("id")), question_id=str(claim.get("id")),
            claim=raw_claim, claim_type=str(claim.get("kind", "other")), source_id=source_id,
            passage_ids=passage_ids, supporting_passage=" ".join(allowed[pid] for pid in passage_ids)[:2600],
            source_role=str(source.get("source_role", "unknown")), source_domain=str(source.get("domain", "")),
            evidence_kind=str(raw.get("evidence_kind", "analysis")), confidence=confidence,
            approved=approved, rejection_reason="" if approved else "low_confidence", extraction_method=method,
        ))
        next_index += 1
    return valid, diagnostics


def _role_family(role: str) -> set[str]:
    role = role.casefold()
    families = {"primary"} if role.startswith("primary_") else set()
    if role.startswith("independent_"):
        families.add("independent")
    if role == "academic_primary":
        families.update({"academic", "primary", "independent"})
    if role in {"primary_official", "independent_specialist", "independent_tier_1"}:
        families.add("industry_report")
    if role in {"academic_primary", "independent_specialist", "primary_regulatory"}:
        families.add("security_researcher")
    return families


def _roles_met(claim: ResearchClaim, evidence: list[EvidenceItem]) -> bool:
    required = {str(item).casefold() for item in claim.get("required_source_roles", [])}
    if not required:
        return True
    available = set().union(*(_role_family(str(item.get("source_role", ""))) for item in evidence)) if evidence else set()
    return required.issubset(available)


def _strict_status(
    claim: ResearchClaim,
    claim_evidence: list[EvidenceItem],
    model_status: str,
    failure_mode: str,
) -> str:
    source_count = len({str(item.get("source_id")) for item in claim_evidence})
    direct_conflict = any(item.get("type") == "direct_contradiction" for item in claim.get("conflicts", []))
    if direct_conflict and source_count >= 2:
        return "contested"
    if not claim_evidence:
        exhausted = int(claim.get("no_gain_streak", 0)) >= 2 or int(claim.get("search_attempts", 0)) >= 3
        return "blocked" if exhausted or failure_mode == ResearchFailureMode.EXTRACTION.value else "unknown"
    if claim.get("requires_execution") and not claim.get("execution_ids"):
        return "partial"
    if claim.get("missing_requirements") or not _roles_met(claim, claim_evidence):
        return "partial"
    required_sources = 2 if len(claim.get("required_source_roles", [])) > 1 else 1
    return "supported" if model_status == "supported" and source_count >= required_sources else "partial"


def _update_last_action(history: list[ResearchAction], **updates: Any) -> list[ResearchAction]:
    if not history:
        return history
    history = [*history]
    history[-1] = ResearchAction(**{**history[-1], **updates})
    return history


async def evaluate_result_node(state: ResearchState) -> dict[str, Any]:
    action = state.get("current_action") or {}
    claim = _claim(state, str(action.get("claim_id", "")))
    if not claim:
        return {"failure_mode": ResearchFailureMode.SYSTEM.value}

    updated = ResearchClaim(**claim)
    updated["attempts"] = int(updated.get("attempts", 0)) + 1
    action_type = str(action.get("action_type", ""))
    evidence = list(state.get("evidence", []))
    diagnostics = list(state.get("evidence_diagnostics", []))
    history = list(state.get("action_history", []))
    failure_mode = state.get("failure_mode", ResearchFailureMode.NONE.value)
    model_status = "unknown"
    evidence_gain = 0

    if action_type == ActionType.SEARCH.value:
        updated["search_attempts"] = int(updated.get("search_attempts", 0)) + 1
        ids = set(state.get("last_action_result", {}).get("source_ids", []))
        packet_sources = [source for source in state.get("sources", []) if source.get("id") in ids and source.get("passages")]
        packet = _source_packet(packet_sources)
        if not packet:
            updated["missing_requirements"] = _unique([*updated.get("missing_requirements", []), "No usable source passage was retrieved."], 8)
        else:
            try:
                raw = await _evaluate_packet(updated, packet)
                new_items, new_diagnostics = _validate_evidence(state, updated, raw.get("evidence"), packet_sources, "model")
                if not new_items and new_diagnostics:
                    retry = await _evaluate_packet(updated, packet, retry=True)
                    new_items, retry_diagnostics = _validate_evidence(state, updated, retry.get("evidence"), packet_sources[:3], "model_retry")
                    new_diagnostics.extend(retry_diagnostics)
                    raw = retry
                evidence.extend(new_items)
                diagnostics.extend(new_diagnostics)
                evidence_gain = sum(1 for item in new_items if item.get("approved"))
                assumptions = [
                    value for item in raw.get("evidence", []) if isinstance(item, dict)
                    for value in item.get("assumptions", []) if isinstance(item.get("assumptions"), list)
                ]
                updated["assumptions"] = _unique([*updated.get("assumptions", []), *assumptions], 12)
                updated["conflicts"] = [*updated.get("conflicts", []), *[item for item in raw.get("conflicts", []) if isinstance(item, dict)]][-12:]
                updated["missing_requirements"] = _unique(raw.get("missing_requirements"), 8)
                model_status = str(raw.get("claim_status", "unknown")).lower()
                if not new_items and new_diagnostics:
                    failure_mode = ResearchFailureMode.EXTRACTION.value
                elif new_items:
                    failure_mode = ResearchFailureMode.NONE.value
            except Exception as exc:
                diagnostics.append(EvidenceDiagnostic(
                    claim_id=str(updated.get("id")), reason=f"evaluator_error:{type(exc).__name__}",
                    raw_claim=str(exc), extraction_method="model",
                ))
                failure_mode = ResearchFailureMode.EXTRACTION.value

        updated["no_gain_streak"] = 0 if evidence_gain else int(updated.get("no_gain_streak", 0)) + 1
        limits = level_limits(state.get("plan", {}).get("level"))
        if int(updated.get("search_attempts", 0)) >= limits["max_searches_per_claim"] or int(updated.get("no_gain_streak", 0)) >= limits["max_no_gain"]:
            if evidence_gain == 0:
                updated["missing_requirements"] = _unique([*updated.get("missing_requirements", []), "Further near-duplicate searches were stopped after diminishing returns."], 8)

    elif action_type == ActionType.EXECUTE.value:
        execution_id = str(state.get("last_action_result", {}).get("execution_id", ""))
        execution = next((item for item in state.get("execution_results", []) if item.get("id") == execution_id), None)
        if execution and execution.get("success"):
            updated["execution_ids"] = _unique([*updated.get("execution_ids", []), execution_id], 8)
            updated["missing_requirements"] = [item for item in updated.get("missing_requirements", []) if "comput" not in item.casefold()]
            model_status = "supported"
        else:
            updated["missing_requirements"] = _unique([*updated.get("missing_requirements", []), "Computational verification did not complete."], 8)

    elif action_type == ActionType.COMPARE.value:
        try:
            raw = await _generate_json(CONFLICT_CLASSIFIER_PROMPT, {
                "claim": updated,
                "evidence": [item for item in evidence if item.get("claim_id") == updated.get("id")],
            }, 2200)
            updated["conflicts"] = [{
                "type": raw.get("type", "none"), "description": raw.get("explanation", ""),
                "resolution": raw.get("resolution", ""), "needs_more_research": raw.get("needs_more_research", False),
            }]
            model_status = "contested" if raw.get("type") == "direct_contradiction" else "supported"
        except Exception as exc:
            failure_mode = ResearchFailureMode.EXTRACTION.value
            diagnostics.append(EvidenceDiagnostic(
                claim_id=str(updated.get("id")), reason="conflict_classifier_error",
                raw_claim=str(exc), extraction_method="conflict",
            ))

    claim_evidence = [item for item in evidence if item.get("claim_id") == updated.get("id") and item.get("approved")]
    updated["evidence_ids"] = _unique([item.get("id") for item in claim_evidence], 30)
    updated["source_ids"] = _unique([item.get("source_id") for item in claim_evidence], 20)
    updated["status"] = _strict_status(updated, claim_evidence, model_status, str(failure_mode))
    history = _update_last_action(
        history,
        source_gain=int(state.get("last_action_result", {}).get("source_gain", 0)),
        evidence_gain=evidence_gain,
    )
    return {
        "claim_ledger": _update_claim(state.get("claim_ledger", []), updated),
        "evidence": evidence, "evidence_diagnostics": diagnostics,
        "action_history": history, "failure_mode": failure_mode, "current_action": None,
    }


def _coverage(ledger: list[ResearchClaim]) -> float:
    if not ledger:
        return 0.0
    score = {"supported": 1.0, "contested": 0.75, "partial": 0.5, "unknown": 0.0, "blocked": 0.0}
    return round(sum(score.get(str(item.get("status")), 0.0) for item in ledger) / len(ledger), 3)


def _search_allowed(claim: ResearchClaim, limits: dict[str, int]) -> bool:
    return (
        int(claim.get("search_attempts", 0)) < limits["max_searches_per_claim"]
        and int(claim.get("no_gain_streak", 0)) < limits["max_no_gain"]
    )


def _next_claim(state: ResearchState) -> ResearchClaim | None:
    ledger = state.get("claim_ledger", [])
    limits = level_limits(state.get("plan", {}).get("level"))
    order = {str(item.get("id")): index for index, item in enumerate(ledger)}
    incomplete = [item for item in ledger if item.get("status") not in {"supported", "contested"}]
    unattempted = [item for item in incomplete if int(item.get("attempts", 0)) == 0]
    if unattempted:
        return min(unattempted, key=lambda item: (0 if item.get("importance") == "must" else 1, order[str(item.get("id"))]))
    eligible = [
        item for item in incomplete
        if _search_allowed(item, limits)
        or (item.get("requires_execution") and item.get("evidence_ids") and not item.get("execution_ids"))
        or item.get("conflicts")
    ]
    return min(
        eligible,
        key=lambda item: (
            0 if item.get("importance") == "must" else 1,
            int(item.get("attempts", 0)),
            order[str(item.get("id"))],
        ),
    ) if eligible else None


def _budget_exhausted(state: ResearchState) -> bool:
    limits = level_limits(state.get("plan", {}).get("level"))
    return int(state.get("iteration", 0)) >= int(state.get("max_actions", limits["max_actions"]))


def _query_similarity(first: str, second: str) -> float:
    a, b = set(_WORD_RE.findall(first.casefold())), set(_WORD_RE.findall(second.casefold()))
    return len(a & b) / max(1, len(a | b))


async def decide_next_action_node(state: ResearchState) -> dict[str, Any]:
    ledger = state.get("claim_ledger", [])
    all_attempted = bool(ledger) and all(int(item.get("attempts", 0)) > 0 for item in ledger)
    must = [item for item in ledger if item.get("importance") == "must"]
    must_ready = bool(must) and all(item.get("status") in {"supported", "contested"} for item in must)
    remaining = _next_claim(state)
    if (all_attempted and must_ready and _coverage(ledger) >= 0.75) or not remaining or _budget_exhausted(state):
        return {
            "current_action": ResearchAction(
                id=f"A{len(state.get('action_history', [])) + 1}", action_type="synthesize",
                reason="Every requested claim was attempted and no higher-value action remains.",
            ),
            "failure_mode": ResearchFailureMode.BUDGET.value if _budget_exhausted(state) and not must_ready else state.get("failure_mode", "none"),
        }

    limits = level_limits(state.get("plan", {}).get("level"))
    active_evidence = [
        {"claim": item.get("claim"), "supporting_passage": item.get("supporting_passage"), "source_id": item.get("source_id")}
        for item in state.get("evidence", [])
        if item.get("claim_id") == remaining.get("id") and item.get("approved")
    ][:8]
    summary = {
        "topic": state.get("plan", {}).get("topic"), "active_claim": remaining,
        "active_evidence": active_evidence,
        "previous_actions_for_claim": [
            {"action_type": item.get("action_type"), "query": item.get("query"), "evidence_gain": item.get("evidence_gain", 0)}
            for item in state.get("action_history", []) if item.get("claim_id") == remaining.get("id")
        ],
        "search_allowed": _search_allowed(remaining, limits),
        "execution_budget_remaining": limits["max_execution_actions"] - int(state.get("execution_actions", 0)),
    }
    try:
        raw = await _generate_json(NEXT_ACTION_PROMPT, summary, 2600)
    except Exception:
        raw = {}

    action_type = str(raw.get("action_type", "search")).lower()
    can_execute = (
        remaining.get("requires_execution") and remaining.get("evidence_ids")
        and not remaining.get("execution_ids")
        and int(state.get("execution_actions", 0)) < limits["max_execution_actions"]
        and bool(str(raw.get("code", "")).strip())
    )
    can_compare = bool(remaining.get("conflicts"))
    if action_type == "execute" and not can_execute:
        action_type = "search"
    if action_type == "compare" and not can_compare:
        action_type = "search"
    if action_type not in {"search", "execute", "compare"}:
        action_type = "search"
    if action_type == "search" and not _search_allowed(remaining, limits):
        action_type = "execute" if can_execute else "compare" if can_compare else "synthesize"

    action = ResearchAction(
        id=f"A{len(state.get('action_history', [])) + 1}", action_type=action_type,
        claim_id=str(remaining.get("id")),
        reason=str(raw.get("reason") or "Resolve the active claim without repeating low-yield work."),
        query=str(raw.get("query") or _compact_query(str(state.get("plan", {}).get("topic", "")), remaining))[:240],
        search_role=str(raw.get("search_role", "primary")),
        preferred_domains=_unique(raw.get("preferred_domains") or remaining.get("preferred_domains"), 6),
        code=str(raw.get("code", "")),
        success_criteria=str(raw.get("success_criteria", "Produce direct support for the active claim.")),
    )
    if action_type == "search":
        prior = [str(item.get("query", "")) for item in state.get("action_history", []) if item.get("action_type") == "search"]
        if any(_query_similarity(action.get("query", ""), query) >= 0.72 for query in prior):
            missing = " ".join(remaining.get("missing_requirements", [])[:1]) or remaining.get("question", "")
            action["query"] = " ".join(f"{remaining.get('title', '')} {missing} authoritative evidence".split())[:220]
    return {"current_action": action}


def _build_review(state: ResearchState) -> ResearchReview:
    ledger = state.get("claim_ledger", [])
    supported = [str(item.get("id")) for item in ledger if item.get("status") == "supported"]
    unresolved = [
        f"{item.get('title')}: {', '.join(item.get('missing_requirements', [])) or item.get('status', 'unknown')}"
        for item in ledger if item.get("status") not in {"supported", "contested"}
    ]
    contradictions = [
        str(conflict.get("description", ""))
        for item in ledger for conflict in item.get("conflicts", [])
        if conflict.get("type") == "direct_contradiction" and conflict.get("description")
    ]
    system_limitations = list(state.get("errors", []))
    if state.get("failure_mode") in {"extraction_failure", "execution_failure", "system_failure"}:
        system_limitations.append(f"Internal research limitation: {state.get('failure_mode')}")
    must = [item for item in ledger if item.get("importance") == "must"]
    decision_safe = bool(must) and all(item.get("status") in {"supported", "contested"} for item in must)
    evidence_count = sum(1 for item in state.get("evidence", []) if item.get("approved"))
    status = "complete" if decision_safe and _coverage(ledger) >= 0.85 else "partial" if evidence_count else "insufficient"
    return ResearchReview(
        verification_status="verified" if status == "complete" else "incomplete",
        research_status=status, decision_safe=decision_safe, coverage_score=_coverage(ledger),
        supported_claim_ids=supported, unresolved_claims=unresolved,
        contradictions=contradictions, system_limitations=_unique(system_limitations, 12),
        failure_mode=str(state.get("failure_mode", "none")),
    )


def _fallback_section(claim: ResearchClaim, evidence: list[EvidenceItem]) -> str:
    lines = [f"## {claim.get('title')}"]
    for item in evidence:
        lines.append(f"- {item.get('claim')} {item.get('source_id')}")
    return "\n\n".join(lines)


def _clean_section(text: str, heading: str) -> str:
    text = re.split(r"\n#{1,2}\s+Sources\b", text.strip(), maxsplit=1, flags=re.I)[0].strip()
    text = re.sub(r"^#{1,3}\s+.*?\n+", "", text, count=1)
    return f"## {heading}\n\n{text}".strip()


async def _render_section(
    claim: ResearchClaim,
    evidence: list[EvidenceItem],
    sources: list[ResearchSource],
    executions: list[ExecutionResult],
    word_budget: int,
    token_budget: int,
) -> str:
    payload = {
        "heading": f"## {claim.get('title')}", "claim": claim, "word_budget": word_budget,
        "approved_evidence": evidence,
        "sources": [{
            "id": source.get("id"), "title": source.get("title"),
            "source_role": source.get("source_role"), "publication_date": source.get("publication_date"),
        } for source in sources],
        "computational_checks": executions,
    }
    try:
        section = _clean_section(await _generate_text(REPORT_SECTION_PROMPT, payload, token_budget), str(claim.get("title")))
        allowed = {str(item.get("source_id")) for item in evidence}
        return section if set(re.findall(r"\bS\d+\b", section)) & allowed else _fallback_section(claim, evidence)
    except Exception:
        return _fallback_section(claim, evidence)


def _split_overview(text: str) -> tuple[str, str]:
    executive = re.search(r"## Executive Summary\s*(.*?)(?=\n## Conclusion|\Z)", text, re.S | re.I)
    conclusion = re.search(r"## Conclusion\s*(.*)$", text, re.S | re.I)
    return (
        executive.group(1).strip() if executive else "",
        conclusion.group(1).strip() if conclusion else "",
    )


def _dedupe_citations(report: str, sources: list[ResearchSource]) -> tuple[str, list[ResearchSource]]:
    unique: list[ResearchSource] = []
    first_by_key: dict[str, str] = {}
    replacements: dict[str, str] = {}
    for source in sources:
        key = _source_key(source)
        source_id = str(source.get("id"))
        if key and key in first_by_key:
            replacements[source_id] = first_by_key[key]
        else:
            if key:
                first_by_key[key] = source_id
            unique.append(source)
    for old, new in replacements.items():
        report = re.sub(rf"\b{re.escape(old)}\b", new, report)
    used = set(re.findall(r"\bS\d+\b", report))
    return report, [source for source in unique if str(source.get("id")) in used]


async def synthesize_report_node(state: ResearchState) -> dict[str, Any]:
    await _emit("Synthesizing every researched claim into a complete report...")
    approved = [item for item in state.get("evidence", []) if item.get("approved")]
    review = _build_review(state)
    topic = str(state.get("plan", {}).get("topic") or state.get("topic_hint") or "Research Report")
    if not approved:
        report = (
            f"# {topic}\n\n## Research limitation\n\nNo claim passed evidence validation. "
            "This indicates a retrieval or evidence-mapping limitation, not that reliable information does not exist."
        )
        if review.get("unresolved_claims"):
            report += "\n\n## Limitations and unresolved issues\n\n" + "\n".join(f"- {item}" for item in review["unresolved_claims"])
        return {"final_report": report, "review": review, "current_action": None}

    source_by_id = {str(item.get("id")): item for item in state.get("sources", [])}
    renderable = [claim for claim in state.get("claim_ledger", []) if any(item.get("claim_id") == claim.get("id") for item in approved)]
    target_words = _safe_int(state.get("plan", {}).get("target_words"), 1600)
    word_budget = max(180, min(700, target_words // max(1, len(renderable))))
    report_tokens = level_limits(state.get("plan", {}).get("level"))["report_tokens"]
    section_tokens = max(900, min(2200, report_tokens // max(2, len(renderable))))

    sections: list[str] = []
    for claim in renderable:
        claim_evidence = [item for item in approved if item.get("claim_id") == claim.get("id")]
        claim_sources = [source_by_id[source_id] for source_id in _unique([item.get("source_id") for item in claim_evidence], 20) if source_id in source_by_id]
        claim_exec = [item for item in state.get("execution_results", []) if item.get("claim_id") == claim.get("id")]
        sections.append(await _render_section(claim, claim_evidence, claim_sources, claim_exec, word_budget, section_tokens))

    summaries = [{"title": claim.get("title"), "status": claim.get("status"), "section": section[:1200]} for claim, section in zip(renderable, sections)]
    try:
        overview = await _generate_text(REPORT_OVERVIEW_PROMPT, {
            "request": state.get("original_request"), "topic": topic,
            "section_summaries": summaries, "review": review,
        }, min(1800, report_tokens // 3))
        executive, conclusion = _split_overview(overview)
    except Exception:
        executive = conclusion = ""
    if not executive:
        executive = "The available evidence supports the findings developed below, while unresolved requested dimensions are stated separately."
    if not conclusion:
        conclusion = "The evidence supports a qualified conclusion rather than unrestricted autonomy or certainty."

    report = f"# {topic}\n\n## Executive Summary\n\n{executive}\n\n" + "\n\n".join(sections)
    report += f"\n\n## Conclusion\n\n{conclusion}"
    if review.get("research_status") != "complete" or review.get("unresolved_claims") or review.get("system_limitations"):
        items = [*review.get("unresolved_claims", []), *review.get("system_limitations", [])]
        report += "\n\n## Limitations and unresolved issues\n\n" + "\n".join(f"- {item}" for item in _unique(items, 20))

    cited_ids = {str(item.get("source_id")) for item in approved}
    cited_sources = [source for source in state.get("sources", []) if str(source.get("id")) in cited_ids]
    report = re.sub(r"\bS\d+\b", lambda match: match.group(0) if match.group(0) in cited_ids else "", report)
    report, cited_sources = _dedupe_citations(report, cited_sources)
    report += "\n\n## Sources\n" + "\n".join(
        f"- {source.get('id')} — {source.get('title')} ({source.get('source_role', 'source')})"
        for source in cited_sources
    )
    return {"final_report": report.strip(), "review": review, "current_action": None}