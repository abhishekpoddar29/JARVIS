from __future__ import annotations

import asyncio
from typing import Any

TrackingEvent = dict[str, Any]


class TrackingHub:
    """Small in-process broadcaster for Jarvis tracking events."""

    def __init__(self, *, queue_size: int = 100) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[TrackingEvent]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[TrackingEvent]:
        queue: asyncio.Queue[TrackingEvent] = asyncio.Queue(
            maxsize=self._queue_size
        )
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(
        self,
        queue: asyncio.Queue[TrackingEvent],
    ) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, event: TrackingEvent) -> None:
        if not event.get("type"):
            raise ValueError("Tracking event requires a type.")

        async with self._lock:
            subscribers = tuple(self._subscribers)

        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            try:
                queue.put_nowait(dict(event))
            except asyncio.QueueFull:
                # A slow UI must never block GPS ingestion.
                pass

    async def publish_session(
        self,
        session: dict[str, Any],
    ) -> None:
        await self.publish(
            {
                "type": "tracking_session",
                "session": session,
            }
        )

    async def publish_status(
        self,
        *,
        session_id: str,
        status: str,
        target_name: str,
        expires_at: str,
        consented_at: str | None = None,
        ended_at: str | None = None,
        reason: str = "",
    ) -> None:
        await self.publish(
            {
                "type": "tracking_status",
                "session_id": session_id,
                "status": status,
                "target_name": target_name,
                "expires_at": expires_at,
                "consented_at": consented_at,
                "ended_at": ended_at,
                "reason": reason,
            }
        )

    async def publish_location(
        self,
        *,
        session_id: str,
        target_name: str,
        expires_at: str,
        point: dict[str, Any],
    ) -> None:
        await self.publish(
            {
                "type": "tracking_update",
                "session_id": session_id,
                "target_name": target_name,
                "status": "ACTIVE",
                "expires_at": expires_at,
                "point": point,
            }
        )


tracking_hub = TrackingHub()