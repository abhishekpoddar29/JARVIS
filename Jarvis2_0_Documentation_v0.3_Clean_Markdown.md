# JARVIS 2.0

*Persistent Stateful AI Personal Assistant*

---

**Version:** 0.3 | **Date:** 2026-06-15 | **Status:** Architecture / Build Plan

**Author:** Abhi | Single-user personal assistant — no multi-user support

---

## 1. Executive Summary

Jarvis 2.0 is a persistent, stateful AI personal assistant built for a single user. It is not a general-purpose chatbot — it is a personal AI presence that remembers context intelligently, takes real actions, and operates through a distinctive voice-orb interface.

> **Vision:** A single agent that lives on your machine, knows your world, handles your tasks, and speaks with you — accessible via text, image, or voice through a cinematic interface.

### Core Differentiators

| Capability | Standard AI Assistants | Jarvis 2.0 |
|------------|------------------------|------------|
| Session Model | Stateless — each chat is fresh | Stateful — remembers within session + distilled facts long-term |
| Interface | Plain text chat | Glowing orb with animated states: Speaking, Listening, Processing, Sleeping |
| Input Types | Text only | Text, image, and voice (push-to-talk + real-time) |
| Memory | No persistence | Daily full recall + end-of-day distillation into semantic memory |
| Action | Answers only | Writes & runs code, searches, scrapes, reads calendar/email, self-modifies |
| Personalization | Generic | Learns preferences, communication style, evolves over time |

### Out of Scope (by design)

- Multi-user support — Jarvis is built exclusively for one person
- Phone / Twilio integration
- Home automation
- Mobile app — web interface only

---

## 2. Interface & User Experience

### 2.1 The Jarvis Orb — Central UI Element

The dominant visual element of the interface is a glowing, animated orb at the screen center. The orb is not decorative — it is the real-time state indicator for the entire assistant. Its appearance changes dynamically based on what Jarvis is doing.

| Orb State | Visual Behaviour | Trigger |
|-----------|------------------|---------|
| Sleeping | Dim, slow pulse — dark blue glow, minimal movement | No interaction for 60+ seconds |
| Wake Up | Brightening animation — expands, shifts from dark to vivid blue, rings ripple outward | New message received or wake-word detected |
| Listening | Bright teal/cyan glow — concentric rings pulse with audio input amplitude in real time | Voice input active (push-to-talk or real-time) |
| Processing | Spinning/orbiting particle rings — deep blue with rotating inner core | Agent thinking, tool execution in progress |
| Speaking | Warm white/blue glow — orb surface ripples in sync with TTS audio waveform | Jarvis delivering a spoken response |
| Idle (Active) | Soft slow breathe — medium brightness, gentle pulse every 3–4 seconds | Conversation open, waiting for next input |

### 2.2 Input Bar — Bottom of Screen

A persistent input bar is docked at the bottom of the screen. It is the primary interaction surface and supports multiple input types simultaneously.

| Input Type | Mechanism | Behaviour |
|------------|-----------|-----------|
| Text | Standard keyboard input in the bar | Submitted on Enter or Send button. Supports Markdown. |
| Image | Paste (Ctrl+V) or drag-and-drop onto the input bar | Image preview shown inline. Sent alongside text to agent. Gemini processes multimodally. |
| Voice (Push-to-Talk) | Hold Space bar or microphone button in the bar | Orb switches to Listening state. Web Speech API transcribes. Transcript appears in bar for review before send. |
| Voice (Fallback) | Automatic fallback when browser STT is unavailable | Routes to OpenAI Whisper API. Same user experience, higher accuracy for non-native English. |
| Voice (Real-time, Tier 3) | Hands-free — Vapi.ai streaming | Full-duplex conversation. Orb animates live. User can interrupt Jarvis mid-sentence. |

### 2.3 Full Layout

```
[ Jarvis 2.0 ] [⚙ Settings] [📤 Export]

─────────────────────────────────────────────────────────────────

✦

╭─────────╮

◈ │ ◉ ORB │ ◈ ← Animated state orb

╰─────────╯

✦

┌──────────────────────────────────────────────────────────────┐

│ Jarvis Good morning, Abhi. Ready when you are. │

│ │

│ You Plot Bitcoin prices for the last 30 days. │

│ │

│ Jarvis On it. [▶ Running code...] │

│ ┌──────────────────────────────────────────────┐ │

│ │ import requests, matplotlib... │ │

│ └──────────────────────────────────────────────┘ │

│ ✓ Done. [Chart Image] Bitcoin up 12% this month. │

└──────────────────────────────────────────────────────────────┘

─────────────────────────────────────────────────────────────────

[📎 Image] [ Type a message or paste an image... ] [🎤] [▶]
```

