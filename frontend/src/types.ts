export type OrbState =
  | "sleeping"
  | "idle"
  | "waking"
  | "listening"
  | "processing"
  | "processing_code"
  | "speaking"
  | "error";

export interface CodeResult {
  code: string;
  stdout: string;
  stderr: string;
  success: boolean;
  images: string[];
  files: any[];
  error: string | null;
  execution_time_ms: number;
  retried: boolean;
}

export interface ApprovalData {
  action_type: "send_email" | "calendar_event" | "self_modify";
  draft: any;
  tool_name: string;
}

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  isStreaming?: boolean;
  code_result?: CodeResult;
  image?: string;
  approval_data?: ApprovalData;
};

export type TrackingStatus =
  | "PENDING"
  | "ACTIVE"
  | "DENIED"
  | "ENDED"
  | "EXPIRED";

export interface TrackingLocationPoint {
  latitude: number;
  longitude: number;
  accuracy: number;
  heading: number | null;
  speed: number | null;
  timestamp: number;
  server_timestamp?: string | null;
}

export interface TrackingSessionState {
  sessionId: string;
  targetName: string;
  maskedPhone?: string;
  durationMinutes?: number;
  status: TrackingStatus;
  createdAt?: string;
  expiresAt: string;
  consentedAt?: string | null;
  endedAt?: string | null;
  lastUpdateAt?: string | null;
  reason?: string;
  latestPoint?: TrackingLocationPoint;
  trail: TrackingLocationPoint[];
  visible: boolean;
}

export type SystemLogLevel = "info" | "success" | "warning" | "error";

export interface SystemLogEntry {
  id: string;
  timestamp: string;
  source: string;
  message: string;
  level: SystemLogLevel;
}

// ── Jarvis Music Player ──────────────────────────────────────────────────────
export type MusicPlaybackState =
  | "idle"
  | "loading"
  | "playing"
  | "paused"
  | "buffering"
  | "blocked"
  | "error";

export interface MusicTrack {
  video_id: string;
  title: string;
  channel_title: string;
  thumbnail: string;
  duration_seconds: number;
  published_at?: string | null;
  metadata_refreshed_at?: string;
}

export interface MusicCommand {
  command_id: string;
  action:
    | "play"
    | "pause"
    | "resume"
    | "next"
    | "previous"
    | "queue_updated"
    | "favorites_updated"
    | "show_queue"
    | "show_favorites"
    | "open"
    | "close";
  visible?: boolean;
  track?: MusicTrack;
  alternatives?: MusicTrack[];
  queue?: MusicTrack[];
  favorites?: MusicTrack[];
}

export interface MusicClientState {
  playback_state: MusicPlaybackState;
  current_track: MusicTrack | null;
  visible: boolean;
  position_seconds: number;
  volume: number;
  updated_at: string;
}


// ── Turn-based Jarvis voice pipeline ────────────────────────────────────────
export type VoicePhase =
  | "inactive"
  | "waking"
  | "listening"
  | "transcribing"
  | "waiting"
  | "speaking"
  | "error";

export interface VoiceTranscriptionResponse {
  transcript: string;
  model: string;
}

export interface VoiceSocketEvent {
  type: "assistant" | "approval_required" | "error";
  text: string;
}

export type VoiceSocketEventListener = (event: VoiceSocketEvent) => void;

// ── Live Jarvis workflow graph ──────────────────────────────────────────────
export type WorkflowEventAction =
  | "boot"
  | "activate"
  | "flow"
  | "success"
  | "error"
  | "reset";

export interface WorkflowEvent {
  id: string;
  action: WorkflowEventAction;
  node?: string;
  from?: string;
  to?: string;
  label?: string;
  detail?: string;
  path?: string[];
  timestamp: string;
}