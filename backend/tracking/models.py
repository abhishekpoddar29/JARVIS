from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TrackingStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DENIED = "DENIED"
    ENDED = "ENDED"
    EXPIRED = "EXPIRED"


TERMINAL_TRACKING_STATUSES = {
    TrackingStatus.DENIED,
    TrackingStatus.ENDED,
    TrackingStatus.EXPIRED,
}


class TrackingSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    target_name: str
    target_phone: str
    requester_name: str = "Abhi"
    duration_minutes: int
    status: TrackingStatus = TrackingStatus.PENDING
    created_at: datetime
    expires_at: datetime
    consented_at: datetime | None = None
    ended_at: datetime | None = None
    last_update_at: datetime | None = None


class CreateTrackingSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_name: str = Field(min_length=1, max_length=100)
    target_phone: str = Field(min_length=8, max_length=20)
    duration_minutes: int = Field(default=30, ge=5, le=60)

    @field_validator("target_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("target_name is required")
        return cleaned

    @field_validator("target_phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        cleaned = value.strip().replace(" ", "").replace("-", "")
        digits = cleaned[1:] if cleaned.startswith("+") else cleaned
        if not digits.isdigit() or not 8 <= len(digits) <= 15:
            raise ValueError(
                "target_phone must contain 8 to 15 digits, "
                "optionally prefixed with +"
            )
        return f"+{digits}"


class TrackingSessionCreated(BaseModel):
    session_id: str
    tracking_url: str
    target_name: str
    masked_phone: str
    duration_minutes: int
    status: TrackingStatus
    created_at: datetime
    expires_at: datetime


class TrackingSessionStatus(BaseModel):
    session_id: str
    target_name: str
    masked_phone: str
    duration_minutes: int
    status: TrackingStatus
    created_at: datetime
    expires_at: datetime
    consented_at: datetime | None = None
    ended_at: datetime | None = None
    last_update_at: datetime | None = None


class PublicTrackingView(BaseModel):
    requester_name: str
    target_name: str
    duration_minutes: int
    status: TrackingStatus
    expires_at: datetime
    consented_at: datetime | None = None
    ended_at: datetime | None = None
    last_update_at: datetime | None = None


class PublicDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["ALLOW", "DENY"]


class LocationPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: float = Field(ge=0, le=100_000)
    heading: float | None = Field(default=None, ge=0, lt=360)
    speed: float | None = Field(default=None, ge=0, le=500)
    timestamp: int = Field(ge=0)
    server_timestamp: datetime | None = None