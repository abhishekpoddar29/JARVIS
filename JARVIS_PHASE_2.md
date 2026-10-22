# JARVIS PHASE 2 - Memory, Continuity, and Conversation Intelligence

> Status: Planning document only  
> Created: August 18, 2026  
> Project root: `C:\Users\abhir\Desktop\projects\Jarvis-0.2`  
> Scope: Memory architecture, conversation persistence, cross-chat recall, UI changes, and implementation plan  
> Rule: No source code is changed by this document

---

## 1. What Jarvis Already Is

Jarvis 2.0 is already a strong personal AI assistant architecture.

It has:

- A FastAPI backend.
- A React + TypeScript frontend.
- A central LangGraph brain.
- Gemini 2.5 Flash as the main reasoning model.
- Redis-backed session memory.
- ChromaDB semantic memory using Gemini embeddings.
- Tool registry and tool dispatcher.
- Human-in-the-loop approval.
- Voice input and browser speech output.
- Specialist agents for research, engineering, and financial analysis.
- Music, tracking, files, uploads, OAuth, and long-term memory UI.

The current main graph is:

```text
memory_retrieval
-> personality
-> gemini
-> tool
-> tool_response
-> finalize
```

This is a good foundation. The problem is not that Jarvis has no memory. The problem is that Jarvis has the wrong memory boundaries for the kind of assistant it is becoming.

Today, Jarvis mostly remembers:

- The current date-bucketed Redis session.
- Some extracted semantic memories in ChromaDB.
- The current LangGraph run state while the process is alive.

But Jarvis does not yet have:

- Durable conversation threads.
- Durable working memory per thread.
- A persistent current-task ledger across turns.
- A cross-chat memory graph.
- A reliable way to remember last tool results across new user turns.
- A way to reopen any past conversation and continue from it.

---

## 2. What Jarvis Will Become After Phase 2

After Phase 2, Jarvis should feel like one continuous personal intelligence rather than a fresh assistant attached to a daily chat buffer.

Jarvis will become:

- Conversation-aware.
- Thread-persistent.
- Cross-chat memory-aware.
- Tool-result-aware.
- Refresh-safe.
- Better at continuing unfinished work.
- Better at remembering decisions from previous chats.
- Better at retrieving the right past context without loading everything.

The target behavior is:

```text
Abhi opens Jarvis
-> Jarvis restores the last active conversation
-> Jarvis knows the current thread summary
-> Jarvis knows open tasks and recent tool outcomes
-> Jarvis can search all prior chats if relevant
-> Jarvis retrieves only the right memory nodes
-> Jarvis answers or acts with continuity
```

Jarvis should eventually feel like:

```text
Store everything.
Summarize continuously.
Connect related memories.
Recall selectively.
Act with continuity.
```

The important constraint:

Jarvis should not inject every past conversation into every prompt. That would make it slower, more expensive, and more confused. Jarvis should store everything locally, but retrieve only a small, relevant memory briefing for each turn.

---

## 3. Research Basis

The Phase 2 plan follows current agent-memory patterns from established systems.

### 3.1 LangGraph memory model

LangGraph separates short-term memory and long-term memory.

- Short-term memory is thread-scoped and stored in graph state.
- Graph state should be persisted with a checkpointer so a thread can resume later.
- Long-term memory can be shared across threads through custom namespaces.

Reference:

