"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, notifyWorkspaceChanged, streamRun } from "@/lib/api";
import type { RunEventOut, RunStreamEvent } from "@/lib/contracts";
import { ActivityItem, friendlyEvent } from "@/lib/runEvents";
import { providerAvailable, providerName } from "@/lib/providers";
import { useWorkspace } from "./WorkspaceContext";
import { useOfficeData } from "./OfficeDataContext";
import { Icon, agentIcon } from "./Icons";

interface ChatTurn {
  id: string;
  role: "user" | "assistant" | "error";
  text: string;
  activity: ActivityItem[];
  streaming?: boolean;
  approval?: boolean;
}
const EMPTY_TURNS: ChatTurn[] = [];
const suggestions: Record<
  string,
  { title: string; detail: string; prompt: string }[]
> = {
  manager: [
    {
      title: "Make a plan",
      detail: "Turn an idea into a clear next step",
      prompt:
        "Help me break down a project idea into a practical plan. First, ask me what I want to build.",
    },
    {
      title: "Understand my project",
      detail: "Find the context that matters",
      prompt:
        "Help me understand the selected project's structure and suggest where to start. Use only sources you can actually access.",
    },
    {
      title: "Think it through",
      detail: "A second perspective on a decision",
      prompt:
        "Help me think through a decision. Ask me about my options, priorities, and constraints.",
    },
  ],
  coder: [
    {
      title: "Review some code",
      detail: "Spot bugs and explain tradeoffs",
      prompt:
        "Help me review code in the selected project. Ask which files or change to focus on.",
    },
    {
      title: "Investigate a bug",
      detail: "Work from evidence to a fix",
      prompt:
        "Help me debug an issue. Ask for the error, expected behavior, and steps to reproduce.",
    },
  ],
  researcher: [
    {
      title: "Explore a question",
      detail: "Evidence, context, and caveats",
      prompt:
        "Help me research a question using the project sources available to you. Ask what I need to understand.",
    },
    {
      title: "Compare approaches",
      detail: "Build a reasoned comparison",
      prompt:
        "Help me compare a few approaches. Ask me for the options and evaluation criteria.",
    },
  ],
  operator: [
    {
      title: "Check a system",
      detail: "Inspect the signals available",
      prompt:
        "Help me check system health using your available monitoring tools. Explain any access limitations.",
    },
    {
      title: "Plan a safe change",
      detail: "Small steps with clear checks",
      prompt:
        "Help me plan a safe operational change with verification and rollback steps. Ask me what needs changing.",
    },
  ],
  writer: [
    {
      title: "Find the right words",
      detail: "A first draft with a clear purpose",
      prompt:
        "Help me draft something. Ask about the audience, purpose, tone, and key points.",
    },
    {
      title: "Make it clearer",
      detail: "Give an existing draft a fresh look",
      prompt:
        "Help me edit a draft for clarity and concision. Ask me to share the text and intended audience.",
    },
  ],
};

