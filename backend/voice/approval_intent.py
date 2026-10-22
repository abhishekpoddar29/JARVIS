from __future__ import annotations

import re
from enum import Enum


class VoiceApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    CANCEL = "CANCEL"


_APPROVE_PHRASES = {
    "approve",
    "approve it",
    "approved",
    "yes approve",
    "yes approve it",
    "proceed",
    "proceed with it",
    "go ahead",
    "go ahead with it",
    "confirm",
    "confirmed",
}
_REJECT_PHRASES = {
    "reject",
    "reject it",
    "deny",
    "deny it",
    "decline",
    "decline it",
    "do not approve",
    "dont approve",
    "no do not approve",
    "no dont approve",
}
_CANCEL_PHRASES = {
    "cancel",
    "cancel it",
    "stop",
    "stop it",
    "abort",
    "abort it",
}


def _normalize(text: str) -> str:
    normalized = text.casefold().replace("’", "'")
    normalized = re.sub(r"[^a-z0-9'\s]", " ", normalized)
    normalized = normalized.replace("don't", "dont")
    return " ".join(normalized.split())


def parse_voice_approval_intent(text: str) -> VoiceApprovalDecision | None:
    """Return a deterministic approval decision for a short spoken command."""
    normalized = _normalize(text)
    if not normalized:
        return None
    if normalized in _CANCEL_PHRASES:
        return VoiceApprovalDecision.CANCEL
    if normalized in _REJECT_PHRASES:
        return VoiceApprovalDecision.REJECT
    if normalized in _APPROVE_PHRASES:
        return VoiceApprovalDecision.APPROVE
    return None
