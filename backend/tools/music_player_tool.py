from __future__ import annotations

from typing import Literal
from uuid import uuid4

from tools.base import BaseTool, ToolResult

from music.models import MusicTrack
from music.runtime import music_store, youtube_music_service
from music.service import MusicServiceError


MusicAction = Literal[
    "play",
    "pause",
    "resume",
    "next",
    "previous",
    "queue",
    "favorite",
    "remove_favorite",
    "list_favorites",
    "list_queue",
    "clear_queue",
    "open",
    "close",
    "current",
]


_ACTION_ALIASES = {
    "start": "play",
    "play_song": "play",
    "unpause": "resume",
    "continue": "resume",
    "skip": "next",
    "add_to_queue": "queue",
    "play_next": "queue",
    "add_favorite": "favorite",
    "like": "favorite",
    "unfavorite": "remove_favorite",
    "remove_from_favorites": "remove_favorite",
    "favorites": "list_favorites",
    "queue_list": "list_queue",
    "show": "open",
    "hide": "close",
    "now_playing": "current",
}


class MusicPlayerTool(BaseTool):
    name: str = "music_player_tool"
    description: str = (
        "Control the Jarvis YouTube music widget. Use it only for explicit music "
        "playback commands such as play, pause, resume, next, queue, favorite, "
        "remove favorite, show favorites, open, or close the player. For play or "
        "queue, pass query with the song title and artist when known. For queue, "
        "queue_position may be 'next' or 'end'. This tool searches only embeddable "
        "YouTube videos and returns a UI command for the visible player widget."
    )

    async def run(
        self,
        action: MusicAction | str,
        query: str = "",
        video_id: str = "",
        queue_position: Literal["next", "end"] | str = "end",
    ) -> ToolResult:
        normalized = str(action or "").strip().casefold().replace("-", "_").replace(" ", "_")
        normalized = _ACTION_ALIASES.get(normalized, normalized)
        clean_query = " ".join(str(query or "").split())
        position = "next" if str(queue_position).casefold() == "next" else "end"

        try:
            if normalized == "play":
                track, alternatives = await self._resolve_track(clean_query)
                await music_store.set_current_track(track)
                return self._result(
                    content=f"Playing {track.title} by {track.channel_title}.",
                    command={
                        "action": "play",
                        "visible": True,
                        "track": track.model_dump(mode="json"),
                        "alternatives": [item.model_dump(mode="json") for item in alternatives],
                    },
                )

            if normalized == "queue":
                track, _ = await self._resolve_track(clean_query)
                queue = await music_store.enqueue(track, position=position)
                wording = "up next" if position == "next" else "to the queue"
                return self._result(
                    content=f"Added {track.title} {wording}.",
                    command={
                        "action": "queue_updated",
                        "visible": True,
                        "queue": [item.model_dump(mode="json") for item in queue],
                    },
                )

            if normalized == "next":
                track = await music_store.pop_next()
                if track is None:
                    return self._result(
                        content="The music queue is empty.",
                        command={"action": "next", "visible": True},
                    )
                await music_store.set_current_track(track)
                return self._result(
                    content=f"Playing the next song: {track.title}.",
                    command={
                        "action": "play",
                        "visible": True,
                        "track": track.model_dump(mode="json"),
                        "alternatives": [],
                    },
                )

            if normalized == "favorite":
                track = await self._resolve_favorite_target(clean_query)
                favorites = await music_store.add_favorite(track)
                return self._result(
                    content=f"Added {track.title} to your Jarvis favorites.",
                    command={
                        "action": "favorites_updated",
                        "visible": True,
                        "favorites": [item.model_dump(mode="json") for item in favorites],
                    },
                )

            if normalized == "remove_favorite":
                target_id = str(video_id or "").strip()
                title = "that song"
                if not target_id and clean_query:
                    track, _ = await self._resolve_track(clean_query)
                    target_id = track.video_id
                    title = track.title
                if not target_id:
                    current = await music_store.current_track()
                    if current:
                        target_id = current.video_id
                        title = current.title
                if not target_id:
                    return ToolResult(
                        ok=False,
                        content="There is no current song to remove from favorites.",
                        data={},
                    )
                favorites = await music_store.remove_favorite(target_id)
                return self._result(
                    content=f"Removed {title} from your Jarvis favorites.",
                    command={
                        "action": "favorites_updated",
                        "visible": True,
                        "favorites": [item.model_dump(mode="json") for item in favorites],
                    },
                )

            if normalized == "list_favorites":
                favorites = await music_store.list_favorites()
                return self._result(
                    content=(
                        f"Opened your music library with {len(favorites)} favorite song(s)."
                    ),
                    command={
                        "action": "show_favorites",
                        "visible": True,
                        "favorites": [item.model_dump(mode="json") for item in favorites],
                    },
                )

            if normalized == "list_queue":
                queue = await music_store.list_queue()
                return self._result(
                    content=f"Opened the queue with {len(queue)} song(s) waiting.",
                    command={
                        "action": "show_queue",
                        "visible": True,
                        "queue": [item.model_dump(mode="json") for item in queue],
                    },
                )

            if normalized == "clear_queue":
                await music_store.clear_queue()
                return self._result(
                    content="Cleared the music queue.",
                    command={"action": "queue_updated", "visible": True, "queue": []},
                )

            if normalized == "current":
                current = await music_store.current_track()
                if current is None:
                    return ToolResult(ok=True, content="No song is currently selected.", data={})
                return self._result(
                    content=f"The selected song is {current.title} by {current.channel_title}.",
                    command={
                        "action": "open",
                        "visible": True,
                        "track": current.model_dump(mode="json"),
                    },
                )

            if normalized in {"pause", "resume", "previous", "open", "close"}:
                return self._result(
                    content={
                        "pause": "Paused the music.",
                        "resume": "Resumed the music.",
                        "previous": "Playing the previous song when available.",
                        "open": "Opened the music player.",
                        "close": "Closed the music player and stopped playback.",
                    }[normalized],
                    command={
                        "action": normalized,
                        "visible": normalized != "close",
                    },
                )

            return ToolResult(
                ok=False,
                content=f"Unsupported music action: {action}.",
                data={
                    "supported_actions": [
                        "play", "pause", "resume", "next", "previous", "queue",
                        "favorite", "remove_favorite", "list_favorites", "list_queue",
                        "clear_queue", "open", "close", "current",
                    ]
                },
            )

        except MusicServiceError as exc:
            return ToolResult(ok=False, content=str(exc), data={}, error=str(exc))
        except Exception as exc:
            return ToolResult(
                ok=False,
                content="The Jarvis music player could not complete that action.",
                data={},
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _resolve_track(self, query: str) -> tuple[MusicTrack, list[MusicTrack]]:
        if not query:
            raise MusicServiceError("Tell me which song or artist to play.")
        results = await youtube_music_service.search(query, limit=8)
        if not results:
            raise MusicServiceError(f"No playable YouTube result was found for '{query}'.")
        return results[0], results[1:5]

    async def _resolve_favorite_target(self, query: str) -> MusicTrack:
        if query:
            track, _ = await self._resolve_track(query)
            return track
        current = await music_store.current_track()
        if current is None:
            raise MusicServiceError(
                "There is no current song to favorite. Play a song or provide a title."
            )
        return current

    @staticmethod
    def _result(*, content: str, command: dict) -> ToolResult:
        command_payload = {
            "command_id": uuid4().hex,
            **command,
        }
        return ToolResult(
            ok=True,
            content=content,
            data={"music_command": command_payload},
        )


music_player_tool = MusicPlayerTool()
