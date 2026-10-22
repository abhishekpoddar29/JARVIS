from __future__ import annotations

import os

from config import get_settings

from .service import YouTubeMusicService
from .store import MusicStore


settings = get_settings()


def _setting(name: str, env_name: str, default):
    value = getattr(settings, name, None)
    if value is None or value == "":
        value = os.getenv(env_name, default)
    return value


music_store = MusicStore(
    redis_url=str(getattr(settings, "redis_url", "redis://127.0.0.1:6379/0")),
    owner="abhi",
)

youtube_music_service = YouTubeMusicService(
    api_key=_setting("youtube_api_key", "YOUTUBE_API_KEY", None),
    region_code=str(_setting("youtube_region_code", "YOUTUBE_REGION_CODE", "IN")),
    relevance_language=str(
        _setting("youtube_relevance_language", "YOUTUBE_RELEVANCE_LANGUAGE", "en")
    ),
    max_results=int(_setting("youtube_search_limit", "YOUTUBE_SEARCH_LIMIT", 8)),
    timeout_seconds=float(
        _setting("youtube_timeout_seconds", "YOUTUBE_TIMEOUT_SECONDS", 12.0)
    ),
    cache_ttl_seconds=int(
        _setting("youtube_cache_ttl_seconds", "YOUTUBE_CACHE_TTL_SECONDS", 600)
    ),
)
