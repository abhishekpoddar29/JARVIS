import { FormEvent, useMemo, useEffect, useRef, useState, useCallback, ChangeEvent } from "react";
import { Send, Brain, Upload, Image as ImageIcon, X, Mic, MicOff, Menu, Paperclip } from "lucide-react";
import { useJarvisSocket } from "./hooks/useJarvisSocket";
import { useJarvisVoice } from "./hooks/useJarvisVoice";
import { useOrb } from "./hooks/useOrb";
import { JarvisOrb } from "./components/Orb/JarvisOrb";
import { CodeBlock } from "./components/CodeBlock";
import { MemoryViewer } from "./components/MemoryViewer";
import { exportConversation } from "./services/exportService";
import type { ChatMessage } from "./types";
import ReactMarkdown from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { ApprovalCard } from "./components/ApprovalCard";
import { HudStatusBar } from "./components/HudStatusBar";
import { LiveLocationMap } from "./components/tracking/LiveLocationMap"
import { TimeWeatherWidget } from "./components/widgets/TimeWeatherWidget"
import { SystemLogsWidget } from "./components/widgets/SystemLogsWidget"
import { MusicPlayerWidget } from "./components/widgets/MusicPlayerWidget";
import { JarvisBrainWidget } from "./components/widgets/JarvisBrainWidget"
import {
  API_URL,
  jarvisFetch,
} from "./services/apiClient";


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


// Reusable message renderer for both Focus View and History Drawer
interface RenderMessageProps {
  message: ChatMessage;
  onApprove: (editedCode?: string) => void;
  onReject: () => void;
}


const RenderMessage = ({
  message,
  onApprove,
  onReject,
}: RenderMessageProps) => (
  <article className={`message ${message.role}`}>
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
          onApprove={onApprove}
          onReject={onReject}
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
);

