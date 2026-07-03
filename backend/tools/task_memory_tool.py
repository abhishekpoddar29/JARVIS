from tools.base import BaseTool, ToolResult
from memory.session_storage import session_store


class TaskMemoryTool(BaseTool):

    name: str = "task_memory_tool"

    description: str = (
        "Store, retrieve, list and clear temporary notes, reminders, tasks "
        "and session memories."
    )

    async def run(
        self,
        action: str = "remember",
        task: str = "",
        query: str = "",
    ) -> ToolResult:

        try:

            action = action.lower().strip()

            print("=" * 80)
            print(f"[TASK MEMORY] Action: {action}")
            print(f"[TASK MEMORY] Task: {task}")
            print(f"[TASK MEMORY] Query: {query}")
            print("=" * 80)

            # =====================================================
            # REMEMBER
            # =====================================================

            if action in ["remember", "save", "store", "add"]:

                memory_text = task.strip()

                if not memory_text:
                    return ToolResult(
                        ok=False,
                        content="Nothing provided to remember.",
                    )

                print(f"[TASK MEMORY] Saving memory: {memory_text}")

                await session_store.append_message(
                    role="memory",
                    content=memory_text,
                )

                # Verify save
                session = await session_store.load_session()

                memory_count = 0

                print("=" * 80)
                print("[TASK MEMORY] SESSION AFTER SAVE")
                print("=" * 80)

                for item in session:

                    if item.get("role") == "memory":
                        memory_count += 1
                        print(item)

                print(
                    f"[TASK MEMORY] Total memories stored: {memory_count}"
                )

                return ToolResult(
                    ok=True,
                    content=f"Saved memory: {memory_text}",
                    data={
                        "action": "remember",
                        "memory": memory_text,
                        "memory_count": memory_count,
                    },
                )

            # =====================================================
            # RETRIEVE
            # =====================================================

            if action in ["retrieve", "recall", "find", "search"]:

                session = await session_store.load_session()

                memories = [
                    m.get("content", "")
                    for m in session
                    if m.get("role") == "memory"
                ]

                print("=" * 80)
                print("[TASK MEMORY] RETRIEVE")
                print(f"[TASK MEMORY] Found {len(memories)} memories")
                print(memories)
                print("=" * 80)

                if not memories:

                    return ToolResult(
                        ok=True,
                        content="No memories found.",
                        data={
                            "memories": [],
                        },
                    )

                # Return latest memory if no query supplied

                if not query.strip():

                    latest_memory = memories[-1]

                    return ToolResult(
                        ok=True,
                        content=latest_memory,
                        data={
                            "latest_memory": latest_memory,
                            "memories": memories,
                        },
                    )

                query_lower = query.lower().strip()

                matches = []

                for memory in memories:

                    memory_lower = memory.lower()

                    # Exact phrase match
                    if query_lower in memory_lower:
                        matches.append(memory)
                        continue

                    # Keyword match
                    score = 0

                    for word in query_lower.split():

                        if word in memory_lower:
                            score += 1

                    if score > 0:
                        matches.append(memory)

                print(f"[TASK MEMORY] Query: {query}")
                print(f"[TASK MEMORY] Matches: {matches}")

                if not matches:

                    return ToolResult(
                        ok=True,
                        content="No matching memories found.",
                        data={
                            "query": query,
                            "matches": [],
                            "memories": memories,
                        },
                    )

                return ToolResult(
                    ok=True,
                    content="\n".join(matches),
                    data={
                        "query": query,
                        "matches": matches,
                    },
                )

            # =====================================================
            # LIST
            # =====================================================

            if action == "list":

                session = await session_store.load_session()

                memories = [
                    m.get("content", "")
                    for m in session
                    if m.get("role") == "memory"
                ]

                return ToolResult(
                    ok=True,
                    content=(
                        "\n".join(
                            f"{i + 1}. {memory}"
                            for i, memory in enumerate(memories)
                        )
                        if memories
                        else "No saved memories."
                    ),
                    data={
                        "memories": memories,
                    },
                )

            # =====================================================
            # CLEAR
            # =====================================================

            if action == "clear":

                await session_store.clear_memories()

                return ToolResult(
                    ok=True,
                    content="All memories cleared.",
                    data={
                        "action": "clear",
                    },
                )

            # =====================================================
            # UNKNOWN
            # =====================================================

            return ToolResult(
                ok=False,
                content=f"Unknown action: {action}",
            )

        except Exception as exc:

            print("[TASK MEMORY ERROR]")
            print(str(exc))

            return ToolResult(
                ok=False,
                content="Task memory operation failed.",
                error=str(exc),
                data={
                    "error": str(exc),
                },
            )