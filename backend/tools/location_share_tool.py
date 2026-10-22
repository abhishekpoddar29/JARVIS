from __future__ import annotations

from urllib.parse import urlparse

from pydantic import ValidationError

from tools.base import BaseTool, ToolResult
from tracking.models import CreateTrackingSessionRequest
from tracking.runtime import tracking_service
from tracking.sms_gateway import AndroidSmsGateway, SmsGatewayError


def _validate_public_tracking_url(tracking_url: str) -> None:
    parsed = urlparse(tracking_url)
    hostname = (parsed.hostname or "").lower()

    if parsed.scheme != "https":
        raise ValueError("Tracking links sent by SMS must use HTTPS.")

    if hostname in {"", "localhost", "127.0.0.1", "::1"}:
        raise ValueError(
            "TRACKING_PUBLIC_BASE_URL must be the public ngrok HTTPS URL, "
            "not localhost."
        )


def _build_sms_text(
    *,
    target_name: str,
    duration_minutes: int,
    tracking_url: str,
) -> str:
    return (
        f"Jarvis location request from Abhi for {target_name}. "
        f"Share your live location for up to {duration_minutes} minutes only "
        f"if you choose to. Nothing is shared until you allow access. "
        f"Open: {tracking_url}"
    )


class LocationShareTool(BaseTool):
    name: str = "location_share_tool"
    description: str = (
        "Creates a temporary consent-based location-sharing session and sends "
        "its secure HTTPS link by SMS through the configured Android SMS "
        "Gateway. Use only when the user explicitly asks to request a friend "
        "or family member's location. Required parameters: target_name and "
        "target_phone. Optional duration_minutes: 5 to 60, default 30. This "
        "sends only a consent request; it does not mean the recipient accepted "
        "or tracking started."
    )

    async def run(
        self,
        target_name: str,
        target_phone: str,
        duration_minutes: int = 30,
    ) -> ToolResult:
        session_id = ""

        try:
            request = CreateTrackingSessionRequest(
                target_name=target_name,
                target_phone=target_phone,
                duration_minutes=duration_minutes,
            )

            session = await tracking_service.create_session(request)
            session_id = session.session_id

            _validate_public_tracking_url(session.tracking_url)

            gateway = AndroidSmsGateway.from_env()
            sms_result = await gateway.send_text(
                phone_number=request.target_phone,
                text=_build_sms_text(
                    target_name=session.target_name,
                    duration_minutes=session.duration_minutes,
                    tracking_url=session.tracking_url,
                ),
            )

            return ToolResult(
                ok=True,
                content=(
                    f"Sent a {session.duration_minutes}-minute location consent "
                    f"request to {session.target_name} at {session.masked_phone}. "
                    "The session is pending until the recipient opens the link "
                    "and explicitly allows location access."
                ),
                data={
                    **session.model_dump(mode="json"),
                    "sms_sent": True,
                    "sms_message_id": sms_result.message_id,
                    "sms_state": sms_result.state,
                },
            )

        except ValidationError as exc:
            return ToolResult(
                ok=False,
                content="The location-sharing request contains invalid recipient details.",
                error=str(exc),
            )

        except (SmsGatewayError, ValueError) as exc:
            if session_id:
                try:
                    await tracking_service.stop_session(session_id)
                except Exception:
                    pass

            return ToolResult(
                ok=False,
                content=(
                    "The tracking session was created but the SMS consent request "
                    "could not be sent. The session has been ended."
                ),
                data={"session_id": session_id, "sms_sent": False},
                error=str(exc),
            )

        except Exception as exc:
            if session_id:
                try:
                    await tracking_service.stop_session(session_id)
                except Exception:
                    pass

            return ToolResult(
                ok=False,
                content="Failed to create and send the temporary location-sharing request.",
                data={"session_id": session_id, "sms_sent": False},
                error=f"{type(exc).__name__}: {exc}",
            )


location_share_tool = LocationShareTool()