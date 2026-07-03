# memory/export_manager.py

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from memory.session_storage import session_store

EXPORTS_DIR = Path(__file__).resolve().parents[1] / "exports"


def _ensure_exports_dir() -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORTS_DIR


def _format_markdown(messages: list[dict[str, Any]], date_str: str) -> str:
    lines: list[str] = [
        f"# Jarvis 2.0 — Conversation Export",
        f"**Date:** {date_str}",
        f"**Messages:** {len([m for m in messages if m['role'] in ('user', 'assistant')])}",
        "",
        "---",
        "",
    ]

    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "").strip()
        timestamp = m.get("timestamp", "")

        if role == "user":
            lines.append(f"### You  `{timestamp}`")
            lines.append(content)
            lines.append("")
        elif role == "assistant":
            lines.append(f"### Jarvis  `{timestamp}`")
            lines.append(content)
            lines.append("")
        # skip tool/system messages in markdown

    lines.append("---")
    lines.append("*Exported from Jarvis 2.0*")
    return "\n".join(lines)


def _format_json(messages: list[dict[str, Any]], date_str: str) -> dict[str, Any]:
    return {
        "export_version": "1.0",
        "date": date_str,
        "exported_at": datetime.now(UTC).isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }


async def export_session() -> tuple[str, str]:
    """
    Pull today's session from Redis, format to Markdown + JSON.
    Returns (markdown_str, filename_stem).
    """
    messages = await session_store.load_session()
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    timestamp_str = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")

    markdown = _format_markdown(messages, date_str)
    payload = _format_json(messages, date_str)

    return markdown, payload, timestamp_str


async def archive_to_disk(payload: dict[str, Any], timestamp_str: str) -> Path:
    """Save JSON archive to exports/ directory."""
    exports_dir = _ensure_exports_dir()
    filepath = exports_dir / f"{timestamp_str}.json"
    filepath.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[EXPORT] Archived to {filepath}")
    return filepath


async def wipe_session() -> None:
    """Delete today's Redis session."""
    await session_store.clear_session()
    print("[EXPORT] Redis session wiped")