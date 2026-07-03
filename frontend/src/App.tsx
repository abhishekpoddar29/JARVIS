// frontend/src/App.tsx

import { FormEvent, useMemo, useEffect, useRef, useState, useCallback, ChangeEvent } from "react";
import { Send, Brain, Upload, Image as ImageIcon, X, Mic, MicOff } from "lucide-react";
import { useJarvisSocket } from "./hooks/useJarvisSocket";
import { useOrb } from "./hooks/useOrb";
import { JarvisOrb } from "./components/Orb/JarvisOrb";
import { CodeBlock } from "./components/CodeBlock";
import { MemoryViewer } from "./components/MemoryViewer";
import { exportConversation } from "./services/exportService";
import type { ChatMessage, OrbState } from "./types";
import ReactMarkdown from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { ApprovalCard } from "./components/ApprovalCard";
import Vapi from "@vapi-ai/web"; // F16: Clean standard import (Requires Vite v5)

const welcomeMessage: ChatMessage = {
  id: "welcome",
  role: "assistant",
  content: "Good evening, Abhi. Core systems are standing by.",
  createdAt: new Date().toISOString(),
};

const resetMessage: ChatMessage = {
  id: "reset",
  role: "assistant",
  content: "Conversation exported and archived. Starting fresh — what's on your mind, Abhi?",
  createdAt: new Date().toISOString(),
};

