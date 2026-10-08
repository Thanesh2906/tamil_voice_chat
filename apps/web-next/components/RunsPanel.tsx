"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getRun, listRuns, WORKSPACE_CHANGED_EVENT } from "@/lib/api";
import type { RunOut, RunSummary } from "@/lib/contracts";
import { friendlyEvent, runAnswerText } from "@/lib/runEvents";
import { Icon } from "./Icons";

export function RunsPanel() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunOut | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const listController = useRef<AbortController | null>(null);
  const detailGeneration = useRef(0);
  const detailController = useRef<AbortController | null>(null);

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
    if (!openId) return;
    const generation = ++detailGeneration.current;
    const load = async () => {
      detailController.current?.abort();
      const controller = new AbortController();
      detailController.current = controller;
      try {
        const next = await getRun(openId, controller.signal);
        if (
          !controller.signal.aborted &&
          generation === detailGeneration.current
        ) {
          setDetail(next);
          setDetailError(null);
        }
      } catch (reason) {
        if (
          !controller.signal.aborted &&
          generation === detailGeneration.current
        )
          setDetailError(
            reason instanceof ApiError
              ? reason.message
              : "Could not load that run.",
          );
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 8000);
    return () => {
      detailController.current?.abort();
      window.clearInterval(timer);
    };
  }, [openId]);

  const toggle = (runId: string) => {
    // Invalidate immediately, before React commits the new selection. A late
    // response for A can never appear under B or reopen a dismissed detail.
    detailGeneration.current++;
    detailController.current?.abort();
    setOpenId((current) => (current === runId ? null : runId));
    setDetail(null);
    setDetailError(null);
  };
  const filteredRuns = runs.filter(
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
          onClick={() => void refresh()}
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
                  <p className="form-error" role="alert">
                    {detailError}
                  </p>
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