export function ChatConsole({
  agentId,
  onAgentChange,
  onApprovals,
}: {
  agentId: string;
  onAgentChange: (id: string) => void;
  onApprovals: () => void;
}) {
  const { projectId, projectName } = useWorkspace();
  const { directory, models, modelsError, agentsError } = useOfficeData();
  const [providerByAgent, setProviderByAgent] = useState<
    Record<string, string>
  >({});
  const provider = providerByAgent[agentId] ?? "";
  const threadKey = `${agentId}:${provider}:${projectId ?? "personal"}`;
  const [conversations, setConversations] = useState<
    Record<string, ChatTurn[]>
  >({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [activeThread, setActiveThread] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const sessions = useRef<Record<string, string>>({});
  const logRef = useRef<HTMLDivElement | null>(null);
  const turns = conversations[threadKey] ?? EMPTY_TURNS;
  const message = drafts[threadKey] ?? "";
  const busy = activeThread !== null;
  const agent = directory?.agents.find((item) => item.id === agentId);
  const name = agent?.name ?? "Jarvis";
  const chosenProvider = models?.providers.find(
    (item) => item.name === provider,
  );
  const unavailableProvider = Boolean(
    provider &&
      (!chosenProvider ||
        !providerAvailable(chosenProvider, models?.allow_cloud ?? false)),
  );
  const prompts = suggestions[agentId] ?? suggestions.manager;

  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [turns]);

  const updateTurn = (
    key: string,
    id: string,
    patch: Partial<ChatTurn> | ((turn: ChatTurn) => Partial<ChatTurn>),
  ) => {
    setConversations((current) => ({
      ...current,
      [key]: (current[key] ?? []).map((turn) =>
        turn.id === id
          ? { ...turn, ...(typeof patch === "function" ? patch(turn) : patch) }
          : turn,
      ),
    }));
  };

  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = message.trim();
    if (
      !text ||
      busyRef.current ||
      unavailableProvider ||
      agent?.available === false
    )
      return;
    busyRef.current = true;
    setActiveThread(threadKey);
    setDrafts((current) => ({ ...current, [threadKey]: "" }));
    const assistantId = crypto.randomUUID();
    setConversations((current) => ({
      ...current,
      [threadKey]: [
        ...(current[threadKey] ?? []),
        { id: crypto.randomUUID(), role: "user", text, activity: [] },
        {
          id: assistantId,
          role: "assistant",
          text: "",
          activity: [],
          streaming: true,
        },
      ],
    }));
    const controller = new AbortController();
    abortRef.current = controller;
    const sessionId = sessions.current[threadKey] ?? crypto.randomUUID();
    sessions.current[threadKey] = sessionId;
    let terminal = false;
    const onEvent = (event: RunStreamEvent) => {
      if (controller.signal.aborted) return;
      if (event.sseEvent === "token") {
        updateTurn(threadKey, assistantId, (turn) => ({
          text: turn.text + String(event.data.text ?? ""),
        }));
        return;
      }
      if (event.sseEvent === "run.completed") {
        terminal = true;
        updateTurn(threadKey, assistantId, {
          text: String(
            event.data.answer ?? "Run completed without a text response.",
          ),
        });
        return;
      }
      if (event.sseEvent === "run.failed") {
        terminal = true;
        updateTurn(threadKey, assistantId, {
          role: "error",
          text: String(event.data.error ?? "That run failed."),
        });
        return;
      }
      if (event.sseEvent === "end") {
        terminal = true;
        if (event.data.status === "awaiting_approval")
          updateTurn(threadKey, assistantId, {
            text: "An action needs your decision. Review its exact arguments in Approvals before allowing it to run.",
            approval: true,
          });
        return;
      }
      if (event.sseEvent === "error") {
        terminal = true;
        updateTurn(threadKey, assistantId, {
          role: "error",
          text: String(event.data.detail ?? "Could not complete this request."),
        });
        return;
      }
      if (event.sseEvent === "run.started") notifyWorkspaceChanged();
      const asRunEvent: RunEventOut = {
        sequence: event.sequence ?? 0,
        type: event.sseEvent,
        data: event.data,
        created_at: "",
      };
      const chip = friendlyEvent(asRunEvent);
      if (chip)
        updateTurn(threadKey, assistantId, (turn) => ({
          activity: [...turn.activity, chip],
        }));
    };
    try {
      await streamRun(
        text,
        {
          provider: provider || undefined,
          projectId: projectId || undefined,
          agentId,
          sessionId,
        },
        onEvent,
        controller.signal,
      );
      if (!terminal && !controller.signal.aborted)
        updateTurn(threadKey, assistantId, {
          role: "error",
          text: "The stream ended before a final result arrived. Check Runs for the saved status.",
        });
    } catch (reason) {
      updateTurn(threadKey, assistantId, {
        role: "error",
        text: controller.signal.aborted
          ? "Stopped receiving this response. The server may still be working; check Runs for the final status."
          : reason instanceof ApiError
            ? reason.message
            : "Connection interrupted. Check Runs before sending the request again.",
      });
    } finally {
      updateTurn(threadKey, assistantId, { streaming: false });
      busyRef.current = false;
      setActiveThread(null);
      abortRef.current = null;
      notifyWorkspaceChanged();
    }
  };

  return (
    <section className="chat-console panel" aria-labelledby="chat-title">
      <header className="chat-heading">
        <span className={`agent-avatar agent-avatar--${agentId}`}>
          <Icon name={agentIcon(agentId)} size={22} />
        </span>
        <div>
          <h2 id="chat-title">
            {name}
            <span className="subtle-tag">
              {agentId === "manager" ? "Manager" : "Specialist"}
            </span>
          </h2>
          <p>{agent?.role ?? "Your personal AI workspace"}</p>
        </div>
        <button
          type="button"
          className="chat-new-button"
          disabled={busy || turns.length === 0}
          onClick={() => {
            setConversations((current) => ({ ...current, [threadKey]: [] }));
            delete sessions.current[threadKey];
          }}
        >
          New chat
        </button>
      </header>
      <div className="chat-toolbar">
        <label>
          <span>Talk to</span>
          <select
            aria-label="Chat agent"
            value={agentId}
            onChange={(event) => onAgentChange(event.target.value)}
          >
            {directory?.agents.length ? (
              directory.agents.map((item) => (
                <option
                  key={item.id}
                  value={item.id}
                  disabled={item.available === false}
                >
                  {item.name} · {item.role}
                  {item.available === false ? " · access required" : ""}
                </option>
              ))
            ) : (
              <option value="manager">Jarvis manager</option>
            )}
          </select>
        </label>
        <label>
          <span>Provider</span>
          <select
            aria-label="Chat provider"
            value={provider}
            onChange={(event) =>
              setProviderByAgent((current) => ({
                ...current,
                [agentId]: event.target.value,
              }))
            }
          >
            <option value="">Auto routing</option>
            {models?.providers.map((item) => (
              <option
                key={item.name}
                value={item.name}
                disabled={!providerAvailable(item, models.allow_cloud)}
              >
                {providerName(item.name)}
                {item.configured === false
                  ? " · needs setup"
                  : item.privacy === "cloud" && !models.allow_cloud
                    ? " · cloud disabled"
                    : ""}
              </option>
            ))}
          </select>
        </label>
      </div>
      {(modelsError ||
        agentsError ||
        unavailableProvider ||
        agent?.available === false) && (
        <p className="form-notice" role="status">
          {agent?.available === false
            ? "This specialist requires additional permissions from your workspace administrator."
            : unavailableProvider
              ? "This provider is no longer available. Choose another provider or Auto routing."
              : (modelsError ?? agentsError)}
        </p>
      )}
      <div
        className="chat-log"
        ref={logRef}
        role="log"
        aria-label={`${name} conversation`}
        aria-live="polite"
        aria-relevant="additions text"
      >
        {turns.length === 0 ? (
          <div className="chat-welcome">
            <span className="welcome-spark">
              <Icon name={agentIcon(agentId)} size={34} />
            </span>
            <p className="eyebrow">A SPACE TO THINK, BUILD & DO</p>
            <h3>
              {agentId === "manager" ? (
                <>
                  Big ideas start
                  <br />
                  with a conversation.
                </>
              ) : (
                `Let's ${agentId === "coder" ? "build something." : agentId === "researcher" ? "find clarity." : agentId === "writer" ? "get it into words." : "work through it."}`
              )}
            </h3>
            <p>
              {agent?.description ??
                "Share what you have in mind. We’ll figure out a useful next step."}
            </p>
            <div className="prompt-grid">
              {prompts.map((prompt) => (
                <button
                  type="button"
                  key={prompt.title}
                  onClick={() =>
                    setDrafts((current) => ({
                      ...current,
                      [threadKey]: prompt.prompt,
                    }))
                  }
                >
                  <span>
                    {prompt.title}
                    <Icon name="arrow" size={15} />
                  </span>
                  <small>{prompt.detail}</small>
                </button>
              ))}
            </div>
          </div>
        ) : (
          turns.map((turn) => (
            <article className={`chat-turn ${turn.role}`} key={turn.id}>
              <div className="turn-author">
                <span
                  className={`turn-avatar ${turn.role === "user" ? "turn-avatar--user" : `agent-avatar--${agentId}`}`}
                >
                  {turn.role === "user" ? (
                    "Y"
                  ) : (
                    <Icon name={agentIcon(agentId)} size={14} />
                  )}
                </span>
                {turn.role === "user"
                  ? "You"
                  : turn.role === "error"
                    ? "Run update"
                    : name}
                {turn.streaming && <small>WORKING</small>}
              </div>
              <div className="turn-content">
                <p>
                  {turn.text}
                  {turn.streaming && (
                    <span className="cursor-blink" aria-hidden="true" />
                  )}
                </p>
                {turn.activity.length > 0 && (
                  <div className="chat-activity">
                    {turn.activity.map((item, index) => (
                      <span
                        className={`activity-chip ${item.pending ? "pending" : ""}`}
                        key={`${item.key}-${index}`}
                      >
                        {item.label}
                      </span>
                    ))}
                  </div>
                )}
                {turn.approval && (
                  <button
                    type="button"
                    className="text-action"
                    onClick={onApprovals}
                  >
                    Review approval <Icon name="arrow" size={14} />
                  </button>
                )}
              </div>
            </article>
          ))
        )}
      </div>
      <form className="chat-composer" onSubmit={send}>
        <textarea
          rows={2}
          value={message}
          onChange={(event) =>
            setDrafts((current) => ({
              ...current,
              [threadKey]: event.target.value,
            }))
          }
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={`Message ${name} in Tamil or English…`}
          aria-label="Message"
        />
        <div className="composer-bottom">
          <span className="composer-context">
            <Icon name="folder" size={14} />
            {projectName ?? "Personal workspace"}
          </span>
          {busy ? (
            <button
              type="button"
              className="stop-button"
              onClick={() => abortRef.current?.abort()}
            >
              <Icon name="stop" size={13} /> Stop stream
            </button>
          ) : (
            <button
              className="send-button"
              aria-label="Send message"
              disabled={
                !message.trim() ||
                unavailableProvider ||
                agent?.available === false ||
                (!directory && Boolean(agentsError))
              }
            >
              <Icon name="arrow" size={19} />
            </button>
          )}
        </div>
      </form>
      <div className="composer-footnote">
        <span>
          {busy && activeThread !== threadKey
            ? "Another conversation is running. You can keep drafting here."
            : "Enter to send · Shift + Enter for a new line"}
        </span>
        <span>Actions stay under your control</span>
      </div>
    </section>
  );
}
