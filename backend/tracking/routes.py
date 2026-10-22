from __future__ import annotations

import json
import os
import secrets
import time

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from .consent_page import render_consent_page
from .models import (
    CreateTrackingSessionRequest,
    LocationPoint,
    PublicDecisionRequest,
    PublicTrackingView,
    TrackingSessionCreated,
    TrackingSessionStatus,
    TrackingStatus,
)
from .runtime import tracking_service

router = APIRouter(tags=["tracking"])


def _require_admin_token(value: str | None) -> None:
    expected = os.getenv("TRACKING_ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="TRACKING_ADMIN_TOKEN is not configured.",
        )
    if not value or not secrets.compare_digest(value, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid tracking admin token.",
        )


def _private_page_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(self)",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; "
            "connect-src 'self' ws: wss:; "
            "img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'"
        ),
    }


@router.post(
    "/api/tracking/sessions",
    response_model=TrackingSessionCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_tracking_session(
    request: CreateTrackingSessionRequest,
    x_tracking_admin_token: str | None = Header(default=None),
) -> TrackingSessionCreated:
    _require_admin_token(x_tracking_admin_token)
    return await tracking_service.create_session(request)


@router.get(
    "/api/tracking/sessions/{session_id}",
    response_model=TrackingSessionStatus,
)
async def get_tracking_session(
    session_id: str,
    x_tracking_admin_token: str | None = Header(default=None),
) -> TrackingSessionStatus:
    _require_admin_token(x_tracking_admin_token)
    session = await tracking_service.get_status(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Tracking session was not found or expired.",
        )
    return session


@router.post(
    "/api/tracking/sessions/{session_id}/stop",
    response_model=TrackingSessionStatus,
)
async def stop_tracking_session(
    session_id: str,
    x_tracking_admin_token: str | None = Header(default=None),
) -> TrackingSessionStatus:
    _require_admin_token(x_tracking_admin_token)
    session = await tracking_service.stop_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Tracking session was not found or expired.",
        )
    return session


@router.get(
    "/track/{public_token}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def tracking_consent_page(
    public_token: str,
) -> HTMLResponse:
    session = await tracking_service.get_public_view(public_token)
    if session is None:
        return HTMLResponse(
            "<h1>Location request unavailable</h1>"
            "<p>This secure link is invalid or has expired.</p>",
            status_code=404,
            headers=_private_page_headers(),
        )

    return HTMLResponse(
        render_consent_page(
            public_token=public_token,
            session=session,
        ),
        headers=_private_page_headers(),
    )


@router.post(
    "/api/tracking/public/{public_token}/decision",
    response_model=PublicTrackingView,
    include_in_schema=False,
)
async def submit_public_decision(
    public_token: str,
    request: PublicDecisionRequest,
) -> PublicTrackingView:
    if request.decision == "ALLOW":
        session = await tracking_service.allow_session(public_token)
    else:
        session = await tracking_service.deny_session(public_token)

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Tracking session was not found or expired.",
        )
    return session


@router.post(
    "/api/tracking/public/{public_token}/stop",
    response_model=PublicTrackingView,
    include_in_schema=False,
)
async def stop_public_tracking(
    public_token: str,
) -> PublicTrackingView:
    session = await tracking_service.stop_by_public_token(
        public_token
    )
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Tracking session was not found or expired.",
        )
    return session


@router.websocket("/ws/tracking/share/{public_token}")
async def tracking_share_websocket(
    websocket: WebSocket,
    public_token: str,
) -> None:
    session = await tracking_service.resolve_public_token(public_token)
    await websocket.accept()

    if session is None:
        await websocket.send_json(
            {
                "type": "tracking_error",
                "message": "Location request is invalid or expired.",
            }
        )
        await websocket.close(code=4404)
        return

    if session.status in {
        TrackingStatus.DENIED,
        TrackingStatus.ENDED,
        TrackingStatus.EXPIRED,
    }:
        await websocket.send_json(
            {
                "type": "tracking_error",
                "message": (
                    f"Location request is {session.status.value}."
                ),
            }
        )
        await websocket.close(code=4403)
        return

    await websocket.send_json(
        {
            "type": "tracking_ready",
            "session_id": session.session_id,
            "status": session.status.value,
            "expires_at": session.expires_at.isoformat(),
        }
    )

    last_accepted_at = 0.0

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > 4096:
                await websocket.close(code=1009)
                return

            now = time.monotonic()
            if now - last_accepted_at < 1.0:
                continue

            try:
                point = LocationPoint.model_validate(
                    json.loads(raw)
                )
                updated_session = (
                    await tracking_service.record_location(
                        public_token=public_token,
                        point=point,
                    )
                )
            except (json.JSONDecodeError, ValidationError) as exc:
                await websocket.send_json(
                    {
                        "type": "tracking_error",
                        "message": "Invalid GPS payload.",
                        "detail": str(exc)[:500],
                    }
                )
                continue
            except KeyError:
                await websocket.send_json(
                    {
                        "type": "tracking_error",
                        "message": "Location request expired.",
                    }
                )
                await websocket.close(code=4408)
                return
            except PermissionError as exc:
                await websocket.send_json(
                    {
                        "type": "tracking_error",
                        "message": str(exc),
                    }
                )
                await websocket.close(code=4403)
                return

            last_accepted_at = now
            await websocket.send_json(
                {
                    "type": "location_ack",
                    "session_id": updated_session.session_id,
                    "server_timestamp": (
                        point.server_timestamp.isoformat()
                        if point.server_timestamp
                        else None
                    ),
                }
            )

    except WebSocketDisconnect:
        return