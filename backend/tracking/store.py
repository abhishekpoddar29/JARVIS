from __future__ import annotations

from redis.asyncio import Redis

from .models import LocationPoint, TrackingSession

SESSION_PREFIX = "tracking:session:"
TOKEN_PREFIX = "tracking:token:"
LATEST_PREFIX = "tracking:latest:"
TRAIL_PREFIX = "tracking:trail:"


class TrackingStore:
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Redis | None = None

    async def connect(self) -> None:
        if self._redis is not None:
            return

        client = Redis.from_url(
            self._redis_url,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
        await client.ping()
        self._redis = client

    async def disconnect(self) -> None:
        client = self._redis
        self._redis = None
        if client is not None:
            await client.aclose()

    def _client(self) -> Redis:
        if self._redis is None:
            raise RuntimeError(
                "TrackingStore is not connected. Call "
                "tracking_store.connect() during FastAPI startup."
            )
        return self._redis

    async def create(
        self,
        session: TrackingSession,
        token_hash: str,
        ttl_seconds: int,
    ) -> None:
        client = self._client()
        session_key = f"{SESSION_PREFIX}{session.session_id}"
        token_key = f"{TOKEN_PREFIX}{token_hash}"

        async with client.pipeline(transaction=True) as pipeline:
            pipeline.set(
                session_key,
                session.model_dump_json(),
                ex=ttl_seconds,
                nx=True,
            )
            pipeline.set(
                token_key,
                session.session_id,
                ex=ttl_seconds,
                nx=True,
            )
            created_session, created_token = await pipeline.execute()

        if not created_session or not created_token:
            await client.delete(session_key, token_key)
            raise RuntimeError("Could not reserve a unique tracking session.")

    async def get_session(
        self,
        session_id: str,
    ) -> TrackingSession | None:
        raw = await self._client().get(
            f"{SESSION_PREFIX}{session_id}"
        )
        return TrackingSession.model_validate_json(raw) if raw else None

    async def get_session_id_by_token_hash(
        self,
        token_hash: str,
    ) -> str | None:
        value = await self._client().get(
            f"{TOKEN_PREFIX}{token_hash}"
        )
        return str(value) if value else None

    async def save_session(self, session: TrackingSession) -> None:
        client = self._client()
        key = f"{SESSION_PREFIX}{session.session_id}"
        ttl = await client.ttl(key)
        if ttl <= 0:
            raise KeyError(
                "Tracking session does not exist or has expired."
            )
        await client.set(key, session.model_dump_json(), ex=ttl)

    async def save_location(
        self,
        *,
        session_id: str,
        point: LocationPoint,
        max_trail_points: int = 100,
    ) -> None:
        client = self._client()
        ttl = await client.ttl(
            f"{SESSION_PREFIX}{session_id}"
        )
        if ttl <= 0:
            raise KeyError(
                "Tracking session does not exist or has expired."
            )

        latest_key = f"{LATEST_PREFIX}{session_id}"
        trail_key = f"{TRAIL_PREFIX}{session_id}"
        payload = point.model_dump_json()

        async with client.pipeline(transaction=True) as pipeline:
            pipeline.set(latest_key, payload, ex=ttl)
            pipeline.rpush(trail_key, payload)
            pipeline.ltrim(trail_key, -max_trail_points, -1)
            pipeline.expire(trail_key, ttl)
            await pipeline.execute()

    async def get_latest_location(
        self,
        session_id: str,
    ) -> LocationPoint | None:
        raw = await self._client().get(
            f"{LATEST_PREFIX}{session_id}"
        )
        return LocationPoint.model_validate_json(raw) if raw else None

    async def get_trail(
        self,
        session_id: str,
    ) -> list[LocationPoint]:
        values = await self._client().lrange(
            f"{TRAIL_PREFIX}{session_id}",
            0,
            -1,
        )
        return [
            LocationPoint.model_validate_json(value)
            for value in values
        ]