- [LangGraph memory concepts](https://docs.langchain.com/oss/python/concepts/memory)
- [LangGraph add and manage memory](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

This confirms that Jarvis should use:

- Thread memory for the current conversation.
- User-level memory for cross-chat recall.
- Persistent checkpointing instead of process-local `MemorySaver`.

### 3.2 Redis agent memory pattern

Redis describes an agent memory design with:

- Working memory per thread.
- Long-term memory across sessions.
- Time-ordered event logs.
- Background summarization and promotion.
- Fast hot-path reads.

Reference:

- [Redis as agent memory](https://redis.io/docs/latest/develop/use-cases/agent-memory/)
- [Redis Agent Memory two-tier model](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/)

This confirms that Redis can stay useful in Jarvis, but it should be thread-scoped rather than date-scoped.

### 3.3 Zep-style user knowledge graph

Zep models each chat as a session, but extracts knowledge into a user-level graph shared across all sessions.

Reference:

- [Zep sessions](https://help.getzep.com/v2/sessions)
- [Zep quickstart](https://help.getzep.com/v2/quickstart)
- [Zep concepts](https://help.getzep.com/v2/concepts)

This supports the idea we discussed:

Each Jarvis conversation can be a summary node. All conversation nodes can contribute to one integrated memory graph for Abhi.

### 3.4 GraphRAG and memory graph reasoning

Graph-based retrieval is useful when the question requires connections across many past events, entities, or topics.

Reference:

- [GraphRAG paper](https://doi.org/10.48550/arXiv.2404.16130)

This supports an Obsidian-style memory graph for Jarvis, as long as retrieval is bounded.

### 3.5 MemGPT-style memory management

MemGPT introduced the idea that LLM agents need controlled memory movement between active context and archival memory.

Reference:

- [MemGPT](https://research.memgpt.ai/)

This supports the key Phase 2 rule:

Jarvis should not try to hold all memory in the active prompt. Jarvis should move information between active working memory, summaries, semantic memory, and archive.

---

## 4. Current Problems Verified in the Codebase

### 4.1 Redis session is date-based, not conversation-based

Current file:

- `backend/memory/session_storage.py`

Current behavior:

- Session key is based on today.
- Memory key is based on today.
- Key-value storage is also namespaced under today.

Problem:

Jarvis remembers "today's messages", not "this conversation thread."

Effect:

- Different topics on the same day are mixed together.
- A new chat is not truly separate.
- Past chats are not first-class objects.
- Research context and temporary values are tied to the current date instead of a conversation.

### 4.2 Tool results are stored but not properly reintroduced

Current file:

- `backend/main.py`

Current behavior:

- Tool result messages are appended to Redis.
- When building history for the next graph run, only `user` and `assistant` roles are included.

Problem:

Jarvis may not see the actual structured output from tools on the next turn.

Effect:

- Jarvis may forget the last tool result.
- Jarvis may repeat a tool unnecessarily.
- Jarvis may run the wrong tool after a follow-up.
- Jarvis may rely on the assistant's prose instead of the actual observation.

### 4.3 LangGraph state is process-local

Current file:

- `backend/agents/graph.py`

Current behavior:

- Graph is compiled with `MemorySaver()`.

Problem:

`MemorySaver` is process-local. It does not provide durable persistence across backend restarts.

Effect:

- HIL recovery is fragile after process restart.
- Long-running graph state is not durable.
- Refresh-safe continuity depends too much on external Redis chat replay.

### 4.4 Per-turn task state is reset every new user turn

Current file:

- `backend/agents/graph_nodes.py`

Current behavior:

- `personality_node` clears `planned_tasks`, `tool_results`, `executed_calls`, `deferred_tool_calls`, and related fields at the start of a new turn.

Problem:

This is correct for one isolated graph run, but wrong for durable human-like continuity.

Effect:

- Jarvis can complete multi-step work inside one run.
- But Jarvis does not retain a structured "what I was doing" ledger into the next user turn.

### 4.5 ChromaDB is used as semantic memory, not working memory

Current files:

- `backend/memory/semantic.py`
- `backend/memory/distiller.py`
- `backend/agents/graph_nodes.py`

Current behavior:

- Chroma retrieves a few relevant long-term facts.
- Distiller extracts stable preferences, facts, decisions, project context, and task outcomes.

Problem:

This is useful, but it is not enough for live continuity.

Effect:

- Chroma can remember "Abhi prefers X."
- Chroma is not ideal for "Jarvis just searched Y and found Z two turns ago."

### 4.6 Frontend restores only today's session

Current file:

- `frontend/src/hooks/useJarvisSocket.ts`

Current behavior:

- On load, frontend calls `/api/session`.
- It restores only the date-based current session.

Problem:

There is no conversation list, no stable selected conversation, and no any-time chat access.

Effect:

- Refresh can restore today's chat only.
- Old conversations are not browsable as conversations.
- Export wipes the current Redis session.

---

## 5. Phase 2 Target Memory Model

Jarvis should have five memory layers.

### 5.1 Raw conversation archive

Purpose:

Store everything exactly as it happened.

Stores:

- User messages.
- Assistant messages.
- Tool calls.
- Tool results.
- HIL cards.
- HIL responses.
- Attachments.
- Code results.
- Tracking events.
- Music commands.
- Research progress events.
- Errors.
- Conversation metadata.

Recommended store:

- SQLite local database.

Why:

- Durable.
- Local.
- Easy to query.
- No separate service required.
- Good fit for "access any chat anytime."

### 5.2 Thread working memory

Purpose:

Tell Jarvis what it is currently doing.

Stores:

- Current conversation id.
- Thread title.
- Rolling summary.
- Last user intent.
- Last assistant commitment.
- Open tasks.
- Blocked tasks.
- Last important tool results.
- Pending HIL state.
- Active specialist subgraph context.

Recommended store:

- Redis for hot access.
- SQLite snapshot for durability.

Why:

- Redis is fast for hot-path reads.
- SQLite makes the state recoverable.

### 5.3 Semantic memory

Purpose:

Remember durable facts and preferences.

Stores:

- Preferences.
- Personal facts.
- Project facts.
- Stable decisions.
- Recurring topics.
- Long-lived task outcomes.

Recommended store:

- Current ChromaDB.

Why:

- Already implemented.
- Good for similarity search.
- Good for long-term recall.

### 5.4 Conversation summary graph

Purpose:

Let Jarvis remember what previous chats were about.

Stores:

- One summary node per conversation.
- Optional segment nodes for large conversations.
- Entity nodes.
- Decision nodes.
- Task nodes.
- Project nodes.
- Tool-result summary nodes.
- Edges between related nodes.

Recommended store:

- SQLite tables for nodes and edges.
- Chroma embeddings on node summaries for semantic lookup.

Why:

- Obsidian-like structure.
- Local and inspectable.
- Does not require Neo4j or another service for Phase 2.

### 5.5 Active prompt memory briefing

Purpose:

Give the model only the right memory, not all memory.

Each graph run should receive:

- Current thread summary.
- Recent raw turns.
- Open tasks.
- Last important tool results.
- Relevant semantic memories.
- Relevant prior conversation nodes.
- Any pinned memories.

The briefing must be small and ranked.

---

## 6. New High-Level Flow

### 6.1 Current flow

```text
frontend sends message
-> main.py appends user message to date session
-> main.py loads today's session
-> graph starts with new active_thread_id
-> memory_retrieval gets Chroma memories
-> personality builds prompt
-> gemini plans or answers
-> tools run
-> assistant response is saved
-> frontend receives answer
```

### 6.2 Phase 2 flow

```text
frontend sends message with conversation_id
-> main.py resolves or creates conversation
-> conversation_store appends user event
-> working_memory loads thread summary, open tasks, last tool results
-> memory_retriever gets relevant cross-chat memory nodes
-> graph starts with persistent thread_id = conversation_id
-> memory_retrieval builds a memory briefing
-> personality injects memory briefing into the system prompt
-> gemini plans or answers
-> tools run
-> tool observations are saved as structured events
-> working_memory updates open tasks and last results
-> summarizer updates thread summary
-> graph memory checkpoint persists
-> assistant event is saved
-> frontend receives answer and updated conversation metadata
-> background distiller promotes durable memories and graph nodes
```

---

## 7. Retrieval Logic

Jarvis should not retrieve everything.

Recommended memory retrieval budget:

- Current thread summary: always included.
- Current open tasks: always included.
- Last important tool results: max 3 to 5.
- Recent raw turns: max 6 to 10 messages.
- Semantic memories: max 3 to 5.
- Cross-chat memory graph nodes: max 3 to 5.
- Full raw old transcript: only when user explicitly asks to inspect or continue that exact chat.

Retrieval ranking should consider:

- Semantic similarity.
- Recency.
- Pinned status.
- Conversation status.
- Same project/topic.
- Same entity.
- Explicit user reference.
- Whether the prior memory contains a decision.
- Whether the prior memory contains unfinished work.

Prompt principle:

```text
Give Jarvis enough context to continue intelligently.
Do not give Jarvis so much context that it gets distracted.
```

---

## 8. Obsidian Brain Logic

The memory graph should grow as Jarvis conversations grow.

### 8.1 Node types

Recommended node types:

- `conversation`
- `conversation_segment`
- `entity`
- `project`
- `decision`
- `task`
- `tool_result`
- `preference`
- `fact`
- `question`
- `open_loop`
- `document`
- `code_area`

### 8.2 Edge types

Recommended edge types:

- `discussed`
- `decided`
- `depends_on`
- `continues`
- `contradicts`
- `supersedes`
- `mentions`
- `produced`
- `blocked_by`
- `resolved_by`
- `related_to`

### 8.3 How graph growth should work

After each conversation milestone:

```text
new messages saved
-> lightweight thread summary update
-> background extraction creates or updates nodes
-> embeddings created for node summaries
-> edges created between related nodes
-> retrieval indexes updated
```

Milestones can be:

- Every N turns.
- On conversation close.
- On export.
- On app idle.
- On manual "distill" action.
- After specialist agent completion.

### 8.4 Avoiding graph clutter

The graph must not create a node for every sentence.

Create nodes only for:

- Stable decisions.
- Important tasks.
- Completed outcomes.
- Project-level facts.
- Repeated topics.
- Meaningful entities.
- Conversations and conversation segments.

Small talk should stay in raw archive only.

---

## 9. Speed and Performance Analysis

This can make Jarvis slower if built incorrectly.

### 9.1 Slow design

Bad pattern:

```text
Every user turn
-> scan all conversations
-> summarize everything
-> traverse entire memory graph
-> inject many old memories into prompt
-> run multiple LLM memory calls before answering
```

Result:

- Slow responses.
- Higher token cost.
- More hallucinated connections.
- More stale context.
- More tool confusion.

### 9.2 Fast design

Good pattern:

```text
Every user turn
-> read current thread working memory
-> query small Chroma index
-> query top graph nodes
-> build compact memory briefing
-> answer
-> update summaries in background
```

Result:

- Fast hot path.
- Better continuity.
- Lower token cost.
- Better tool selection.
- Cross-chat recall only when useful.

### 9.3 Hot-path vs background work

Hot path:

- Save user event.
- Load working memory.
- Retrieve top memories.
- Invoke graph.
- Save assistant and tool events.

Background:

- Deep summarization.
- Entity extraction.
- Graph edge generation.
- Semantic memory promotion.
- Deduplication.
- Conversation title generation.

This is the key performance rule of Phase 2.

---

## 10. Backend Files To Update

### 10.1 `backend/main.py`

Purpose of changes:

- Make chat conversation-aware.
- Stop treating today as the only session.
- Accept or create `conversation_id`.
- Pass `conversation_id` into graph state.
- Persist every event into conversation archive.
- Load thread working memory before graph invocation.
- Save structured tool results.
- Restore HIL and active graph state by conversation.
- Return conversation metadata to frontend.

Current areas affected:

- `/api/session`
- `/api/export`
- `/ws/chat`
- `_send_completed_graph_result`
- HIL resume logic
- session history construction

Required logic:

```text
On frontend connect:
  restore selected conversation or create one

On message:
  resolve conversation_id
  append user event
  build prompt context from memory manager
  run graph with thread_id = conversation_id
  append tool events
  append assistant event
  update working memory
  return updated conversation metadata
```

Compatibility rule:

Keep `/api/session` temporarily for the current frontend until the new conversation APIs are fully wired.

### 10.2 `backend/config.py`

Purpose of changes:

- Add settings for Phase 2 storage and memory limits.

Recommended settings:

- `CONVERSATION_DB_PATH`
- `WORKING_MEMORY_TTL_SECONDS`
- `MEMORY_RECENT_TURN_LIMIT`
- `MEMORY_TOOL_RESULT_LIMIT`
- `MEMORY_GRAPH_NODE_LIMIT`
- `MEMORY_SEMANTIC_LIMIT`
- `THREAD_SUMMARY_MAX_CHARS`
- `MEMORY_BACKGROUND_DISTILL_ENABLED`
- `LANGGRAPH_CHECKPOINT_BACKEND`
- `LANGGRAPH_CHECKPOINT_DB_PATH`

Compatibility rule:

Defaults should work locally without requiring new environment variables.

### 10.3 `backend/agents/graph.py`

Purpose of changes:

- Replace process-local `MemorySaver()` with persistent checkpointing.
- Use `conversation_id` as the stable graph `thread_id`.

Recommended options:

- SQLite checkpointer for local-first durability.
- Redis checkpointer later if Redis becomes the central memory runtime.

Compatibility rule:

If persistent checkpointing fails, fall back clearly and visibly during startup, not silently.

### 10.4 `backend/agents/graph_state.py`

Purpose of changes:

- Add explicit state fields for durable continuity.

Fields to add:

- `conversation_id`
- `conversation_title`
- `thread_summary`
- `working_memory`
- `memory_briefing`
- `recent_turns`
- `recent_tool_results`
- `open_tasks`
- `blocked_tasks`
- `last_user_intent`
- `last_assistant_action`
- `cross_chat_memories`
- `conversation_status`
- `pending_hil`

Compatibility rule:

Keep existing fields such as `planned_tasks`, `tool_calls`, `tool_results`, and `tool_data`. Existing orchestration should not be rewritten in Phase 2.

### 10.5 `backend/agents/graph_nodes.py`

Purpose of changes:

- Load more complete memory context.
- Preserve durable working memory while still clearing per-turn execution buffers.
- Inject the right memory briefing into the prompt.
- Save or expose task ledger outcomes to the memory layer.

Specific changes:

- `memory_retrieval_node` should retrieve:
  - Chroma semantic memories
  - current thread summary
  - open tasks
  - last important tool results
  - relevant prior conversation nodes

- `personality_node` should include:
  - long-term memory context
  - thread summary
  - active task ledger
  - recent tool observations
  - cross-chat relevant memory

- Per-turn reset should clear:
  - current `tool_calls`
  - current `deferred_tool_calls`
  - current `current_tool_results`
  - current execution counters

- Per-turn reset should not erase durable context:
  - open tasks loaded from working memory
  - thread summary
  - recent tool result memory
  - conversation id

Compatibility rule:

Do not weaken the existing bounded autonomy rules:

- `MAX_TOOL_ROUNDS`
- `MAX_TOTAL_TOOL_CALLS`
- `MAX_PARALLEL_TOOLS`
- serial handling for HIL-sensitive tools

### 10.6 `backend/tools/research_tool.py`

Purpose of changes:

- Make research context conversation-aware.
- Preserve verified findings and unresolved claims across turns and chats.

Current issue:

- Research context is stored through date-scoped session key-value storage.

Required change:

```text
research_context key = conversation_id + normalized topic
```

Optional user-level recall:

```text
research_memory key = user namespace + topic/entity
```

What to persist:

- topic
- original request
- claim ledger summary
- verified claims
- unresolved claims
- contradictions
- sources used
- final report summary
- decision_safe flag
- created_at

Compatibility rule:

The research subgraph should remain unchanged initially. Phase 2 should change the wrapper's context storage and output persistence before modifying the research graph itself.

### 10.7 `backend/memory/session_storage.py`

Purpose of changes:

- Evolve from date-session store to thread-scoped working memory store.

Possible path:

- Keep old methods for compatibility.
- Add new methods using `conversation_id`.

New responsibilities:

- append event by conversation
- load recent thread events
- get/set working memory by conversation
- store pending HIL state
- store current task ledger

Compatibility rule:

Do not remove date-based methods immediately. Deprecate after frontend and backend have moved to conversation APIs.

### 10.8 `backend/memory/distiller.py`

Purpose of changes:

- Distill from conversation threads, not only today's session.
- Promote stable information into ChromaDB.
- Create or update memory graph nodes.

New behavior:

```text
conversation transcript
-> thread summary
-> durable semantic memories
-> graph nodes and edges
```

Compatibility rule:

Keep existing `/api/memory/distill` behavior during transition, but let it accept an optional `conversation_id`.

### 10.9 `backend/memory/export_manager.py`

Purpose of changes:

- Export one selected conversation.
- Export all conversations if requested.
- Stop coupling export with wiping the active session.

Current issue:

- Export archives today's Redis session and wipes it.

New behavior:

- Export should not reset memory by default.
- Reset should be a separate explicit action.

### 10.10 `backend/tools/task_memory_tool.py`

Purpose of changes:

- Make temporary task memories conversation-aware.
- Decide whether a saved memory is:
  - thread-only
  - user-level semantic memory
  - pinned memory

Compatibility rule:

Existing actions should still work:

- remember
- retrieve
- list
- clear

But they should operate in the right namespace.

---

## 11. Backend Files To Create

### 11.1 `backend/memory/conversation_store.py`

Purpose:

Durable local source of truth for conversations.

Responsibilities:

- Create conversation.
- List conversations.
- Load conversation.
- Append event.
- Update title.
- Update status.
- Save metadata.
- Save attachments references.
- Query messages.
- Export conversation.

Recommended storage:

- SQLite database at `backend/data/jarvis_conversations.db`.

### 11.2 `backend/memory/working_memory.py`

Purpose:

Manage the compact "what Jarvis is doing now" memory.

Responsibilities:

- Load working memory by conversation.
- Save working memory by conversation.
- Update open tasks.
- Update last tool results.
- Update last assistant action.
- Save pending HIL info.
- Clear completed ephemeral state.

### 11.3 `backend/memory/thread_summary.py`

Purpose:

Maintain rolling summaries for each conversation.

Responsibilities:

- Summarize new turns into existing summary.
- Keep summary short.
- Track decisions.
- Track open loops.
- Track tool outcomes.
- Track unresolved questions.

### 11.4 `backend/memory/memory_graph.py`

Purpose:

Create the Obsidian-style graph of Jarvis memory.

Responsibilities:

- Create nodes.
- Create edges.
- Deduplicate nodes.
- Link conversation nodes to entities, decisions, tasks, and projects.
- Query graph neighborhoods.

### 11.5 `backend/memory/memory_retriever.py`

Purpose:

Build the memory briefing for each graph invocation.

Responsibilities:

- Search ChromaDB.
- Search conversation summary nodes.
- Search graph neighbors.
- Rank memories.
- Apply memory budget limits.
- Return compact context.

### 11.6 `backend/api/conversation_routes.py`

Purpose:

Expose conversation APIs to the frontend.

Endpoints to add:

- `GET /api/conversations`
- `POST /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `PATCH /api/conversations/{conversation_id}`
- `DELETE /api/conversations/{conversation_id}` or archive-only
- `GET /api/conversations/{conversation_id}/messages`
- `POST /api/conversations/{conversation_id}/export`
- `POST /api/conversations/{conversation_id}/distill`
- `GET /api/conversations/{conversation_id}/memory`

Deletion should be conservative. Prefer archive/hide first.

### 11.7 `backend/memory/checkpointing.py`

Purpose:

Own persistent LangGraph checkpointer setup.

Responsibilities:

- Choose checkpointer from config.
- Initialize storage.
- Return checkpointer to graph builder.
- Keep fallback behavior explicit.

---

## 12. Frontend Changes

Phase 2 needs frontend changes because memory is now conversation-based, not session-based.

### 12.1 `frontend/src/hooks/useJarvisSocket.ts`

Purpose of changes:

- Track `conversationId`.
- Send `conversation_id` with messages.
- Restore selected conversation instead of only `/api/session`.
- Handle server-provided conversation metadata.
- Update message list when switching conversations.
- Keep WebSocket connected while changing active conversation.

New state:

- `conversationId`
- `conversations`
- `activeConversation`
- `isLoadingConversation`
- `conversationSummary`
- `openTasks`
- `memoryBriefing`

New behavior:

```text
on app load:
  load conversation list
  restore last active conversation id from localStorage
  load messages for that conversation
  connect websocket

on send message:
  include conversation_id
  optimistically append user message
  backend appends durable event

on server response:
  append assistant message
  update conversation metadata
```

### 12.2 `frontend/src/App.tsx`

Purpose of changes:

- Replace "Session History" drawer with a real conversation history interface.
- Add new chat action.
- Add conversation title and status display.
- Add memory/working-state indicators.
- Keep focus view behavior if desired.

UI changes:

- Left or drawer conversation list.
- Current conversation header.
- New Chat button.
- Search conversations.
- Conversation status badges.
- Archive conversation action.
- Export selected conversation action.

Current export button should change:

From:

```text
Export & reset conversation
```

To:

```text
Export conversation
```

Reset should become separate:

```text
New chat
```

### 12.3 `frontend/src/types.ts`

Purpose of changes:

Add types:

- `Conversation`
- `ConversationSummary`
- `ConversationStatus`
- `ConversationEvent`
- `WorkingMemoryState`
- `OpenTask`
- `MemoryNode`
- `MemoryEdge`
- `MemoryBriefing`

Extend `ChatMessage`:

- `conversationId`
- `eventId`
- `toolResults`
- `toolName`
- `metadata`

### 12.4 `frontend/src/services/conversationService.ts`

Purpose:

New frontend API service for conversations.

Methods:

- list conversations
- create conversation
- get conversation
- get messages
- update title
- archive conversation
- export conversation
- get memory graph summary

### 12.5 `frontend/src/services/memoryService.ts`

Purpose of changes:

- Keep semantic memory API.
- Add graph memory API only if memory graph UI is included in Phase 2.
- Add conversation-specific distill action.

### 12.6 New UI components

Recommended components:

- `ConversationList.tsx`
- `ConversationHeader.tsx`
- `ConversationSearch.tsx`
- `ConversationStatusBadge.tsx`
- `WorkingMemoryPanel.tsx`
- `MemoryGraphPanel.tsx` later, if visual graph is added

### 12.7 `frontend/src/styles.css`

Purpose of changes:

- Style conversation browser.
- Style active thread header.
- Style open task badges.
- Style memory status indicators.
- Make history access feel like a durable archive, not a temporary drawer.

Design guidance:

- Keep it aligned with current Jarvis cockpit style.
- Avoid turning memory into a heavy admin screen.
- Make current conversation and previous conversations easy to scan.

---

## 13. Data Model Plan

### 13.1 SQLite tables

Recommended tables:

- `conversations`
- `conversation_events`
- `conversation_summaries`
- `working_memory_snapshots`
- `memory_nodes`
- `memory_edges`
- `memory_node_embeddings`
- `conversation_exports`

### 13.2 Conversation record

Fields:

- `id`
- `title`
- `status`
- `created_at`
- `updated_at`
- `last_message_at`
- `summary`
- `message_count`
- `tool_event_count`
- `has_open_tasks`
- `pending_hil`
- `archived`

### 13.3 Event record

Fields:

- `id`
- `conversation_id`
- `sequence`
- `role`
- `type`
- `content`
- `metadata_json`
- `created_at`

Event types:

- `user_message`
- `assistant_message`
- `tool_call`
- `tool_result`
- `hil_request`
- `hil_response`
- `system_event`
- `attachment`
- `progress_event`
- `error`

### 13.4 Working memory snapshot

Fields:

- `conversation_id`
- `summary`
- `open_tasks_json`
- `blocked_tasks_json`
- `recent_tool_results_json`
- `last_user_intent`
- `last_assistant_action`
- `pending_hil_json`
- `updated_at`

### 13.5 Memory graph node

Fields:

- `id`
- `type`
- `title`
- `summary`
- `source_conversation_id`
- `source_event_id`
- `confidence`
- `importance`
- `created_at`
- `updated_at`

### 13.6 Memory graph edge

Fields:

- `id`
- `source_node_id`
- `target_node_id`
- `type`
- `weight`
- `evidence`
- `created_at`

---

## 14. Prompt Logic Changes

Current prompt sources:

- `PersonalityEngine`
- Jinja templates in `backend/agents/prompts`
- `TOOL_ROUTER_PROMPT`
- `TOOL_RESPONSE_PROMPT`

Phase 2 should not replace this prompt system.

It should add a structured memory section:

```text
CURRENT CONVERSATION MEMORY:
- Summary
- Open tasks
- Last tool results
- Pending commitments

RELEVANT PAST CONVERSATIONS:
- Conversation node summaries
- Decisions
- Related project facts

LONG-TERM MEMORY:
- Semantic memories from Chroma
```

Prompt rules:

- Recent tool results outrank older semantic memories.
- Current open tasks outrank cross-chat recall.
- Cross-chat recall should be used only when relevant.
- Past memories should not override live user instruction.
- Current location questions must still use `location_tool`, not remembered location.
- Sensitive actions still require HIL.

---

## 15. Compatibility And Build Safety

Phase 2 must not break the current build.

Safety strategy:

- Add new storage modules without removing old session APIs.
- Keep `/api/session` until frontend fully migrates.
- Keep current `session_store.append_message` calls during transition.
- Add conversation persistence alongside existing Redis writes first.
- Add new APIs behind separate routes.
- Keep existing graph nodes and orchestration limits.
- Replace checkpointer only after storage is verified.
- Keep ChromaDB schema compatible.
- Keep backup files untouched.

Important:

Do not remove:

- existing tool registry
- current HIL flow
- current voice flow
- current music flow
- current tracking flow
- existing memory viewer
- existing export behavior until replacement is tested

---

## 16. Verification Plan

### 16.1 Unit-level verification

Verify:

- Conversation create/list/load.
- Event append ordering.
- Recent message retrieval.
- Working memory load/save.
- Summary update.
- Graph node creation.
- Graph edge creation.
- Memory retrieval ranking.
- Export selected conversation.

### 16.2 Backend integration verification

Verify:

- First message creates a conversation.
- Refresh restores the same conversation.
- New chat creates a separate conversation.
- Switching conversations loads correct messages.
- Tool result is stored as structured event.
- Follow-up question sees last tool result.
- HIL survives refresh if checkpointer supports it.
- Research context is scoped correctly.
- Existing `/api/session` still works during migration.

### 16.3 Frontend verification

Verify:

- Conversation list loads.
- Last active conversation restores.
- New chat works.
- Sending message includes `conversation_id`.
- Old chats can be reopened.
- Export does not wipe memory unexpectedly.
- Session history drawer replacement works on desktop and mobile.
- Memory viewer still works.

### 16.4 Behavior verification

Test scenarios:

1. Ask Jarvis to research a topic. Follow up with "what was the conclusion?"
2. Start a new chat and ask "what did we decide about memory?"
3. Refresh during a conversation and continue.
4. Ask Jarvis to run a tool, then ask a follow-up that depends on the result.
5. Start two different topics on the same day and confirm they do not mix.
6. Ask for a current location and confirm Jarvis still uses `location_tool`.
7. Export a conversation and confirm it remains accessible.
8. Trigger HIL, refresh, and confirm pending approval can resume.

---

## 17. Implementation Phases

### Phase 2.1 - Durable Conversations

Goal:

Make every chat a durable local conversation.

Work:

- Create `conversation_store.py`.
- Create SQLite database and tables.
- Add conversation APIs.
- Update `main.py` to create/use `conversation_id`.
- Keep old session restore temporarily.
- Add frontend conversation service.
- Add conversation list UI.

Success:

Jarvis can reopen any previous chat.

### Phase 2.2 - Working Memory

Goal:

Make Jarvis remember what it is doing within a conversation.

Work:

- Create `working_memory.py`.
- Add working memory fields to graph state.
- Persist thread summary, open tasks, recent tool results.
- Inject working memory into graph prompt.
- Preserve useful task state between turns.

Success:

Jarvis does not forget the last result or active task.

### Phase 2.3 - Persistent Graph Checkpointing

Goal:

Make LangGraph state durable.

Work:

- Create `checkpointing.py`.
- Replace `MemorySaver`.
- Use `conversation_id` as graph thread id.
- Test HIL resume and refresh.

Success:

Paused graph state is not only process-local.

### Phase 2.4 - Cross-Chat Memory Graph

Goal:

Make Jarvis remember what previous chats were about.

Work:

- Create `memory_graph.py`.
- Add graph node and edge tables.
- Upgrade `distiller.py`.
- Create conversation summary nodes.
- Link related conversations and entities.

Success:

New chats can recall relevant previous chat summaries.

### Phase 2.5 - Selective Retrieval

Goal:

Keep Jarvis fast while improving recall.

Work:

- Create `memory_retriever.py`.
- Rank memory candidates.
- Apply memory budgets.
- Add memory briefing to graph state.
- Add tests for not overloading prompts.

Success:

Jarvis recalls relevant memory without slowing normal turns.

### Phase 2.6 - UI Polish And Memory Visibility

Goal:

Make memory understandable and controllable.

Work:

- Improve conversation browser.
- Add thread summary display.
- Add open task badges.
- Add memory source indicators.
- Optional memory graph viewer.

Success:

Abhi can see and manage what Jarvis remembers.

---

## 18. Risks And How To Avoid Them

### Risk: Jarvis becomes slow

Cause:

- Too much retrieval or synchronous summarization.

Prevention:

- Keep hot path small.
- Run graph extraction in background.
- Use strict retrieval limits.
- Cache memory briefings.

### Risk: Jarvis remembers wrong things

Cause:

- Bad summary extraction.
- Weak deduplication.
- Old memory overriding current instruction.

Prevention:

- Store source references.
- Track confidence.
- Let user edit/delete memories.
- Prefer current conversation over older memory.

### Risk: Memory graph becomes noisy

Cause:

- Creating nodes for trivial messages.

Prevention:

- Extract only important nodes.
- Keep raw archive separate.
- Use importance scores.

### Risk: Existing build breaks

Cause:

- Replacing session flow too quickly.

Prevention:

- Add new APIs first.
- Keep old APIs during transition.
- Migrate frontend after backend is stable.
- Preserve existing tool and HIL contracts.

### Risk: Privacy or unwanted retention

Cause:

- Storing everything by default.

Prevention:

- Local-only storage.
- Add archive/delete controls.
- Add memory sensitivity metadata.
- Keep explicit wipe/export controls.

---

## 19. Final Phase 2 Target

After Phase 2, Jarvis should be able to:

- Remember every conversation locally.
- Reopen any chat.
- Continue unfinished work after refresh.
- Remember previous chats in new chats.
- Keep a compact summary of each conversation.
- Build an Obsidian-like memory graph.
- Retrieve only the relevant memories.
- Preserve current tool and agent behavior.
- Stay fast by moving heavy memory work into the background.

The guiding principle is:

```text
Jarvis should store all experience, summarize meaningful experience,
connect related experience, and recall only what matters now.
```

This is the correct direction for Jarvis because it matches the current architecture: one central graph brain, specialist agents for deeper reasoning, and memory as the continuity layer that lets the whole system feel like one persistent personal assistant.

