// frontend/src/hooks/useJarvisSocket.ts

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  ApprovalData,
  ChatMessage,
  CodeResult,
  MusicClientState,
  MusicCommand,
  MusicTrack,
  SystemLogEntry,
  SystemLogLevel,
  TrackingLocationPoint,
  TrackingSessionState,
  TrackingStatus,
  VoiceSocketEvent,
  VoiceSocketEventListener,
  WorkflowEvent,
} from "../types";
import {
  type OrbState,
  resolveOrbState,
} from "../components/Orb/OrbStates";
import {
  API_URL,
  jarvisFetch,
  websocketUrlWithAuth,
} from "../services/apiClient";


const WS_URL =
  websocketUrlWithAuth(
    import.meta.env.VITE_WS_URL ??
      "ws://127.0.0.1:8000/ws/chat",
  );


interface SocketMessage {
  type:
    | "status"
    | "message"
    | "stream_chunk"
    | "error"
    | "heartbeat"
    | "orb_state"
    | "approval_request"
    | "research_progress"
    | "hil_card"
    | "tracking_session"
    | "tracking_status"
    | "tracking_update"
    | "music_command"
    | "workflow_event";

  state?: string;
  orb_state?: string;
  id?: string;
  role?: string;
  content?: string;
  chunk?: string;
  created_at?: string;
  message?: string;
  code_result?: CodeResult;
  image?: string;
  action_type?:
    | "send_email"
    | "calendar_event"
    | "self_modify";
  draft?: any;
  tool_name?: string;

  session?: {
    session_id: string;
    target_name: string;
    masked_phone?: string;
    duration_minutes?: number;
    status: TrackingStatus;
    created_at?: string;
    expires_at: string;
  };
  session_id?: string;
  target_name?: string;
  status?: TrackingStatus;
  expires_at?: string;
  consented_at?: string | null;
  ended_at?: string | null;
  reason?: string;
  point?: TrackingLocationPoint;

  // Music player command payload
  command_id?: string;
  action?: MusicCommand["action"];
  visible?: boolean;
  track?: MusicTrack;
  alternatives?: MusicTrack[];
  queue?: MusicTrack[];
  favorites?: MusicTrack[];

  voice_response?: boolean;
  voice_prompt?: string;
  card?: Record<string, unknown>;
  workflow?: Partial<WorkflowEvent>;
}


interface RedisMessage {
  role: string;
  content: string;
  timestamp: string;
  tool_results?: Array<{
    tool: string;
    success: boolean;
    data?: {
      code_result?: CodeResult;
    };
  }>;
  attachment_metadata?: {
    image?: string;
  };
}


function appendUniqueMessage(
  current: ChatMessage[],
  message: ChatMessage,
): ChatMessage[] {
  if (
    current.some(
      (existing) => existing.id === message.id,
    )
  ) {
    return current;
  }

  return [...current, message];
}

function createLogEntry(
  source: string,
  message: string,
  level: SystemLogLevel = "info",
): SystemLogEntry {
  return {
    id: crypto.randomUUID(),
    timestamp: new Date().toISOString(),
    source,
    message,
    level,
  };
}

