from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


class SmsGatewayError(RuntimeError):
    """Raised when the Android SMS Gateway cannot accept a message."""


@dataclass(frozen=True, slots=True)
class SmsGatewayResult:
    message_id: str
    state: str
    raw: dict[str, Any]


class AndroidSmsGateway:
    """Async client for SMS Gateway for Android Local Server mode."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = 10.0,
        sim_number: int | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._timeout_seconds = timeout_seconds
        self._sim_number = sim_number

    @classmethod
    def from_env(cls) -> "AndroidSmsGateway":
        base_url = os.getenv("SMS_GATEWAY_URL", "").strip()
        username = os.getenv("SMS_GATEWAY_USERNAME", "").strip()
        password = os.getenv("SMS_GATEWAY_PASSWORD", "").strip()

        missing = [
            name
            for name, value in (
                ("SMS_GATEWAY_URL", base_url),
                ("SMS_GATEWAY_USERNAME", username),
                ("SMS_GATEWAY_PASSWORD", password),
            )
            if not value
        ]
        if missing:
            raise SmsGatewayError(
                "Missing SMS Gateway configuration: " + ", ".join(missing)
            )

        try:
            timeout_seconds = float(
                os.getenv("SMS_GATEWAY_TIMEOUT_SECONDS", "10")
            )
        except ValueError as exc:
            raise SmsGatewayError(
                "SMS_GATEWAY_TIMEOUT_SECONDS must be numeric."
            ) from exc

        if timeout_seconds <= 0:
            raise SmsGatewayError(
                "SMS_GATEWAY_TIMEOUT_SECONDS must be greater than zero."
            )

        raw_sim_number = os.getenv("SMS_GATEWAY_SIM_NUMBER", "").strip()
        sim_number: int | None = None

        if raw_sim_number:
            try:
                sim_number = int(raw_sim_number)
            except ValueError as exc:
                raise SmsGatewayError(
                    "SMS_GATEWAY_SIM_NUMBER must be an integer."
                ) from exc

            if sim_number not in {1, 2, 3}:
                raise SmsGatewayError(
                    "SMS_GATEWAY_SIM_NUMBER must be 1, 2, or 3."
                )

        return cls(
            base_url=base_url,
            username=username,
            password=password,
            timeout_seconds=timeout_seconds,
            sim_number=sim_number,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(
                auth=httpx.BasicAuth(self._username, self._password),
                timeout=self._timeout_seconds,
            ) as client:
                response = await client.get(f"{self._base_url}/health")
                return response.is_success
        except httpx.HTTPError:
            return False

    async def send_text(
        self,
        *,
        phone_number: str,
        text: str,
    ) -> SmsGatewayResult:
        destination = str(phone_number or "").strip()
        message = str(text or "").strip()

        if not destination:
            raise SmsGatewayError("SMS destination phone number is required.")
        if not message:
            raise SmsGatewayError("SMS message text is required.")

        payload: dict[str, Any] = {
            "textMessage": {"text": message},
            "phoneNumbers": [destination],
        }
        if self._sim_number is not None:
            payload["simNumber"] = self._sim_number

        try:
            async with httpx.AsyncClient(
                auth=httpx.BasicAuth(self._username, self._password),
                timeout=httpx.Timeout(
                    self._timeout_seconds,
                    connect=min(self._timeout_seconds, 5.0),
                ),
            ) as client:
                response = await client.post(
                    f"{self._base_url}/message",
                    json=payload,
                    headers={"Accept": "application/json"},
                )
        except httpx.ConnectError as exc:
            raise SmsGatewayError(
                "Cannot connect to the Android SMS Gateway. "
                "Confirm the phone is online and on the same Wi-Fi."
            ) from exc
        except httpx.TimeoutException as exc:
            raise SmsGatewayError(
                "The Android SMS Gateway timed out. "
                "Check the phone's Messages tab before retrying."
            ) from exc
        except httpx.HTTPError as exc:
            raise SmsGatewayError(f"SMS Gateway request failed: {exc}") from exc

        if not response.is_success:
            detail = self._response_detail(response)
            raise SmsGatewayError(
                f"SMS Gateway rejected the request "
                f"({response.status_code}): {detail}"
            )

        data = self._response_json(response)
        message_id = str(data.get("id") or data.get("messageId") or "").strip()
        state = str(data.get("state") or data.get("status") or "ACCEPTED").strip()

        return SmsGatewayResult(
            message_id=message_id,
            state=state,
            raw=data,
        )

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        if not response.content:
            return {}
        try:
            value = response.json()
        except ValueError:
            return {"response_text": response.text[:1000]}
        if isinstance(value, dict):
            return value
        return {"response": value}

    @classmethod
    def _response_detail(cls, response: httpx.Response) -> str:
        data = cls._response_json(response)
        for key in ("message", "detail", "error", "response_text"):
            value = data.get(key)
            if value:
                return str(value)[:1000]
        return response.reason_phrase or "Unknown error"