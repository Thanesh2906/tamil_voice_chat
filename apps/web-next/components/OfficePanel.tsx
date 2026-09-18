"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, approveTool, denyTool, getOfficeSnapshot } from "@/lib/api";
import type { ApprovalView, OfficeSnapshot } from "@/lib/contracts";

const POLL_MS = 8000;

function describeArgs(args: Record<string, unknown>): string {
  const entries = Object.entries(args).filter(([key]) => key !== "content");
  const preview = entries.map(([key, value]) => `${key}=${String(value)}`).join(", ");
  return preview.length > 140 ? `${preview.slice(0, 140)}…` : preview;
}

export function OfficePanel() {
  const [snapshot, setSnapshot] = useState<OfficeSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const mounted = useRef(true);

  const refresh = useCallback((signal?: AbortSignal) => {
    return getOfficeSnapshot(signal)
      .then((next) => {
        if (mounted.current) setSnapshot(next);
        return next;
      })
      .catch((reason: unknown) => {
        if (signal?.aborted || !mounted.current) return;
        setError(reason instanceof ApiError ? reason.message : "Office API unavailable.");
      });
  }, []);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    void refresh(controller.signal);
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => {
      mounted.current = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refresh]);

  const decide = async (approval: ApprovalView, decision: "approve" | "deny") => {
    setDecidingId(approval.id);
    try {
      await (decision === "approve" ? approveTool(approval.id) : denyTool(approval.id));
      await refresh();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : `Could not ${decision} that action.`);
    } finally {
      setDecidingId(null);
    }
  };

  return (
    <section className="office-panel panel" aria-labelledby="office-title">
      <header className="panel-heading">
        <div><p className="eyebrow">Live operations</p><h2 id="office-title">AI Office</h2></div>
        <span className={`connection-pill ${snapshot ? "online" : "offline"}`}>{snapshot ? "Live" : "Offline"}</span>
      </header>

      {error && <div className="honest-empty"><strong>No live agent data</strong><span>{error}</span></div>}
      {!error && !snapshot && <div className="loading-lines" aria-label="Loading office data"><span /><span /><span /></div>}
      {snapshot && (
        <>
          <div className="agent-grid">
            {snapshot.agents.length === 0 && <p className="muted">No registered agents.</p>}
            {snapshot.agents.map((agent) => (
              <article className="agent-card" key={agent.id}>
                <div className="agent-card__top"><span className={`agent-light ${agent.status}`} /><span>{agent.status.replace("_", " ")}</span></div>
                <h3>{agent.name}</h3><p>{agent.role}</p>
                <small>{agent.currentTask ?? agent.model ?? "No active task"}</small>
              </article>
            ))}
          </div>
          <div className="office-columns">
            <div>
              <h3 className="section-label">Task queue</h3>
              {snapshot.tasks.length ? snapshot.tasks.map((task) => <div className="list-row" key={task.id}><span>{task.title}</span><b>{task.status}</b></div>) : <p className="muted">Queue is empty.</p>}
            </div>
            <div>
              <h3 className="section-label">Approvals</h3>
              {snapshot.approvals.length ? snapshot.approvals.map((approval) => (
                <div className="list-row approval approval-row" key={approval.id}>
                  <div className="approval-top">
                    <span>{approval.toolName}</span>
                    <b>{approval.risk}</b>
                  </div>
                  <span className="approval-args">{describeArgs(approval.args)}</span>
                  <div className="approval-actions">
                    <button
                      className="btn-approve"
                      disabled={decidingId === approval.id}
                      onClick={() => void decide(approval, "approve")}
                    >
                      {decidingId === approval.id ? "…" : "Approve"}
                    </button>
                    <button
                      className="btn-deny"
                      disabled={decidingId === approval.id}
                      onClick={() => void decide(approval, "deny")}
                    >
                      {decidingId === approval.id ? "…" : "Deny"}
                    </button>
                  </div>
                </div>
              )) : <p className="muted">Nothing needs approval.</p>}
            </div>
          </div>
          <div className="activity-feed">
            <h3 className="section-label">Verified activity</h3>
            {snapshot.events.length ? snapshot.events.slice(-6).reverse().map((event) => (
              <div className="event-row" key={event.id}>
                <time dateTime={event.createdAt}>{new Date(event.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
                <span><b>{event.agentName}</b>{event.summary}</span>
              </div>
            )) : <p className="muted">No persisted activity yet.</p>}
          </div>
        </>
      )}
    </section>
  );
}
