import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from config import get_settings

logger = logging.getLogger(__name__)


def _today_key() -> str:
    return f"session:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def _memory_key() -> str:
    return f"memory:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def _build_message(role: str, content: str, **kwargs) -> dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_calls": kwargs.get("tool_calls"),
        "tool_results": kwargs.get("tool_results"),
        "attachment_metadata": kwargs.get("attachment_metadata"),
    }


class SessionStore:

    def __init__(self):
        settings = get_settings()

        self._redis_url = settings.redis_url
        self._ttl = settings.session_ttl_seconds
        self._client: aioredis.Redis | None = None
        self._use_in_memory = False
        self._in_memory_sessions = {}
        self._in_memory_task_memories = {}
        self._in_memory_kv = {}

    # ==========================================================
    # CONNECTIONS
    # ==========================================================

    async def connect(self):
        self._client = aioredis.from_url(
            self._redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

        try:
            await self._client.ping()
            logger.info(f"Redis connected: {self._redis_url}")

        except Exception as exc:
            logger.warning(
                f"Redis connection failed: {exc}. "
                "Falling back to IN-MEMORY storage."
            )
            self._use_in_memory = True
            self._client = None

    async def disconnect(self):
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("Redis disconnected.")

    # ==========================================================
    # CHAT SESSION MEMORY
    # ==========================================================

    async def append_message(
        self,
        role: str,
        content: str,
        **kwargs,
    ) -> None:

        if self._use_in_memory:
            key = _today_key()
            message = _build_message(
                role,
                content,
                **kwargs,
            )
            if key not in self._in_memory_sessions:
                self._in_memory_sessions[key] = []
            self._in_memory_sessions[key].append(json.dumps(message))
            return

        if not self._client:
            raise RuntimeError(
                "Redis connection not initialized."
            )

        key = _today_key()

        message = _build_message(
            role,
            content,
            **kwargs,
        )

        await self._client.rpush(
            key,
            json.dumps(message),
        )

        await self._client.expire(
            key,
            self._ttl,
        )

    async def load_session(
        self,
        date_str: str | None = None,
    ) -> list[dict]:

        if self._use_in_memory:
            key = (
                f"session:{date_str}"
                if date_str
                else _today_key()
            )
            raw = self._in_memory_sessions.get(key, [])
            return [
                json.loads(item)
                for item in raw
            ]

        if not self._client:
            raise RuntimeError(
                "Redis connection not initialized."
            )

        key = (
            f"session:{date_str}"
            if date_str
            else _today_key()
        )

        raw = await self._client.lrange(
            key,
            0,
            -1,
        )

        return [
            json.loads(item)
            for item in raw
        ]

    async def clear_session(
        self,
        date_str: str | None = None,
    ) -> None:

        if self._use_in_memory:
            key = (
                f"session:{date_str}"
                if date_str
                else _today_key()
            )
            if key in self._in_memory_sessions:
                del self._in_memory_sessions[key]
            logger.info(
                f"Session cleared (in-memory): {key}"
            )
            return

        if not self._client:
            raise RuntimeError(
                "Redis connection not initialized."
            )

        key = (
            f"session:{date_str}"
            if date_str
            else _today_key()
        )

        await self._client.delete(key)

        logger.info(
            f"Session cleared: {key}"
        )

    async def session_exists(
        self,
        date_str: str | None = None,
    ) -> bool:

        if self._use_in_memory:
            key = (
                f"session:{date_str}"
                if date_str
                else _today_key()
            )
            return key in self._in_memory_sessions

        if not self._client:
            raise RuntimeError(
                "Redis connection not initialized."
            )

        key = (
            f"session:{date_str}"
            if date_str
            else _today_key()
        )

        return bool(
            await self._client.exists(key)
        )

    # ==========================================================
    # TASK MEMORY STORAGE
    # ==========================================================

    async def save_memory(
        self,
        text: str,
    ) -> None:

        if self._use_in_memory:
            key = _memory_key()
            if key not in self._in_memory_task_memories:
                self._in_memory_task_memories[key] = []
            self._in_memory_task_memories[key].append(text)
            logger.info(
                f"[MEMORY SAVED (in-memory)] {text}"
            )
            return

        if not self._client:
            raise RuntimeError(
                "Redis connection not initialized."
            )

        key = _memory_key()

        await self._client.rpush(
            key,
            text,
        )

        await self._client.expire(
            key,
            self._ttl,
        )

        logger.info(
            f"[MEMORY SAVED] {text}"
        )

    async def load_memories(self) -> list[str]:

        if self._use_in_memory:
            key = _memory_key()
            memories = self._in_memory_task_memories.get(key, [])
            logger.info(
                f"[MEMORY LOADED (in-memory)] {len(memories)} memories"
            )
            return list(memories)

        if not self._client:
            raise RuntimeError(
                "Redis connection not initialized."
            )

        key = _memory_key()

        memories = await self._client.lrange(
            key,
            0,
            -1,
        )

        logger.info(
            f"[MEMORY LOADED] {len(memories)} memories"
        )

        return memories

    async def clear_memories(self) -> None:

        if self._use_in_memory:
            key = _memory_key()
            if key in self._in_memory_task_memories:
                del self._in_memory_task_memories[key]
            logger.info(
                f"[MEMORY CLEARED (in-memory)] {key}"
            )
            return

        if not self._client:
            raise RuntimeError(
                "Redis connection not initialized."
            )

        key = _memory_key()

        await self._client.delete(key)

        logger.info(
            f"[MEMORY CLEARED] {key}"
        )


    # ==========================================================
    # KEY-VALUE STORE (research context, agent state, etc.)
    # ==========================================================

    async def get_value(self, key: str) -> str | None:
        """Get a raw string value namespaced to today's session."""
        if self._use_in_memory:
            full_key = f"{_today_key()}:kv:{key}"
            return self._in_memory_kv.get(full_key)

        if not self._client:
            raise RuntimeError("Redis connection not initialized.")
        full_key = f"{_today_key()}:kv:{key}"
        return await self._client.get(full_key)

    async def set_value(self, key: str, value: str) -> None:
        """Set a raw string value namespaced to today's session."""
        if self._use_in_memory:
            full_key = f"{_today_key()}:kv:{key}"
            self._in_memory_kv[full_key] = value
            return

        if not self._client:
            raise RuntimeError("Redis connection not initialized.")
        full_key = f"{_today_key()}:kv:{key}"
        await self._client.set(full_key, value, ex=self._ttl)

    async def delete_value(self, key: str) -> None:
        """Delete a key-value entry."""
        if self._use_in_memory:
            full_key = f"{_today_key()}:kv:{key}"
            if full_key in self._in_memory_kv:
                del self._in_memory_kv[full_key]
            return

        if not self._client:
            raise RuntimeError("Redis connection not initialized.")
        full_key = f"{_today_key()}:kv:{key}"
        await self._client.delete(full_key)


session_store = SessionStore()