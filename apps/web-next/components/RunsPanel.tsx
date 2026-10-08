"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, listRuns, WORKSPACE_CHANGED_EVENT } from "@/lib/api";
import type { RunSummary } from "@/lib/contracts";
import { friendlyEvent, runAnswerText } from "@/lib/runEvents";
import { Icon } from "./Icons";
import { RunControls } from "./RunControls";
import { useRunData } from "./RunDataContext";

export function RunsPanel() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const { savedRuns, runErrors, refreshRun } = useRunData();
  const detail = openId ? savedRuns[openId] : null;
  const detailError = openId ? runErrors[openId] : null;
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const listController = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    try {
      const list = await listRuns(controller.signal);
      if (!controller.signal.aborted) {
        setRuns(list);
        setError(null);
      }
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(
          reason instanceof ApiError
            ? reason.message
            : "Could not load runs. Displayed results may be out of date.",
        );
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 8000);
    const changed = () => void refresh();
    window.addEventListener(WORKSPACE_CHANGED_EVENT, changed);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
      listController.current?.abort();
      window.removeEventListener(WORKSPACE_CHANGED_EVENT, changed);
    };
  }, [refresh]);

  useEffect(() => {
    if (openId) void refreshRun(openId);
  }, [openId, refreshRun]);

  const toggle = (runId: string) => {
    // Details are keyed by run ID, so late responses cannot change selection.
    setOpenId((current) => (current === runId ? null : runId));
  };
  const filteredRuns = runs.map((run) => ({ ...run, ...savedRuns[run.id] })).filter(
    (run) =>
      filter === "all" ||
      (filter === "attention"
        ? ["failed", "paused", "awaiting_approval"].includes(run.status)
        : run.status === filter),
  );

  return (
    <section className="runs-panel panel" aria-labelledby="runs-title">
      <header className="section-heading">
        <div>
          <p className="eyebrow">EVERY STEP, ACCOUNTED FOR</p>
          <h2 id="runs-title">Run history</h2>
        </div>
        <button
          type="button"
          className="secondary-button"
          onClick={() => { void refresh(); if (openId) void refreshRun(openId); }}
        >
          <Icon name="refresh" size={15} />
          Refresh
        </button>
      </header>
      <div className="filter-tabs" aria-label="Filter runs">
        {[
          ["all", "All runs"],
          ["running", "Running"],
          ["attention", "Needs attention"],
          ["completed", "Completed"],
          ["cancelled", "Cancelled"],
        ].map(([value, label]) => (
          <button
            type="button"
            key={value}
            aria-pressed={filter === value}
            className={filter === value ? "active" : ""}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && (
        <p className="form-notice" role="status">
          {error}
        </p>
      )}
      {loading && <p className="muted">Loading recorded runs…</p>}
      {!loading && filteredRuns.length === 0 && (
        <div className="large-empty">
          <span className="empty-icon">
            <Icon name="activity" size={28} />
          </span>
          <h3>
            {runs.length
              ? "No runs in this view."
              : "Your work leaves a trail."}
          </h3>
          <p>
            {runs.length
              ? "Try another filter to see more of your history."
              : "Start a conversation to see its saved result, model choice, and tool activity here."}
          </p>
        </div>
      )}
      <div className="runs-list">
        {filteredRuns.map((run) => (
          <article
            className={`run-item ${openId === run.id ? "expanded" : ""}`}
            key={run.id}
          >
            <button
              type="button"
              className="run-row"
              aria-expanded={openId === run.id}
              aria-controls={`run-${run.id}`}
              onClick={() => toggle(run.id)}
            >
              <span className="run-icon">
                <Icon name="activity" size={18} />
              </span>
              <span className="run-copy">
                <strong>{run.input_preview || "Untitled run"}</strong>
                <small>
                  {run.agent_id ?? "manager"} <span>·</span>{" "}
                  {new Date(run.created_at).toLocaleString()}
                </small>
              </span>
              <span className={`run-status run-status--${run.status}`}>
                {run.status.replaceAll("_", " ")}
              </span>
              <Icon name="chevron" className="run-chevron" size={16} />
            </button>
            {openId === run.id && (
              <div className="run-detail" id={`run-${run.id}`}>
                {detailError ? (
                  <div>
                    <p className="form-error" role="alert">{detailError}</p>
                    <button type="button" className="text-action" onClick={() => void refreshRun(run.id)}>Refresh saved status</button>
                  </div>
                ) : !detail ? (
                  <p className="muted">Loading run details…</p>
                ) : (
                  <>
                    <div className="run-detail-meta">
                      <span>Run ID</span>
                      <span>{detail.id}</span>
                    </div>
                    {runAnswerText(detail) && (
                      <div className="saved-answer">
                        <h3 className="section-label">Saved response</h3>
                        <p>{runAnswerText(detail)}</p>
                      </div>
                    )}
                    <RunControls run={detail} />
                    <h3 className="section-label">Event timeline</h3>
                    {detail.events.length === 0 && (
                      <p className="muted">No events recorded.</p>
                    )}
                    {detail.events.map((event) => (
                      <details className="run-event" key={event.sequence}>
                        <summary>
                          <span className="event-sequence">
                            {String(event.sequence).padStart(2, "0")}
                          </span>
                          <span>
                            <b>{event.type.replaceAll(".", " · ")}</b>
                            <small>
                              {friendlyEvent(event)?.label ??
                                "View recorded event data"}
                            </small>
                          </span>
                          <Icon name="chevron" size={14} />
                        </summary>
                        <pre>{JSON.stringify(event.data, null, 2)}</pre>
                      </details>
                    ))}
                  </>
                )}
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
