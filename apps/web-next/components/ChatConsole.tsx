"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, getModels, streamRun } from "@/lib/api";
import type { ProviderInfo, RunEventOut, RunStreamEvent } from "@/lib/contracts";
import { ActivityItem, friendlyEvent } from "@/lib/runEvents";
import { useWorkspace } from "./WorkspaceContext";

interface ChatTurn {
  id: string;
  role: "user" | "assistant" | "error";
  text: string;
  activity: ActivityItem[];
  streaming?: boolean;
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
  const abortRef = useRef<AbortController | null>(null);

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

  useEffect(() => () => abortRef.current?.abort(), []);

  const updateAssistantTurn = (id: string, patch: Partial<ChatTurn> | ((turn: ChatTurn) => Partial<ChatTurn>)) => {
    setTurns((current) =>
      current.map((turn) => (turn.id === id ? { ...turn, ...(typeof patch === "function" ? patch(turn) : patch) } : turn)),
    );
  };

  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = message.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    setMessage("");
    const assistantId = `a-${Date.now()}`;
    setTurns((current) => [
      ...current,
      { id: `u-${Date.now()}`, role: "user", text, activity: [] },
      { id: assistantId, role: "assistant", text: "", activity: [], streaming: true },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;

    const onEvent = (streamEvent: RunStreamEvent) => {
      if (streamEvent.sseEvent === "token") {
        const chunk = String(streamEvent.data.text ?? "");
        updateAssistantTurn(assistantId, (turn) => ({ text: turn.text + chunk }));
        return;
      }
      if (streamEvent.sseEvent === "run.completed") {
        updateAssistantTurn(assistantId, { text: String(streamEvent.data.answer ?? "") });
        return;
      }
      if (streamEvent.sseEvent === "run.failed") {
        updateAssistantTurn(assistantId, { role: "error", text: String(streamEvent.data.error ?? "That run failed.") });
        return;
      }
      if (streamEvent.sseEvent === "end") {
        if (streamEvent.data.status === "awaiting_approval") {
          updateAssistantTurn(assistantId, {
            text: "I want to take an action that needs your approval first — see Approvals in AI Office below.",
          });
        }
        return;
      }
      if (streamEvent.sseEvent === "error") {
        updateAssistantTurn(assistantId, { role: "error", text: "Could not reach Jarvis." });
        return;
      }
      // Every other frame is a real RunEvent (run.started, run.classified,
      // retrieval.completed, tool.completed, model.selected, ...): show it as
      // a live activity chip using the same formatting RunsPanel uses for
      // history, so a run looks the same whether you watch it happen or
      // reopen it afterward.
      const asRunEvent: RunEventOut = {
        sequence: streamEvent.sequence ?? 0,
        type: streamEvent.sseEvent,
        data: streamEvent.data,
        created_at: "",
      };
      const chip = friendlyEvent(asRunEvent);
      if (chip) {
        updateAssistantTurn(assistantId, (turn) => ({ activity: [...turn.activity, chip] }));
      }
    };

    try {
      await streamRun(
        text,
        { provider: provider || undefined, projectId: projectId || undefined },
        onEvent,
        controller.signal,
      );
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(reason instanceof ApiError ? reason.message : "Could not reach Jarvis.");
      }
    } finally {
      updateAssistantTurn(assistantId, { streaming: false });
      setBusy(false);
    }
  };

  return (
    <section className="chat-console panel" aria-labelledby="chat-title">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">Text channel · live {projectName ? `· ${projectName}` : ""}</p>
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
            <p>
              {turn.text}
              {turn.streaming && <span className="cursor-blink" aria-hidden="true" />}
            </p>
            {turn.activity.length > 0 && (
              <div className="chat-activity">
                {turn.activity.map((item) => (
                  <span className={`activity-chip ${item.pending ? "pending" : ""}`} key={item.key}>{item.label}</span>
                ))}
              </div>
            )}
          </div>
        ))}
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
