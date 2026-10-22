from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4

from .models import (
    CreateTrackingSessionRequest,
    LocationPoint,
    PublicTrackingView,
    TERMINAL_TRACKING_STATUSES,
    TrackingSession,
    TrackingSessionCreated,
    TrackingSessionStatus,
    TrackingStatus,
)
from .store import TrackingStore
from .tracking_hub import tracking_hub

TOKEN_BYTES = 32


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def hash_public_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mask_phone(phone: str) -> str:
    if len(phone) <= 5:
        return "••••"
    return f"{phone[:3]} •••••• {phone[-4:]}"


class TrackingService:
    def __init__(
        self,
        store: TrackingStore,
        public_base_url: str,
    ) -> None:
        self._store = store
        self._public_base_url = public_base_url.rstrip("/")

    async def create_session(
        self,
        request: CreateTrackingSessionRequest,
    ) -> TrackingSessionCreated:
        if not self._public_base_url:
            raise RuntimeError(
                "TRACKING_PUBLIC_BASE_URL is not configured."
            )

        now = utc_now()
        session = TrackingSession(
            session_id=str(uuid4()),
            target_name=request.target_name,
            target_phone=request.target_phone,
            requester_name="Abhi",
            duration_minutes=request.duration_minutes,
            status=TrackingStatus.PENDING,
            created_at=now,
            expires_at=(
                now + timedelta(minutes=request.duration_minutes)
            ),
        )

        public_token = secrets.token_urlsafe(TOKEN_BYTES)
        await self._store.create(
            session=session,
            token_hash=hash_public_token(public_token),
            ttl_seconds=request.duration_minutes * 60,
        )

        created = TrackingSessionCreated(
            session_id=session.session_id,
            tracking_url=(
                f"{self._public_base_url}/track/"
                f"{quote(public_token, safe='')}"
            ),
            target_name=session.target_name,
            masked_phone=mask_phone(session.target_phone),
            duration_minutes=session.duration_minutes,
            status=session.status,
            created_at=session.created_at,
            expires_at=session.expires_at,
        )
        await tracking_hub.publish_session(
            created.model_dump(mode="json")
        )
        return created

    async def get_status(
        self,
        session_id: str,
    ) -> TrackingSessionStatus | None:
        session = await self._store.get_session(session_id)
        return self._to_status(session) if session else None

    async def resolve_public_token(
        self,
        public_token: str,
    ) -> TrackingSession | None:
        token = str(public_token or "").strip()
        if not token:
            return None

        session_id = (
            await self._store.get_session_id_by_token_hash(
                hash_public_token(token)
            )
        )
        return (
            await self._store.get_session(session_id)
            if session_id
            else None
        )

    async def get_public_view(
        self,
        public_token: str,
    ) -> PublicTrackingView | None:
        session = await self.resolve_public_token(public_token)
        return self._to_public_view(session) if session else None

    async def allow_session(
        self,
        public_token: str,
    ) -> PublicTrackingView | None:
        session = await self.resolve_public_token(public_token)
        if session is None:
            return None
        if session.status in TERMINAL_TRACKING_STATUSES:
            return self._to_public_view(session)

        if session.consented_at is None:
            session.consented_at = utc_now()
            await self._store.save_session(session)

        await self._publish_status(
            session,
            reason="CONSENT_GRANTED",
        )
        return self._to_public_view(session)

    async def deny_session(
        self,
        public_token: str,
    ) -> PublicTrackingView | None:
        session = await self.resolve_public_token(public_token)
        if session is None:
            return None
        if session.status in TERMINAL_TRACKING_STATUSES:
            return self._to_public_view(session)

        session.status = TrackingStatus.DENIED
        session.ended_at = utc_now()
        await self._store.save_session(session)
        await self._publish_status(
            session,
            reason="RECIPIENT_DENIED",
        )
        return self._to_public_view(session)

    async def stop_by_public_token(
        self,
        public_token: str,
    ) -> PublicTrackingView | None:
        session = await self.resolve_public_token(public_token)
        if session is None:
            return None
        await self._end_session(
            session,
            reason="RECIPIENT_STOPPED",
        )
        return self._to_public_view(session)

    async def stop_session(
        self,
        session_id: str,
    ) -> TrackingSessionStatus | None:
        session = await self._store.get_session(session_id)
        if session is None:
            return None
        await self._end_session(
            session,
            reason="REQUESTER_STOPPED",
        )
        return self._to_status(session)

    async def record_location(
        self,
        *,
        public_token: str,
        point: LocationPoint,
    ) -> TrackingSession:
        session = await self.resolve_public_token(public_token)
        if session is None:
            raise KeyError(
                "Tracking session was not found or expired."
            )
        if session.status in TERMINAL_TRACKING_STATUSES:
            raise PermissionError(
                f"Tracking session is {session.status.value}."
            )
        if session.consented_at is None:
            raise PermissionError(
                "Location sharing has not been allowed."
            )

        now = utc_now()
        if now >= session.expires_at:
            session.status = TrackingStatus.EXPIRED
            session.ended_at = now
            try:
                await self._store.save_session(session)
            except KeyError:
                pass
            await self._publish_status(
                session,
                reason="SESSION_EXPIRED",
            )
            raise PermissionError(
                "Tracking session has expired."
            )

        point.server_timestamp = now
        became_active = session.status != TrackingStatus.ACTIVE
        session.status = TrackingStatus.ACTIVE
        session.last_update_at = now

        await self._store.save_session(session)
        await self._store.save_location(
            session_id=session.session_id,
            point=point,
            max_trail_points=100,
        )

        if became_active:
            await self._publish_status(
                session,
                reason="FIRST_LOCATION_RECEIVED",
            )

        await tracking_hub.publish_location(
            session_id=session.session_id,
            target_name=session.target_name,
            expires_at=session.expires_at.isoformat(),
            point=point.model_dump(mode="json"),
        )
        return session

    async def _end_session(
        self,
        session: TrackingSession,
        *,
        reason: str,
    ) -> None:
        if session.status in TERMINAL_TRACKING_STATUSES:
            return
        session.status = TrackingStatus.ENDED
        session.ended_at = utc_now()
        await self._store.save_session(session)
        await self._publish_status(session, reason=reason)

    async def _publish_status(
        self,
        session: TrackingSession,
        *,
        reason: str,
    ) -> None:
        await tracking_hub.publish_status(
            session_id=session.session_id,
            status=session.status.value,
            target_name=session.target_name,
            expires_at=session.expires_at.isoformat(),
            consented_at=(
                session.consented_at.isoformat()
                if session.consented_at
                else None
            ),
            ended_at=(
                session.ended_at.isoformat()
                if session.ended_at
                else None
            ),
            reason=reason,
        )

    @staticmethod
    def _to_status(
        session: TrackingSession,
    ) -> TrackingSessionStatus:
        return TrackingSessionStatus(
            session_id=session.session_id,
            target_name=session.target_name,
            masked_phone=mask_phone(session.target_phone),
            duration_minutes=session.duration_minutes,
            status=session.status,
            created_at=session.created_at,
            expires_at=session.expires_at,
            consented_at=session.consented_at,
            ended_at=session.ended_at,
            last_update_at=session.last_update_at,
        )

    @staticmethod
    def _to_public_view(
        session: TrackingSession,
    ) -> PublicTrackingView:
        return PublicTrackingView(
            requester_name=session.requester_name,
            target_name=session.target_name,
            duration_minutes=session.duration_minutes,
            status=session.status,
            expires_at=session.expires_at,
            consented_at=session.consented_at,
            ended_at=session.ended_at,
            last_update_at=session.last_update_at,
        )