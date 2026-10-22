from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request, WebSocket, status

from config import get_settings


APP_TOKEN_HEADER = "x-jarvis-app-token"


def auth_is_enabled() -> bool:
    settings = get_settings()
    return bool(settings.jarvis_auth_required or settings.jarvis_app_token)


def _expected_token() -> str:
    settings = get_settings()
    token = (settings.jarvis_app_token or "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JARVIS_APP_TOKEN is required when app auth is enabled.",
        )
    return token


def _token_matches(value: str | None) -> bool:
    if not auth_is_enabled():
        return True
    provided = (value or "").strip()
    if not provided:
        return False
    return secrets.compare_digest(provided, _expected_token())


async def require_app_auth(
    request: Request,
    x_jarvis_app_token: str | None = Header(default=None),
) -> None:
    if not auth_is_enabled():
        return

    bearer = request.headers.get("authorization", "").strip()
    bearer_token = ""
    if bearer.lower().startswith("bearer "):
        bearer_token = bearer[7:].strip()

    query_token = request.query_params.get("token")
    if (
        _token_matches(x_jarvis_app_token)
        or _token_matches(bearer_token)
        or _token_matches(query_token)
    ):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid Jarvis app token.",
    )


async def require_websocket_auth(websocket: WebSocket) -> bool:
    if not auth_is_enabled():
        return True

    header_token = websocket.headers.get(APP_TOKEN_HEADER)
    bearer = websocket.headers.get("authorization", "").strip()
    bearer_token = bearer[7:].strip() if bearer.lower().startswith("bearer ") else ""
    query_token = websocket.query_params.get("token")

    return (
        _token_matches(header_token)
        or _token_matches(bearer_token)
        or _token_matches(query_token)
    )