export function App() {
  const [draft, setDraft] = useState("");
  const [pendingImage, setPendingImage] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [isMemoryOpen, setIsMemoryOpen] = useState(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false); // F17 UI: History drawer state
  const [uploadedFile, setUploadedFile] = useState<string | null>(null);

  
  const chatPanelRef = useRef<HTMLElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const csvFileInputRef = useRef<HTMLInputElement>(null);

  const handleFileUpload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await jarvisFetch(`${API_URL}/api/upload`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      setUploadedFile(data.filename);
      setDraft(`Please analyze the uploaded file: ${data.filename}`);
    } catch (err) {
      alert("File upload failed.");
    }
    e.target.value = "";
  };


  const {
    messages,
    sendMessage,
    clearMessages,
    orbState: rawOrbState,
    isConnected,
    connectionError,
    isLoadingSession,
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
  } = useJarvisSocket();

  const { orbState } = useOrb({
    rawState: rawOrbState,
    isConnected,
    isLoadingSession,
    hasError: !!connectionError,
  });

  const isTrackingWidgetVisible = Boolean(
    trackingSession?.visible,
  );

  const {
    isActive: isVoiceActive,
    isBusy: isVoiceBusy,
    phase: voicePhase,
    error: voiceError,
    orbState: voiceOrbState,
    toggle: toggleVoiceCall,
  } = useJarvisVoice({
    isSocketConnected: isConnected,
    sendVoiceMessage,
    subscribeVoiceEvents,
  });

  const finalOrbState = voiceOrbState || orbState;

  useEffect(() => {
    const panel = chatPanelRef.current;
    if (panel && isHistoryOpen) panel.scrollTop = panel.scrollHeight;
  }, [messages, isHistoryOpen]);

  const allMessages = useMemo(() => {
    if (isLoadingSession) return [];
    if (messages.length === 0) return [welcomeMessage];
    return messages;
  }, [messages, isLoadingSession]);

  // Focus View shows the latest user and assistant messages.
  const lastUserMsg = [...allMessages]
    .reverse()
    .find((message) => message.role === "user");
  const lastAssistantMsg = [...allMessages]
    .reverse()
    .find((message) => message.role === "assistant");

  const handleExport = useCallback(async () => {
    if (isExporting || !isConnected) return;
    setIsExporting(true);
    try {
      const result = await exportConversation();
      if (result.ok) clearMessages([resetMessage]);
      else console.error("Export failed:", result.error);
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
    <main
      className={`app-shell ${
        isTrackingWidgetVisible ? "tracking-visible" : ""
      }`}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
    >
      <div className="jarvis-ambient-grid" aria-hidden="true" />

      <JarvisOrb
        state={finalOrbState}
        isSpeaking={finalOrbState === "speaking"}
      />

      <aside
        className="dashboard-left-rail"
        aria-label="Jarvis information widgets"
      >
        <TimeWeatherWidget />
        <SystemLogsWidget
          logs={systemLogs}
          isConnected={isConnected}
        />
      </aside>

      <JarvisBrainWidget
        event={workflowEvent}
        isConnected={isConnected}
        orbState={finalOrbState}
        voicePhase={voicePhase}
      />

      {trackingSession?.visible && (
        <LiveLocationMap
          session={trackingSession}
          isConnected={isConnected}
          onStop={stopTracking}
          onDismiss={dismissTracking}
        />
      )}

      <MusicPlayerWidget
        command={musicCommand}
        trackingActive={isTrackingWidgetVisible}
        onStateChange={sendMusicState}
      />

      <MemoryViewer isOpen={isMemoryOpen} onClose={() => setIsMemoryOpen(false)} />

      {/* F17 UI: History Drawer */}
      <div className={`history-drawer ${isHistoryOpen ? 'open' : ''}`}>
        <div className="drawer-header">
          <h2>Session History</h2>
          <button className="close-btn" onClick={() => setIsHistoryOpen(false)}><X size={18} /></button>
        </div>
        <section className="chat-panel" ref={chatPanelRef} aria-label="Conversation History">
          {isLoadingSession ? (
            <div className="session-loading">Restoring today's session...</div>
          ) : (
            allMessages.map((message) => (
              <RenderMessage 
                key={message.id} 
                message={message} 
                onApprove={(editedCode) => sendApproval(true, editedCode)} 
                onReject={() => sendApproval(false)}
              />
            ))
          )}
        </section>
      </div>

      <header className="topbar">
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <button className="menu-btn" title="View Chat History" onClick={() => setIsHistoryOpen(true)}>
            <Menu size={20} />
          </button>
          <div>
            <h1>J.A.R.V.I.S</h1>
            <p>
              {isLoadingSession
                ? "Restoring session..."
                : isConnected
                ? "Online"
                : connectionError ?? "Connecting..."}
            </p>
          </div>
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

      {/* F17 UI: Focus View (Only the latest exchange) */}
      <div className="focus-view">
        {isLoadingSession ? (
          <div className="session-loading">Restoring today's session...</div>
        ) : (
          <>
            {lastUserMsg && (
              <RenderMessage 
                message={lastUserMsg} 
                onApprove={(editedCode) => sendApproval(true, editedCode)} 
                onReject={() => sendApproval(false)}
              />
            )}
            {lastAssistantMsg && lastAssistantMsg.id !== lastUserMsg?.id && (
              <RenderMessage 
                message={lastAssistantMsg} 
                onApprove={(editedCode) => sendApproval(true, editedCode)} 
                onReject={() => sendApproval(false)}
              />
            )}
          </>
        )}
      </div>


      <HudStatusBar orbState={finalOrbState} researchProgress={researchProgress} />

      <form className="input-bar" onSubmit={handleSubmit}>
        {/* Hidden input for Images */}
        <input
          type="file"
          ref={fileInputRef}
          onChange={handleFileSelect}
          accept="image/*"
          style={{ display: "none" }}
        />
        
        {/* Hidden input for Data Files (CSV, JSON, etc.) - ADD THIS REF AT TOP OF FILE */}
        <input
          type="file"
          ref={csvFileInputRef}
          onChange={handleFileUpload}
          accept=".csv,.json,.txt,.xlsx"
          style={{ display: "none" }}
        />

        {/* Image Preview */}
        {pendingImage && (
          <div className="image-preview-container">
            <img src={pendingImage} alt="Pending upload" className="image-preview" />
            <button type="button" className="remove-image-btn" onClick={() => setPendingImage(null)} title="Remove image">
              <X size={14} />
            </button>
          </div>
        )}

        {/* File Preview Chip */}
        {uploadedFile && (
          <div className="image-preview-container" style={{ background: "rgba(83, 224, 244, 0.1)", borderColor: "rgba(83, 224, 244, 0.3)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "8px 12px", color: "#8ce9f7", fontSize: "13px" }}>
              <Paperclip size={14} />
              {uploadedFile}
            </div>
            <button type="button" className="remove-image-btn" onClick={() => setUploadedFile(null)} title="Remove file">
              <X size={14} />
            </button>
          </div>
        )}

        <div className="input-row">
          <button
            type="button"
            title="Attach Image"
            onClick={() => fileInputRef.current?.click()}
            className="attach-btn"
          >
            <ImageIcon size={18} />
          </button>
          
          <button 
            type="button" 
            title="Attach Data File (CSV, JSON, TXT)"
            onClick={() => csvFileInputRef.current?.click()}
            className="attach-btn"
          >
            <Paperclip size={18} />
          </button>

          <button
            type="button"
            title={
              voiceError ??
              (isVoiceActive
                ? `End Voice Session (${voicePhase})`
                : isVoiceBusy
                  ? "Voice is processing..."
                  : "Start Jarvis Voice")
            }
            onClick={toggleVoiceCall}
            className={`voice-btn ${isVoiceActive ? "active" : ""}`}
            disabled={!isConnected}
          >
            {isVoiceActive ? <MicOff size={18} /> : <Mic size={18} />}
          </button>

          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onPaste={handlePaste}
            placeholder="Ask Jarvis anything... (Paste image or attach file)"
            aria-label="Message"
            disabled={!isConnected || isLoadingSession}
            autoComplete="off"
          />
          <button 
            type="submit" 
            title="Send" 
            disabled={!isConnected || isLoadingSession || (!draft.trim() && !pendingImage && !uploadedFile)}
          >
            <Send size={16} />
          </button>
        </div>
      </form>

    </main>
  );
}