---

## 3. Functional Requirements

### 3.1 MVP Scope Boundary

The MVP is intentionally narrower than the full Jarvis 2.0 vision. Its goal is to produce a reliable daily-driver assistant before adding heavier systems like code execution, long-term memory distillation, voice, calendar/email, autonomous scraping, or self-modification.

| MVP Principle | Decision |
|---------------|----------|
| Build the conversation loop first | Text chat, streaming, basic tool calls, and export must work before advanced modalities. |
| Keep tools few but real | The first tool registry should support web search and date/time utilities before expanding. |
| Make memory inspectable early | Session memory ships in MVP; long-term semantic memory ships in MVP+ after schemas and controls are stable. |
| Avoid hidden autonomy | Any action that sends, deletes, modifies, purchases, executes code, or accesses private accounts requires explicit approval. |
| Ship a simple orb first | MVP orb needs clear state changes, not final cinematic polish. |

### Tier 1 — MVP / Foundation (Weeks 1–3)

#### F1 — Text Chat Interface

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F1 |
| **Name** | Text Chat Interface |
| **Description** | User types messages and receives streamed responses in the Jarvis UI. This is the primary interaction loop for MVP. |
| **Priority** | P0 |
| **Acceptance Criteria** | - Clean chat with Jarvis / User message bubbles; - Markdown rendering including code blocks; - Typing/processing indicator; - Full session scrollback; - Enter-to-send and explicit Send button; - Error messages are visible and recoverable |

#### F2 — Session Memory & Conversation Persistence

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F2 |
| **Name** | Daily Session Memory |
| **Description** | Jarvis remembers the current day's conversation and can restore scrollback after refresh. Long-term semantic memory is designed in MVP but implemented in MVP+. |
| **Priority** | P0 |
| **Acceptance Criteria** | - Today's conversation is available to the agent as context; - Browser refresh does not erase the visible conversation; - Session records include timestamps, role, content, tool calls, and attachments metadata; - Session TTL defaults to 24 hours but is configurable; - Raw session export is available through F14 |

#### F3 — Basic Tool Registry

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F3 |
| **Name** | MCP-Compatible Tool Registry |
| **Description** | Jarvis capabilities are exposed through a structured tool registry. The MVP registry only needs a small number of tools, but the interface should support future MCP-compatible expansion. |
| **Priority** | P0 |
| **Acceptance Criteria** | - Tools define name, description, input schema, output schema, risk level, and approval requirement; - Tool parameters are validated before execution; - Tool results are normalized before being sent to the agent; - Tool failures return structured errors; - Registry loads tools at startup; - Hot reload can be deferred to MVP+ |

#### F4 — Web Search & Quick Research

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F4 |
| **Name** | Web Search Integration |
| **Description** | Jarvis searches the web for current facts, news, and data using Perplexity API, with cited responses. |
| **Priority** | P0 |
| **Acceptance Criteria** | - 'What is the current Bitcoin price?' → live data with source; - 'Latest news about GPT-5' → accurate, cited result; - Sources are cited in responses; - Failures are clearly explained; - Optional fallback to Serper + direct fetch can ship after the first reliable Perplexity integration |

#### F5 — Personality Engine

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F5 |
| **Name** | Consistent Personality & Tone |
| **Description** | Jarvis has a fixed personality: helpful, slightly witty, concise, and respectful. Tone adapts to the user's emotional context while preserving the core character. |
| **Priority** | P0 |
| **Acceptance Criteria** | - Consistent voice across sessions and tools; - Uses user's first name naturally, not robotically; - Adapts tone when user is stressed or urgent; - personality.yaml is the single source of truth; - Hot reload can be deferred to MVP+ |

#### F6 — Basic Orb State Indicator

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F6 |
| **Name** | MVP Orb State Indicator |
| **Description** | The orb gives immediate visual feedback for assistant state. MVP focuses on clear state transitions, not final Three.js/audio-reactive polish. |
| **Priority** | P0 |
| **Acceptance Criteria** | - Idle, Processing, Error, and Active states are visually distinct; - Orb state is driven by backend/WebSocket events; - No layout shift when state changes; - Advanced audio-reactive animation remains Tier 3 |

#### F7 — Conversation Export

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F7 |
| **Name** | Export Conversations |
| **Description** | User can export the current day's full conversation at any time. Available from day one. |
| **Priority** | P0 |
| **Acceptance Criteria** | - Export button visible in the header; - Export as Markdown and JSON; - Export includes timestamps, messages, tool results, and attachment references; - Filename: jarvis-export-YYYY-MM-DD.md or .json |

