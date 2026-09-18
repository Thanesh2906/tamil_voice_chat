"use client";

import { useEffect, useState } from "react";
import { ApiError, getRun, listRuns } from "@/lib/api";
import type { RunOut, RunSummary } from "@/lib/contracts";
import { friendlyEvent } from "@/lib/runEvents";

export function RunsPanel() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunOut | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    listRuns()
      .then((list) => {
        setRuns(list);
        setError(null);
      })
      .catch((reason: unknown) => setError(reason instanceof ApiError ? reason.message : "Could not load runs."));
  };

  useEffect(() => {
    refresh();
  }, []);

  const open = async (runId: string) => {
    if (openId === runId) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(runId);
    setLoading(true);
    try {
      setDetail(await getRun(runId));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not load that run.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="side-card panel" aria-labelledby="runs-title">
      <div className="side-card__heading">
        <h3 className="section-label" id="runs-title">Recent runs</h3>
        <button type="button" className="link-button" onClick={refresh}>Refresh</button>
      </div>

      {error && <p className="muted">{error}</p>}
      {!error && runs.length === 0 && <p className="muted">No runs yet — send a chat message.</p>}
      <div className="runs-list">
        {runs.map((run) => (
          <div key={run.id}>
            <button type="button" className="run-row" onClick={() => void open(run.id)}>
              <span>{run.input_preview || "(empty)"}</span>
              <b className={`run-status run-status--${run.status}`}>{run.status.replace("_", " ")}</b>
            </button>
            {openId === run.id && (
              <div className="run-detail">
                {loading && <p className="muted">Loading…</p>}
                {!loading && detail && detail.events.length === 0 && <p className="muted">No events recorded.</p>}
                {!loading && detail?.events.map((event) => {
                  const friendly = friendlyEvent(event);
                  return (
                    <div className="run-event" key={event.sequence}>
                      <span>{event.sequence}</span>
                      <b>{event.type}</b>
                      <p>{friendly?.label ?? JSON.stringify(event.data)}</p>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
