from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from .models import (
    FavoriteRequest,
    MusicClientState,
    MusicSearchResponse,
    MusicTrack,
    QueueRequest,
)
from .runtime import music_store, youtube_music_service
from .service import MusicServiceError


router = APIRouter(prefix="/api/music", tags=["music"])
_METADATA_MAX_AGE = timedelta(days=29)


async def _refresh_stale_tracks(tracks: list[MusicTrack]) -> list[MusicTrack]:
    if not tracks or not youtube_music_service.configured:
        return tracks
    cutoff = datetime.now(timezone.utc) - _METADATA_MAX_AGE
    stale_ids = [
        track.video_id
        for track in tracks
        if track.metadata_refreshed_at < cutoff
    ]
    if not stale_ids:
        return tracks
    try:
        refreshed = await youtube_music_service.get_tracks(stale_ids)
    except MusicServiceError:
        return tracks
    refreshed_by_id = {track.video_id: track for track in refreshed}
    return [refreshed_by_id.get(track.video_id, track) for track in tracks]


def _http_error(exc: MusicServiceError) -> HTTPException:
    status = 503 if "not configured" in str(exc).casefold() else 502
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/search", response_model=MusicSearchResponse)
async def search_music(
    q: str = Query(min_length=1, max_length=180),
    limit: int = Query(default=8, ge=1, le=12),
) -> MusicSearchResponse:
    try:
        results = await youtube_music_service.search(q, limit=limit)
    except MusicServiceError as exc:
        raise _http_error(exc) from exc
    return MusicSearchResponse(query=q, results=results)


@router.get("/favorites", response_model=list[MusicTrack])
async def list_favorites() -> list[MusicTrack]:
    tracks = await music_store.list_favorites()
    refreshed = await _refresh_stale_tracks(tracks)
    for before, after in zip(tracks, refreshed):
        if before.metadata_refreshed_at != after.metadata_refreshed_at:
            await music_store.add_favorite(after)
    return refreshed


@router.post("/favorites", response_model=list[MusicTrack])
async def add_favorite(request: FavoriteRequest) -> list[MusicTrack]:
    return await music_store.add_favorite(request.track)


@router.delete("/favorites/{video_id}", response_model=list[MusicTrack])
async def remove_favorite(video_id: str) -> list[MusicTrack]:
    return await music_store.remove_favorite(video_id.strip())


@router.get("/queue", response_model=list[MusicTrack])
async def list_queue() -> list[MusicTrack]:
    return await music_store.list_queue()


@router.post("/queue", response_model=list[MusicTrack])
async def enqueue(request: QueueRequest) -> list[MusicTrack]:
    return await music_store.enqueue(request.track, position=request.position)


@router.post("/queue/next", response_model=MusicTrack | None)
async def pop_next() -> MusicTrack | None:
    track = await music_store.pop_next()
    if track is not None:
        await music_store.set_current_track(track)
    return track


@router.delete("/queue", status_code=204)
async def clear_queue() -> None:
    await music_store.clear_queue()


@router.get("/state", response_model=MusicClientState)
async def get_music_state() -> MusicClientState:
    return await music_store.get_client_state()


@router.post("/state", response_model=MusicClientState)
async def set_music_state(state: MusicClientState) -> MusicClientState:
    return await music_store.set_client_state(state)
