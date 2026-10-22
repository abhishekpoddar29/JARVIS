from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

try:
    from redis.asyncio import Redis
except ImportError:  # Allows the feature to degrade gracefully during local setup.
    Redis = None  # type: ignore[assignment,misc]

from .models import MusicClientState, MusicTrack


class MusicStore:
    """Redis-backed music state with an in-memory fallback for local development."""

    def __init__(
        self,
        redis_url: str,
        *,
        owner: str = "abhi",
        persistence_path: str | Path | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._owner = owner
        self._persistence_path = Path(
            persistence_path
            or (
                Path(__file__).resolve().parents[1]
                / "data"
                / f"music_favorites_{owner}.json"
            )
        )
        self._redis: Any = None
        self._lock = asyncio.Lock()
        self._memory_favorites: dict[str, MusicTrack] = {}
        self._memory_favorite_order: list[str] = []
        self._memory_queue: list[MusicTrack] = []
        self._memory_recent: list[MusicTrack] = []
        self._memory_state = MusicClientState()

    @property
    def using_redis(self) -> bool:
        return self._redis is not None

    async def connect(self) -> None:
        if self._redis is None and Redis is not None:
            try:
                client = Redis.from_url(
                    self._redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    health_check_interval=30,
                )
                await client.ping()
                self._redis = client
                print("[MusicStore] Connected to Redis.")
            except Exception as exc:
                self._redis = None
                print(
                    "[MusicStore] Redis unavailable; "
                    f"using durable file fallback: {exc}"
                )

        await self._restore_persistent_favorites()

    async def disconnect(self) -> None:
        client = self._redis
        self._redis = None
        if client is not None:
            await client.aclose()

    def _key(self, suffix: str) -> str:
        return f"music:{suffix}:{self._owner}"

    @staticmethod
    def _dump_track(track: MusicTrack) -> str:
        return track.model_dump_json()

    @staticmethod
    def _load_track(raw: str | None) -> MusicTrack | None:
        if not raw:
            return None
        try:
            return MusicTrack.model_validate_json(raw)
        except Exception:
            return None

    def _read_persistent_favorites_sync(self) -> list[MusicTrack]:
        try:
            if not self._persistence_path.is_file():
                return []
            payload = json.loads(
                self._persistence_path.read_text(encoding="utf-8")
            )
            if not isinstance(payload, list):
                return []
            tracks: list[MusicTrack] = []
            for item in payload:
                try:
                    tracks.append(MusicTrack.model_validate(item))
                except Exception:
                    continue
            return tracks
        except Exception:
            return []

    def _write_persistent_favorites_sync(
        self,
        tracks: list[MusicTrack],
    ) -> None:
        self._persistence_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        temporary = self._persistence_path.with_suffix(
            self._persistence_path.suffix + ".tmp"
        )
        temporary.write_text(
            json.dumps(
                [track.model_dump(mode="json") for track in tracks],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self._persistence_path)

    async def _read_persistent_favorites(self) -> list[MusicTrack]:
        return await asyncio.to_thread(
            self._read_persistent_favorites_sync
        )

    async def _persist_favorites(
        self,
        tracks: list[MusicTrack],
    ) -> None:
        await asyncio.to_thread(
            self._write_persistent_favorites_sync,
            [track.model_copy(deep=True) for track in tracks],
        )

    async def _write_favorites_to_redis(
        self,
        tracks: list[MusicTrack],
    ) -> None:
        if self._redis is None or not tracks:
            return

        now = time.time()
        async with self._redis.pipeline(transaction=True) as pipe:
            for index, track in enumerate(tracks):
                pipe.hset(
                    self._key("favorites"),
                    track.video_id,
                    self._dump_track(track),
                )
                pipe.zadd(
                    self._key("favorite_order"),
                    {track.video_id: now - index},
                )
            await pipe.execute()

    async def _restore_persistent_favorites(self) -> None:
        disk_tracks = await self._read_persistent_favorites()

        if self._redis is not None:
            redis_ids = await self._redis.zrevrange(
                self._key("favorite_order"),
                0,
                -1,
            )
            if redis_ids:
                values = await self._redis.hmget(
                    self._key("favorites"),
                    redis_ids,
                )
                redis_tracks = [
                    track
                    for raw in values
                    if (track := self._load_track(raw))
                ]
                if redis_tracks:
                    await self._persist_favorites(redis_tracks)
                    return

            if disk_tracks:
                await self._write_favorites_to_redis(disk_tracks)
                return

        async with self._lock:
            self._memory_favorites = {
                track.video_id: track.model_copy(deep=True)
                for track in disk_tracks
            }
            self._memory_favorite_order = [
                track.video_id for track in disk_tracks
            ]

    async def _primary_favorites(self) -> list[MusicTrack]:
        if self._redis is not None:
            ids = await self._redis.zrevrange(
                self._key("favorite_order"),
                0,
                -1,
            )
            if not ids:
                return []
            values = await self._redis.hmget(
                self._key("favorites"),
                ids,
            )
            return [
                track
                for raw in values
                if (track := self._load_track(raw))
            ]

        async with self._lock:
            return [
                self._memory_favorites[video_id].model_copy(deep=True)
                for video_id in self._memory_favorite_order
                if video_id in self._memory_favorites
            ]

    async def set_client_state(self, state: MusicClientState | dict[str, Any]) -> MusicClientState:
        parsed = state if isinstance(state, MusicClientState) else MusicClientState.model_validate(state)
        if self._redis is not None:
            await self._redis.set(
                self._key("state"),
                parsed.model_dump_json(),
                ex=7 * 24 * 60 * 60,
            )
        else:
            async with self._lock:
                self._memory_state = parsed
        return parsed

    async def get_client_state(self) -> MusicClientState:
        if self._redis is not None:
            raw = await self._redis.get(self._key("state"))
            if raw:
                try:
                    return MusicClientState.model_validate_json(raw)
                except Exception:
                    pass
        async with self._lock:
            return self._memory_state.model_copy(deep=True)

    async def current_track(self) -> MusicTrack | None:
        return (await self.get_client_state()).current_track

    async def set_current_track(
        self,
        track: MusicTrack,
        *,
        playback_state: str = "loading",
        visible: bool = True,
    ) -> None:
        current = await self.get_client_state()
        payload = current.model_copy(
            update={
                "current_track": track,
                "playback_state": playback_state,
                "visible": visible,
            }
        )
        await self.set_client_state(payload)
        await self.add_recent(track)

    async def add_favorite(self, track: MusicTrack) -> list[MusicTrack]:
        if self._redis is not None:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.hset(
                    self._key("favorites"),
                    track.video_id,
                    self._dump_track(track),
                )
                pipe.zadd(
                    self._key("favorite_order"),
                    {track.video_id: time.time()},
                )
                await pipe.execute()
        else:
            async with self._lock:
                self._memory_favorites[track.video_id] = track.model_copy(deep=True)
                if track.video_id in self._memory_favorite_order:
                    self._memory_favorite_order.remove(track.video_id)
                self._memory_favorite_order.insert(0, track.video_id)
        favorites = await self._primary_favorites()
        await self._persist_favorites(favorites)
        return favorites

    async def remove_favorite(self, video_id: str) -> list[MusicTrack]:
        if self._redis is not None:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.hdel(self._key("favorites"), video_id)
                pipe.zrem(self._key("favorite_order"), video_id)
                await pipe.execute()
        else:
            async with self._lock:
                self._memory_favorites.pop(video_id, None)
                if video_id in self._memory_favorite_order:
                    self._memory_favorite_order.remove(video_id)
        favorites = await self._primary_favorites()
        await self._persist_favorites(favorites)
        return favorites

    async def list_favorites(self) -> list[MusicTrack]:
        primary_tracks = await self._primary_favorites()
        if primary_tracks:
            return primary_tracks

        disk_tracks = await self._read_persistent_favorites()
        if not disk_tracks:
            return []

        if self._redis is not None:
            await self._write_favorites_to_redis(disk_tracks)
        else:
            async with self._lock:
                self._memory_favorites = {
                    track.video_id: track.model_copy(deep=True)
                    for track in disk_tracks
                }
                self._memory_favorite_order = [
                    track.video_id for track in disk_tracks
                ]
        return disk_tracks

    async def enqueue(self, track: MusicTrack, *, position: str = "end") -> list[MusicTrack]:
        if self._redis is not None:
            key = self._key("queue")
            if position == "next":
                await self._redis.lpush(key, self._dump_track(track))
            else:
                await self._redis.rpush(key, self._dump_track(track))
            await self._redis.expire(key, 7 * 24 * 60 * 60)
        else:
            async with self._lock:
                if position == "next":
                    self._memory_queue.insert(0, track.model_copy(deep=True))
                else:
                    self._memory_queue.append(track.model_copy(deep=True))
        return await self.list_queue()

    async def list_queue(self) -> list[MusicTrack]:
        if self._redis is not None:
            values = await self._redis.lrange(self._key("queue"), 0, -1)
            return [track for raw in values if (track := self._load_track(raw))]
        async with self._lock:
            return [track.model_copy(deep=True) for track in self._memory_queue]

    async def pop_next(self) -> MusicTrack | None:
        if self._redis is not None:
            return self._load_track(await self._redis.lpop(self._key("queue")))
        async with self._lock:
            return self._memory_queue.pop(0).model_copy(deep=True) if self._memory_queue else None

    async def clear_queue(self) -> None:
        if self._redis is not None:
            await self._redis.delete(self._key("queue"))
        else:
            async with self._lock:
                self._memory_queue.clear()

    async def add_recent(self, track: MusicTrack) -> None:
        if self._redis is not None:
            key = self._key("recent")
            existing = await self._redis.lrange(key, 0, 49)
            filtered = [
                raw for raw in existing
                if (parsed := self._load_track(raw)) and parsed.video_id != track.video_id
            ]
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.delete(key)
                pipe.rpush(key, self._dump_track(track), *filtered[:24])
                await pipe.execute()
            return
        async with self._lock:
            self._memory_recent = [
                item for item in self._memory_recent if item.video_id != track.video_id
            ]
            self._memory_recent.insert(0, track.model_copy(deep=True))
            self._memory_recent = self._memory_recent[:25]