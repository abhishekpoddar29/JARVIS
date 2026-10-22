from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .models import MusicTrack


_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_DURATION_RE = re.compile(
    r"^P(?:\d+D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


class MusicServiceError(RuntimeError):
    pass


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    tracks: list[MusicTrack]


class YouTubeMusicService:
    """Small async YouTube Data API v3 client with ranking and TTL caching."""

    def __init__(
        self,
        *,
        api_key: str | None,
        region_code: str = "IN",
        relevance_language: str = "en",
        max_results: int = 8,
        timeout_seconds: float = 12.0,
        cache_ttl_seconds: int = 600,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._region_code = (region_code or "IN").strip().upper()
        self._relevance_language = (relevance_language or "en").strip()
        self._max_results = max(3, min(int(max_results), 20))
        self._timeout_seconds = max(3.0, float(timeout_seconds))
        self._cache_ttl_seconds = max(30, int(cache_ttl_seconds))
        self._cache: dict[str, _CacheEntry] = {}
        self._cache_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def search(self, query: str, *, limit: int = 8) -> list[MusicTrack]:
        clean_query = " ".join(str(query or "").split())
        if not clean_query:
            raise MusicServiceError("A song title or artist is required.")
        if not self._api_key:
            raise MusicServiceError(
                "YOUTUBE_API_KEY is not configured in backend/.env."
            )

        result_limit = max(1, min(int(limit), self._max_results))
        cache_key = f"{self._region_code}:{clean_query.casefold()}:{result_limit}"
        now = time.monotonic()

        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and cached.expires_at > now:
                return [track.model_copy(deep=True) for track in cached.tracks]
            if cached:
                self._cache.pop(cache_key, None)

        search_payload = await self._get_json(
            _SEARCH_URL,
            {
                "key": self._api_key,
                "part": "snippet",
                "q": clean_query,
                "type": "video",
                "order": "relevance",
                "maxResults": result_limit,
                "regionCode": self._region_code,
                "relevanceLanguage": self._relevance_language,
                "safeSearch": "moderate",
                "videoEmbeddable": "true",
                "videoSyndicated": "true",
                "videoCategoryId": "10",
            },
        )

        search_items = search_payload.get("items") or []
        ids = [
            str((item.get("id") or {}).get("videoId") or "").strip()
            for item in search_items
        ]
        ids = [video_id for video_id in ids if video_id]
        if not ids:
            return []

        detail_tracks = await self.get_tracks(ids)
        by_id = {track.video_id: track for track in detail_tracks}

        # Preserve candidates returned by search, then rank them for music intent.
        candidates = [by_id[video_id] for video_id in ids if video_id in by_id]
        candidates.sort(
            key=lambda track: self._rank_track(clean_query, track),
            reverse=True,
        )
        candidates = candidates[:result_limit]

        async with self._cache_lock:
            self._cache[cache_key] = _CacheEntry(
                expires_at=now + self._cache_ttl_seconds,
                tracks=[track.model_copy(deep=True) for track in candidates],
            )

        return candidates

    async def get_tracks(self, video_ids: list[str]) -> list[MusicTrack]:
        if not self._api_key:
            raise MusicServiceError(
                "YOUTUBE_API_KEY is not configured in backend/.env."
            )

        unique_ids = list(dict.fromkeys(video_id.strip() for video_id in video_ids if video_id.strip()))
        if not unique_ids:
            return []

        payload = await self._get_json(
            _VIDEOS_URL,
            {
                "key": self._api_key,
                "part": "snippet,contentDetails,status",
                "id": ",".join(unique_ids[:50]),
            },
        )

        tracks: list[MusicTrack] = []
        for item in payload.get("items") or []:
            status = item.get("status") or {}
            if status.get("embeddable") is False:
                continue
            if status.get("uploadStatus") not in {None, "processed"}:
                continue
            if status.get("privacyStatus") not in {None, "public"}:
                continue

            snippet = item.get("snippet") or {}
            thumbnails = snippet.get("thumbnails") or {}
            thumbnail = self._best_thumbnail(thumbnails)
            video_id = str(item.get("id") or "").strip()
            title = html.unescape(str(snippet.get("title") or "")).strip()
            if not video_id or not title:
                continue

            tracks.append(
                MusicTrack(
                    video_id=video_id,
                    title=title,
                    channel_title=html.unescape(
                        str(snippet.get("channelTitle") or "")
                    ).strip(),
                    thumbnail=thumbnail,
                    duration_seconds=self.parse_duration(
                        str((item.get("contentDetails") or {}).get("duration") or "")
                    ),
                    published_at=snippet.get("publishedAt"),
                )
            )

        order = {video_id: index for index, video_id in enumerate(unique_ids)}
        tracks.sort(key=lambda track: order.get(track.video_id, 10_000))
        return tracks

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=True,
                headers={"Accept": "application/json"},
            ) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body = exc.response.json()
                detail = str((body.get("error") or {}).get("message") or "")
            except Exception:
                detail = exc.response.text[:300]
            raise MusicServiceError(
                f"YouTube API request failed ({exc.response.status_code}). {detail}".strip()
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise MusicServiceError(f"Could not reach the YouTube Data API: {exc}") from exc

        if not isinstance(payload, dict):
            raise MusicServiceError("YouTube returned an invalid response.")
        return payload

    @staticmethod
    def parse_duration(value: str) -> int:
        match = _DURATION_RE.match(value.strip())
        if not match:
            return 0
        return (
            int(match.group("hours") or 0) * 3600
            + int(match.group("minutes") or 0) * 60
            + int(match.group("seconds") or 0)
        )

    @staticmethod
    def _best_thumbnail(thumbnails: dict[str, Any]) -> str:
        for key in ("maxres", "standard", "high", "medium", "default"):
            url = str((thumbnails.get(key) or {}).get("url") or "").strip()
            if url:
                return url
        return ""

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(part for part in _NON_WORD_RE.split(value.casefold()) if part)

    def _rank_track(self, query: str, track: MusicTrack) -> float:
        normalized_query = self._normalize(query)
        normalized_title = self._normalize(track.title)
        normalized_channel = self._normalize(track.channel_title)
        query_tokens = set(normalized_query.split())
        title_tokens = set(normalized_title.split())
        combined = f"{normalized_title} {normalized_channel}"

        score = 0.0
        if normalized_query and normalized_query in normalized_title:
            score += 90
        if query_tokens:
            score += 45 * (len(query_tokens & title_tokens) / len(query_tokens))
            if query_tokens.issubset(set(combined.split())):
                score += 28

        lower_title = track.title.casefold()
        lower_channel = track.channel_title.casefold()
        if "official audio" in lower_title:
            score += 24
        elif "official video" in lower_title or "official music video" in lower_title:
            score += 20
        if lower_channel.endswith(" - topic") or lower_channel.endswith(" topic"):
            score += 18
        if "vevo" in lower_channel or "official" in lower_channel:
            score += 10

        channel_identity = normalized_channel.removesuffix(" topic").strip()
        if channel_identity and channel_identity in normalized_query:
            score += 38

        requested_variants = {
            word for word in ("live", "remix", "cover", "karaoke", "slowed", "sped")
            if word in normalized_query
        }
        penalties = {
            "reaction": 38,
            "review": 26,
            "cover": 80,
            "karaoke": 60,
            "slowed": 22,
            "sped up": 22,
            "nightcore": 28,
            "8d audio": 24,
            "live": 12,
            "remix": 14,
        }
        for phrase, penalty in penalties.items():
            if phrase in lower_title and not any(word in phrase for word in requested_variants):
                score -= penalty

        # Most songs are under 12 minutes. Keep long-form compilations out of the top result.
        if track.duration_seconds:
            if 90 <= track.duration_seconds <= 480:
                score += 12
            elif track.duration_seconds > 900:
                score -= 30
        return score
