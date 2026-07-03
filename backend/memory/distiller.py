from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, UTC
from typing import Any

from agents.gemini_client import GeminiClientError, generate_reply
from config import get_settings
from memory.chromadb_schemas import MemoryEntry, MemoryType, SensitivityLevel
from memory.semantic import semantic_memory
from memory.session_storage import session_store

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Prompts ──────────────────────────────────────────────────────────────────────

_DISTILLATION_PROMPT = """\
You are Jarvis's memory distillation engine. Your job is to read a conversation transcript 
between Jarvis (the AI) and Abhi (the user) and extract important long-term memories.

EXTRACT:
- Explicit preferences (e.g., "I prefer dark mode", "I hate cilantro")
- Decisions made (e.g., "Let's use React for this project")
- Personal facts (e.g., "I work in fintech", "I live in Mumbai")
- Project context (e.g., "I'm currently building Jarvis 2.0")
- Task outcomes (e.g., "Generated a chart of Bitcoin prices")

IGNORE:
- Small talk, greetings, pleasantries ("Thanks!", "Good morning")
- Transient questions that don't reveal preferences (e.g., "What's the weather?")
- Raw tool outputs or code snippets

OUTPUT FORMAT:
Respond ONLY with a valid JSON array of objects. No markdown fences. No explanations.
If there are no memories to extract, return an empty array: []

Each object must have this exact schema:
{
  "type": "preference | personal_fact | decision | task_outcome | project_context | recurring_topic",
  "content": "The concise memory statement in third person (e.g., 'Abhi prefers concise answers.')",
  "confidence": 0.0 to 1.0,
  "sensitivity": "low | personal | private"
}

Example valid response:
[
  {"type": "preference", "content": "Abhi prefers to use Python over JavaScript for backend development.", "confidence": 0.95, "sensitivity": "low"},
  {"type": "project_context", "content": "Abhi is building Jarvis 2.0, a persistent AI assistant.", "confidence": 0.99, "sensitivity": "personal"}
]
"""


class Distiller:
    """
    Reads today's session, extracts semantic memories via Gemini Flash,
    deduplicates against ChromaDB, and saves new memories.
    """

    async def run(self, date_str: str | None = None) -> dict[str, Any]:
        """
        Executes the distillation pipeline.
        Returns a summary dictionary of what was processed.
        """
        logger.info("[Distiller] Starting memory distillation pipeline...")
        
        # Step 1: Load today's session from Redis
        messages = await session_store.load_session(date_str)
        if not messages:
            logger.info("[Distiller] No session messages found to distill.")
            return {"status": "skipped", "reason": "No messages found", "new_memories": 0}

        # Step 2: Format transcript for Gemini
        transcript = self._format_transcript(messages)
        if len(transcript.split()) < 20: # Skip very short conversations
            logger.info("[Distiller] Transcript too short to extract meaningful memories.")
            return {"status": "skipped", "reason": "Transcript too short", "new_memories": 0}

        # Step 3: Extract memories via Gemini Flash
        raw_memories = await self._extract_memories(transcript)
        if not raw_memories:
            logger.info("[Distiller] Gemini extracted no memories from transcript.")
            return {"status": "completed", "new_memories": 0, "duplicates_skipped": 0}

        # Step 4: Deduplicate and Save
        new_count = 0
        dup_count = 0
        
        for raw_mem in raw_memories:
            try:
                # Validate and parse into our Pydantic schema
                memory_entry = MemoryEntry(
                    type=MemoryType(raw_mem.get("type", "personal_fact")),
                    content=raw_mem.get("content", "").strip(),
                    confidence=float(raw_mem.get("confidence", 0.7)),
                    sensitivity=SensitivityLevel(raw_mem.get("sensitivity", "low")),
                    source_ref=f"session:{date_str or datetime.now(UTC).strftime('%Y-%m-%d')}"
                )
                
                if not memory_entry.content:
                    continue

                # Step 4a: Semantic Deduplication
                is_dup = self._check_for_duplicate(memory_entry.content)
                if is_dup:
                    dup_count += 1
                    continue

                # Step 4b: Save to ChromaDB
                semantic_memory.add_memory(memory_entry)
                new_count += 1

            except (ValueError, KeyError) as e:
                logger.warning(f"[Distiller] Skipping malformed memory entry: {raw_mem} - Error: {e}")
                continue

        logger.info(f"[Distiller] Pipeline complete. New memories: {new_count}, Duplicates skipped: {dup_count}")
        return {
            "status": "completed",
            "new_memories": new_count,
            "duplicates_skipped": dup_count,
            "total_extracted": len(raw_memories)
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _format_transcript(self, messages: list[dict]) -> str:
        """Converts Redis message objects into a readable text transcript."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown").capitalize()
            content = msg.get("content", "").strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    async def _extract_memories(self, transcript: str) -> list[dict]:
        """Calls Gemini Flash to extract structured JSON memories from text."""
        loop = asyncio.get_running_loop()
        try:
            reply = await loop.run_in_executor(
                None,
                lambda: generate_reply(
                    api_key=settings.gemini_api_key,
                    model=settings.gemini_model, # Ensure this points to Flash for cost/speed
                    user_message=f"Conversation Transcript:\n\n{transcript}",
                    system_instruction=_DISTILLATION_PROMPT,
                    temperature=0.1,
                    max_output_tokens=2000,
                ),
            )
            
            text = reply.text.strip()
            # Clean up markdown fences if Gemini adds them despite instructions
            if text.startswith("```json"):
                text = text[7:-3].strip()
            elif text.startswith("```"):
                text = text[3:-3].strip()

            memories = json.loads(text)
            if isinstance(memories, list):
                return memories
            return []
            
        except (GeminiClientError, json.JSONDecodeError) as exc:
            logger.error(f"[Distiller] Gemini extraction or JSON parsing failed: {exc}")
            return []

    def _check_for_duplicate(self, content: str) -> bool:
        """
        Queries ChromaDB to see if a semantically similar memory already exists.
        If the top result is highly similar (distance < 0.15), we consider it a duplicate.
        """
        try:
            results = semantic_memory.collection.query(
                query_texts=[content],
                n_results=1,
                include=["distances"]
            )
            
            if results and results.get("distances") and results["distances"][0]:
                # ChromaDB L2 distance: lower is closer. < 0.15 is almost identical.
                distance = results["distances"][0][0]
                if distance < 0.15:
                    logger.info(f"[Distiller] Duplicate detected (distance {distance:.3f}): {content[:40]}...")
                    return True
                    
        except Exception as e:
            logger.warning(f"[Distiller] Deduplication check failed: {e}")
            
        return False


# ── Singleton instance ───────────────────────────────────────────────────────────

distiller = Distiller()