### Tier 1.5 — MVP+ / Reliability Expansion (Weeks 3–5)

#### F8 — Sandboxed Code Execution

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F8 |
| **Name** | Sandboxed Code Execution |
| **Description** | User describes a task → Jarvis writes code → runs it in an E2B cloud sandbox → returns output, charts, or files with a plain-English explanation. |
| **Priority** | P1 |
| **Acceptance Criteria** | - Python execution through E2B; - Capture stdout, stderr, generated images, and downloadable files; - Code shown in collapsible block with Run/Edit/Copy controls; - Retry once with debugging context; - Execution requires approval for network access, package installation, or file generation |

#### F9 — Long-Term Semantic Memory

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F9 |
| **Name** | Smart Long-Term Memory |
| **Description** | End-of-day distillation summarizes important facts, preferences, decisions, and task outcomes into ChromaDB. Raw conversation retention remains configurable. |
| **Priority** | P1 |
| **Acceptance Criteria** | - Distillation job extracts structured JSON; - Memory entries follow the schema in Section 4.5; - Explicit user memories can be pinned; - User can view, edit, delete, and export memories; - Retrieval includes confidence and source metadata; - Raw logs are deleted or archived according to configuration |

#### F10 — Image & Screenshot Input

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F10 |
| **Name** | Multimodal Visual Input |
| **Description** | User pastes or uploads an image/screenshot and Jarvis analyzes it with Gemini's multimodal capability. |
| **Priority** | P1 |
| **Acceptance Criteria** | - Paste image directly into input bar or drag-and-drop; - Image preview shown before sending; - Image and text are sent in one request; - Jarvis can describe screenshots, extract visible tables, diagnose visible errors, and assess suspicious emails; - Attachment metadata is included in exports |

#### F11 — Memory Viewer UI

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F11 |
| **Name** | Memory Viewer & Editor |
| **Description** | User can inspect and control what Jarvis knows. This is required before long-term memory becomes trusted. |
| **Priority** | P1 |
| **Acceptance Criteria** | - View memories grouped by type; - Edit memory content; - Delete memory entries; - Pin/unpin entries; - Show source date, confidence, and last-used metadata; - Manual distillation can be triggered from settings |

### Tier 2 — Should Have (Weeks 5–8)

#### F12 — Voice Input

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F12 |
| **Name** | Push-to-Talk + Whisper Fallback |
| **Description** | Hold-to-talk voice input using browser Web Speech API with automatic fallback to OpenAI Whisper API when browser STT is unsupported or confidence is too low. |
| **Priority** | P1 |
| **Acceptance Criteria** | - Hold Space bar or microphone button to record; - Orb switches to Listening state with real-time amplitude feedback; - Transcript appears in input bar before sending; - User can edit transcript; - Whisper fallback triggers on incompatibility or low confidence; - Confidence indicator is visible |

#### F13 — Calendar & Email Integration

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F13 |
| **Name** | Google Workspace Integration |
| **Description** | Read calendar, schedule events, draft emails, and send only after explicit user approval. |
| **Priority** | P1 |
| **Acceptance Criteria** | - OAuth2 connection to Google Calendar and Gmail; - Calendar read works for today's schedule; - Event creation shows preview before write; - Email draft shows full preview; - Send email only after user confirms; - OAuth tokens are encrypted at rest; - Tool calls are logged in the audit trail |

#### F14 — Deep Research Agent

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F14 |
| **Name** | Multi-Step Research & Synthesis |
| **Description** | For complex research tasks, Jarvis runs an iterative research subgraph: searching, source evaluation, cross-referencing, and structured report generation. |
| **Priority** | P1 |
| **Acceptance Criteria** | - Multiple search iterations; - Source credibility labels; - Contradictions are flagged; - Structured Markdown output with citations and confidence notes; - Save report as downloadable file on request; - Key findings summarized before the full report |

### Tier 3 — Advanced (Weeks 8–12)

#### F15 — Autonomous Web Surfing & Scraping

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F15 |
| **Name** | Web Scraping Agent (Playwright) |
| **Description** | Jarvis navigates real websites using Playwright browser automation, extracts structured data, and reports findings. |
| **Priority** | P2 |
| **Acceptance Criteria** | - Handles JavaScript-rendered pages; - Respects robots.txt and rate limits; - Runs without host filesystem access; - Reports partial results if scraping fails; - Requires approval before interacting with authenticated accounts, forms, purchases, or destructive actions |