function progressWorkflowPath(message = ""): string[] {
  const value = message.trim().toLowerCase();

  // Claim-driven research subgraph.
  if (value.includes("decompos") || value.includes("researchable claims")) {
    return [
      "progress_emitter",
      "research",
      "plan_research_node",
      "claim_planner_prompt",
      "claim_ledger_state",
    ];
  }
  if (value.includes("synthesiz") || value.includes("complete report")) {
    return [
      "progress_emitter",
      "research",
      "synthesize_report_node",
      "report_prompts",
      "section_renderer",
      "citation_deduper",
      "review_state",
      "final_report_state",
      "response_dispatch",
    ];
  }
  if (value.includes("working on")) {
    if (value.includes(": execute")) {
      return [
        "progress_emitter",
        "research",
        "acquire_evidence_node",
        "research_execution_tool",
        "code",
      ];
    }
    if (value.includes(": compare")) {
      return [
        "progress_emitter",
        "research",
        "evaluate_result_node",
        "conflict_classifier",
        "strict_status",
        "claim_ledger_state",
        "decide_next_action_node",
      ];
    }
    return [
      "progress_emitter",
      "research",
      "acquire_evidence_node",
      "research_source_client",
      "web_search_tool",
      "web_scraper_tool",
    ];
  }

  // Financial analyst subgraph emits through the same progress channel.
  if (value.includes("initializing financial research")) {
    return [
      "progress_emitter",
      "financial",
      "financial_request_state",
      "initialize_research",
    ];
  }
  if (value.includes("resolving company identity")) {
    return [
      "progress_emitter",
      "financial",
      "company_identity_node",
      "generate_reply",
      "company_state",
    ];
  }
  if (value.includes("validating company identity")) {
    return [
      "progress_emitter",
      "financial",
      "identity_validation_node",
      "company_matcher",
      "identity_validation_state",
    ];
  }
  if (value.includes("historical financial statements")) {
    return [
      "progress_emitter",
      "financial",
      "historical_financials_node",
      "raw_financials_e2b",
      "sandbox_create",
      "raw_financials_state",
    ];
  }
  if (value.includes("searching and scraping current financial context")) {
    return [
      "progress_emitter",
      "financial",
      "source_research_node",
      "financial_search",
      "web_search_tool",
      "financial_scraper",
      "web_scraper_tool",
      "source_texts_state",
    ];
  }
  if (value.includes("building deterministic financial facts")) {
    return [
      "progress_emitter",
      "financial",
      "financial_fact_node",
      "financial_facts_state",
    ];
  }
  if (value.includes("reconciling financial facts")) {
    return [
      "progress_emitter",
      "financial",
      "reconciliation_node",
      "reconcile_facts",
      "reconciliation_state",
    ];
  }
  if (value.includes("reviewing accounting classifications")) {
    return [
      "progress_emitter",
      "financial",
      "accounting_node",
      "accounting_agent",
      "accounting_state",
    ];
  }
  if (value.includes("applying accounting adjustments")) {
    return [
      "progress_emitter",
      "financial",
      "apply_accounting_adjustments_node",
      "accounting_adjustments",
      "financial_facts_state",
    ];
  }
  if (value.includes("calculating deterministic financial metrics")) {
    return [
      "progress_emitter",
      "financial",
      "metric_engine_node",
      "calculate_metrics",
      "metrics_state",
    ];
  }
  if (value.includes("assessing financial health")) {
    return [
      "progress_emitter",
      "financial",
      "financial_health_node",
      "health_scoring",
      "financial_health_state",
    ];
  }
  if (value.includes("collecting market data")) {
    return [
      "progress_emitter",
      "financial",
      "market_data_node",
      "market_data_collector",
      "market_data_state",
    ];
  }
  if (value.includes("calculating valuation multiples")) {
    return [
      "progress_emitter",
      "financial",
      "valuation_node",
      "valuation_engine",
      "valuation_state",
    ];
  }
  if (value.includes("preparing final analyst report")) {
    return [
      "progress_emitter",
      "financial",
      "final_report_node",
      "financial_report_builder",
      "analyst_report_state",
      "response_dispatch",
    ];
  }

  return ["progress_emitter", "jarvis_graph"];
}

function registeredToolNode(value?: string): string | undefined {
  if (!value) return undefined;
  const key = value.trim().toLowerCase().replace(/[-\s]+/g, "_");
  const aliases: Record<string, string> = {
    datetime: "datetime_tool",
    location: "location_tool",
    weather: "weather_tool",
    news: "news_tool",
    web_search: "web_search_tool",
    system_info: "system_info_tool",
    task_memory: "task_memory_tool",
    research: "research_tool",
    code_execution: "code_execution_tool",
    calendar: "calendar_tool",
    email: "email_tool",
    web_scraper: "web_scraper_tool",
    self_modify: "self_modify_tool",
    engineering_team: "engineering_team_tool",
    financial_analyst: "financial_analyst_tool",
    location_share: "location_share_tool",
    music_player: "music_player_tool",
  };
  return aliases[key] ?? aliases[key.replace(/_tool$/, "")] ?? (key.endsWith("_tool") ? key : undefined);
}

function engineeringCardNode(card?: Record<string, unknown>): string | undefined {
  if (!card) return undefined;
  const candidates = [
    card.current_node,
    card.node,
    card.stage,
    card.owner,
    card.title,
    card.action_type,
  ];
  const value = candidates.find((item) => typeof item === "string");
  if (typeof value !== "string") return undefined;
  const key = value.toLowerCase();
  if (key.includes("product")) return "product_manager_node";
  if (key.includes("requirement") || key.includes("architect")) return "requirement_architect_node";
  if (key.includes("roadmap")) return "roadmap_node";
  if (key.includes("manifest")) return "file_manifest_node";
  if (key.includes("frontend") && key.includes("repair")) return "frontend_repair_node";
  if (key.includes("backend") && key.includes("repair")) return "backend_repair_node";
  if (key.includes("frontend")) return "frontend_engineer_node";
  if (key.includes("backend")) return "backend_engineer_node";
  if (key.includes("test") && key.includes("repair")) return "test_repair_node";
  if (key.includes("sandbox")) return "sandbox_test_node";
  if (key.includes("test")) return "test_engineer_node";
  if (key.includes("review") || key.includes("repair")) return "review_and_repair_node";
  if (key.includes("package") || key.includes("save")) return "package_and_save_node";
  return undefined;
}


