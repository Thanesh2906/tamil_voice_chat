export type VoiceState =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "error";

export type AgentStatus =
  | "offline"
  | "idle"
  | "working"
  | "waiting_approval"
  | "error";

export interface AgentView {
  id: string;
  name: string;
  role: string;
  status: AgentStatus;
  model?: string;
  currentTask?: string;
  description?: string;
  capabilities?: string[];
}

export interface AgentProfile {
  id: string;
  name: string;
  role: string;
  description: string;
  mode: string;
  capabilities: string[];
  tools_enabled: boolean;
  available?: boolean;
}

export interface AgentsResponse {
  agents: AgentProfile[];
  execution: { mode: string; durable_queue: boolean; approval_resume: boolean };
}

export interface OfficeEvent {
  id: string;
  sequence: number;
  createdAt: string;
  agentName: string;
  type: string;
  summary: string;
}

export interface TaskView {
  id: string;
  title: string;
  status:
    | "queued"
    | "running"
    | "blocked"
    | "completed"
    | "failed"
    | "cancelled";
  progress?: number;
}

// Matches services/api/__init__.py office_snapshot()'s real "approvals" shape --
// a pending ToolInvocation, not the fields ("summary", "expiresAt") an earlier
// version of this type assumed before the tool gateway existed.
export interface ApprovalView {
  id: string;
  toolName: string;
  risk: "read" | "write" | "exec";
  args: Record<string, unknown>;
  requestedAt: string;
}

export interface OfficeSnapshot {
  generatedAt: string;
  agents: AgentView[];
  events: OfficeEvent[];
  tasks: TaskView[];
  approvals: ApprovalView[];
}

export type VoiceEvent =
  | {
      type: "authenticated" | "ready" | "barge_in";
      data?: Record<string, unknown>;
    }
  | {
      type: "partial" | "transcript" | "token" | "final";
      data?: { text?: string };
    }
  | { type: "audio"; data?: { audio?: string; media_type?: string } }
  | {
      type: "model";
      data?: { provider?: string; model?: string; reason?: string };
    }
  | { type: "error"; data?: { detail?: string } }
  | { type: "citation"; data?: Record<string, unknown> };

// ---- Auth -------------------------------------------------------------

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

export interface UserPublic {
  id: string;
  email: string;
  display_name: string;
  preferred_language: string;
  scopes: string[];
  created_at: string;
}

// ---- Models (multi-model router) --------------------------------------

export interface ProviderInfo {
  name: string;
  privacy: "local" | "cloud";
  default_model: string;
  configured?: boolean;
  verified?: boolean;
  verification_status?: string;
}

export interface ModelsResponse {
  allow_cloud: boolean;
  providers: ProviderInfo[];
}

export interface DesktopStatus {
  protocol_version: number;
  state: "disconnected";
  paired: false;
  machine_id: null;
  session_id: null;
  platform_support: string[];
  capabilities: { name: string; implemented: boolean; available: boolean }[];
  reason: string;
}

// ---- Durable runs -------------------------------------------------------

export interface RunControls {
  can_resume: boolean;
  can_cancel: boolean;
  resume_blocked_reason: string | null;
  cancellation_requested: boolean;
  executing_tool_ids: string[];
  uncertain_tool_ids: string[];
}

export interface RunEventOut {
  sequence: number;
  type: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface RunOut extends RunControls {
  id: string;
  agent_id?: string;
  execution_agent_id?: string;
  status: "running" | "completed" | "failed" | "awaiting_approval" | "paused" | "cancelled";
  input_text: string;
  created_at: string;
  completed_at?: string | null;
  events: RunEventOut[];
}

// One frame from POST /runs/stream. `sseEvent` is "end"/"error" for the two
// terminal frames (whose `data` is {status} / {detail}), or a run event type
// ("run.started", "token", "model.selected", ...) matching RunEventOut.type
// for every other frame. `sequence` is null for an ephemeral event (e.g.
// "token") that was never persisted as a RunEvent row.
export interface RunStreamEvent {
  sseEvent: string;
  sequence: number | null;
  data: Record<string, unknown>;
}

export interface RunSummary extends RunControls {
  id: string;
  agent_id?: string;
  execution_agent_id?: string;
  status: RunOut["status"];
  input_preview: string;
  created_at: string;
  completed_at?: string | null;
}

// ---- Tool gateway -------------------------------------------------------

// ---- Projects / RAG -------------------------------------------------------

export interface ProjectView {
  id: string;
  name: string;
  role: string;
}

export interface IngestResponse {
  project_id: string;
  files_seen: number;
  chunks_indexed: number;
  skipped: string[];
  indexed_files: Record<string, unknown>[];
  job_id?: string;
}

export interface ToolInvocationOut {
  id: string;
  tool_name: string;
  risk: "read" | "write" | "exec";
  status:
    | "pending"
    | "auto_approved"
    | "approved"
    | "executing"
    | "denied"
    | "completed"
    | "failed"
    | "uncertain";
  args: Record<string, unknown>;
  result?: unknown;
  error?: string | null;
  created_at: string;
  decided_at?: string | null;
  run_id?: string | null;
  run_status?: RunOut["status"] | null;
}