#### F16 — Real-Time Conversational Voice

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F16 |
| **Name** | Interruptible Full-Duplex Voice |
| **Description** | Full-duplex voice conversation via Vapi.ai. Jarvis speaks, user can interrupt mid-sentence, and the orb animates with the live audio state. |
| **Priority** | P2 |
| **Acceptance Criteria** | - Barge-in support; - Natural turn-taking; - Latency shown in status bar; - Graceful fallback to push-to-talk; - Conversation transcript saved to session memory; - <500ms remains an aspirational target, validated under real network conditions |

#### F17 — Codebase Self-Modification

| Attribute | Detail |
|-----------|--------|
| **Feature ID** | F17 |
| **Name** | Controlled Self-Modification |
| **Description** | User requests a small tool or config change → Jarvis proposes a plan → creates an isolated branch → edits only allowed files → runs tests → shows diff → activates only after approval. |
| **Priority** | P3 |
| **Acceptance Criteria** | - Scope limited to new tools, tests, prompts, and config; - Protected files cannot be edited; - Clean checkpoint or branch is created before changes; - Diff is shown before activation; - Tests must pass; - Human approval required for every modification; - Rollback command is generated and visible |
---

## 4. Memory Architecture

> **Design principle:** Jarvis remembers intelligently, not exhaustively. Full recall within a day. Smart distillation at the end of the day. No memory overload.

### 4.1 Two-Layer Memory Model

| Layer | Storage | Scope | Lifetime | Purpose |
|-------|---------|-------|----------|---------|
| Session Memory | Redis (in-memory) | Today's full conversation | Current day only | Complete context for today's interactions |
| Semantic Memory | ChromaDB (vector DB) | Distilled facts & preferences | Permanent (editable) | Long-term knowledge about user — what matters |

### 4.2 End-of-Day Distillation Pipeline

At midnight (or manually triggered), a background job runs the following pipeline:

| Step | Action |
|------|--------|
| Step 1 | Load today's full Redis session log |
| Step 2 | Send to Gemini Flash with prompt: 'Extract important facts, preferences, decisions, and recurring topics. Return as structured JSON. Ignore small talk.' |
| Step 3 | Deduplicate against existing ChromaDB entries (vector similarity check) |
| Step 4 | Embed new/updated facts and upsert into ChromaDB |
| Step 5 | Archive raw session log to cold storage (or delete — configurable) |
| Step 6 | Log: 'N facts updated, M new facts added' |

### 4.3 What Gets Distilled vs. Discarded

| Category | Distilled into ChromaDB? | Example |
|----------|--------------------------|---------|
| Explicit preferences | ✅ Yes — high priority | 'I hate cilantro', 'Prefer dark mode' |
| Decisions made | ✅ Yes | 'Chose React over Vue for this project' |
| Personal facts | ✅ Yes | 'Works in fintech', 'Lives in Mumbai' |
| Topics of interest | ✅ Yes | 'Researching CRISPR', 'Building Jarvis 2.0' |
| Task completions | ✅ Yes — brief | 'Wrote Bitcoin price script (June 14)' |
| Small talk / pleasantries | ❌ No | 'Thanks!' / 'Good morning' |
| Repeated queries | ❌ No — deduped | If same fact already exists in ChromaDB |
| Raw conversation turns | ❌ No — deleted/archived | The full back-and-forth text |

### 4.4 Memory Controls

- **Pin a memory:** 'Jarvis, always remember this' → marked permanent, never overwritten by distillation
- **Delete a memory:** via Memory Viewer UI — select and delete any fact
- **View all memories:** /memory endpoint or sidebar panel
- **Manual distillation:** trigger end-of-day job at any time via settings
- **Edit a memory:** user edits always override automatic distillation
- **Export memories:** user can export semantic memory separately from raw conversations

### 4.5 Semantic Memory Entry Schema

Every long-term memory entry should be stored with explicit metadata so Jarvis can retrieve, explain, edit, and delete memories predictably.

| Field | Type | Purpose |
|-------|------|---------|
| id | string | Stable unique identifier for the memory entry. |
| type | enum | preference, personal_fact, decision, task_outcome, project_context, relationship, boundary, recurring_topic. |
| content | string | Human-readable memory statement. |
| source_date | date | Date the memory was created or inferred. |
| source_ref | string | Link/reference to source session, export, or distillation batch. |
| confidence | float | 0.0–1.0 confidence that the memory is accurate. Explicit user statements default higher than inferred facts. |
| pinned | boolean | If true, the memory is never overwritten by automatic distillation. |
| sensitivity | enum | low, personal, private, secret. Controls display, retrieval, and export behavior. |
| last_used | datetime | Last time this memory influenced a response. |
| expires_at | datetime/null | Optional expiration for temporary preferences or short-lived facts. |
| embedding_model | string | Model used to create the vector embedding. |
| updated_at | datetime | Last modification timestamp. |