function createTrackingSessionState(
  session: NonNullable<SocketMessage["session"]>,
): TrackingSessionState {
  return {
    sessionId: session.session_id,
    targetName: session.target_name,
    maskedPhone: session.masked_phone,
    durationMinutes: session.duration_minutes,
    status: session.status,
    createdAt: session.created_at,
    expiresAt: session.expires_at,
    consentedAt: null,
    endedAt: null,
    lastUpdateAt: null,
    trail: [],
    visible: false,
  };
}

function isSamePoint(
  first: TrackingLocationPoint | undefined,
  second: TrackingLocationPoint,
): boolean {
  if (!first) return false;
  return (
    first.timestamp === second.timestamp &&
    first.latitude === second.latitude &&
    first.longitude === second.longitude
  );
}


export function useJarvisSocket() {
  const ws = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef =
    useRef<ReturnType<typeof setTimeout> | null>(
      null,
    );
  const shouldReconnectRef = useRef(true);

  const [messages, setMessages] =
    useState<ChatMessage[]>([]);
  const [orbState, setOrbState] =
    useState<OrbState>("sleeping");
  const [isConnected, setIsConnected] =
    useState(false);
  const [
    connectionError,
    setConnectionError,
  ] = useState<string | null>(null);
  const [
    isLoadingSession,
    setIsLoadingSession,
  ] = useState(true);
  const [
    researchProgress,
    setResearchProgress,
  ] = useState<string | null>(null);
  const [trackingSession, setTrackingSession] =
    useState<TrackingSessionState | null>(null);
  const [musicCommand, setMusicCommand] =
    useState<MusicCommand | null>(null);
  const [workflowEvent, setWorkflowEvent] =
    useState<WorkflowEvent | null>(null);
  const [systemLogs, setSystemLogs] = useState<SystemLogEntry[]>([
    createLogEntry("CORE", "Jarvis interface initialized."),
  ]);
  const lastTrackingLogAtRef = useRef(0);
  const voiceEventListenersRef = useRef(
    new Set<VoiceSocketEventListener>(),
  );

  const appendLog = useCallback(
    (
      source: string,
      message: string,
      level: SystemLogLevel = "info",
    ) => {
      setSystemLogs((current) => [
        ...current.slice(-39),
        createLogEntry(source, message, level),
      ]);
    },
    [],
  );


  const publishWorkflow = useCallback(
    (event: Omit<WorkflowEvent, "id" | "timestamp">) => {
      setWorkflowEvent({
        ...event,
        id: crypto.randomUUID(),
        timestamp: new Date().toISOString(),
      });
    },
    [],
  );


  useEffect(() => {
    async function restoreSession() {
      const controller = new AbortController();
      const timeoutId = setTimeout(
        () => controller.abort(),
        3_000,
      );

      try {
        const response = await jarvisFetch(
          `${API_URL}/api/session`,
          { signal: controller.signal },
        );

        if (!response.ok) {
          throw new Error(
            `Session fetch failed: ${response.status}`,
          );
        }

        const data = await response.json();

        const restored: ChatMessage[] = (
          data.messages ?? []
        )
          .filter(
            (message: RedisMessage) =>
              message.role === "user" ||
              message.role === "assistant",
          )
          .map((message: RedisMessage) => {
            const codeResult =
              message.tool_results?.[0]?.data
                ?.code_result;
            const image =
              message.attachment_metadata?.image;

            return {
              id: crypto.randomUUID(),
              role: message.role as
                | "user"
                | "assistant",
              content: message.content,
              createdAt: message.timestamp,
              code_result: codeResult,
              image,
            };
          });

        if (restored.length > 0) {
          setMessages(restored);
        }
        appendLog(
          "MEMORY",
          restored.length > 0
            ? `Restored ${restored.length} session messages.`
            : "No previous session messages found.",
          "success",
        );
        publishWorkflow({
          action: "success",
          node: "session_store",
          from: "fastapi_app",
          to: "session_store",
          path: ["fastapi_app", "session_history", "session_store"],
          label: restored.length > 0 ? "Session memory restored" : "Memory ready",
        });
      } catch (error) {
        if (
          error instanceof DOMException &&
          error.name === "AbortError"
        ) {
          console.warn(
            "Session fetch timed out. Starting with empty chat.",
          );
          appendLog(
            "MEMORY",
            "Session restore timed out; using an empty session.",
            "warning",
          );
        } else {
          console.warn(
            "Could not restore session from Redis:",
            error,
          );
          appendLog(
            "MEMORY",
            "Could not restore the previous session.",
            "warning",
          );
        }
      } finally {
        clearTimeout(timeoutId);
        setIsLoadingSession(false);
      }
    }

    void restoreSession();
  }, [appendLog, publishWorkflow]);


  const connectSocket = useCallback(() => {
    const current = ws.current;

    if (
      current &&
      (
        current.readyState === WebSocket.OPEN ||
        current.readyState === WebSocket.CONNECTING
      )
    ) {
      return;
    }

    const socket = new WebSocket(WS_URL);
    ws.current = socket;

    socket.onopen = () => {
      if (ws.current !== socket) return;

      setIsConnected(true);
      setConnectionError(null);
      setOrbState("idle");
      console.log(
        "Jarvis WebSocket connected",
      );
      appendLog(
        "SOCKET",
        "Connected to FastAPI /ws/chat.",
        "success",
      );
      publishWorkflow({
        action: "boot",
        node: "jarvis_graph",
        from: "fastapi_app",
        to: "jarvis_graph",
        path: ["fastapi_app", "websocket_chat", "jarvis_graph"],
        label: "Core systems online",
      });
    };

    socket.onmessage = (event) => {
      let data: SocketMessage;

      try {
        data = JSON.parse(event.data);
      } catch (error) {
        console.error(
          "Invalid WebSocket message:",
          error,
        );
        return;
      }

      switch (data.type) {
        case "heartbeat":
          return;

        case "workflow_event": {
          const workflow = data.workflow;
          if (!workflow?.action) return;
          publishWorkflow({
            action: workflow.action,
            node: workflow.node,
            from: workflow.from,
            to: workflow.to,
            path: workflow.path,
            label: workflow.label,
            detail: workflow.detail,
          });
          return;
        }

        case "orb_state":
          if (data.orb_state) {
            setOrbState(
              resolveOrbState(data.orb_state),
            );
            const path =
              data.orb_state === "processing_code"
                ? ["route_after_gemini", "tool_node", "tool_executor", "code_execution_tool", "code"]
                : data.orb_state === "speaking"
                  ? ["finalize_node", "completed_result", "response_dispatch"]
                  : data.orb_state === "listening"
                    ? ["voice", "microphone"]
                    : data.orb_state === "error"
                      ? ["websocket_chat", "runtime", "error"]
                      : ["websocket_chat", "jarvis_graph"];
            publishWorkflow({
              action: data.orb_state === "error" ? "error" : "activate",
              node: path[path.length - 1],
              from: path[0],
              to: path[path.length - 1],
              path,
              label: `Jarvis ${data.orb_state.replace(/_/g, " ")}`,
            });
          }
          return;

        case "status":
          if (data.state) {
            setOrbState(
              resolveOrbState(data.state),
            );
            const path =
              data.state === "idle"
                ? ["response_dispatch", "websocket_chat", "idle"]
                : data.state === "error"
                  ? ["websocket_chat", "runtime", "error"]
                  : ["websocket_chat", "jarvis_graph", "processing"];
            publishWorkflow({
              action: data.state === "error" ? "error" : data.state === "idle" ? "success" : "activate",
              node: path[path.length - 1],
              from: path[0],
              to: path[path.length - 1],
              path,
              label: `System ${data.state}`,
            });
          }
          return;

        case "research_progress":
          setResearchProgress(
            data.message ?? "Researching...",
          );
          setOrbState(
            resolveOrbState("processing"),
          );
          const progressMessage = data.message ?? "Research workflow running.";
          const path = progressWorkflowPath(progressMessage);
          appendLog(
            path.includes("financial") ? "FINANCE" : "RESEARCH",
            progressMessage,
          );
          publishWorkflow({
            action: "flow",
            node: path[path.length - 1],
            from: path[0],
            to: path[path.length - 1],
            path,
            label: progressMessage,
          });
          return;

        case "message": {
          setResearchProgress(null);

          const message: ChatMessage = {
            id:
              data.id ??
              crypto.randomUUID(),
            role:
              (
                data.role as
                  | "user"
                  | "assistant"
              ) ?? "assistant",
            content: data.content ?? "",
            createdAt:
              data.created_at ??
              new Date().toISOString(),
            code_result: data.code_result,
            image: data.image,
          };

          setMessages((currentMessages) => {
            const last =
              currentMessages[
                currentMessages.length - 1
              ];

            if (
              last?.role === "assistant" &&
              last?.isStreaming
            ) {
              return [
                ...currentMessages.slice(0, -1),
                {
                  ...message,
                  isStreaming: false,
                },
              ];
            }

            return appendUniqueMessage(
              currentMessages,
              message,
            );
          });

          setOrbState("idle");
          publishWorkflow({
            action: "success",
            node: "response_dispatch",
            from: "finalize_node",
            to: "response_dispatch",
            path: ["finalize_node", "completed_result", "response_dispatch"],
            label: "Response delivered",
          });
          if (data.voice_response && message.content.trim()) {
            const event: VoiceSocketEvent = {
              type: "assistant",
              text: message.content,
            };
            for (const listener of voiceEventListenersRef.current) listener(event);
          }
          return;
        }

        case "approval_request": {
          const approvalData: ApprovalData = {
            action_type: data.action_type!,
            draft: data.draft,
            tool_name: data.tool_name!,
          };

          const message: ChatMessage = {
            id:
              data.id ??
              crypto.randomUUID(),
            role: "assistant",
            content:
              "I have prepared an action that requires your approval.",
            createdAt:
              data.created_at ??
              new Date().toISOString(),
            approval_data: approvalData,
          };

          setMessages((currentMessages) =>
            appendUniqueMessage(
              currentMessages,
              message,
            ),
          );
          setOrbState("idle");
          const approvalTool = registeredToolNode(data.tool_name);
          const approvalPath = [
            "jarvis_graph",
            "tool_node",
            "tool_executor",
            ...(approvalTool ? [approvalTool] : []),
            "tool_interrupt_gate",
            "human_approval",
          ];
          publishWorkflow({
            action: "activate",
            node: "human_approval",
            from: approvalPath[0],
            to: "human_approval",
            path: approvalPath,
            label: "Awaiting approval",
          });
          if (data.voice_response) {
            const event: VoiceSocketEvent = {
              type: "approval_required",
              text: data.voice_prompt ??
                "This action requires your approval. Say approve, reject, or cancel.",
            };
            for (const listener of voiceEventListenersRef.current) listener(event);
          }
          return;
        }

        case "stream_chunk":
          setMessages((currentMessages) => {
            const last =
              currentMessages[
                currentMessages.length - 1
              ];

            if (
              last?.role === "assistant" &&
              last?.isStreaming
            ) {
              return [
                ...currentMessages.slice(0, -1),
                {
                  ...last,
                  content:
                    last.content +
                    (data.chunk ?? ""),
                },
              ];
            }

            return [
              ...currentMessages,
              {
                id: crypto.randomUUID(),
                role: "assistant",
                content: data.chunk ?? "",
                createdAt:
                  new Date().toISOString(),
                isStreaming: true,
              },
            ];
          });
          setOrbState("speaking");
          publishWorkflow({
            action: "activate",
            node: "response_dispatch",
            from: "gemini_node",
            to: "response_dispatch",
            path: ["gemini_node", "route_after_gemini", "finalize_node", "completed_result", "response_dispatch"],
            label: "Streaming response",
          });
          return;

        case "hil_card": {
          const prompt =
            data.voice_prompt ??
            "This engineering action requires approval. Say approve, reject, or cancel.";
          const message: ChatMessage = {
            id: data.id ?? crypto.randomUUID(),
            role: "assistant",
            content: prompt,
            createdAt: data.created_at ?? new Date().toISOString(),
          };

          setMessages((currentMessages) =>
            appendUniqueMessage(currentMessages, message),
          );
          setOrbState("idle");
          const engineeringNode = engineeringCardNode(data.card);
          const engineeringPath = [
            "engineering_team_tool",
            "engineering",
            ...(engineeringNode ? [engineeringNode, "engineering"] : []),
            "human_approval",
          ];
          publishWorkflow({
            action: "activate",
            node: "human_approval",
            from: engineeringPath[0],
            to: "human_approval",
            path: engineeringPath,
            label: "Engineering approval required",
          });
          if (data.voice_response) {
            const event: VoiceSocketEvent = {
              type: "approval_required",
              text: prompt,
            };
            for (const listener of voiceEventListenersRef.current) listener(event);
          }
          return;
        }


case "tracking_session": {
  if (!data.session) return;

  const nextSession = createTrackingSessionState(
    data.session,
  );
  setTrackingSession(nextSession);
  publishWorkflow({
    action: "flow",
    node: "tracking_service",
    from: "location_share_tool",
    to: "tracking_service",
    path: ["location_share_tool", "tracking", "consent_gate", "tracking_service", "tracking_store"],
    label: "Location workflow started",
  });
  appendLog(
    "TRACKING",
    `Location request created for ${nextSession.targetName}.`,
    "success",
  );
  return;
}

case "tracking_status": {
  if (!data.session_id || !data.expires_at) return;

  setTrackingSession((current) => {
    const sameSession =
      current?.sessionId === data.session_id;
    const status = data.status ?? current?.status ?? "PENDING";
    const consentGranted =
      data.reason === "CONSENT_GRANTED" ||
      Boolean(data.consented_at) ||
      status === "ACTIVE";

    return {
      sessionId: data.session_id!,
      targetName:
        data.target_name ??
        (sameSession ? current?.targetName : undefined) ??
        "Recipient",
      maskedPhone: sameSession
        ? current?.maskedPhone
        : undefined,
      durationMinutes: sameSession
        ? current?.durationMinutes
        : undefined,
      status,
      createdAt: sameSession
        ? current?.createdAt
        : undefined,
      expiresAt: data.expires_at!,
      consentedAt:
        data.consented_at ??
        (sameSession ? current?.consentedAt : null),
      endedAt:
        data.ended_at ??
        (sameSession ? current?.endedAt : null),
      lastUpdateAt: sameSession
        ? current?.lastUpdateAt
        : null,
      reason: data.reason,
      latestPoint: sameSession
        ? current?.latestPoint
        : undefined,
      trail: sameSession
        ? current?.trail ?? []
        : [],
      visible:
        (sameSession ? current?.visible : false) ||
        consentGranted,
    };
  });

  const statusMessage = (() => {
    switch (data.reason) {
      case "CONSENT_GRANTED":
        return `${data.target_name ?? "Recipient"} allowed location access.`;
      case "FIRST_LOCATION_RECEIVED":
        return "First live GPS position received.";
      case "RECIPIENT_DENIED":
        return "Recipient denied the location request.";
      case "RECIPIENT_STOPPED":
        return "Recipient stopped location sharing.";
      case "REQUESTER_STOPPED":
        return "Location sharing stopped from Jarvis.";
      case "SESSION_EXPIRED":
        return "Location-sharing session expired.";
      default:
        return `Tracking status changed to ${data.status ?? "PENDING"}.`;
    }
  })();

  const trackingStatusPath = [
    "tracking_service",
    "tracking_hub",
    "tracking_bridge",
    "tracking_status_state",
    ...(data.status === "ACTIVE" ? ["live_map"] : []),
  ];
  publishWorkflow({
    action: data.status === "DENIED" || data.status === "EXPIRED" ? "error" : "success",
    node: trackingStatusPath[trackingStatusPath.length - 1],
    from: trackingStatusPath[0],
    to: trackingStatusPath[trackingStatusPath.length - 1],
    path: trackingStatusPath,
    label: statusMessage,
  });
  appendLog(
    "TRACKING",
    statusMessage,
    data.status === "DENIED" || data.status === "EXPIRED"
      ? "warning"
      : data.status === "ENDED"
        ? "info"
        : "success",
  );
  return;
}

case "tracking_update": {
  if (
    !data.session_id ||
    !data.expires_at ||
    !data.point
  ) {
    return;
  }

  setTrackingSession((current) => {
    const sameSession =
      current?.sessionId === data.session_id;
    const previousTrail = sameSession
      ? current?.trail ?? []
      : [];
    const lastPoint =
      previousTrail[previousTrail.length - 1];
    const trail = isSamePoint(lastPoint, data.point!)
      ? previousTrail
      : [...previousTrail, data.point!].slice(-100);

    return {
      sessionId: data.session_id!,
      targetName:
        data.target_name ??
        (sameSession ? current?.targetName : undefined) ??
        "Recipient",
      maskedPhone: sameSession
        ? current?.maskedPhone
        : undefined,
      durationMinutes: sameSession
        ? current?.durationMinutes
        : undefined,
      status: "ACTIVE",
      createdAt: sameSession
        ? current?.createdAt
        : undefined,
      expiresAt: data.expires_at!,
      consentedAt: sameSession
        ? current?.consentedAt
        : null,
      endedAt: null,
      lastUpdateAt:
        data.point!.server_timestamp ??
        new Date().toISOString(),
      reason: "LOCATION_UPDATE",
      latestPoint: data.point!,
      trail,
      visible: true,
    };
  });

  const now = Date.now();
  if (now - lastTrackingLogAtRef.current > 5_000) {
    lastTrackingLogAtRef.current = now;
    publishWorkflow({
      action: "activate",
      node: "live_map",
      from: "tracking_hub",
      to: "live_map",
      path: ["tracking_hub", "tracking_bridge", "tracking_update_state", "gps_stream", "live_map"],
      label: "Live GPS update",
    });
    appendLog(
      "GPS",
      `Live position updated (±${Math.round(data.point.accuracy)} m).`,
      "success",
    );
  }
  return;
}

        case "music_command": {
          if (!data.action) return;

          const nextCommand: MusicCommand = {
            command_id:
              data.command_id ??
              data.id ??
              crypto.randomUUID(),
            action: data.action,
            visible: data.visible,
            track: data.track,
            alternatives: data.alternatives,
            queue: data.queue,
            favorites: data.favorites,
          };

          setMusicCommand(nextCommand);
          const musicPath =
            data.action === "favorites_updated" || data.action === "show_favorites"
              ? ["music_player_tool", "music", "music_store", "favorites_state"]
              : data.action === "queue_updated" || data.action === "show_queue"
                ? ["music_player_tool", "music", "music_store", "queue_state"]
                : data.action === "play"
                  ? [
                      "music_player_tool",
                      "music",
                      "youtube_search",
                      "music_store",
                      "music_command",
                      "music_client_state",
                      "playback_state",
                    ]
                  : ["music_player_tool", "music", "music_command", "music_client_state", "playback_state"];
          publishWorkflow({
            action: "flow",
            node: musicPath[musicPath.length - 1],
            from: musicPath[0],
            to: musicPath[musicPath.length - 1],
            path: musicPath,
            label: data.track
              ? `${data.action}: ${data.track.title}`
              : `Music ${data.action}`,
          });
          appendLog(
            "MUSIC",
            data.track
              ? `${data.action}: ${data.track.title}`
              : `Music command received: ${data.action}.`,
            "success",
          );
          return;
        }

        case "error":
          setConnectionError(
            data.message ?? "Unknown error",
          );
          setOrbState("error");
          publishWorkflow({
            action: "error",
            node: "error",
            from: "websocket_chat",
            to: "error",
            path: ["websocket_chat", "runtime", "error"],
            label: data.message ?? "Workflow error",
          });
          appendLog(
            "ERROR",
            data.message ?? "Unknown WebSocket error.",
            "error",
          );
          if (data.voice_response) {
            const event: VoiceSocketEvent = {
              type: "error",
              text: data.message ?? "Unknown voice error.",
            };
            for (const listener of voiceEventListenersRef.current) listener(event);
          }
          return;
      }
    };

    socket.onclose = () => {
      if (ws.current === socket) {
        ws.current = null;
      }

      setIsConnected(false);
      setOrbState("sleeping");
      publishWorkflow({
        action: "error",
        node: "websocket_chat",
        path: ["fastapi_app", "websocket_chat", "error"],
        label: "Backend connection closed",
      });
      appendLog(
        "SOCKET",
        "Connection closed; reconnecting shortly.",
        "warning",
      );

      if (!shouldReconnectRef.current) {
        return;
      }

      if (reconnectTimeoutRef.current) {
        clearTimeout(
          reconnectTimeoutRef.current,
        );
      }

      reconnectTimeoutRef.current =
        setTimeout(() => {
          connectSocket();
        }, 2_000);
    };

    socket.onerror = (error) => {
      setConnectionError(
        "WebSocket error — is FastAPI running?",
      );
      setOrbState("error");
      publishWorkflow({
        action: "error",
        node: "websocket_chat",
        path: ["fastapi_app", "websocket_chat", "error"],
        label: "WebSocket connection error",
      });
      console.error(
        "WebSocket error:",
        error,
      );
      appendLog(
        "SOCKET",
        "WebSocket connection error.",
        "error",
      );
    };
  }, [appendLog, publishWorkflow]);


  useEffect(() => {
    shouldReconnectRef.current = true;
    connectSocket();

    return () => {
      shouldReconnectRef.current = false;

      if (reconnectTimeoutRef.current) {
        clearTimeout(
          reconnectTimeoutRef.current,
        );
        reconnectTimeoutRef.current = null;
      }

      const socket = ws.current;
      ws.current = null;

      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, [connectSocket]);


  const sendMessage = useCallback(
    (message: string, image?: string) => {
      if (
        ws.current?.readyState !==
        WebSocket.OPEN
      ) {
        setConnectionError(
          "Not connected to Jarvis backend",
        );
        setOrbState("error");
        return;
      }

      setResearchProgress(null);
      publishWorkflow({
        action: "flow",
        node: "jarvis_graph",
        from: "websocket_chat",
        to: "jarvis_graph",
        path: ["websocket_chat", "session_store", "jarvis_graph", "memory_retrieval_node"],
        label: "Routing user request",
      });
      appendLog("CHAT", "User request sent to Jarvis.");

      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: message,
        createdAt:
          new Date().toISOString(),
        image,
      };

      setMessages((currentMessages) => [
        ...currentMessages,
        userMessage,
      ]);

      ws.current.send(
        JSON.stringify({
          message,
          image,
        }),
      );
      setOrbState("processing");
    },
    [appendLog, publishWorkflow],
  );


const sendVoiceMessage = useCallback(
  (message: string): boolean => {
    if (ws.current?.readyState !== WebSocket.OPEN) {
      appendLog("VOICE", "Cannot send voice input while disconnected.", "error");
      return false;
    }

    const transcript = message.trim();
    if (!transcript) return false;

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: transcript,
      createdAt: new Date().toISOString(),
    };
    setMessages((currentMessages) => [...currentMessages, userMessage]);
    setResearchProgress(null);
    publishWorkflow({
      action: "flow",
      node: "jarvis_graph",
      from: "voice_router",
      to: "jarvis_graph",
      path: ["voice", "transcribe", "voice_router", "websocket_chat", "jarvis_graph"],
      label: "Routing voice transcript",
    });
    appendLog("VOICE", "Voice transcript sent to Jarvis.");
    ws.current.send(
      JSON.stringify({
        message: transcript,
        source: "voice",
      }),
    );
    setOrbState("processing");
    return true;
  },
  [appendLog, publishWorkflow],
);


const subscribeVoiceEvents = useCallback(
  (listener: VoiceSocketEventListener) => {
    voiceEventListenersRef.current.add(listener);
    return () => voiceEventListenersRef.current.delete(listener);
  },
  [],
);


  const sendApproval = useCallback(
    (
      approved: boolean,
      editedCode?: string,
    ) => {
      if (
        ws.current?.readyState !==
        WebSocket.OPEN
      ) {
        setConnectionError(
          "Not connected to Jarvis backend",
        );
        setOrbState("error");
        return;
      }

      const payload: {
        type: "approval_response";
        approved: boolean;
        edited_code?: string;
      } = {
        type: "approval_response",
        approved,
      };

      if (editedCode) {
        payload.edited_code = editedCode;
      }

      ws.current.send(
        JSON.stringify(payload),
      );
      publishWorkflow({
        action: "flow",
        node: "graph_resume",
        from: "human_approval",
        to: "graph_resume",
        path: ["human_approval", "graph_resume", "jarvis_graph"],
        label: approved ? "Approval granted" : "Approval rejected",
      });
      setOrbState("processing");
    },
    [publishWorkflow],
  );


  const clearMessages = useCallback(
    (initial: ChatMessage[] = []) => {
      setMessages(initial);
    },
    [],
  );

const stopTracking = useCallback(
  (sessionId: string) => {
    if (
      ws.current?.readyState !==
      WebSocket.OPEN
    ) {
      appendLog(
        "TRACKING",
        "Cannot stop tracking while Jarvis is disconnected.",
        "error",
      );
      return;
    }

    ws.current.send(
      JSON.stringify({
        type: "tracking_stop",
        session_id: sessionId,
      }),
    );
    publishWorkflow({
      action: "activate",
      node: "tracking_stop",
      from: "websocket_chat",
      to: "tracking_service",
      path: [
        "websocket_chat",
        "tracking_stop",
        "tracking_service",
        "tracking_hub",
        "tracking_bridge",
        "tracking_status_state",
      ],
      label: "Stopping location workflow",
    });
    appendLog(
      "TRACKING",
      "Stop request sent to the tracking service.",
      "warning",
    );
  },
  [appendLog, publishWorkflow],
);


const dismissTracking = useCallback(() => {
  setTrackingSession((current) => {
    if (!current) return null;
    if (
      current.status === "ACTIVE" ||
      current.status === "PENDING"
    ) {
      return current;
    }
    return null;
  });
}, []);


  const sendMusicState = useCallback((state: MusicClientState) => {
    if (ws.current?.readyState !== WebSocket.OPEN) {
      return;
    }
    ws.current.send(
      JSON.stringify({
        type: "music_state",
        state,
      }),
    );
  }, []);


  return {
    messages,
    setMessages,
    sendMessage,
    orbState,
    isConnected,
    connectionError,
    isLoadingSession,
    clearMessages,
    sendApproval,
    researchProgress,
    trackingSession,
    systemLogs,
    stopTracking,
    dismissTracking,
    musicCommand,
    sendMusicState,
    sendVoiceMessage,
    subscribeVoiceEvents,
    workflowEvent,
  };
}
