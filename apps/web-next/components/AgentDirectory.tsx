"use client";

import { useOfficeData } from "./OfficeDataContext";
import { Icon, agentIcon } from "./Icons";

export function AgentDirectory({
  onChat,
}: {
  onChat: (agentId: string) => void;
}) {
  const { directory, snapshot, agentsError, connection, refresh, refreshing } =
    useOfficeData();
  return (
    <div className="directory-view">
      <section className="directory-intro">
        <span className="eyebrow">GOOD WORK IS A TEAM EFFORT</span>
        <h2>
          The right perspective.
          <br />
          <span>For every kind of work.</span>
        </h2>
        <p>
          Start with your manager for a plan, or go straight to a specialist.
          Choose a model provider inside each conversation.
        </p>
      </section>
      {agentsError && (
        <div className="form-notice" role="status">
          {agentsError}
          <button
            type="button"
            className="text-action"
            disabled={refreshing}
            onClick={() => void refresh()}
          >
            Try again
          </button>
        </div>
      )}
      {!directory && !agentsError && (
        <div className="loading-lines" aria-label="Loading agent directory">
          <span />
          <span />
        </div>
      )}
      <div className="directory-grid">
        {directory?.agents.map((agent) => {
          const activity = snapshot?.agents.find(
            (item) => item.id === agent.id,
          );
          const status =
            agent.available === false
              ? "access required"
              : connection !== "connected"
                ? "unverified"
                : (activity?.status ?? "idle");
          return (
            <article
              className={`directory-card panel directory-card--${agent.id}`}
              key={agent.id}
            >
              <div className="directory-card-top">
                <span className={`agent-avatar agent-avatar--${agent.id}`}>
                  <Icon name={agentIcon(agent.id)} size={25} />
                </span>
                <span className={`agent-state agent-state--${status}`}>
                  <span className="tiny-dot" />
                  {status === "idle" ? "Ready" : status.replaceAll("_", " ")}
                </span>
              </div>
              <p className="eyebrow">
                {agent.id === "manager" ? "THE BIG PICTURE" : agent.role}
              </p>
              <h3>{agent.name}</h3>
              <p className="agent-description">{agent.description}</p>
              <div className="agent-capabilities">
                {agent.capabilities.map((capability) => (
                  <span key={capability}>
                    {capability.replaceAll("_", " ")}
                  </span>
                ))}
              </div>
              <div className="agent-task-note">
                <Icon name={agent.tools_enabled ? "shield" : "pen"} size={14} />
                <span>
                  {activity?.currentTask ??
                    (agent.tools_enabled
                      ? "Tools are policy-checked"
                      : "Focused conversation")}
                </span>
              </div>
              <button
                className={
                  agent.id === "manager" ? "primary-button" : "secondary-button"
                }
                type="button"
                disabled={agent.available === false}
                onClick={() => onChat(agent.id)}
              >
                {agent.available === false
                  ? "Administrator access required"
                  : `Talk to ${agent.name}`}
                <Icon name="arrow" size={16} />
              </button>
            </article>
          );
        })}
      </div>
      <section className="setup-note">
        <Icon name="team" size={23} />
        <div>
          <h3>Specialist roles, shared accountability.</h3>
          <p>
            These are selectable AI personas, not independently running workers.
            The manager can delegate within a request; each actual run and tool
            decision is recorded in your workspace.
          </p>
        </div>
      </section>
    </div>
  );
}
