from __future__ import annotations

import asyncio

from fastapi import WebSocket

from .tracking_hub import TrackingEvent, tracking_hub


class TrackingEventBridge:
    """Forwards tracking events through an accepted Jarvis WebSocket."""

    def __init__(
        self,
        *,
        queue: asyncio.Queue[TrackingEvent],
        task: asyncio.Task[None],
    ) -> None:
        self._queue = queue
        self._task = task
        self._closed = False

    @classmethod
    async def start(
        cls,
        websocket: WebSocket,
    ) -> "TrackingEventBridge":
        queue = await tracking_hub.subscribe()
        task = asyncio.create_task(
            cls._forward(websocket, queue),
            name="jarvis-tracking-event-bridge",
        )
        return cls(queue=queue, task=task)

    @staticmethod
    async def _forward(
        websocket: WebSocket,
        queue: asyncio.Queue[TrackingEvent],
    ) -> None:
        try:
            while True:
                await websocket.send_json(await queue.get())
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            await tracking_hub.unsubscribe(self._queue)