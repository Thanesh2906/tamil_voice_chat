export type VoiceState = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "error";

export type AgentStatus = "offline" | "idle" | "working" | "waiting_approval" | "error";

export interface AgentView {
  id: string;
  name: string;
  role: string;
  status: AgentStatus;
  model?: string;
  currentTask?: string;
}

export interface RunEvent {
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
  status: "queued" | "running" | "blocked" | "completed" | "failed" | "cancelled";
  progress?: number;
}

export interface ApprovalView {
  id: string;
  toolName: string;
  summary: string;
  risk: "medium" | "high" | "critical";
  expiresAt: string;
}

export interface OfficeSnapshot {
  generatedAt: string;
  agents: AgentView[];
  events: RunEvent[];
  tasks: TaskView[];
  approvals: ApprovalView[];
}

export type VoiceEvent =
  | { type: "authenticated" | "ready" | "barge_in"; data?: Record<string, unknown> }
  | { type: "partial" | "transcript" | "token" | "final"; data?: { text?: string } }
  | { type: "audio"; data?: { audio?: string; media_type?: string } }
  | { type: "error"; data?: { detail?: string } }
  | { type: "citation"; data?: Record<string, unknown> };
