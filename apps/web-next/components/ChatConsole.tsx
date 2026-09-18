"use client";

import { FormEvent, useEffect, useState } from "react";
import { ApiError, createRun, getModels } from "@/lib/api";
import type { ProviderInfo, RunOut } from "@/lib/contracts";
import { ActivityItem, runActivity, runAnswerText } from "@/lib/runEvents";
import { useWorkspace } from "./WorkspaceContext";

interface ChatTurn {
  id: string;
  role: "user" | "assistant" | "error";
  text: string;
  activity?: ActivityItem[];
}

function summarize(run: RunOut): ChatTurn {
  const activity = runActivity(run);
  if (run.status === "failed") {
    return { id: run.id, role: "error", text: runAnswerText(run), activity };
  }
  return { id: run.id, role: "assistant", text: runAnswerText(run), activity };
}

export function ChatConsole() {
  const { projectId, projectName } = useWorkspace();
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [message, setMessage] = useState("");
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [allowCloud, setAllowCloud] = useState(false);
  const [provider, setProvider] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getModels(controller.signal)
      .then((response) => {
        setProviders(response.providers);
        setAllowCloud(response.allow_cloud);
      })
      .catch(() => {
        /* the model picker just stays on "Auto" if this fails */
      });
    return () => controller.abort();
  }, []);

  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = message.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    setMessage("");
    setTurns((current) => [...current, { id: `u-${Date.now()}`, role: "user", text }]);
    try {
      const run = await createRun(text, { provider: provider || undefined, projectId: projectId || undefined });
      setTurns((current) => [...current, summarize(run)]);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not reach Jarvis.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="chat-console panel" aria-labelledby="chat-title">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">Text channel {projectName ? `· ${projectName}` : ""}</p>
          <h2 id="chat-title">Chat with Jarvis</h2>
        </div>
        <div className="chat-picker">
          <label htmlFor="model-select" className="muted">Model</label>
          <select id="model-select" value={provider} onChange={(event) => setProvider(event.target.value)}>
            <option value="">Auto (recommended)</option>
            {providers.map((item) => (
              <option key={item.name} value={item.name} disabled={item.privacy === "cloud" && !allowCloud}>
                {item.name} · {item.default_model} {item.privacy === "cloud" && !allowCloud ? "(cloud disabled)" : ""}
              </option>
            ))}
          </select>
        </div>
      </header>

      {!projectId && <p className="chat-hint">No project selected — Jarvis will still read files/run commands, but won&apos;t search indexed project sources. Pick one in Projects.</p>}

      <div className="chat-log" aria-live="polite">
        {turns.length === 0 && <p className="chat-empty">Ask Jarvis anything — it can read project files and propose actions on its own.</p>}
        {turns.map((turn) => (
          <div className={`chat-turn ${turn.role}`} key={turn.id}>
            <span>{turn.role === "user" ? "You" : turn.role === "error" ? "Error" : "Jarvis"}</span>
            <p>{turn.text}</p>
            {turn.activity && turn.activity.length > 0 && (
              <div className="chat-activity">
                {turn.activity.map((item) => (
                  <span className={`activity-chip ${item.pending ? "pending" : ""}`} key={item.key}>{item.label}</span>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="chat-turn assistant"><span>Jarvis</span><p className="muted">Thinking…</p></div>}
      </div>

      {error && <div className="form-error" role="alert">{error}</div>}

      <form className="chat-input-row" onSubmit={send}>
        <input
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Type in Tamil or English…"
          disabled={busy}
          aria-label="Message"
        />
        <button className="mic" disabled={busy || !message.trim()}>Send</button>
      </form>
    </section>
  );
}
