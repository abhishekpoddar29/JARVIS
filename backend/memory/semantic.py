from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import chromadb
from google import genai  # ← Modern Google GenAI SDK

from config import get_settings
from memory.chromadb_schemas import MemoryEntry

logger = logging.getLogger(__name__)
settings = get_settings()

# ── ChromaDB Configuration ──────────────────────────────────────────────────────

CHROMA_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "chroma"
CHROMA_DB_PATH.mkdir(parents=True, exist_ok=True)


# ── Custom Gemini Embedding Function ────────────────────────────────────────────
# Using the modern google-genai SDK to bypass ChromaDB's broken legacy wrapper.

class CustomGeminiEmbeddingFunction(chromadb.EmbeddingFunction):
    def __init__(self, api_key: str, model_name: str = "models/gemini-embedding-001"):
        # Initialize the modern Google GenAI Client
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def __call__(self, input: chromadb.Documents) -> chromadb.Embeddings:
        """
        ChromaDB calls this method with a list of documents.
        We use the modern client to generate embeddings for the batch.
        """
        try:
            response = self.client.models.embed_content(
                model=self.model_name,
                contents=input
            )
            # The modern SDK returns a list of ContentEmbedding objects with a .values attribute
            return [emb.values for emb in response.embeddings]
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise

    def name(self):
        return "custom_gemini_embedding"


# Initialize our custom embedding function
gemini_ef = CustomGeminiEmbeddingFunction(api_key=settings.gemini_api_key)


class SemanticMemoryManager:
    """
    Manages CRUD operations for Jarvis's long-term semantic memory in ChromaDB.
    """

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not configured for ChromaDB embeddings")

        # Initialize persistent local client
        self.client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
        
        # Get or create the Jarvis memory collection
        self.collection = self.client.get_or_create_collection(
            name="jarvis_semantic_memory",
            embedding_function=gemini_ef,
            metadata={"description": "Long-term distilled semantic memory for Jarvis 2.0"}
        )
        
        logger.info(f"SemanticMemoryManager initialized. Path: {CHROMA_DB_PATH}")

    # ── Create / Update ───────────────────────────────────────────────────────

    def add_memory(self, memory: MemoryEntry) -> None:
        """Adds a new memory or updates it if the ID already exists."""
        try:
            self.collection.upsert(
                ids=[memory.id],
                documents=[memory.content],
                metadatas=[memory.to_chroma_metadata()]
            )
            logger.info(f"[Memory Saved] Type: {memory.type.value} | Content: {memory.content[:50]}...")
        except Exception as e:
            logger.error(f"Failed to save memory {memory.id}: {e}")
            raise

    # ── Read ──────────────────────────────────────────────────────────────────

    def query_memories(self, query_text: str, n_results: int = 5) -> list[MemoryEntry]:
        """Searches the vector database for memories relevant to the query text."""
        if self.collection.count() == 0:
            return []

        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=min(n_results, self.collection.count()),
                include=["documents", "metadatas", "distances"]
            )
            
            memories: list[MemoryEntry] = []
            if results and results.get("documents"):
                documents = results["documents"][0]
                metadatas = results["metadatas"][0]
                ids = results["ids"][0]
                
                for i in range(len(documents)):
                    memory = MemoryEntry.from_chroma_result(
                        chroma_id=ids[i],
                        document=documents[i],
                        metadata=metadatas[i]
                    )
                    memories.append(memory)
                    
            return memories

        except Exception as e:
            logger.error(f"Failed to query memories for '{query_text}': {e}")
            return []

    def get_all_memories(self) -> list[MemoryEntry]:
        """Retrieves all memories (used for the F11 Memory Viewer UI)."""
        if self.collection.count() == 0:
            return []

        try:
            results = self.collection.get(include=["documents", "metadatas"])
            memories: list[MemoryEntry] = []
            
            for i in range(len(results["ids"])):
                memory = MemoryEntry.from_chroma_result(
                    chroma_id=results["ids"][i],
                    document=results["documents"][i],
                    metadata=results["metadatas"][i]
                )
                memories.append(memory)
                
            return memories
        except Exception as e:
            logger.error(f"Failed to retrieve all memories: {e}")
            return []

    def get_memory_by_id(self, memory_id: str) -> MemoryEntry | None:
        """Retrieves a single memory by its exact ID."""
        try:
            result = self.collection.get(ids=[memory_id], include=["documents", "metadatas"])
            if result and result["ids"]:
                return MemoryEntry.from_chroma_result(
                    chroma_id=result["ids"][0],
                    document=result["documents"][0],
                    metadata=result["metadatas"][0]
                )
            return None
        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            return None

    # ── Update ────────────────────────────────────────────────────────────────

    def update_last_used(self, memory_id: str) -> None:
        """Updates the last_used timestamp for a retrieved memory."""
        from datetime import datetime, UTC
        try:
            memory = self.get_memory_by_id(memory_id)
            if memory:
                memory.last_used = datetime.now(UTC)
                self.collection.update(
                    ids=[memory.id],
                    metadatas=[memory.to_chroma_metadata()]
                )
        except Exception as e:
            logger.warning(f"Failed to update last_used for {memory_id}: {e}")

    def update_memory(self, memory: MemoryEntry) -> None:
        """Updates an existing memory's content or metadata (e.g., pinning)."""
        try:
            self.collection.update(
                ids=[memory.id],
                documents=[memory.content],
                metadatas=[memory.to_chroma_metadata()]
            )
            logger.info(f"[Memory Updated] ID: {memory.id}")
        except Exception as e:
            logger.error(f"Failed to update memory {memory.id}: {e}")

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete_memory(self, memory_id: str) -> None:
        """Deletes a specific memory by ID."""
        try:
            self.collection.delete(ids=[memory_id])
            logger.info(f"[Memory Deleted] ID: {memory_id}")
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")

    def wipe_all_memories(self) -> None:
        """DESTRUCTIVE: Wipes the entire semantic memory collection."""
        try:
            self.client.delete_collection("jarvis_semantic_memory")
            self.collection = self.client.get_or_create_collection(
                name="jarvis_semantic_memory",
                embedding_function=gemini_ef,
                metadata={"description": "Long-term distilled semantic memory for Jarvis 2.0"}
            )
            logger.warning("[Memory Wiped] All semantic memories deleted.")
        except Exception as e:
            logger.error(f"Failed to wipe memories: {e}")


# ── Singleton instance ───────────────────────────────────────────────────────────

semantic_memory = SemanticMemoryManager()