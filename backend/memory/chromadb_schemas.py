from __future__ import annotations

from datetime import date, datetime, UTC
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enums for structured classification ──────────────────────────────────────────

class MemoryType(str, Enum):
    PREFERENCE = "preference"
    PERSONAL_FACT = "personal_fact"
    DECISION = "decision"
    TASK_OUTCOME = "task_outcome"
    PROJECT_CONTEXT = "project_context"
    RELATIONSHIP = "relationship"
    BOUNDARY = "boundary"
    RECURRING_TOPIC = "recurring_topic"


class SensitivityLevel(str, Enum):
    LOW = "low"
    PERSONAL = "personal"
    PRIVATE = "private"
    SECRET = "secret"


# ── Core Schema ──────────────────────────────────────────────────────────────────

class MemoryEntry(BaseModel):
    """
    Represents a single distilled semantic memory entry.
    Based on the Jarvis 2.0 Architecture Doc Section 4.5.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: MemoryType
    content: str
    source_date: date = Field(default_factory=lambda: datetime.now(UTC).date())
    source_ref: Optional[str] = None  # e.g., "session:2026-06-23" or "manual_edit"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    pinned: bool = False
    sensitivity: SensitivityLevel = SensitivityLevel.LOW
    last_used: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    embedding_model: str = "models/text-embedding-004" # Default Gemini embedding model
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_chroma_metadata(self) -> dict[str, Any]:
        """
        Flattens the model into a dictionary of primitives for ChromaDB metadata.
        ChromaDB strictly requires metadata values to be str, int, float, or bool.
        """
        return {
            "id": self.id,
            "type": self.type.value,
            "source_date": self.source_date.isoformat(),
            "source_ref": self.source_ref or "unknown",
            "confidence": self.confidence,
            "pinned": self.pinned,
            "sensitivity": self.sensitivity.value,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "embedding_model": self.embedding_model,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_chroma_result(cls, chroma_id: str, document: str, metadata: dict[str, Any]) -> "MemoryEntry":
        """
        Reconstructs the Pydantic model from a ChromaDB query result.
        """
        # Parse ISO strings back into date/datetime objects
        source_date = datetime.strptime(metadata.get("source_date", "1970-01-01"), "%Y-%m-%d").date()
        last_used_str = metadata.get("last_used")
        expires_at_str = metadata.get("expires_at")
        created_at_str = metadata.get("created_at")
        updated_at_str = metadata.get("updated_at")

        return cls(
            id=chroma_id,
            type=MemoryType(metadata.get("type", "personal_fact")),
            content=document, # In ChromaDB, the text is stored in the 'document' field
            source_date=source_date,
            source_ref=metadata.get("source_ref"),
            confidence=float(metadata.get("confidence", 0.5)),
            pinned=bool(metadata.get("pinned", False)),
            sensitivity=SensitivityLevel(metadata.get("sensitivity", "low")),
            last_used=datetime.fromisoformat(last_used_str) if last_used_str else None,
            expires_at=datetime.fromisoformat(expires_at_str) if expires_at_str else None,
            embedding_model=metadata.get("embedding_model", "unknown"),
            created_at=datetime.fromisoformat(created_at_str) if created_at_str else datetime.now(UTC),
            updated_at=datetime.fromisoformat(updated_at_str) if updated_at_str else datetime.now(UTC),
        )