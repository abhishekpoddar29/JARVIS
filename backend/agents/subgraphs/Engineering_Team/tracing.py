from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4


logger = logging.getLogger("engineering_team_v2")

TraceSink = Callable[[dict[str, Any]], Awaitable[None]]

_trace_sink: ContextVar[TraceSink | None] = ContextVar(
    "engineering_trace_sink",
    default=None,
)


def set_trace_sink(sink: TraceSink | None) -> None:
    _trace_sink.set(sink)


async def emit_trace(event: dict[str, Any]) -> None:
    payload = dict(event)
    logger.info(json.dumps(payload, ensure_ascii=False, default=str))

    sink = _trace_sink.get()
    if sink is not None:
        try:
            await sink(payload)
        except Exception:
            logger.exception("Engineering trace sink failed.")


@asynccontextmanager
async def trace_span(
    *,
    request_id: str,
    node: str,
    operation: str,
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    span_id = uuid4().hex
    started = time.perf_counter()

    await emit_trace({
        "type": "agent_trace",
        "event": "SPAN_STARTED",
        "trace_id": request_id,
        "span_id": span_id,
        "node": node,
        "operation": operation,
        "metadata": metadata or {},
    })

    try:
        yield span_id
    except Exception as exc:
        await emit_trace({
            "type": "agent_trace",
            "event": "SPAN_FAILED",
            "trace_id": request_id,
            "span_id": span_id,
            "node": node,
            "operation": operation,
            "duration_ms": round(
                (time.perf_counter() - started) * 1000,
                2,
            ),
            "error": f"{type(exc).__name__}: {exc}",
        })
        raise
    else:
        await emit_trace({
            "type": "agent_trace",
            "event": "SPAN_COMPLETED",
            "trace_id": request_id,
            "span_id": span_id,
            "node": node,
            "operation": operation,
            "duration_ms": round(
                (time.perf_counter() - started) * 1000,
                2,
            ),
        })