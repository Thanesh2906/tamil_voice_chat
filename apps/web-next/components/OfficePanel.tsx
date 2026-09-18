"use client";

import { useEffect, useState } from "react";
import { getOfficeSnapshot } from "@/lib/api";
import type { OfficeSnapshot } from "@/lib/contracts";

export function OfficePanel() {
  const [snapshot, setSnapshot] = useState<OfficeSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getOfficeSnapshot(controller.signal).then(setSnapshot).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Office API unavailable.");
    });
    return () => controller.abort();
  }, []);

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
            <div><h3 className="section-label">Task queue</h3>{snapshot.tasks.length ? snapshot.tasks.map((task) => <div className="list-row" key={task.id}><span>{task.title}</span><b>{task.status}</b></div>) : <p className="muted">Queue is empty.</p>}</div>
            <div><h3 className="section-label">Approvals</h3>{snapshot.approvals.length ? snapshot.approvals.map((approval) => <div className="list-row approval" key={approval.id}><span>{approval.summary}</span><b>{approval.risk}</b></div>) : <p className="muted">Nothing needs approval.</p>}</div>
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