### 4.6 Memory Retrieval Rules

- Retrieve only memories relevant to the current user request, not the entire profile.
- Prefer pinned and explicit memories over inferred memories.
- Do not use low-confidence memories for important decisions without phrasing uncertainty.
- Do not surface private or secret memories unless directly relevant.
- When a memory affects a major recommendation, Jarvis may briefly explain the reason: 'I suggested this because you previously said...'
- User edits and deletes always override automatic distillation.

---

## 5. Agent Architecture

### 5.1 Single Main Agent with Sub-Agents

Jarvis runs as one primary LangGraph agent that handles all real-time user interactions. When a request is complex, the main agent delegates to specialized sub-agents and tools running in the background. The user always interacts with the main agent only.

| Component | Role | Technology |
|-----------|------|------------|
| Main Agent | Receives all user input. Decides routing. Manages state. Returns all responses. | LangGraph + Gemini 2.5 Pro |
| Router Node | Fast intent classification: simple QA, code, research, calendar, voice, etc. | Gemini 2.5 Flash (fast, cheap) |
| Code Sub-Agent | Writes, validates, and executes code in sandbox | E2B + Gemini 2.5 Pro |
| Research Sub-Agent | Multi-step search, source evaluation, synthesis | Perplexity API + Gemini Pro |
| Scraper Sub-Agent | Browser automation and structured data extraction | Playwright |
| Self-Modify Sub-Agent | Reads codebase, writes new tools, runs tests, commits | Gemini Pro + Git + test runner |
| Memory Manager | Read/write all memory layers. Runs distillation job. | Redis + ChromaDB |
| Tool Registry | MCP-compatible tool discovery and execution | MCP Protocol |

### 5.2 Main Agent Graph Flow

```
User Input (text / image / voice transcript)
│
▼
┌─────────────────────┐
│ ENTRY NODE │ ← Load Redis session, query ChromaDB for relevant facts,
│ │ detect emotional tone, set personality params
└──────────┬──────────┘
│
▼
┌─────────────────────┐
│ ROUTER NODE │ ← Gemini Flash classifies intent
│ (Gemini Flash) │ simple_qa / code / research / calendar / scrape / modify
└──────────┬──────────┘
┌─────┴──────────────────────────────────┐
│ │ │ │ │
▼ ▼ ▼ ▼ ▼
[Direct [Code [Research [Calendar [Scrape/
Reply] Sub-Agent] Sub-Agent] /Email] Modify]
│ │ │ │ │
└─────┬──────────────────────────────┘────┘
│
▼
┌─────────────────────┐
│ RESPONSE FORMATTER │ ← Format output, update Redis session,
│ │ save checkpoint, check if approval needed
└──────────┬──────────┘
│
┌──────┴──────┐
│ │
[No approval [Needs approval]
│ → 'Confirm before I send that email?'
▼ ▼
[Stream [Await user
response confirmation]
to UI]
```

---

## 6. Technology Stack

### 6.1 Full Stack Overview

| Layer | Technology | Purpose |
|-------|-----------|---------|
| LLM — Primary | Google Gemini 2.5 Pro | Reasoning, coding, multimodal (text + image), tool use |
| LLM — Fast | Google Gemini 2.5 Flash | Router classification, distillation, simple queries |
| Agent Orchestration | LangGraph 0.2+ | Stateful graph, checkpointing, human-in-the-loop |
| Backend | FastAPI (Python) | Async API server, WebSocket, dependency injection |
| Frontend | React + TypeScript + Vite | Orb UI, chat interface, voice controls |
| Orb Animation | Three.js or Framer Motion | Real-time 3D orb with state-driven animations |
| Voice (Primary) | Web Speech API | Push-to-talk, browser-native, free, low-latency |
| Voice (Fallback) | OpenAI Whisper API | Fallback for unsupported browsers or low confidence |
| Voice (Real-time) | Vapi.ai (Tier 3) | Full-duplex conversational voice with barge-in |
| Session Memory | Redis 7+ | Full in-session conversation context, TTL = 24h |
| Semantic Memory | ChromaDB | Vector embeddings, distilled facts, preference retrieval |
| Checkpointing | SQLite (LangGraph) | Agent state persistence — resume after crash |
| Code Sandbox | E2B | Secure ephemeral cloud execution environment |
| Web Search | Perplexity API | Fast cited web search |
| Web Scraping | Playwright | Browser automation for JavaScript-rendered pages |
| Calendar/Email | Google Calendar + Gmail API | OAuth2 integration for workspace data |
| Deployment | Docker + VPS (Railway/Render) | Always-on self-hosted, not Hugging Face Spaces |
| Version Control | Git | Safety layer for self-modification rollback |

