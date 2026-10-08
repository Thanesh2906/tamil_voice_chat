"use client";

import { useState } from "react";
import { useRunData } from "./RunDataContext";
import { useOfficeData } from "./OfficeDataContext";
import { Icon } from "./Icons";

export function OfficePanel({
  view = "summary",
  onApprovals,
}: {
  view?: "summary" | "approvals" | "tasks";
  onApprovals?: () => void;
}) {
  const { snapshot, error, connection, refresh, refreshing, directory } =
    useOfficeData();
  const { decidingId, decisionLocks, refreshDecisions, decisionNotice, decisionError: actionError, decideTool } = useRunData();
  const [reviewed, setReviewed] = useState<Record<string, string>>({});
  const approvals = snapshot?.approvals ?? [];
  const tasks = snapshot?.tasks ?? [];
  const pending = approvals.length;

  if (view === "summary")
    return (
      <section className="office-summary panel">
        <header className="section-heading">
          <div>
            <p className="eyebrow">A PULSE ON YOUR WORK</p>
            <h2>Office activity</h2>
          </div>
          <span
            className={`status-label ${connection === "connected" ? "status-label--green" : "status-label--muted"}`}
          >
            <span className="tiny-dot" />
            {connection === "connected"
              ? "Connected"
              : connection === "loading"
                ? "Connecting"
                : "Disconnected"}
          </span>
        </header>
        {error && (
          <p className="form-notice" role="status">
            {error}
          </p>
        )}
        <button
          className={`approval-summary ${pending ? "has-pending" : ""}`}
          type="button"
          onClick={onApprovals}
        >
          <span className="summary-icon">
            <Icon name="shield" size={20} />
          </span>
          <span>
            <b>
              {connection === "loading"
                ? "Checking approvals"
                : pending
                  ? `${pending} ${pending === 1 ? "action needs" : "actions need"} your review`
                  : "Your decisions live here"}
            </b>
            <small>
              {pending
                ? "Review before anything happens"
                : "Actions needing approval will appear here"}
            </small>
          </span>
          <Icon name="chevron" size={16} />
        </button>
        <div className="activity-feed">
          <h3 className="section-label">Recent activity</h3>
          {snapshot?.events.length ? (
            [...snapshot.events]
              .reverse()
              .slice(0, 5)
              .map((event) => (
                <div className="event-row" key={event.id}>
                  <span className="event-dot" />
                  <div>
                    <b>{event.agentName}</b>
                    <p>{event.summary}</p>
                  </div>
                  <time dateTime={event.createdAt}>
                    {new Date(event.createdAt).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </div>
              ))
          ) : (
            <div className="compact-empty">
              <Icon name="activity" size={25} />
              <p>
                {connection === "loading"
                  ? "Loading saved activity…"
                  : "A fresh start."}
              </p>
              <span>Your real activity will appear as you work.</span>
            </div>
          )}
        </div>
      </section>
    );

  return (
    <section
      className="office-panel panel"
      aria-labelledby={`office-${view}-title`}
    >
      <header className="section-heading">
        <div>
          <p className="eyebrow">
            {view === "approvals"
              ? "YOU HAVE THE FINAL SAY"
              : "RECORDED BY YOUR SERVER"}
          </p>
          <h2 id={`office-${view}-title`}>
            {view === "approvals" ? "Approval inbox" : "Task activity"}
          </h2>
        </div>
        <button
          className="secondary-button"
          type="button"
          onClick={() => void refresh()}
          disabled={refreshing}
        >
          <Icon name="refresh" size={15} />
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </header>
      {error && (
        <div className="form-notice" role="status">
          <strong>
            {snapshot ? "Last received data. " : "Not connected. "}
          </strong>
          {error}
        </div>
      )}
      {!snapshot && !error && (
        <div className="loading-lines" aria-label="Loading office data">
          <span />
          <span />
          <span />
        </div>
      )}
      {view === "approvals" && (
        <>
          <p className="section-description">
            Review the exact action and arguments below. Approving permits that
            tool to execute on the server.{" "}
            {directory?.execution.approval_resume
              ? "The run then continues from the saved result when safe. New actions require their own approvals."
              : "Check Runs for the saved result and available next steps."}
          </p>
          {actionError && (
            <div className="form-error" role="alert">
              {actionError}
              <button type="button" className="text-action" disabled={Boolean(decidingId)} onClick={() => void refreshDecisions()}>Check pending approvals</button>
            </div>
          )}
          {decisionNotice && (
            <div className="form-notice" role="status">
              {decisionNotice}
            </div>
          )}
          {snapshot && approvals.length === 0 && (
            <div className="large-empty">
              <span className="empty-icon">
                <Icon name="shield" size={30} />
              </span>
              <h3>Nothing waiting on you.</h3>
              <p>
                When an agent proposes a sensitive action, you’ll see the full
                request here before deciding.
              </p>
            </div>
          )}
          <div className="approvals-list">
            {approvals.map((approval) => (
              <article className="approval-card" key={approval.id}>
                <div className="approval-top">
                  <span className="approval-tool">
                    <Icon name="terminal" size={18} />
                    <strong>{approval.toolName}</strong>
                  </span>
                  <span className="risk-tag">{approval.risk} action</span>
                </div>
                <div className="approval-meta">
                  <span>
                    Requested {new Date(approval.requestedAt).toLocaleString()}
                  </span>
                  <span>ID: {approval.id}</span>
                </div>
                <h3 className="section-label">Exact arguments</h3>
                <pre
                  className="approval-args"
                  tabIndex={0}
                  aria-label={`Exact arguments for ${approval.toolName}`}
                >
                  {JSON.stringify(approval.args, null, 2)}
                </pre>
                <label className="review-checkbox">
                  <input
                    type="checkbox"
                    checked={
                      reviewed[approval.id] === JSON.stringify(approval.args)
                    }
                    onChange={(event) =>
                      setReviewed((current) => ({
                        ...current,
                        [approval.id]: event.target.checked
                          ? JSON.stringify(approval.args)
                          : "",
                      }))
                    }
                  />
                  I reviewed the complete arguments and want this action to run.
                </label>
                <div className="approval-actions">
                  <button
                    className="secondary-button danger"
                    disabled={Boolean(decidingId) || decisionLocks[approval.id] || connection !== "connected"}
                    onClick={() => void decideTool(approval.id, "deny")}
                  >
                    Deny action
                  </button>
                  <button
                    className="primary-button"
                    disabled={
                      Boolean(decidingId) ||
                      decisionLocks[approval.id] ||
                      connection !== "connected" ||
                      reviewed[approval.id] !== JSON.stringify(approval.args)
                    }
                    onClick={() => void decideTool(approval.id, "approve")}
                  >
                    <Icon name="check" size={15} />
                    {decidingId === approval.id
                      ? "Applying decision…"
                      : decisionLocks[approval.id]
                        ? "Check saved decision"
                        : "Approve & run"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        </>
      )}
      {view === "tasks" && (
        <>
          <p className="section-description">
            Actual runs and indexing jobs from your workspace. Execution happens
            within API requests; there is no background worker queue.
          </p>
          {snapshot && tasks.length === 0 ? (
            <div className="large-empty">
              <span className="empty-icon">
                <Icon name="activity" size={30} />
              </span>
              <h3>A clean slate.</h3>
              <p>
                Send a message or index a project to create your first task.
              </p>
            </div>
          ) : (
            <div className="task-list">
              {tasks.map((task) => (
                <article className="task-row" key={task.id}>
                  <span className="task-icon">
                    <Icon
                      name={task.status === "completed" ? "check" : "clock"}
                      size={18}
                    />
                  </span>
                  <div>
                    <h3>{task.title}</h3>
                    <small>{task.id}</small>
                    {typeof task.progress === "number" && (
                      <progress
                        value={Math.min(100, Math.max(0, task.progress))}
                        max={100}
                        aria-label="Task progress"
                      />
                    )}
                  </div>
                  <span className={`run-status run-status--${task.status}`}>
                    {task.status.replaceAll("_", " ")}
                  </span>
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