export function App() {
  const [draft, setDraft] = useState("");
  const [pendingImage, setPendingImage] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [isMemoryOpen, setIsMemoryOpen] = useState(false);
  const [voiceOrbState, setVoiceOrbState] = useState<OrbState | null>(null);
  const [isVapiActive, setIsVapiActive] = useState(false);
  
  const chatPanelRef = useRef<HTMLElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const vapiRef = useRef<any>(null);

  const {
    messages,
    sendMessage,
    clearMessages,
    setMessages,
    orbState: rawOrbState,
    isConnected,
    connectionError,
    isLoadingSession,
    sendApproval,
    researchProgress,
  } = useJarvisSocket();

  const { orbState, stateLabel } = useOrb({
    rawState: rawOrbState,
    isConnected,
    isLoadingSession,
    hasError: !!connectionError,
  });

  // Override orb state if voice is active
  const finalOrbState = voiceOrbState || orbState;

  // F16: Initialize Vapi SDK
  useEffect(() => {
    const apiKey = import.meta.env.VITE_VAPI_API_KEY;
    const assistantId = import.meta.env.VITE_VAPI_ASSISTANT_ID;

    // DEBUG: This will print "undefined" if the .env file isn't loading
    console.log("[VAPI DEBUG] API Key loaded:", apiKey ? "Yes" : "No"); 

    if (!apiKey || !assistantId) {
      console.error("[VAPI] Missing API Key or Assistant ID in frontend/.env");
      return;
    }

    const vapi = new Vapi(apiKey);

    vapiRef.current = vapi;

    // Map Vapi events to Jarvis UI
    vapi.on("call-start", () => {
      setIsVapiActive(true);
      setVoiceOrbState("listening");
    });

    vapi.on("call-end", () => {
      setIsVapiActive(false);
      setVoiceOrbState(null);
    });

    vapi.on("speech-start", () => {
      setVoiceOrbState("listening");
    });

    vapi.on("speech-end", () => {
      setVoiceOrbState("processing");
    });

    vapi.on("message", (message: any) => {
      // F16 FIX: Only render USER transcripts to the UI.
      // Assistant messages and Code Blocks are pushed directly via the WebSocket 
      // by the backend so they render with rich UI (CodeBlocks, ApprovalCards).
      if (message.type === "transcript" && message.isFinal && message.from === "user") {
        const text = message.text || message.content;
        if (text) {
          setMessages((prev) => [...prev, {
            id: crypto.randomUUID(),
            role: "user",
            content: text,
            createdAt: new Date().toISOString()
          }]);
        }
      }
    });

    vapi.on("error", (e: any) => {
      console.error("[VAPI] Error:", e);
      setIsVapiActive(false);
      setVoiceOrbState(null);
    });

    // FIX: Do NOT return vapi.stop() here! React Strict Mode will unmount and remount
    // the component on initial load, which would call stop() and instantly hang up the call.
    // The user can hang up using the Mic button (toggleVoiceCall).
  }, [setMessages]);

  // F16: Start/Stop Voice Call
  const toggleVoiceCall = useCallback(() => {
    if (!vapiRef.current) {
      console.error("[VAPI] Cannot start call: SDK is not initialized.");
      return;
    }
    
    if (isVapiActive) {
      vapiRef.current.stop();
    } else {
      vapiRef.current.start(import.meta.env.VITE_VAPI_ASSISTANT_ID);
    }
  }, [isVapiActive]);

  useEffect(() => {
    const panel = chatPanelRef.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }, [messages]);

  const allMessages = useMemo(() => {
    if (isLoadingSession) return [];
    if (messages.length === 0) return [welcomeMessage];
    return messages;
  }, [messages, isLoadingSession]);

  const handleExport = useCallback(async () => {
    if (isExporting || !isConnected) return;
    setIsExporting(true);
    try {
      const result = await exportConversation();
      if (result.ok) {
        clearMessages([resetMessage]);
      } else {
        console.error("Export failed:", result.error);
      }
    } finally {
      setIsExporting(false);
    }
  }, [isExporting, isConnected, clearMessages]);

  const handleImageFile = (file: File) => {
    if (!file.type.startsWith("image/")) return;
    const reader = new FileReader();
    reader.onload = (e) => setPendingImage(e.target?.result as string);
    reader.readAsDataURL(file);
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData.items;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith("image/")) {
        const file = items[i].getAsFile();
        if (file) handleImageFile(file);
        break;
      }
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const files = e.dataTransfer.files;
    if (files.length > 0) handleImageFile(files[0]);
  };

  const handleDragOver = (e: React.DragEvent) => e.preventDefault();

  const handleFileSelect = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) handleImageFile(e.target.files[0]);
    e.target.value = "";
  };

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = draft.trim();
    if (!message && !pendingImage) return;
    sendMessage(message || "What is in this image?", pendingImage || undefined);
    setDraft("");
    setPendingImage(null);
  }

  return (
    <main className="app-shell" onDrop={handleDrop} onDragOver={handleDragOver}>

      <JarvisOrb state={finalOrbState} />

      <div className="state-label" aria-label={`Jarvis state: ${stateLabel}`}>
        {stateLabel}
      </div>

      <MemoryViewer isOpen={isMemoryOpen} onClose={() => setIsMemoryOpen(false)} />

      <header className="topbar">
        <div>
          <h1>Jarvis 2.0</h1>
          <p>
            {isLoadingSession
              ? "Restoring session..."
              : isConnected
              ? "Online"
              : connectionError ?? "Connecting..."}
          </p>
        </div>
        <nav className="actions" aria-label="Application actions">
          <button type="button" title="View Jarvis Memory" onClick={() => setIsMemoryOpen(true)}>
            <Brain size={16} />
          </button>
          <button
            type="button"
            title={isExporting ? "Exporting..." : "Export & reset conversation"}
            onClick={handleExport}
            disabled={isExporting || !isConnected || isLoadingSession}
          >
            <Upload size={16} />
          </button>
        </nav>
      </header>

      <section className="chat-panel" ref={chatPanelRef} aria-label="Conversation">
        {isLoadingSession ? (
          <div className="session-loading">Restoring today's session...</div>
        ) : (
          allMessages.map((message) => (
            <article key={message.id} className={`message ${message.role}`}>
              <div className="message-role">
                {message.role === "assistant" ? "Jarvis" : "You"}
              </div>
              <div className="message-content">
                {message.image && (
                  <img src={message.image} alt="User attachment" className="message-image" />
                )}
                {message.approval_data && (
                  <ApprovalCard
                    data={message.approval_data}
                    onApprove={() => sendApproval(true)}
                    onReject={() => sendApproval(false)}
                  />
                )}

                {message.code_result && <CodeBlock result={message.code_result} />}
                {message.content && (
                  <ReactMarkdown
                    components={{
                      code({ node, inline, className, children, ...props }: any) {
                        const match = /language-(\w+)/.exec(className || "");
                        return !inline && match ? (
                          <SyntaxHighlighter
                            style={oneDark}
                            language={match[1]}
                            PreTag="div"
                            {...props}
                          >
                            {String(children).replace(/\n$/, "")}
                          </SyntaxHighlighter>
                        ) : (
                          <code className={className} {...props}>{children}</code>
                        );
                      },
                    }}
                  >
                    {message.content}
                  </ReactMarkdown>
                )}
              </div>
            </article>
          ))
        )}
      </section>

      {researchProgress && (
        <div className="research-progress-card">
          <div className="progress-icon">
            <svg className="spinner" viewBox="0 0 24 24" width="20" height="20">
              <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" opacity="0.2"/>
              <path d="M12 2 A10 10 0 0 1 22 12" stroke="currentColor" strokeWidth="3" fill="none"/>
            </svg>
          </div>
          <div className="progress-text">
            <span className="progress-label">Deep Research Active</span>
            <span className="progress-message">{researchProgress}</span>
          </div>
        </div>
      )}

      <form className="input-bar" onSubmit={handleSubmit}>
        <input 
          type="file" 
          ref={fileInputRef} 
          onChange={handleFileSelect} 
          accept="image/*" 
          style={{ display: 'none' }} 
        />

        {pendingImage && (
          <div className="image-preview-container">
            <img src={pendingImage} alt="Pending upload" className="image-preview" />
            <button 
              type="button" 
              className="remove-image-btn" 
              onClick={() => setPendingImage(null)}
              title="Remove image"
            >
              <X size={14} />
            </button>
          </div>
        )}

        <div className="input-row">
          <button 
            type="button" 
            title="Attach image"
            onClick={() => fileInputRef.current?.click()}
            className="attach-btn"
          >
            <ImageIcon size={18} />
          </button>

          {/* F16: Vapi Voice Call Button */}
          <button 
            type="button"
            title={isVapiActive ? "End Voice Call" : "Start Voice Call"}
            onClick={toggleVoiceCall}
            className={`voice-btn ${isVapiActive ? 'active' : ''}`}
          >
            {isVapiActive ? <MicOff size={18} /> : <Mic size={18} />}
          </button>

          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onPaste={handlePaste}
            placeholder="Ask Jarvis anything... (Paste or drop an image)"
            aria-label="Message"
            disabled={!isConnected || isLoadingSession}
            autoComplete="off"
          />
          <button
            type="submit"
            title="Send"
            disabled={!isConnected || isLoadingSession || (!draft.trim() && !pendingImage)}
          >
            <Send size={16} />
          </button>
        </div>
      </form>

    </main>
  );
}