> **Deployment note:** Hugging Face Spaces is unsuitable for a persistent always-on assistant due to cold-start delays and sleep-on-inactivity. Use Railway, Render, or a VPS (e.g. Hetzner ~€5/month) for reliable uptime.

---

## 7. Project Folder Structure

```
jarvis2/
├── .env # API keys (gitignored)
├── .env.example # Template
├── docker-compose.yml # Local dev stack
├── Dockerfile
├── README.md
├── requirements.txt
│
├── backend/
│ ├── main.py # FastAPI app entry point
│ ├── config.py # Settings, Pydantic models
│ ├── websocket.py # WebSocket handler
│ │
│ ├── graph/ # LangGraph agent
│ │ ├── graph.py # Main graph definition
│ │ ├── nodes.py # Entry, Router, Response nodes
│ │ ├── edges.py # Conditional routing logic
│ │ ├── state.py # GraphState TypedDict
│ │ ├── checkpoint.py # SQLite checkpoint config
│ │ └── subgraphs/
│ │ ├── research.py # Deep research sub-agent
│ │ ├── code_gen.py # Code generation + E2B
│ │ ├── scraper.py # Playwright scraping sub-agent
│ │ └── self_modify.py # Self-modification sub-agent
│ │
│ ├── agents/
│ │ ├── router.py # Intent classification
│ │ ├── executor.py # Tool execution coordinator
│ │ ├── personality.py # Personality engine
│ │ └── prompts/ # System prompts (.txt)
│ │
│ ├── tools/ # MCP-compatible tools
│ │ ├── registry.py # Auto-discovery, registration
│ │ ├── base.py # BaseTool class + MCP schema
│ │ ├── code_execution.py # E2B sandbox
│ │ ├── web_search.py # Perplexity API
│ │ ├── web_scraper.py # Playwright automation
│ │ ├── calendar.py # Google Calendar
│ │ ├── email.py # Gmail
│ │ ├── weather.py # OpenWeatherMap
│ │ └── datetime_tool.py # Time/date utilities
│ │
│ ├── memory/
│ │ ├── session.py # Redis session operations
│ │ ├── semantic.py # ChromaDB vector ops
│ │ ├── distiller.py # End-of-day distillation job
│ │ ├── manager.py # Unified memory interface
│ │ └── schemas.py # Memory data models
│ │
│ ├── scheduler/
│ │ ├── jobs.py # APScheduler job definitions
│ │ └── distill_job.py # Nightly distillation trigger
│ │
│ ├── voice/
│ │ └── whisper.py # Whisper API fallback handler
│ │
│ └── utils/
│ ├── logger.py
│ ├── security.py # Input sanitization, rate limiting
│ └── exceptions.py
│
├── frontend/
│ ├── src/
│ │ ├── App.tsx
│ │ ├── components/
│ │ │ ├── Orb/
│ │ │ │ ├── JarvisOrb.tsx # Main animated orb
│ │ │ │ └── OrbStates.ts # State machine for orb
│ │ │ ├── Chat/
│ │ │ │ ├── ChatWindow.tsx
│ │ │ │ ├── MessageBubble.tsx
│ │ │ │ └── CodeBlock.tsx # Monaco editor
│ │ │ ├── Input/
│ │ │ │ ├── InputBar.tsx # Unified text+image+voice bar
│ │ │ │ ├── ImagePreview.tsx
│ │ │ │ └── VoiceButton.tsx
│ │ │ ├── Memory/
│ │ │ │ └── MemoryViewer.tsx
│ │ │ └── Layout/
│ │ │ ├── Header.tsx # Export button lives here
│ │ │ └── StatusBar.tsx # Latency, tool used, memory tier
│ │ ├── hooks/
│ │ │ ├── useWebSocket.ts
│ │ │ ├── useVoice.ts # Web Speech + Whisper fallback
│ │ │ └── useOrb.ts # Orb state management
│ │ ├── services/
│ │ │ ├── api.ts
│ │ │ └── websocket.ts
│ │ └── types/
│ │ ├── message.ts
│ │ ├── orb.ts
│ │ └── memory.ts
│
├── personality.yaml # Single source of truth for personality
│
├── scripts/
│ ├── setup.sh
│ ├── dev.sh
│ └── deploy.sh
│
└── tests/
├── unit/
├── integration/
└── e2e/ # Playwright tests
```

---

## 8. Development Phases

### Phase 1 — MVP Foundation (Weeks 1–3)

