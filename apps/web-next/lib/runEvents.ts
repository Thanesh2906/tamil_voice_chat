import type { RunEventOut, RunOut } from "./contracts";

export interface ActivityItem {
  key: string;
  label: string;
  pending?: boolean;
}

/** Turns one RunEvent into a short, human label. Returns null for events that
 * are meaningful in the raw timeline (run.started/run.classified) but not
 * worth a chip in a compact activity strip. */
export function friendlyEvent(event: RunEventOut): ActivityItem | null {
  const key = `${event.sequence}-${event.type}`;
  switch (event.type) {
    case "agent.selected":
      return {
        key,
        label: `Working with ${String(event.data.agent_id ?? "manager")}`,
      };
    case "agent.delegated":
      return {
        key,
        label: `Delegated to ${String(event.data.to_agent_id ?? event.data.agent_id ?? "specialist")}`,
      };
    case "run.paused":
      return { key, label: "Run paused after tool decision" };
    case "retrieval.completed":
      return {
        key,
        label: `Read ${event.data.chunk_count ?? 0} project source(s)`,
      };
    case "tool.completed":
      return { key, label: `Used ${String(event.data.tool_name ?? "tool")}` };
    case "tool.rejected":
      return {
        key,
        label: `Refused ${String(event.data.name ?? "a tool call")}`,
      };
    case "tool.failed":
      return { key, label: `${String(event.data.tool_name ?? "tool")} failed` };
    case "tool.pending_approval":
      return {
        key,
        label: `Waiting on your approval to run ${String(event.data.tool_name ?? "a tool")}`,
        pending: true,
      };
    case "model.selected":
      return {
        key,
        label: `Answered by ${String(event.data.provider ?? "?")} · ${String(event.data.model ?? "")}`,
      };
    case "run.tool_limit_reached":
      return { key, label: "Stopped after the tool-call limit" };
    default:
      return null;
  }
}

export function runActivity(run: RunOut): ActivityItem[] {
  return run.events
    .map(friendlyEvent)
    .filter((item): item is ActivityItem => item !== null);
}

export function runAnswerText(run: RunOut): string {
  if (run.status === "awaiting_approval") {
    return "I want to take an action that needs your approval first — see Approvals in AI Office.";
  }
  if (run.status === "paused") {
    return "This run is paused after an approval decision. Review its recorded tool result below, then send a new message to continue.";
  }
  if (run.status === "failed") {
    const failure = run.events.find((event) => event.type === "run.failed");
    return String(failure?.data.error ?? "That run failed.");
  }
  const completed = run.events.find((event) => event.type === "run.completed");
  return String(completed?.data.answer ?? "");
}
