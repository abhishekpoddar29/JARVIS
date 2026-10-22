import { useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import * as THREE from "three";

import type {
  OrbState,
  VoicePhase,
  WorkflowEvent,
  WorkflowEventAction,
} from "../../types";
import "./JarvisBrainWidget.css";

interface JarvisBrainWidgetProps {
  event: WorkflowEvent | null;
  isConnected: boolean;
  orbState: OrbState;
  voicePhase: VoicePhase;
}

type NodeKind = "hub" | "function" | "tool" | "state";

type NodeSeed = {
  id: string;
  label: string;
  kind?: NodeKind;
};

type GroupSeed = {
  id: string;
  label: string;
  color: number;
  children: NodeSeed[];
};

type NodeDefinition = NodeSeed & {
  group: string;
  color: number;
  position: [number, number, number];
  size: number;
};

type Runtime = {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  graph: THREE.Group;
  nodes: Map<string, THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>>;
  glows: Map<string, THREE.Sprite>;
  edges: Map<string, THREE.Line<THREE.BufferGeometry, THREE.LineBasicMaterial>>;
  parents: Map<string, string>;
  nodeUntil: Map<string, { until: number; action: WorkflowEventAction }>;
  edgeUntil: Map<string, number>;
  flows: Array<{
    mesh: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>;
    from: THREE.Vector3;
    to: THREE.Vector3;
    startedAt: number;
  }>;
  view: {
    autoAngle: number;
    dragging: boolean;
    fitDistance: number;
    lastFrame: number;
    lastX: number;
    lastY: number;
    pitch: number;
    pointerId: number | null;
    speed: number;
    yaw: number;
    zoom: number;
  };
};


const CYAN = 0x54e7f4;
const BLUE = 0x4da8ff;
const MINT = 0x72e5bd;
const GOLD = 0xf5bd62;
const VIOLET = 0x9a8cff;
const CORAL = 0xff7a76;
const ICE = 0xa7edf5;
const ORANGE = 0xffa35c;
const LIME = 0xa6ea73;
const MAGENTA = 0xe28cff;
const STEEL = 0x86a7b2;
const AMBER = 0xe8c46a;

const DEFAULT_ROTATION_SPEED = 0.16;
const MIN_ROTATION_SPEED = 0;
const MAX_ROTATION_SPEED = 0.64;
const ROTATION_SPEED_STEP = 0.04;
const MIN_ZOOM = 0.72;
const MAX_ZOOM = 1.75;
const ZOOM_STEP = 0.1;

const GROUPS: GroupSeed[] = [
  {
    id: "core",
    label: "Jarvis Main Graph",
    color: CYAN,
    children: [
      { id: "jarvis_graph", label: "jarvis_graph", kind: "hub" },
      { id: "jarvis_state", label: "JarvisState", kind: "state" },
      { id: "memory_retrieval_node", label: "memory_retrieval_node", kind: "function" },
      { id: "personality_node", label: "personality_node", kind: "function" },
      { id: "gemini_node", label: "gemini_node", kind: "function" },
      { id: "route_after_gemini", label: "route_after_gemini", kind: "function" },
      { id: "tool_node", label: "tool_node", kind: "function" },
      { id: "tool_interrupt_gate", label: "Tool interrupt gate", kind: "function" },
      { id: "dynamic_tool_loader", label: "Dynamic tool loader", kind: "function" },
      { id: "tool_response_node", label: "tool_response_node", kind: "function" },
      { id: "response_chain_guard", label: "Tool-chain safety guards", kind: "function" },
      { id: "finalize_node", label: "finalize_node", kind: "function" },
      { id: "graph_resume", label: "LangGraph resume", kind: "function" },
      { id: "human_approval", label: "Human approval", kind: "function" },
      { id: "response_dispatch", label: "Assistant response dispatch", kind: "hub" },
    ],
  },
  {
    id: "runtime",
    label: "FastAPI Runtime",
    color: STEEL,
    children: [
      { id: "fastapi_app", label: "FastAPI application", kind: "hub" },
      { id: "lifespan", label: "lifespan", kind: "function" },
      { id: "websocket_chat", label: "chat_socket /ws/chat", kind: "function" },
      { id: "heartbeat", label: "WebSocket heartbeat", kind: "function" },
      { id: "orb_emitter", label: "orb_emitter", kind: "function" },
      { id: "progress_emitter", label: "progress_emitter", kind: "function" },
      { id: "session_history", label: "Session history loader", kind: "function" },
      { id: "interrupt_to_ui", label: "_send_interrupt_to_ui", kind: "function" },
      { id: "completed_result", label: "_send_completed_graph_result", kind: "function" },
      { id: "music_state_sync", label: "Music client-state sync", kind: "function" },
      { id: "tracking_stop", label: "Tracking stop handler", kind: "function" },
      { id: "tracking_bridge_runtime", label: "TrackingEventBridge", kind: "function" },
      { id: "voice_approval_intent", label: "Voice approval parser", kind: "function" },
      { id: "upload_api", label: "Upload API", kind: "function" },
      { id: "export_api", label: "Export API", kind: "function" },
      { id: "memory_api", label: "Memory API", kind: "function" },
      { id: "google_oauth", label: "Google OAuth", kind: "function" },
      { id: "token_store", label: "OAuth token store", kind: "state" },
      { id: "mcp_manager", label: "MCPManager", kind: "function" },
      { id: "tool_registration", label: "Tool registration", kind: "function" },
    ],
  },
  {
    id: "memory",
    label: "Memory Services",
    color: MINT,
    children: [
      { id: "session_store", label: "Redis session_store", kind: "state" },
      { id: "semantic_memory", label: "Semantic memory", kind: "state" },
      { id: "chromadb", label: "ChromaDB", kind: "state" },
      { id: "memory_query", label: "query_memories", kind: "function" },
      { id: "memory_distiller", label: "Memory distiller", kind: "function" },
      { id: "memory_schema", label: "Memory schemas", kind: "state" },
      { id: "export_manager", label: "Export manager", kind: "function" },
      { id: "archive_store", label: "Session archive", kind: "state" },
    ],
  },
  {
    id: "gemini",
    label: "Gemini Client",
    color: VIOLET,
    children: [
      { id: "generate_reply", label: "generate_reply", kind: "function" },
      { id: "generate_reply_with_history", label: "generate_reply_with_history", kind: "function" },
      { id: "stream_reply", label: "stream_reply", kind: "function" },
      { id: "gemini_payload", label: "Gemini request payload", kind: "state" },
      { id: "gemini_http_post", label: "_post HTTP transport", kind: "function" },
      { id: "timeout_detector", label: "_is_timeout_error", kind: "function" },
      { id: "retry_backoff", label: "_backoff_delay", kind: "function" },
      { id: "extract_text", label: "_extract_text", kind: "function" },
      { id: "gemini_reply_state", label: "GeminiReply", kind: "state" },
      { id: "gemini_error_state", label: "GeminiClientError", kind: "state" },
    ],
  },
  {
    id: "tools",
    label: "Registered Tools",
    color: LIME,
    children: [
      { id: "tool_registry", label: "Tool registry", kind: "hub" },
      { id: "tool_executor", label: "Tool executor", kind: "hub" },
      { id: "datetime_tool", label: "DateTimeTool", kind: "tool" },
      { id: "location_tool", label: "LocationTool", kind: "tool" },
      { id: "weather_tool", label: "WeatherTool", kind: "tool" },
      { id: "news_tool", label: "NewsTool", kind: "tool" },
      { id: "web_search_tool", label: "WebSearchTool", kind: "tool" },
      { id: "system_info_tool", label: "SystemInfoTool", kind: "tool" },
      { id: "task_memory_tool", label: "TaskMemoryTool", kind: "tool" },
      { id: "research_tool", label: "ResearchTool", kind: "tool" },
      { id: "code_execution_tool", label: "CodeExecutionTool", kind: "tool" },
      { id: "calendar_tool", label: "CalendarTool", kind: "tool" },
      { id: "email_tool", label: "EmailTool", kind: "tool" },
      { id: "web_scraper_tool", label: "WebScraperTool", kind: "tool" },
      { id: "self_modify_tool", label: "SelfModifyTool", kind: "tool" },
      { id: "engineering_team_tool", label: "EngineeringTeamTool", kind: "tool" },
      { id: "financial_analyst_tool", label: "FinancialAnalystTool", kind: "tool" },
      { id: "location_share_tool", label: "LocationShareTool", kind: "tool" },
      { id: "music_player_tool", label: "MusicPlayerTool", kind: "tool" },
      { id: "mcp_tool_pool", label: "Dynamic MCP tools", kind: "tool" },
    ],
  },
  {
    id: "research",
    label: "Research Subgraph",
    color: BLUE,
    children: [
      { id: "plan_research_node", label: "plan_research_node", kind: "hub" },
      { id: "acquire_evidence_node", label: "acquire_evidence_node", kind: "hub" },
      { id: "evaluate_result_node", label: "evaluate_result_node", kind: "hub" },
      { id: "decide_next_action_node", label: "decide_next_action_node", kind: "hub" },
      { id: "synthesize_report_node", label: "synthesize_report_node", kind: "hub" },
      { id: "claim_ledger_state", label: "claim_ledger", kind: "state" },
      { id: "current_action_state", label: "current_action", kind: "state" },
      { id: "research_sources_state", label: "sources", kind: "state" },
      { id: "research_evidence_state", label: "evidence", kind: "state" },
      { id: "evidence_diagnostics_state", label: "evidence_diagnostics", kind: "state" },
      { id: "action_history_state", label: "action_history", kind: "state" },
      { id: "execution_results_state", label: "execution_results", kind: "state" },
      { id: "review_state", label: "ResearchReview", kind: "state" },
      { id: "final_report_state", label: "final_report", kind: "state" },
      { id: "research_source_client", label: "ResearchSourceClient", kind: "function" },
      { id: "research_execution_tool", label: "research_execution_tool", kind: "tool" },
      { id: "source_role_corrector", label: "_correct_source_role", kind: "function" },
      { id: "merge_sources", label: "_merge_sources", kind: "function" },
      { id: "source_packet", label: "_source_packet", kind: "function" },
      { id: "evidence_evaluator", label: "EVIDENCE_EVALUATOR_PROMPT", kind: "function" },
      { id: "evidence_validator", label: "_validate_evidence", kind: "function" },
      { id: "conflict_classifier", label: "CONFLICT_CLASSIFIER_PROMPT", kind: "function" },
      { id: "strict_status", label: "_strict_status", kind: "function" },
      { id: "coverage_guard", label: "_coverage", kind: "function" },
      { id: "search_budget_guard", label: "_budget_exhausted", kind: "function" },
      { id: "no_gain_guard", label: "Diminishing-return guard", kind: "function" },
      { id: "section_renderer", label: "_render_section", kind: "function" },
      { id: "citation_deduper", label: "_dedupe_citations", kind: "function" },
      { id: "claim_planner_prompt", label: "CLAIM_PLANNER_PROMPT", kind: "function" },
      { id: "next_action_prompt", label: "NEXT_ACTION_PROMPT", kind: "function" },
      { id: "report_prompts", label: "Report overview + section prompts", kind: "function" },
    ],
  },
  {
    id: "engineering",
    label: "Engineering Subgraph",
    color: CORAL,
    children: [
      { id: "product_manager_node", label: "product_manager_node", kind: "hub" },
      { id: "requirement_architect_node", label: "requirement_architect_node", kind: "hub" },
      { id: "roadmap_node", label: "roadmap_node", kind: "function" },
      { id: "file_manifest_node", label: "file_manifest_node", kind: "function" },
      { id: "frontend_engineer_node", label: "frontend_engineer_node", kind: "function" },
      { id: "backend_engineer_node", label: "backend_engineer_node", kind: "function" },
      { id: "frontend_repair_node", label: "frontend_repair_node", kind: "function" },
      { id: "backend_repair_node", label: "backend_repair_node", kind: "function" },
      { id: "test_engineer_node", label: "test_engineer_node", kind: "function" },
      { id: "test_repair_node", label: "test_repair_node", kind: "function" },
      { id: "sandbox_test_node", label: "sandbox_test_node", kind: "function" },
      { id: "review_and_repair_node", label: "review_and_repair_node", kind: "hub" },
      { id: "multi_repair_node", label: "multi_repair_node", kind: "function" },
      { id: "package_and_save_node", label: "package_and_save_node", kind: "hub" },
      { id: "engineering_budget_guard", label: "budget_guard", kind: "function" },
      { id: "engineering_trace", label: "trace_span / emit_trace", kind: "function" },
      { id: "manifest_validator", label: "Manifest validation", kind: "function" },
      { id: "project_writer", label: "write_project", kind: "function" },
      { id: "engineering_sandbox_runner", label: "run_project_in_sandbox", kind: "tool" },
      { id: "engineering_request_state", label: "request", kind: "state" },
      { id: "pm_report_state", label: "pm_report", kind: "state" },
      { id: "engineering_spec_state", label: "engineering_spec", kind: "state" },
      { id: "roadmap_state", label: "roadmap", kind: "state" },
      { id: "manifest_state", label: "manifest", kind: "state" },
      { id: "frontend_work_state", label: "frontend_work", kind: "state" },
      { id: "backend_work_state", label: "backend_work", kind: "state" },
      { id: "test_work_state", label: "test_work", kind: "state" },
      { id: "sandbox_result_state", label: "sandbox_result", kind: "state" },
      { id: "review_result_state", label: "review_result", kind: "state" },
      { id: "repair_history_state", label: "repair_history", kind: "state" },
      { id: "package_result_state", label: "package_result", kind: "state" },
      { id: "engineering_control_state", label: "control", kind: "state" },
    ],
  },
  {
    id: "financial",
    label: "Financial Analyst Subgraph",
    color: GOLD,
    children: [
      { id: "initialize_research", label: "initialize_research", kind: "hub" },
      { id: "company_identity_node", label: "company_identity_node", kind: "hub" },
      { id: "identity_validation_node", label: "identity_validation_node", kind: "function" },
      { id: "historical_financials_node", label: "historical_financials_node", kind: "function" },
      { id: "source_research_node", label: "source_research_node", kind: "function" },
      { id: "financial_fact_node", label: "financial_fact_node", kind: "function" },
      { id: "reconciliation_node", label: "reconciliation_node", kind: "function" },
      { id: "accounting_node", label: "accounting_node", kind: "function" },
      { id: "apply_accounting_adjustments_node", label: "apply_accounting_adjustments_node", kind: "function" },
      { id: "metric_engine_node", label: "metric_engine_node", kind: "hub" },
      { id: "financial_health_node", label: "financial_health_node", kind: "hub" },
      { id: "market_data_node", label: "market_data_node", kind: "function" },
      { id: "valuation_node", label: "valuation_node", kind: "hub" },
      { id: "final_report_node", label: "final_report_node", kind: "hub" },
      { id: "raw_financials_e2b", label: "_collect_raw_financials_with_e2b", kind: "tool" },
      { id: "financial_search", label: "_collect_search_results", kind: "tool" },
      { id: "financial_scraper", label: "_scrape_financial_sources", kind: "tool" },
      { id: "company_matcher", label: "Company identity matcher", kind: "function" },
      { id: "reconcile_facts", label: "reconcile_facts", kind: "function" },
      { id: "accounting_agent", label: "Accounting LLM agent", kind: "function" },
      { id: "accounting_adjustments", label: "apply_accounting_adjustments", kind: "function" },
      { id: "calculate_metrics", label: "calculate_metrics", kind: "function" },
      { id: "health_scoring", label: "Financial health scoring", kind: "function" },
      { id: "market_data_collector", label: "_collect_market_data", kind: "function" },
      { id: "valuation_engine", label: "Valuation multiple engine", kind: "function" },
      { id: "financial_report_builder", label: "_build_final_report_text", kind: "function" },
      { id: "financial_request_state", label: "request", kind: "state" },
      { id: "company_state", label: "company", kind: "state" },
      { id: "identity_validation_state", label: "identity_validation", kind: "state" },
      { id: "raw_financials_state", label: "raw_financials", kind: "state" },
      { id: "financial_sources_state", label: "sources", kind: "state" },
      { id: "source_texts_state", label: "source_texts", kind: "state" },
      { id: "financial_facts_state", label: "financial_facts", kind: "state" },
      { id: "reconciliation_state", label: "reconciliation", kind: "state" },
      { id: "accounting_state", label: "accounting", kind: "state" },
      { id: "metrics_state", label: "metrics", kind: "state" },
      { id: "missing_metrics_state", label: "missing_metrics", kind: "state" },
      { id: "financial_health_state", label: "financial_health", kind: "state" },
      { id: "market_data_state", label: "market_data", kind: "state" },
      { id: "valuation_state", label: "valuation", kind: "state" },
      { id: "analyst_report_state", label: "final_report", kind: "state" },
      { id: "financial_audit_state", label: "audit", kind: "state" },
      { id: "financial_execution_state", label: "execution", kind: "state" },
    ],
  },
  {
    id: "code",
    label: "Code + E2B Runtime",
    color: ORANGE,
    children: [
      { id: "code_agent_execute", label: "CodeAgent.execute", kind: "hub" },
      { id: "generate_code", label: "_generate_code", kind: "function" },
      { id: "extract_code", label: "_extract_code", kind: "function" },
      { id: "detect_packages", label: "_detect_packages", kind: "function" },
      { id: "sandbox_create", label: "AsyncSandbox.create", kind: "tool" },
      { id: "upload_input", label: "Sandbox file upload", kind: "function" },
      { id: "install_packages", label: "pip package install", kind: "function" },
      { id: "write_script", label: "Write script.py", kind: "function" },
      { id: "run_script", label: "Execute Python script", kind: "function" },
      { id: "stdout_capture", label: "Capture stdout", kind: "state" },
      { id: "stderr_capture", label: "Capture stderr", kind: "state" },
      { id: "artifact_scan", label: "Recursive artifact scan", kind: "function" },
      { id: "image_capture", label: "Image capture", kind: "function" },
      { id: "file_export", label: "Export generated files", kind: "function" },
      { id: "retry_generation", label: "One-shot code repair", kind: "function" },
      { id: "sandbox_kill", label: "Sandbox cleanup", kind: "function" },
      { id: "code_result_state", label: "CodeExecutionResult", kind: "state" },
      { id: "code_artifact_state", label: "CodeArtifact", kind: "state" },
    ],
  },
  {
    id: "voice",
    label: "Voice Pipeline",
    color: MAGENTA,
    children: [
      { id: "voice_api", label: "Voice API routes", kind: "function" },
      { id: "microphone", label: "Microphone", kind: "function" },
      { id: "audio_worklet", label: "AudioWorklet", kind: "function" },
      { id: "voice_capture", label: "Voice capture", kind: "function" },
      { id: "resample", label: "16 kHz resampler", kind: "function" },
      { id: "transcribe", label: "Speech transcription", kind: "function" },
      { id: "voice_router", label: "Voice-to-chat router", kind: "function" },
      { id: "approval_intent", label: "Approval intent parser", kind: "function" },
      { id: "tts", label: "Text-to-speech", kind: "function" },
      { id: "voice_event_queue", label: "Voice event queue", kind: "state" },
    ],
  },
  {
    id: "music",
    label: "Music Subsystem",
    color: AMBER,
    children: [
      { id: "music_routes", label: "Music API routes", kind: "function" },
      { id: "music_store", label: "Music store", kind: "state" },
      { id: "music_client_state", label: "Client playback state", kind: "state" },
      { id: "youtube_search", label: "YouTube track search", kind: "tool" },
      { id: "music_command", label: "music_command", kind: "state" },
      { id: "playback_state", label: "Playback state", kind: "state" },
      { id: "queue_state", label: "Music queue", kind: "state" },
      { id: "favorites_state", label: "Music favorites", kind: "state" },
    ],
  },
  {
    id: "tracking",
    label: "Tracking Runtime",
    color: ICE,
    children: [
      { id: "tracking_routes", label: "Tracking API routes", kind: "function" },
      { id: "tracking_service", label: "Tracking service", kind: "function" },
      { id: "tracking_store", label: "Tracking store", kind: "state" },
      { id: "tracking_bridge", label: "TrackingEventBridge", kind: "function" },
      { id: "tracking_hub", label: "Tracking event hub", kind: "function" },
      { id: "consent_gate", label: "Consent gate", kind: "function" },
      { id: "gps_stream", label: "Live GPS stream", kind: "function" },
      { id: "tracking_status_state", label: "Tracking status", kind: "state" },
      { id: "tracking_update_state", label: "Tracking update", kind: "state" },
      { id: "live_map", label: "Live location map", kind: "function" },
    ],
  },
  {
    id: "states",
    label: "Runtime States",
    color: 0x8fa9af,
    children: [
      { id: "sleeping", label: "Sleeping", kind: "state" },
      { id: "idle", label: "Idle", kind: "state" },
      { id: "waking", label: "Waking", kind: "state" },
      { id: "listening", label: "Listening", kind: "state" },
      { id: "processing", label: "Processing", kind: "state" },
      { id: "processing_code", label: "Processing code", kind: "state" },
      { id: "speaking", label: "Speaking", kind: "state" },
      { id: "error", label: "Error", kind: "state" },
    ],
  },
];

const TOOL_NODE_IDS = [
  "datetime_tool", "location_tool", "weather_tool", "news_tool", "web_search_tool",
  "system_info_tool", "task_memory_tool", "research_tool", "code_execution_tool",
  "calendar_tool", "email_tool", "web_scraper_tool", "self_modify_tool",
  "engineering_team_tool", "financial_analyst_tool", "location_share_tool",
  "music_player_tool", "mcp_tool_pool",
] as const;

const PIPELINE_EDGES: Array<[string, string]> = [
  // FastAPI runtime and main graph.
  ["fastapi_app", "lifespan"], ["lifespan", "session_store"], ["lifespan", "tracking_store"],
  ["lifespan", "music_store"], ["lifespan", "tool_registration"], ["tool_registration", "tool_registry"],
  ["lifespan", "mcp_manager"], ["mcp_manager", "mcp_tool_pool"], ["mcp_tool_pool", "tool_registry"],
  ["fastapi_app", "websocket_chat"], ["fastapi_app", "session_history"],
  ["websocket_chat", "heartbeat"], ["websocket_chat", "session_history"],
  ["websocket_chat", "session_store"], ["session_store", "jarvis_graph"],
  ["session_history", "session_store"], ["websocket_chat", "tracking_bridge_runtime"],
  ["websocket_chat", "idle"], ["websocket_chat", "error"],
  ["tracking_bridge_runtime", "tracking_bridge"], ["websocket_chat", "jarvis_graph"],
  ["websocket_chat", "orb_emitter"], ["websocket_chat", "progress_emitter"],
  ["orb_emitter", "states"], ["progress_emitter", "jarvis_graph"],
  ["progress_emitter", "research"], ["progress_emitter", "financial"],
  ["jarvis_graph", "jarvis_state"], ["jarvis_graph", "memory_retrieval_node"],
  ["memory_retrieval_node", "semantic_memory"], ["semantic_memory", "memory_query"],
  ["memory_query", "memory_retrieval_node"], ["memory_retrieval_node", "personality_node"],
  ["personality_node", "gemini_node"], ["gemini_node", "generate_reply_with_history"],
  ["generate_reply_with_history", "gemini_payload"], ["gemini_payload", "gemini_http_post"],
  ["gemini_http_post", "timeout_detector"], ["timeout_detector", "retry_backoff"],
  ["gemini_http_post", "extract_text"], ["extract_text", "gemini_reply_state"],
  ["gemini_reply_state", "gemini_node"], ["gemini_http_post", "gemini_error_state"],
  ["gemini_node", "route_after_gemini"], ["route_after_gemini", "tool_node"],
  ["route_after_gemini", "finalize_node"], ["tool_node", "tool_executor"],
  ["tool_executor", "tool_interrupt_gate"], ["tool_interrupt_gate", "human_approval"],
  ["human_approval", "graph_resume"], ["graph_resume", "jarvis_graph"], ["graph_resume", "tool_node"],
  ["tool_interrupt_gate", "tool_response_node"], ["tool_response_node", "response_chain_guard"],
  ["response_chain_guard", "gemini_node"], ["response_chain_guard", "finalize_node"],
  ["tool_node", "dynamic_tool_loader"], ["dynamic_tool_loader", "tool_registry"],
  ["finalize_node", "completed_result"], ["completed_result", "response_dispatch"],
  ["response_dispatch", "websocket_chat"], ["interrupt_to_ui", "human_approval"],
  ["websocket_chat", "interrupt_to_ui"], ["websocket_chat", "music_state_sync"],
  ["music_state_sync", "music_store"], ["websocket_chat", "tracking_stop"],
  ["tracking_stop", "tracking_service"], ["websocket_chat", "voice_approval_intent"],
  ["voice_approval_intent", "approval_intent"], ["approval_intent", "graph_resume"],
  ["upload_api", "code"], ["export_api", "export_manager"], ["export_manager", "archive_store"],
  ["memory_api", "semantic_memory"], ["memory_api", "memory_distiller"],
  ["google_oauth", "token_store"],

  // Tool dispatch.
  ["tool_node", "tool_registry"], ["tool_registry", "tool_executor"],
  ...TOOL_NODE_IDS.map((id): [string, string] => ["tool_executor", id]),
  ...TOOL_NODE_IDS.map((id): [string, string] => [id, "tool_interrupt_gate"]),
  ["research_tool", "research"], ["code_execution_tool", "code"],
  ["engineering_team_tool", "engineering"], ["financial_analyst_tool", "financial"],
  ["music_player_tool", "music"], ["location_share_tool", "tracking"],
  ["self_modify_tool", "dynamic_tool_loader"], ["task_memory_tool", "semantic_memory"],
  ["calendar_tool", "google_oauth"], ["email_tool", "google_oauth"],

  // Research graph and working state.
  ["research", "plan_research_node"], ["plan_research_node", "claim_planner_prompt"],
  ["claim_planner_prompt", "claim_ledger_state"], ["claim_ledger_state", "current_action_state"],
  ["current_action_state", "acquire_evidence_node"], ["acquire_evidence_node", "research_source_client"],
  ["research_source_client", "web_search_tool"], ["research_source_client", "web_scraper_tool"],
  ["web_search_tool", "web_scraper_tool"],
  ["web_search_tool", "research_sources_state"], ["web_scraper_tool", "research_sources_state"],
  ["acquire_evidence_node", "research_execution_tool"], ["research_execution_tool", "code"],
  ["research_execution_tool", "execution_results_state"], ["acquire_evidence_node", "action_history_state"],
  ["acquire_evidence_node", "evaluate_result_node"], ["research_sources_state", "source_role_corrector"],
  ["source_role_corrector", "merge_sources"], ["merge_sources", "source_packet"],
  ["source_packet", "evidence_evaluator"], ["evidence_evaluator", "evidence_validator"],
  ["evidence_validator", "research_evidence_state"], ["evidence_validator", "evidence_diagnostics_state"],
  ["evaluate_result_node", "conflict_classifier"], ["conflict_classifier", "strict_status"],
  ["research_evidence_state", "strict_status"], ["strict_status", "claim_ledger_state"],
  ["claim_ledger_state", "decide_next_action_node"],
  ["evaluate_result_node", "decide_next_action_node"], ["decide_next_action_node", "coverage_guard"],
  ["decide_next_action_node", "search_budget_guard"], ["decide_next_action_node", "no_gain_guard"],
  ["decide_next_action_node", "next_action_prompt"], ["next_action_prompt", "current_action_state"],
  ["decide_next_action_node", "acquire_evidence_node"], ["decide_next_action_node", "synthesize_report_node"],
  ["claim_ledger_state", "synthesize_report_node"], ["research_evidence_state", "synthesize_report_node"],
  ["synthesize_report_node", "report_prompts"], ["report_prompts", "section_renderer"],
  ["section_renderer", "citation_deduper"], ["citation_deduper", "review_state"],
  ["review_state", "final_report_state"], ["final_report_state", "response_dispatch"],

  // Engineering graph, parallel owners, testing and repair loops.
  ["engineering", "engineering_request_state"], ["engineering_request_state", "product_manager_node"],
  ["product_manager_node", "pm_report_state"], ["pm_report_state", "requirement_architect_node"],
  ["requirement_architect_node", "engineering_spec_state"], ["engineering_spec_state", "roadmap_node"],
  ["roadmap_node", "roadmap_state"], ["roadmap_state", "file_manifest_node"],
  ["file_manifest_node", "manifest_validator"], ["manifest_validator", "manifest_state"],
  ["manifest_state", "frontend_engineer_node"], ["manifest_state", "backend_engineer_node"],
  ["frontend_engineer_node", "frontend_work_state"], ["backend_engineer_node", "backend_work_state"],
  ["frontend_work_state", "test_engineer_node"], ["backend_work_state", "test_engineer_node"],
  ["test_engineer_node", "test_work_state"], ["test_work_state", "sandbox_test_node"],
  ["sandbox_test_node", "engineering_sandbox_runner"], ["engineering_sandbox_runner", "sandbox_result_state"],
  ["sandbox_result_state", "review_and_repair_node"], ["review_and_repair_node", "review_result_state"],
  ["review_result_state", "frontend_repair_node"], ["review_result_state", "backend_repair_node"],
  ["review_result_state", "test_repair_node"], ["review_result_state", "multi_repair_node"],
  ["review_result_state", "roadmap_node"], ["review_result_state", "file_manifest_node"],
  ["frontend_repair_node", "frontend_work_state"], ["backend_repair_node", "backend_work_state"],
  ["test_repair_node", "test_work_state"], ["multi_repair_node", "frontend_work_state"],
  ["multi_repair_node", "backend_work_state"], ["review_result_state", "repair_history_state"],
  ["review_and_repair_node", "package_and_save_node"], ["sandbox_result_state", "package_and_save_node"],
  ["package_and_save_node", "project_writer"], ["project_writer", "package_result_state"],
  ["package_result_state", "response_dispatch"], ["engineering", "human_approval"],
  ["engineering", "engineering_budget_guard"],
  ["engineering_budget_guard", "generate_reply"], ["engineering", "engineering_trace"],
  ["engineering_trace", "engineering_control_state"],

  // Financial analyst graph and deterministic data flow.
  ["financial", "financial_request_state"], ["financial_request_state", "initialize_research"],
  ["initialize_research", "financial_execution_state"], ["initialize_research", "financial_audit_state"],
  ["initialize_research", "company_identity_node"], ["company_identity_node", "generate_reply"],
  ["company_identity_node", "company_state"], ["generate_reply", "company_state"],
  ["company_state", "identity_validation_node"],
  ["identity_validation_node", "company_matcher"], ["company_matcher", "identity_validation_state"],
  ["identity_validation_state", "historical_financials_node"], ["identity_validation_state", "source_research_node"],
  ["historical_financials_node", "raw_financials_e2b"], ["raw_financials_e2b", "sandbox_create"],
  ["sandbox_create", "raw_financials_state"], ["raw_financials_e2b", "raw_financials_state"],
  ["source_research_node", "financial_search"],
  ["financial_search", "web_search_tool"], ["web_search_tool", "financial_scraper"],
  ["source_research_node", "financial_scraper"], ["financial_scraper", "web_scraper_tool"],
  ["web_scraper_tool", "source_texts_state"], ["financial_search", "financial_sources_state"],
  ["financial_scraper", "source_texts_state"], ["raw_financials_state", "financial_fact_node"],
  ["source_texts_state", "financial_fact_node"], ["financial_fact_node", "financial_facts_state"],
  ["financial_facts_state", "reconciliation_node"], ["reconciliation_node", "reconcile_facts"],
  ["reconcile_facts", "reconciliation_state"], ["reconciliation_state", "accounting_node"],
  ["accounting_node", "accounting_agent"], ["accounting_agent", "generate_reply"],
  ["accounting_agent", "accounting_state"], ["accounting_state", "apply_accounting_adjustments_node"],
  ["apply_accounting_adjustments_node", "accounting_adjustments"],
  ["accounting_adjustments", "financial_facts_state"], ["financial_facts_state", "metric_engine_node"],
  ["reconciliation_state", "metric_engine_node"], ["metric_engine_node", "calculate_metrics"],
  ["calculate_metrics", "metrics_state"], ["metric_engine_node", "missing_metrics_state"],
  ["metrics_state", "financial_health_node"], ["financial_health_node", "health_scoring"],
  ["health_scoring", "financial_health_state"], ["company_state", "market_data_node"],
  ["market_data_node", "market_data_collector"], ["market_data_collector", "market_data_state"],
  ["metrics_state", "valuation_node"], ["market_data_state", "valuation_node"],
  ["financial_health_state", "valuation_node"], ["valuation_node", "valuation_engine"],
  ["valuation_engine", "valuation_state"], ["financial_health_state", "final_report_node"],
  ["valuation_state", "final_report_node"], ["accounting_state", "final_report_node"],
  ["final_report_node", "financial_report_builder"], ["financial_report_builder", "analyst_report_state"],
  ["analyst_report_state", "response_dispatch"],

  // Code generation and E2B execution.
  ["code", "code_agent_execute"], ["code_agent_execute", "generate_code"],
  ["generate_code", "generate_reply"], ["generate_reply", "extract_code"],
  ["extract_code", "detect_packages"], ["detect_packages", "sandbox_create"],
  ["sandbox_create", "upload_input"], ["sandbox_create", "install_packages"],
  ["upload_input", "write_script"], ["install_packages", "write_script"],
  ["write_script", "run_script"], ["run_script", "stdout_capture"], ["run_script", "stderr_capture"],
  ["run_script", "artifact_scan"], ["artifact_scan", "image_capture"],
  ["artifact_scan", "file_export"], ["image_capture", "code_artifact_state"],
  ["file_export", "code_artifact_state"], ["stdout_capture", "code_result_state"],
  ["stderr_capture", "code_result_state"], ["run_script", "retry_generation"],
  ["retry_generation", "generate_code"], ["code_result_state", "sandbox_kill"],
  ["sandbox_kill", "response_dispatch"],

  // Voice, music and location workflows.
  ["voice_api", "microphone"], ["microphone", "audio_worklet"], ["audio_worklet", "voice_capture"],
  ["voice_capture", "resample"], ["resample", "transcribe"], ["transcribe", "voice_router"],
  ["voice_router", "websocket_chat"], ["response_dispatch", "voice_event_queue"],
  ["voice_event_queue", "tts"], ["human_approval", "approval_intent"],
  ["music", "music_routes"], ["music_player_tool", "youtube_search"],
  ["youtube_search", "music_store"], ["music_store", "music_command"],
  ["music_command", "music_client_state"], ["music_client_state", "playback_state"],
  ["music_store", "queue_state"], ["music_store", "favorites_state"],
  ["music_state_sync", "music_client_state"], ["music_command", "response_dispatch"],
  ["tracking", "tracking_routes"], ["location_share_tool", "consent_gate"],
  ["consent_gate", "tracking_service"], ["tracking_service", "tracking_store"],
  ["tracking_service", "tracking_hub"], ["tracking_hub", "tracking_bridge"],
  ["tracking_bridge", "tracking_status_state"], ["tracking_bridge", "tracking_update_state"],
  ["tracking_update_state", "gps_stream"], ["gps_stream", "live_map"],
  ["tracking_status_state", "live_map"],

  // Runtime state visibility.
  ["sleeping", "websocket_chat"], ["idle", "jarvis_graph"], ["waking", "voice"],
  ["listening", "microphone"], ["processing", "jarvis_graph"],
  ["processing_code", "code"], ["speaking", "response_dispatch"], ["error", "runtime"],
];

const NODE_ALIASES: Record<string, string> = {
  socket: "websocket_chat",
  websocket: "websocket_chat",
  websocket_chat: "websocket_chat",
  input: "websocket_chat",
  orchestrator: "jarvis_graph",
  graph: "jarvis_graph",
  router: "route_after_gemini",
  llm: "gemini_node",
  model: "gemini_node",
  gemini: "gemini_node",
  personality: "personality_node",
  memory_retrieval: "memory_retrieval_node",
  tool: "tool_node",
  tool_response: "tool_response_node",
  finalize: "finalize_node",
  human: "human_approval",
  response: "response_dispatch",
  final: "response_dispatch",

  research_graph: "research",
  plan: "plan_research_node",
  planner: "plan_research_node",
  claim_planner: "plan_research_node",
  claim_ledger: "claim_ledger_state",
  acquire: "acquire_evidence_node",
  search: "acquire_evidence_node",
  evidence: "evaluate_result_node",
  evaluate: "evaluate_result_node",
  decide: "decide_next_action_node",
  next_action: "decide_next_action_node",
  synthesis: "synthesize_report_node",
  synthesize: "synthesize_report_node",
  section_writer: "section_renderer",
  section_validator: "section_renderer",
  report_assembler: "synthesize_report_node",
  source_ranker: "source_role_corrector",
  scraper: "web_scraper_tool",
  passage_extractor: "source_packet",
  execution_gate: "research_execution_tool",

  engineering_graph: "engineering",
  planner_agent: "product_manager_node",
  coder_agent: "frontend_engineer_node",
  reviewer_agent: "review_and_repair_node",
  test_runner: "sandbox_test_node",
  hil_gate: "human_approval",
  file_writer: "package_and_save_node",

  financial_graph: "financial",
  financial_analyst: "financial",
  company_identity: "company_identity_node",
  identity_validation: "identity_validation_node",
  historical_financials: "historical_financials_node",
  source_research: "source_research_node",
  financial_facts: "financial_fact_node",
  reconciliation: "reconciliation_node",
  accounting: "accounting_node",
  metric_engine: "metric_engine_node",
  financial_health: "financial_health_node",
  market_data: "market_data_node",
  valuation: "valuation_node",
  analyst_report: "final_report_node",

  code_agent: "code_agent_execute",
  sandbox: "sandbox_create",
  e2b: "sandbox_create",
  music_tool: "music_player_tool",
  tracking_service_runtime: "tracking_service",
  location_share: "location_share_tool",
  voice_router_node: "voice_router",
};


const edgeKey = (from: string, to: string) => `${from}->${to}`;

function fibonacciPoint(index: number, total: number, radius: number): THREE.Vector3 {
  const y = 1 - 2 * ((index + 0.5) / total);
  const horizontal = Math.sqrt(Math.max(0, 1 - y * y));
  const angle = Math.PI * (3 - Math.sqrt(5)) * index;
  return new THREE.Vector3(
    Math.cos(angle) * horizontal * radius,
    y * radius,
    Math.sin(angle) * horizontal * radius,
  );
}

function nodeSize(kind: NodeKind, id: string): number {
  if (id === "core") return 0.17;
  if (kind === "hub") return 0.1;
  if (kind === "tool") return 0.054;
  return 0.044;
}

function buildGraphDefinition(): {
  nodes: NodeDefinition[];
  edges: Array<[string, string, number]>;
  parents: Map<string, string>;
} {
  const nodes: NodeDefinition[] = [];
  const edges: Array<[string, string, number]> = [];
  const parents = new Map<string, string>();
  const edgeSet = new Set<string>();
  const addEdge = (from: string, to: string, opacity: number) => {
    const key = [from, to].sort().join("|");
    if (from === to || edgeSet.has(key)) return;
    edgeSet.add(key);
    edges.push([from, to, opacity]);
  };

  GROUPS.forEach((group, groupIndex) => {
    const hubPosition = group.id === "core"
      ? new THREE.Vector3(0, 0, 0)
      : fibonacciPoint(groupIndex - 1, GROUPS.length - 1, 1.82);

    nodes.push({
      id: group.id,
      label: group.label,
      kind: "hub",
      group: group.id,
      color: group.color,
      position: [hubPosition.x, hubPosition.y, hubPosition.z],
      size: nodeSize("hub", group.id),
    });
    parents.set(group.id, "core");

    const normal = hubPosition.lengthSq() > 0.01
      ? hubPosition.clone().normalize()
      : new THREE.Vector3(0, 0, 1);
    let tangent = new THREE.Vector3(0, 1, 0).cross(normal);
    if (tangent.lengthSq() < 0.01) tangent = new THREE.Vector3(1, 0, 0).cross(normal);
    tangent.normalize();
    const bitangent = normal.clone().cross(tangent).normalize();
    const clusterRadius = Math.min(0.56, 0.34 + group.children.length * 0.005);

    group.children.forEach((child, childIndex) => {
      const local = fibonacciPoint(
        childIndex,
        Math.max(1, group.children.length),
        group.id === "core" ? 0.9 : clusterRadius,
      );
      const position = group.id === "core"
        ? local
        : hubPosition.clone()
            .addScaledVector(tangent, local.x)
            .addScaledVector(bitangent, local.y)
            .addScaledVector(normal, local.z * 0.78);

      const kind = child.kind ?? "function";
      nodes.push({
        ...child,
        kind,
        group: group.id,
        color: group.color,
        position: [position.x, position.y, position.z],
        size: nodeSize(kind, child.id),
      });
      parents.set(child.id, group.id);
      addEdge(group.id, child.id, child.kind === "state" ? 0.045 : 0.065);
    });
  });

  PIPELINE_EDGES.forEach(([from, to]) => addEdge(from, to, 0.16));
  return { nodes, edges, parents };
}

const GRAPH = buildGraphDefinition();
const NODE_IDS = new Set(GRAPH.nodes.map((node) => node.id));
const NODE_LABELS = new Map(GRAPH.nodes.map((node) => [node.id, node.label]));

function normalizeNode(value?: string): string | undefined {
  if (!value) return undefined;
  const key = value.trim().toLowerCase().replace(/[-\s]+/g, "_");
  if (NODE_ALIASES[key]) return NODE_ALIASES[key];
  if (NODE_IDS.has(key)) return key;
  return key.endsWith("_tool") ? "tool_executor" : undefined;
}

function glowTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const context = canvas.getContext("2d")!;
  const gradient = context.createRadialGradient(64, 64, 2, 64, 64, 62);
  gradient.addColorStop(0, "rgba(255,255,255,1)");
  gradient.addColorStop(0.22, "rgba(83,231,244,.72)");
  gradient.addColorStop(1, "rgba(83,231,244,0)");
  context.fillStyle = gradient;
  context.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(canvas);
}