| Day | Task | Deliverable |
|-----|------|-------------|
| 1 | FastAPI scaffold + health endpoint + Docker setup | GET /api/health returns 200 |
| 2 | LangGraph hello world + Gemini connection | Basic agent responds in terminal |
| 3 | React + Vite setup + basic chat UI | Text bubbles, input bar, static layout |
| 4 | WebSocket connection — React ↔ FastAPI bidirectional | Real-time message loop working |
| 5 | Session memory + conversation persistence | History survives page refresh |
| 6 | Basic tool registry + date/time utility | First structured tool call works |
| 7 | Perplexity web search tool | Current-facts query returns cited answer |
| 8 | Personality engine + system prompt tuning | Jarvis has consistent voice |
| 9 | Export conversation — Markdown and JSON | Download/export button works |
| 10 | MVP orb state indicator | Idle/Processing/Error/Active states work |
| 11 | Markdown rendering + code block display | Responses are readable and polished |
| 12 | Tool-call logging + recoverable errors | User sees what happened when tools fail |
| 13–14 | Stabilization and daily-driver testing | MVP usable for real conversations |

### Phase 1.5 — MVP+ Reliability Expansion (Weeks 3–5)

| Week | Task | Deliverable |
|------|------|-------------|
| 3–4 | E2B sandboxed code execution | Python snippets run safely with captured output |
| 4 | ChromaDB setup + semantic memory schema | Long-term memory entries stored with metadata |
| 4 | End-of-day distillation pipeline | Facts/preferences extracted into structured JSON |
| 5 | Memory Viewer UI | User can view, edit, delete, and pin memories |
| 5 | Image/screenshot input | Paste/drop image, preview, analyze with Gemini |

### Phase 2 — Voice & Private Tools (Weeks 5–8)

| Week | Task | Deliverable |
|------|------|-------------|
| 5–6 | Push-to-talk voice + Whisper fallback | Speak → transcript → editable send |
| 6 | Orb Listening animation + waveform | Visual feedback during voice input |
| 6–7 | Google Calendar OAuth2 + read events | 'What's my day?' reads calendar |
| 7 | Gmail draft/send with approval gate | Draft → preview → confirm → send |
| 8 | Deep research sub-agent | Multi-step cited research report |

### Phase 3 — Advanced Autonomy (Weeks 8–12)

| Week | Task | Deliverable |
|------|------|-------------|
| 8–9 | Playwright web scraping sub-agent | Structured extraction from JS-rendered pages |
| 9–10 | Vapi.ai real-time conversational voice | Full-duplex voice with barge-in fallback path |
| 10–11 | Controlled self-modification: tool creation | Branch → edit → test → diff → approve |
| 11–12 | Controlled self-modification: config/personality edits | Safe personality.yaml changes with preview |
| 12 | Advanced orb polish | Three.js/audio-reactive animations |
---

## 9. Personality Configuration

The personality.yaml file is the single source of truth for Jarvis's character. It is hot-reloaded on save and can be modified by the self-modification agent on user request.

```yaml
# personality.yaml
name: 'Jarvis'
version: '2.0'

voice:
tone: 'helpful, slightly witty, concise'
formality: 'casual-professional'
humor: 'dry, occasional — never forced'
urgency_handling: 'calm and methodical when user is stressed'

behavior:
proactive: false # Jarvis speaks when spoken to. No unsolicited interruptions.
initiative: 'suggest, never nag'
correction_style: 'gentle, assumes user is right'
unknown_handling: 'admit ignorance, offer to research'
approval_gate: true # Always confirm before destructive or sending actions

memory:
recall_style: 'organic — reference past context naturally, not robotically'
preference_weight: 'high — never violate stated preferences'
distillation_time: '00:00' # Midnight daily

boundaries:
never_execute_harmful_code: true
always_confirm_destructive_actions: true
cite_sources: true
admit_tool_usage: true
```

---

## 10. Security, Permissions & Safety Model

Jarvis has access to sensitive capabilities: code execution, web browsing, private accounts, persistent memory, and eventually self-modification. Security is therefore a product requirement, not an implementation detail.

### 10.1 Tool Risk Levels

| Risk Level | Examples | Approval Requirement |
|------------|----------|----------------------|
| Low | Date/time, local formatting, harmless calculations | No approval after tool is enabled. |
| Medium | Web search, summarization, reading non-private files selected by user | Show tool activity; approval optional by setting. |
| High | Code execution, package installation, web scraping, reading calendar/email | Explicit approval before first action in a task. |
| Critical | Sending email, creating calendar events, deleting data, purchases, self-modification | Explicit approval every time; show preview/diff before execution. |

### 10.2 Approval Gates

