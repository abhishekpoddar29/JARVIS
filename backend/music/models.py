from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class MusicTrack(BaseModel):
    """Public track metadata sent between FastAPI and the React widget."""

    video_id: str = Field(min_length=6, max_length=32)
    title: str = Field(min_length=1, max_length=300)
    channel_title: str = Field(default="", max_length=200)
    thumbnail: str = Field(default="", max_length=2048)
    duration_seconds: int = Field(default=0, ge=0, le=86_400)
    published_at: str | None = None
    metadata_refreshed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("video_id", "title", "channel_title", "thumbnail")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class MusicSearchResponse(BaseModel):
    query: str
    results: list[MusicTrack]


class FavoriteRequest(BaseModel):
    track: MusicTrack


class QueueRequest(BaseModel):
    track: MusicTrack
    position: Literal["next", "end"] = "end"


class MusicClientState(BaseModel):
    playback_state: Literal[
        "idle",
        "loading",
        "playing",
        "paused",
        "buffering",
        "blocked",
        "error",
    ] = "idle"
    current_track: MusicTrack | None = None
    visible: bool = False
    position_seconds: float = Field(default=0, ge=0)
    volume: int = Field(default=70, ge=0, le=100)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
