export type OrbState = 
  | "sleeping" | "idle" | "waking" | "listening" 
  | "processing" | "processing_code" | "speaking" | "error" ;

export interface CodeResult {
  code: string; stdout: string; stderr: string; success: boolean;
  images: string[]; files: any[]; error: string | null;
  execution_time_ms: number; retried: boolean;
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
  approval_data?: ApprovalData; // ← F13: Approval Gate payload
};