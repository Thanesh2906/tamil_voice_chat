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
      return { key, label: "Run paused" };
    case "run.resumed":
      return { key, label: "Continued from saved results" };
    case "run.cancelled":
      return { key, label: "Cancellation recorded" };
    case "tool.denied":
      return { key, label: `${String(event.data.tool_name ?? "Tool")} denied` };
    case "tool.uncertain":
      return { key, label: `${String(event.data.tool_name ?? "Tool")} outcome uncertain`, pending: true };
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
  const resolvedTools = new Set(run.events
    .filter((event) => ["tool.completed", "tool.failed", "tool.denied", "tool.uncertain"].includes(event.type))
    .map((event) => event.data.invocation_id)
    .filter(Boolean));
  return run.events
    .filter((event) => event.type !== "tool.pending_approval" ||
      (run.status === "awaiting_approval" && !resolvedTools.has(event.data.invocation_id)))
    .map(friendlyEvent)
    .filter((item): item is ActivityItem => item !== null);
}

export function runAnswerText(run: RunOut): string {
  if (run.status === "awaiting_approval") {
    return "An action needs your decision. Review its exact arguments in Approvals before allowing it to run.";
  }
  if (run.status === "paused") {
    return run.uncertain_tool_ids?.length
      ? "This run is paused because a tool's outcome is uncertain. Review the saved events before taking further action."
      : run.can_resume
        ? "This run is paused. Resume it to continue from its saved results."
        : "This run is paused. Review its saved status and tool results.";
  }
  if (run.status === "cancelled") {
    return run.executing_tool_ids?.length
      ? "Cancellation was requested. An in-flight tool may still finish; check its saved result."
      : "This run was cancelled. Actions already completed were not undone.";
  }
  if (run.status === "failed") {
    const failure = run.events.findLast((event) => event.type === "run.failed");
    return String(failure?.data.error ?? "That run failed.");
  }
  const completed = run.events.findLast((event) => event.type === "run.completed");
  return String(completed?.data.answer ?? "");
}