- Jarvis never sends emails, creates calendar events, deletes files, modifies code, purchases items, or submits forms without explicit confirmation.
- Approval prompts must describe the exact action, target, and expected effect.
- Approval is scoped to one action or one clearly described batch, never open-ended permission.
- The UI should show whether Jarvis is waiting for approval, executing, or blocked.

### 10.3 Sandbox Boundaries

- Code execution runs in E2B or another isolated sandbox, not directly on the host machine.
- Sandboxes are ephemeral by default.
- Network access, package installation, and file outputs are individually controlled.
- Secrets are never injected into arbitrary generated code unless the user explicitly approves the specific secret and destination.

### 10.4 Secrets & OAuth Tokens

- API keys live in .env and are never committed.
- OAuth tokens are encrypted at rest.
- Tool logs redact secrets before display, export, or memory distillation.
- Memory distillation must not store credentials, tokens, passwords, or sensitive one-time codes.

### 10.5 Prompt-Injection Defense

- Web pages, emails, PDFs, and scraped content are treated as untrusted input.
- Retrieved content cannot override system instructions, tool policies, approval gates, or memory rules.
- Tool calls are decided by Jarvis policy, not by instructions found inside external content.
- High-risk tool calls require confirmation even if external content asks Jarvis to act automatically.

### 10.6 Audit Trail

Every tool call should produce an audit record containing timestamp, tool name, risk level, input summary, approval status, result status, and error message if any. Audit records are separate from semantic memory and should be exportable for debugging.

---

## 11. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Self-modification corrupts codebase | Medium | Critical | Isolated branch before changes. Protected paths. Diff preview. Tests must pass before activation. Human approval required. |
| Memory distillation loses important context | Low | Medium | Distillation prompt engineered carefully. User can manually pin facts. Memory viewer allows correction. |
| E2B sandbox limits / cost spike | Medium | High | Cache common environments. Rate-limit code execution per session. Alert when usage spikes. |
| Gemini API rate limits | Medium | Medium | Flash for routing/distillation (cheaper). Retry with exponential backoff. Cache repeated queries. |
| Web Speech API unsupported (Firefox) | High | Low | Automatic Whisper fallback — transparent to user. |
| Vapi.ai latency exceeds 500ms | Medium | Medium | Fallback to push-to-talk. Show latency in status bar. Adjust TTS chunk size. |
| Playwright scraping breaks on site changes | High | Low | Graceful degradation — return partial results. Log failures. User notified if scrape fails. |
| Scope creep stalls Phase 1 | High | High | MVP scope is locked to text chat, session memory, basic tools, web search, export, personality, and simple orb states. |

---

## 12. Glossary

| Term | Definition |
|------|------------|
| Main Agent | The single LangGraph agent that handles all real-time user interaction. |
| Sub-Agent | A specialized graph or function invoked by the main agent for complex tasks (research, code, scraping, self-modification). |
| Orb | The animated central UI element that visualizes Jarvis's current state in real time. |
| Session Memory | Today's full conversation stored in Redis. Cleared/archived after end-of-day distillation. |
| Semantic Memory | ChromaDB vector store of distilled facts and preferences. Permanent and searchable. |
| Distillation | The nightly background process that summarizes the day's conversation into semantic memory facts. |
| Memory Pinning | Marking a fact as permanent — immune to distillation overwriting. |
| MCP | Model Context Protocol — standard interface for connecting LLMs to tools. |
| E2B | Cloud sandbox for secure ephemeral code execution. |
| Barge-in | User speaking while Jarvis is speaking, triggering an interruption. Tier 3 feature via Vapi.ai. |
| Push-to-talk | Hold a button while speaking, release to send. Tier 2 voice input method. |
| Whisper Fallback | Automatic routing to OpenAI Whisper API when browser STT is unavailable or confidence is low. |
| Self-Modification | Agent writing new tools or editing config files within strictly scoped safety guardrails. |
| Rollback Branch | A git branch created before every self-modification, enabling instant revert. |
| LangGraph | Python framework for building stateful, cyclical LLM agent graphs with checkpointing. |

---

## 13. Document History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-06-13 | Initial architecture document | AI Assistant |
| 0.2 | 2026-06-14 | Major revision: removed Phone/HomeAuto/Multi-user. Added Orb UI, image input, Whisper fallback, smart memory model, VPS deployment, export feature. Restructured all sections. | Abhi + AI Assistant |
| 0.3 | 2026-06-15 | Tightened MVP scope, deduplicated feature ownership, added semantic memory schema, security model, approval gates, and revised development phases. | Abhi + Codex |

---

*This document is a living specification. It will evolve as the project progresses.*

