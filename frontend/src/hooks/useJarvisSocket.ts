// frontend/src/hooks/useJarvisSocket.ts

import { useEffect, useRef, useState, useCallback } from "react";
import type { ChatMessage, CodeResult, ApprovalData } from "../types";
import { type OrbState, resolveOrbState } from "../components/Orb/OrbStates";

const WS_URL = import.meta.env.VITE_WS_URL ?? "ws://127.0.0.1:8000/ws/chat";
const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

interface SocketMessage {
  type: "status" | "message" | "stream_chunk" | "error" | "heartbeat" | "orb_state" | "approval_request" | "research_progress";
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
  action_type?: "send_email" | "calendar_event";
  draft?: any;
  tool_name?: string;
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

export function useJarvisSocket() {
  const ws = useRef<WebSocket | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [orbState, setOrbState] = useState<OrbState>("sleeping");
  const [isConnected, setIsConnected] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [isLoadingSession, setIsLoadingSession] = useState(true);
  const [researchProgress, setResearchProgress] = useState<string | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);


    // ── Hydrate session from Redis on mount ──────────────────────────────────
  useEffect(() => {
      async function restoreSession() {
        // Add a 3-second timeout so the UI never freezes forever if the backend hangs
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);
  
        try {
          const res = await fetch(`${API_URL}/api/session`, {
            signal: controller.signal
          });
          if (!res.ok) throw new Error(`Session fetch failed: ${res.status}`);
          const data = await res.json();
  
          const restored: ChatMessage[] = (data.messages ?? [])
            .filter((m: RedisMessage) => m.role === "user" || m.role === "assistant")
            .map((m: RedisMessage) => {
              const codeResult = m.tool_results?.[0]?.data?.code_result;
              const image = m.attachment_metadata?.image;
              
              return {
                id: crypto.randomUUID(),
                role: m.role as "user" | "assistant",
                content: m.content,
                createdAt: m.timestamp,
                code_result: codeResult,
                image: image,
              };
            });
  
          if (restored.length > 0) {
            setMessages(restored);
          }
        } catch (err) {
          if (err instanceof DOMException && err.name === 'AbortError') {
            console.warn("Session fetch timed out. Starting with empty chat.");
          } else {
            console.warn("Could not restore session from Redis:", err);
          }
        } finally {
          clearTimeout(timeoutId);
          setIsLoadingSession(false); // Ensure loading state is cleared no matter what
        }
      }
  
      restoreSession();
  }, []);

  // ── WebSocket connection logic ───────────────────────────────────────────
  const connectSocket = useCallback(() => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) return;

    const socket = new WebSocket(WS_URL);
    ws.current = socket;

    socket.onopen = () => {
      setIsConnected(true);
      setConnectionError(null);
      setOrbState("idle");
      console.log("Jarvis WebSocket connected");
    };

    socket.onmessage = (event) => {
      const data: SocketMessage = JSON.parse(event.data);

      switch (data.type) {
        case "heartbeat":
          break;

        case "orb_state":
          if (data.orb_state) setOrbState(resolveOrbState(data.orb_state));
          break;

        case "status":
          if (data.state) setOrbState(resolveOrbState(data.state));
          break;
        
        case "research_progress":
          setResearchProgress(data.message ?? "Researching...");
          setOrbState(resolveOrbState("processing"));
          break; // ← FIX: Added missing break!

        case "message":
          setResearchProgress(null);
          const msg: ChatMessage = {
            id: data.id ?? crypto.randomUUID(),
            role: (data.role as "user" | "assistant") ?? "assistant",
            content: data.content ?? "",
            createdAt: data.created_at ?? new Date().toISOString(),
            code_result: data.code_result,
            image: data.image,
          };
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.role === "assistant" && last?.isStreaming) {
              return [...prev.slice(0, -1), { ...msg, isStreaming: false }];
            }
            return [...prev, msg];
          });
          setOrbState("idle");
          break;

        case "approval_request": {
          const msg: ChatMessage = {
            id: data.id ?? crypto.randomUUID(),
            role: "assistant",
            content: "I have prepared an action that requires your approval.",
            createdAt: data.created_at ?? new Date().toISOString(),
            approval_data: {
              action_type: data.action_type!,
              draft: data.draft,
              tool_name: data.tool_name!
            }
          };
          setMessages((prev) => [...prev, msg]);
          setOrbState("idle");
          break;
        }

        case "stream_chunk": {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.role === "assistant" && last?.isStreaming) {
              return [
                ...prev.slice(0, -1),
                { ...last, content: last.content + (data.chunk ?? "") },
              ];
            }
            return [
              ...prev,
              {
                id: crypto.randomUUID(),
                role: "assistant" as const,
                content: data.chunk ?? "",
                createdAt: new Date().toISOString(),
                isStreaming: true,
              },
            ];
          });
          setOrbState("speaking");
          break;
        }

        case "error":
          setConnectionError(data.message ?? "Unknown error");
          setOrbState("error");
          break;
      }
    };

    socket.onclose = () => {
      setIsConnected(false);
      setOrbState("sleeping");
      console.log("Jarvis WebSocket disconnected. Attempting reconnect in 2s...");
      
      // FIX: Properly clear existing timeout and re-run connectSocket
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = setTimeout(() => {
        connectSocket();
      }, 2000);
    };

    socket.onerror = (err) => {
      setConnectionError("WebSocket error — is FastAPI running?");
      setOrbState("error");
      console.error("WebSocket error:", err);
    };
  }, []);

  // Initialize connection on mount
  useEffect(() => {
    connectSocket();
    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (ws.current) ws.current.close();
    };
  }, [connectSocket]);

  const sendMessage = useCallback((message: string, image?: string) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      setResearchProgress(null);
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: message,
        createdAt: new Date().toISOString(),
        image: image,
      };
      setMessages((prev) => [...prev, userMsg]);
      
      const payloadStr = JSON.stringify({ message, image });
      ws.current.send(payloadStr);
      setOrbState("processing");
    } else {
      setConnectionError("Not connected to Jarvis backend");
      setOrbState("error");
    }
  }, []);

  const sendApproval = useCallback((approved: boolean) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ type: "approval_response", approved }));
      setOrbState("processing");
    }
  }, []);

  const clearMessages = useCallback((initial: ChatMessage[] = []) => {
    setMessages(initial);
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
  };
}