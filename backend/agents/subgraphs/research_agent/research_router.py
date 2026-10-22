from __future__ import annotations

from .research_state import ResearchState


def route_after_decision(state: ResearchState) -> str:
    action = state.get("current_action") or {}
    return "synthesize" if action.get("action_type") == "synthesize" else "acquire"