function eventDuration(action: WorkflowEventAction): number {
  if (action === "error") return 2_800;
  if (action === "success") return 1_800;
  if (action === "boot") return 2_400;
  return 2_100;
}

function stateNode(orbState: OrbState): string {
  return NODE_IDS.has(orbState) ? orbState : "idle";
}

function voiceNode(phase: VoicePhase): string | undefined {
  const nodes: Record<VoicePhase, string | undefined> = {
    inactive: undefined,
    waking: "voice",
    listening: "microphone",
    transcribing: "transcribe",
    waiting: "voice_router",
    speaking: "tts",
    error: "error",
  };
  return nodes[phase];
}

export function JarvisBrainWidget({
  event,
  isConnected,
  orbState,
  voicePhase,
}: JarvisBrainWidgetProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<Runtime | null>(null);
  const [minimized, setMinimized] = useState(false);
  const [maximized, setMaximized] = useState(false);
  const [size, setSize] = useState({ width: 320, height: 242 });
  const restoreSizeRef = useRef(size);
  const [caption, setCaption] = useState("Neural systems standing by");
  const [tone, setTone] = useState<WorkflowEventAction>("reset");
  const [rendererFailed, setRendererFailed] = useState(false);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [rotationSpeed, setRotationSpeed] = useState(DEFAULT_ROTATION_SPEED);
  const [zoom, setZoom] = useState(1);
  const nodeMeshes = useMemo(() => GRAPH.nodes.length, []);

  const limits = () => ({
    minWidth: 270,
    minHeight: 205,
    maxWidth: Math.max(270, Math.min(660, window.innerWidth - 40)),
    maxHeight: Math.max(205, Math.min(540, window.innerHeight - 98)),
  });

  const clampSize = (width: number, height: number) => {
    const { minWidth, minHeight, maxWidth, maxHeight } = limits();
    return {
      width: Math.min(maxWidth, Math.max(minWidth, Math.round(width))),
      height: Math.min(maxHeight, Math.max(minHeight, Math.round(height))),
    };
  };

  const toggleMaximized = () => {
    if (minimized) setMinimized(false);
    if (maximized) {
      setSize(clampSize(restoreSizeRef.current.width, restoreSizeRef.current.height));
      setMaximized(false);
      return;
    }

    restoreSizeRef.current = size;
    const { maxWidth, maxHeight } = limits();
    setSize({ width: maxWidth, height: maxHeight });
    setMaximized(true);
  };

  const applyRotationSpeed = (next: number) => {
    const value = THREE.MathUtils.clamp(
      Math.round(next * 100) / 100,
      MIN_ROTATION_SPEED,
      MAX_ROTATION_SPEED,
    );
    setRotationSpeed(value);
    if (runtimeRef.current) runtimeRef.current.view.speed = value;
  };

  const applyZoom = (next: number) => {
    const value = THREE.MathUtils.clamp(
      Math.round(next * 100) / 100,
      MIN_ZOOM,
      MAX_ZOOM,
    );
    setZoom(value);
    const runtime = runtimeRef.current;
    if (!runtime) return;
    runtime.view.zoom = value;
    runtime.camera.position.z = runtime.view.fitDistance / value;
    runtime.camera.updateProjectionMatrix();
  };

  const resetGraphView = () => {
    applyRotationSpeed(DEFAULT_ROTATION_SPEED);
    applyZoom(1);
    const runtime = runtimeRef.current;
    if (!runtime) return;
    runtime.view.autoAngle = 0;
    runtime.view.pitch = -0.08;
    runtime.view.yaw = 0;
  };

  const handleMiddleMouseDown = (event: ReactMouseEvent<HTMLElement>) => {
    if (event.button === 1) event.preventDefault();
  };

  const handleMiddleMouseClick = (event: ReactMouseEvent<HTMLElement>) => {
    if (event.button !== 1) return;
    event.preventDefault();
    setMinimized((value) => !value);
  };

  const beginResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (minimized) return;

    const startX = event.clientX;
    const startY = event.clientY;
    const startSize = size;
    setMaximized(false);

    const onMove = (moveEvent: PointerEvent) => {
      const next = clampSize(
        startSize.width + (moveEvent.clientX - startX),
        startSize.height + (startY - moveEvent.clientY),
      );
      restoreSizeRef.current = next;
      setSize(next);
    };
    const stop = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      document.body.classList.remove("jarvis-brain-resizing");
    };

    document.body.classList.add("jarvis-brain-resizing");
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", stop, { once: true });
    window.addEventListener("pointercancel", stop, { once: true });
  };

  useEffect(() => {
    const onWindowResize = () => {
      if (maximized) {
        const { maxWidth, maxHeight } = limits();
        setSize({ width: maxWidth, height: maxHeight });
      } else {
        setSize((current) => clampSize(current.width, current.height));
      }
    };
    window.addEventListener("resize", onWindowResize);
    return () => window.removeEventListener("resize", onWindowResize);
  }, [maximized]);

  const widgetStyle: CSSProperties | undefined = minimized
    ? undefined
    : { width: size.width, height: size.height };

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || minimized) return;

    let frame = 0;
    let resizeObserver: ResizeObserver | null = null;
    setRendererFailed(false);
    mount.replaceChildren();

    try {
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(43, 1, 0.1, 100);
      camera.position.set(0, 0.08, 7.35);

      const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setClearColor(0x000000, 0);
      mount.appendChild(renderer.domElement);

      const graph = new THREE.Group();
      graph.rotation.x = -0.08;
      scene.add(graph);

      const nodes = new Map<string, THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>>();
      const glows = new Map<string, THREE.Sprite>();
      const edges = new Map<string, THREE.Line<THREE.BufferGeometry, THREE.LineBasicMaterial>>();
      const texture = glowTexture();
      const positions = new Map(
        GRAPH.nodes.map((node) => [node.id, new THREE.Vector3(...node.position)]),
      );

      for (const node of GRAPH.nodes) {
        const geometry = new THREE.SphereGeometry(node.size, node.kind === "hub" ? 18 : 12, node.kind === "hub" ? 18 : 12);
        const material = new THREE.MeshBasicMaterial({
          color: node.color,
          transparent: true,
          opacity: node.kind === "hub" ? 0.88 : 0.58,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.copy(positions.get(node.id)!);
        mesh.userData.baseColor = new THREE.Color(node.color);
        mesh.userData.label = node.label;
        mesh.userData.kind = node.kind;
        graph.add(mesh);
        nodes.set(node.id, mesh);

        const glow = new THREE.Sprite(new THREE.SpriteMaterial({
          map: texture,
          color: node.color,
          transparent: true,
          opacity: node.kind === "hub" ? 0.12 : 0.025,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        }));
        glow.position.copy(mesh.position);
        const scale = node.kind === "hub" ? node.size * 5.5 : node.size * 4.2;
        glow.scale.set(scale, scale, scale);
        glow.userData.baseScale = scale;
        graph.add(glow);
        glows.set(node.id, glow);
      }

      for (const [from, to, opacity] of GRAPH.edges) {
        const fromPosition = positions.get(from);
        const toPosition = positions.get(to);
        if (!fromPosition || !toPosition) continue;
        const line = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([fromPosition, toPosition]),
          new THREE.LineBasicMaterial({
            color: 0x72dbe7,
            transparent: true,
            opacity,
            depthWrite: false,
          }),
        );
        line.userData.baseOpacity = opacity;
        graph.add(line);
        edges.set(edgeKey(from, to), line);
        edges.set(edgeKey(to, from), line);
      }

      const runtime: Runtime = {
        scene,
        camera,
        renderer,
        graph,
        nodes,
        glows,
        edges,
        parents: GRAPH.parents,
        nodeUntil: new Map(),
        edgeUntil: new Map(),
        flows: [],
        view: {
          autoAngle: 0,
          dragging: false,
          fitDistance: 7.35,
          lastFrame: performance.now(),
          lastX: 0,
          lastY: 0,
          pitch: -0.08,
          pointerId: null,
          speed: rotationSpeed,
          yaw: 0,
          zoom,
        },
      };
      runtimeRef.current = runtime;

      const resize = () => {
        const width = Math.max(1, mount.clientWidth);
        const height = Math.max(1, mount.clientHeight);
        const aspect = width / height;
        renderer.setSize(width, height, false);
        camera.aspect = aspect;
        runtime.view.fitDistance = aspect < 1 ? 8.25 : aspect > 1.8 ? 6.9 : 7.35;
        camera.position.z = runtime.view.fitDistance / runtime.view.zoom;
        camera.updateProjectionMatrix();
      };
      resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(mount);
      resize();

      const raycaster = new THREE.Raycaster();
      const pointer = new THREE.Vector2();
      const canvas = renderer.domElement;

      const stopDragging = (pointerEvent?: PointerEvent) => {
        if (!runtime.view.dragging) return;
        if (pointerEvent && runtime.view.pointerId !== pointerEvent.pointerId) return;
        if (pointerEvent && canvas.hasPointerCapture(pointerEvent.pointerId)) {
          canvas.releasePointerCapture(pointerEvent.pointerId);
        }
        runtime.view.dragging = false;
        runtime.view.pointerId = null;
        canvas.style.cursor = "grab";
      };

      const onPointerDown = (pointerEvent: PointerEvent) => {
        if (pointerEvent.button !== 0) return;
        pointerEvent.preventDefault();
        runtime.view.dragging = true;
        runtime.view.pointerId = pointerEvent.pointerId;
        runtime.view.lastX = pointerEvent.clientX;
        runtime.view.lastY = pointerEvent.clientY;
        canvas.setPointerCapture(pointerEvent.pointerId);
        canvas.style.cursor = "grabbing";
        setHoveredNode(null);
      };

      const onPointerMove = (pointerEvent: PointerEvent) => {
        if (runtime.view.dragging && runtime.view.pointerId === pointerEvent.pointerId) {
          const deltaX = pointerEvent.clientX - runtime.view.lastX;
          const deltaY = pointerEvent.clientY - runtime.view.lastY;
          runtime.view.lastX = pointerEvent.clientX;
          runtime.view.lastY = pointerEvent.clientY;
          runtime.view.yaw += deltaX * 0.008;
          runtime.view.pitch = THREE.MathUtils.clamp(
            runtime.view.pitch + deltaY * 0.008,
            -1.15,
            1.15,
          );
          return;
        }

        const rect = canvas.getBoundingClientRect();
        pointer.x = ((pointerEvent.clientX - rect.left) / rect.width) * 2 - 1;
        pointer.y = -((pointerEvent.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(pointer, camera);
        const hit = raycaster.intersectObjects(Array.from(nodes.values()), false)[0];
        const label = hit ? String(hit.object.userData.label ?? "") : null;
        canvas.style.cursor = label ? "crosshair" : "grab";
        setHoveredNode((current: string | null) => current === label ? current : label);
      };

      const onWheel = (wheelEvent: WheelEvent) => {
        wheelEvent.preventDefault();
        const step = wheelEvent.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
        const value = THREE.MathUtils.clamp(
          Math.round((runtime.view.zoom + step) * 100) / 100,
          MIN_ZOOM,
          MAX_ZOOM,
        );
        runtime.view.zoom = value;
        camera.position.z = runtime.view.fitDistance / value;
        camera.updateProjectionMatrix();
        setZoom(value);
      };

      const onPointerLeave = () => {
        if (!runtime.view.dragging) canvas.style.cursor = "grab";
        setHoveredNode(null);
      };

      canvas.style.cursor = "grab";
      canvas.addEventListener("pointerdown", onPointerDown);
      canvas.addEventListener("pointermove", onPointerMove);
      canvas.addEventListener("pointerup", stopDragging);
      canvas.addEventListener("pointercancel", stopDragging);
      canvas.addEventListener("pointerleave", onPointerLeave);
      canvas.addEventListener("wheel", onWheel, { passive: false });

      const activeColor = new THREE.Color(0xeaffff);
      const successColor = new THREE.Color(0x76f2c2);
      const errorColor = new THREE.Color(0xff5f69);
      const lines = new Set(edges.values());

      const animate = (now: number) => {
        frame = requestAnimationFrame(animate);
        const delta = Math.min(0.05, Math.max(0, (now - runtime.view.lastFrame) / 1_000));
        runtime.view.lastFrame = now;
        if (!runtime.view.dragging) runtime.view.autoAngle += runtime.view.speed * delta;
        graph.rotation.y = runtime.view.yaw + runtime.view.autoAngle;
        graph.rotation.x = runtime.view.pitch;
        for (const [id, mesh] of nodes) {
          const state = runtime.nodeUntil.get(id);
          const active = Boolean(state && state.until > now);
          const material = mesh.material;
          const glow = glows.get(id)!;
          const glowMaterial = glow.material as THREE.SpriteMaterial;
          const kind = String(mesh.userData.kind) as NodeKind;
          const pulse = active ? 1 + 0.34 * (0.5 + 0.5 * Math.sin(now * 0.012)) : 1;
          mesh.scale.setScalar(pulse);
          material.color.copy(
            state?.action === "error" ? errorColor :
            state?.action === "success" ? successColor :
            active ? activeColor : mesh.userData.baseColor,
          );
          material.opacity = active ? 1 : kind === "hub" ? 0.88 : 0.58;
          glowMaterial.opacity = active ? 0.72 : kind === "hub" ? 0.12 : 0.025;
          const baseScale = Number(glow.userData.baseScale ?? 0.35);
          glow.scale.setScalar(baseScale * (active ? pulse * 1.35 : 1));
        }

        const activeLines = new Set<THREE.Line<THREE.BufferGeometry, THREE.LineBasicMaterial>>();
        for (const [key, until] of runtime.edgeUntil) {
          if (until > now) {
            const line = runtime.edges.get(key);
            if (line) activeLines.add(line);
          }
        }
        for (const line of lines) {
          line.material.opacity = activeLines.has(line)
            ? 0.82
            : Number(line.userData.baseOpacity ?? 0.06);
        }

        runtime.flows = runtime.flows.filter((flow) => {
          const progress = (now - flow.startedAt) / 1_150;
          if (progress >= 1) {
            graph.remove(flow.mesh);
            flow.mesh.geometry.dispose();
            flow.mesh.material.dispose();
            return false;
          }
          const eased = 1 - Math.pow(1 - progress, 3);
          flow.mesh.position.lerpVectors(flow.from, flow.to, eased);
          flow.mesh.scale.setScalar(0.75 + 0.5 * Math.sin(progress * Math.PI));
          return true;
        });

        renderer.render(scene, camera);
      };
      frame = requestAnimationFrame(animate);

      return () => {
        cancelAnimationFrame(frame);
        resizeObserver?.disconnect();
        canvas.removeEventListener("pointerdown", onPointerDown);
        canvas.removeEventListener("pointermove", onPointerMove);
        canvas.removeEventListener("pointerup", stopDragging);
        canvas.removeEventListener("pointercancel", stopDragging);
        canvas.removeEventListener("pointerleave", onPointerLeave);
        canvas.removeEventListener("wheel", onWheel);
        runtime.flows.forEach((flow) => {
          flow.mesh.geometry.dispose();
          flow.mesh.material.dispose();
        });
        graph.traverse((object: THREE.Object3D) => {
          const drawable = object as THREE.Mesh | THREE.Line | THREE.Sprite;
          if ("geometry" in drawable && drawable.geometry) drawable.geometry.dispose();
          if ("material" in drawable && drawable.material) {
            const materials = Array.isArray(drawable.material) ? drawable.material : [drawable.material];
            materials.forEach((material: THREE.Material) => material.dispose());
          }
        });
        texture.dispose();
        renderer.dispose();
        renderer.domElement.remove();
        runtimeRef.current = null;
      };
    } catch (error) {
      console.error("[Jarvis Brain] Three.js initialization failed:", error);
      setRendererFailed(true);
    }
  }, [minimized]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime || !event) return;
    const now = performance.now();
    const duration = eventDuration(event.action);

    if (event.action === "reset") {
      runtime.nodeUntil.clear();
      runtime.edgeUntil.clear();
      setCaption(event.label ?? "Neural systems standing by");
      setTone("reset");
      return;
    }

    const activate = (id: string | undefined, action: WorkflowEventAction, until: number) => {
      if (!id || !runtime.nodes.has(id)) return;
      runtime.nodeUntil.set(id, { until, action });
      const parent = runtime.parents.get(id);
      if (parent && parent !== id && runtime.nodes.has(parent)) {
        runtime.nodeUntil.set(parent, { until: Math.min(until, now + 1_450), action: "activate" });
      }
    };

    const animateEdge = (
      from: string | undefined,
      to: string | undefined,
      startedAt: number,
      until: number,
    ) => {
      if (!from || !to || from === to) return;
      const line = runtime.edges.get(edgeKey(from, to));
      if (line) runtime.edgeUntil.set(edgeKey(from, to), until);

      const fromMesh = runtime.nodes.get(from);
      const toMesh = runtime.nodes.get(to);
      if (!fromMesh || !toMesh) return;

      const particle = new THREE.Mesh(
        new THREE.SphereGeometry(0.04, 10, 10),
        new THREE.MeshBasicMaterial({ color: 0xeaffff }),
      );
      particle.position.copy(fromMesh.position);
      runtime.graph.add(particle);
      runtime.flows.push({
        mesh: particle,
        from: fromMesh.position.clone(),
        to: toMesh.position.clone(),
        startedAt,
      });
    };

    const explicitPath = (event.path ?? [])
      .map((item) => normalizeNode(item))
      .filter((item): item is string => Boolean(item));
    const from = normalizeNode(event.from);
    const node = normalizeNode(event.node ?? event.to);
    const to = normalizeNode(event.to ?? event.node);
    const path = explicitPath.length > 0
      ? explicitPath
      : [from, node, to].filter((item, index, values): item is string =>
          Boolean(item) && item !== values[index - 1],
        );
    const until = now + duration;

    if (event.action === "boot") {
      GROUPS.forEach((group, index) => {
        runtime.nodeUntil.set(group.id, { until: until + index * 80, action: "boot" });
      });
    }

    path.forEach((id, index) => {
      const isLast = index === path.length - 1;
      activate(
        id,
        isLast ? event.action : "activate",
        isLast ? until : Math.min(until, now + 1_350 + index * 90),
      );
      if (index > 0) {
        animateEdge(
          path[index - 1],
          id,
          now + Math.min(index * 70, 420),
          until,
        );
      }
    });

    if (path.length === 0) {
      activate(node, event.action, until);
      activate(from, "activate", now + 1_100);
      activate(to, event.action, until);
      animateEdge(from, to, now, until);
    }

    const captionNode = path[path.length - 1] ?? node;
    setCaption(
      event.label ??
      (captionNode ? NODE_LABELS.get(captionNode) : undefined) ??
      "Workflow active",
    );
    setTone(event.action);
  }, [event, minimized]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const now = performance.now();
    const activate = (id: string, duration = 1_250) => {
      if (!runtime.nodes.has(id)) return;
      runtime.nodeUntil.set(id, { until: now + duration, action: id === "error" ? "error" : "activate" });
      const parent = runtime.parents.get(id);
      if (parent && runtime.nodes.has(parent)) {
        runtime.nodeUntil.set(parent, { until: now + 950, action: "activate" });
      }
    };

    const currentState = stateNode(orbState);
    activate(currentState);
    const stateTarget =
      currentState === "processing_code" ? "code" :
      currentState === "speaking" ? "response_dispatch" :
      currentState === "listening" ? "microphone" :
      currentState === "error" ? "runtime" :
      currentState === "waking" ? "voice" :
      "jarvis_graph";
    runtime.edgeUntil.set(edgeKey(currentState, stateTarget), now + 1_000);

    const activeVoiceNode = voiceNode(voicePhase);
    if (activeVoiceNode) activate(activeVoiceNode);
    if (voicePhase !== "inactive") activate("voice", 1_400);
  }, [minimized, orbState, voicePhase]);

  return (
    <section
      className={`jarvis-brain-widget ${minimized ? "is-minimized" : ""} ${maximized ? "is-maximized" : ""}`}
      style={widgetStyle}
      aria-label="Live Jarvis workflow"
      onMouseDown={handleMiddleMouseDown}
      onAuxClick={handleMiddleMouseClick}
    >
      {!minimized && (
        <button
          type="button"
          className="jarvis-brain-resize-handle"
          onPointerDown={beginResize}
          aria-label="Resize workflow widget from the top-right corner"
          title="Drag to resize"
        />
      )}

      <header className="jarvis-brain-header">
        <div>
          <span className={`jarvis-brain-signal tone-${tone}`} />
          <strong>NEURAL WORKFLOW</strong>
          {!minimized && <small>{nodeMeshes} NODES</small>}
        </div>
        <div className="jarvis-brain-controls">
          {!minimized && (
            <button
              type="button"
              onClick={toggleMaximized}
              aria-label={maximized ? "Restore workflow widget" : "Maximize workflow widget"}
              title={maximized ? "Restore" : "Maximize"}
            >
              {maximized ? "↙" : "↗"}
            </button>
          )}
          <button
            type="button"
            onClick={() => setMinimized((value: boolean) => !value)}
            aria-label={minimized ? "Expand workflow widget" : "Minimize workflow widget"}
            title={minimized ? "Expand" : "Minimize"}
          >
            {minimized ? "+" : "−"}
          </button>
        </div>
      </header>

      {!minimized && (
        <>
          <div className="jarvis-brain-canvas">
            <div className="jarvis-brain-renderer" ref={mountRef}>
              {rendererFailed && <span>Neural renderer unavailable</span>}
            </div>
            <div className="jarvis-brain-view-controls" aria-label="Graph view controls">
              <div className="jarvis-brain-control-group">
                <span>ROT</span>
                <button
                  type="button"
                  onClick={() => applyRotationSpeed(rotationSpeed - ROTATION_SPEED_STEP)}
                  disabled={rotationSpeed <= MIN_ROTATION_SPEED}
                  aria-label="Decrease graph rotation speed"
                  title="Slower rotation"
                >−</button>
                <output>{(rotationSpeed / DEFAULT_ROTATION_SPEED).toFixed(1)}×</output>
                <button
                  type="button"
                  onClick={() => applyRotationSpeed(rotationSpeed + ROTATION_SPEED_STEP)}
                  disabled={rotationSpeed >= MAX_ROTATION_SPEED}
                  aria-label="Increase graph rotation speed"
                  title="Faster rotation"
                >+</button>
              </div>
              <div className="jarvis-brain-control-group">
                <span>ZOOM</span>
                <button
                  type="button"
                  onClick={() => applyZoom(zoom - ZOOM_STEP)}
                  disabled={zoom <= MIN_ZOOM}
                  aria-label="Zoom graph out"
                  title="Zoom out"
                >−</button>
                <output>{Math.round(zoom * 100)}%</output>
                <button
                  type="button"
                  onClick={() => applyZoom(zoom + ZOOM_STEP)}
                  disabled={zoom >= MAX_ZOOM}
                  aria-label="Zoom graph in"
                  title="Zoom in"
                >+</button>
              </div>
              <button
                type="button"
                className="jarvis-brain-reset-view"
                onClick={resetGraphView}
                aria-label="Reset graph rotation, zoom, and speed"
                title="Reset graph view"
              >↺</button>
            </div>
            <div className="jarvis-brain-gesture-hint">DRAG · WHEEL · MIDDLE CLICK</div>
            {hoveredNode && <div className="jarvis-brain-tooltip">{hoveredNode}</div>}
          </div>
          <footer>
            <span className={isConnected ? "online" : "offline"}>
              {isConnected ? "LINKED" : "OFFLINE"}
            </span>
            <p title={caption}>{caption}</p>
          </footer>
        </>
      )}
    </section>
  );
}