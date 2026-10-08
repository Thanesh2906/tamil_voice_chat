"use client";

import type { RunOut } from "@/lib/contracts";
import { useRunData } from "./RunDataContext";
import { Icon } from "./Icons";

export function RunControls({ run }: { run: RunOut }) {
  const { actOnRun, refreshRun, pendingRuns, runErrors } = useRunData();
  const pending = pendingRuns[run.id] ?? {};
  const error = runErrors[run.id];
  const executing = run.executing_tool_ids ?? [];
  const uncertain = run.uncertain_tool_ids ?? [];
  const unfinished = ["paused", "running", "awaiting_approval"].includes(run.status);
  if (!unfinished && !executing.length && !uncertain.length && !error && !pending.resume && !pending.cancel && run.status !== "cancelled") return null;

  return (
    <div className="run-controls" aria-label="Run controls">
      {error && <p className="form-error" role="alert">{error}</p>}
      {pending.resume && <p className="muted" role="status">{run.cancellation_requested ? "The earlier resume request is still finishing. Cancellation remains recorded." : "Continuing this saved run. You can request cancellation while it is working."}</p>}
      {pending.cancel && <p className="muted" role="status">Requesting cancellation. Server confirmation has not arrived yet.</p>}
      {run.cancellation_requested && (
        <p className="form-notice" role="status">
          {executing.length ? "Cancellation requested. A tool is still executing and may finish." : "Run cancelled. No further steps will start."} Completed effects are not reversed.
        </p>
      )}
      {executing.length > 0 && <p className="run-safety-note">{executing.length} {executing.length === 1 ? "tool is" : "tools are"} executing. Cancellation cannot undo or guarantee stopping an in-flight action.</p>}
      {uncertain.length > 0 && <p className="form-notice" role="status">The outcome of {uncertain.length} {uncertain.length === 1 ? "tool is" : "tools are"} uncertain. An effect may already have happened. Resume is blocked; verify the outcome before starting a separate request. Nothing will be retried automatically.</p>}
      {run.resume_blocked_reason && unfinished && !pending.resume && <p className="run-safety-note">{run.resume_blocked_reason}</p>}
      {run.status === "paused" && run.can_resume && !pending.resume && <p className="run-safety-note">Resume uses the saved tool results. New actions that need permission require new approvals.</p>}
      {(unfinished || error || pending.resume || pending.cancel) && <div className="run-control-actions">
        {(run.can_resume || run.status === "paused") && <button type="button" className="primary-button" disabled={!run.can_resume || Boolean(error) || Boolean(pending.resume || pending.cancel)} onClick={() => void actOnRun(run.id, "resume")}>
          <Icon name="arrow" size={14} />{pending.resume ? "Resuming…" : "Resume run"}
        </button>}
        {(run.can_cancel || pending.cancel) && <button type="button" className="secondary-button danger" disabled={!run.can_cancel || Boolean(error) || Boolean(pending.cancel)} onClick={() => void actOnRun(run.id, "cancel")}>
          <Icon name="stop" size={13} />{pending.cancel ? "Cancelling…" : "Cancel run"}
        </button>}
        <button type="button" className="text-action" disabled={Boolean(pending.resume || pending.cancel)} onClick={() => void refreshRun(run.id)}><Icon name="refresh" size={14} />Refresh saved status</button>
      </div>}
      {unfinished && !run.cancellation_requested && !executing.length && <p className="run-safety-note">Cancelling stops future steps. Actions already completed are not undone.</p>}
    </div>
  );
}
