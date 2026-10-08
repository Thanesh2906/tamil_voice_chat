"use client";

import { useState } from "react";
import { useOfficeData } from "./OfficeDataContext";
import { providerName } from "@/lib/providers";
import { Icon } from "./Icons";

const setup: Record<
  string,
  { variables: string[]; url?: string; description: string }
> = {
  ollama: {
    variables: ["LLM_BASE_URL", "LLM_MODEL"],
    url: "https://ollama.com/",
    description: "Run a local model through your Ollama service.",
  },
  openai: {
    variables: ["OPENAI_API_KEY", "OPENAI_MODEL"],
    url: "https://platform.openai.com/",
    description: "Connect OpenAI models through the server-side API.",
  },
  anthropic: {
    variables: ["ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"],
    url: "https://console.anthropic.com/",
    description: "Bring Claude into your manager and specialist conversations.",
  },
  gemini: {
    variables: ["GEMINI_API_KEY", "GEMINI_MODEL"],
    url: "https://aistudio.google.com/",
    description: "Use Google Gemini with your configured API access.",
  },
  openrouter: {
    variables: ["OPENROUTER_API_KEY", "OPENROUTER_MODEL"],
    url: "https://openrouter.ai/",
    description: "Choose open and proprietary models via one provider.",
  },
  groq: {
    variables: ["GROQ_API_KEY", "GROQ_MODEL"],
    url: "https://console.groq.com/",
    description: "Use Groq-hosted models for fast inference.",
  },
  xai: {
    variables: ["XAI_API_KEY", "XAI_MODEL"],
    url: "https://console.x.ai/",
    description: "Connect xAI’s Grok models. This is separate from Groq.",
  },
  self_hosted: {
    variables: [
      "SELF_HOSTED_BASE_URL",
      "SELF_HOSTED_MODEL",
      "SELF_HOSTED_API_KEY (if required)",
    ],
    description: "Use your own OpenAI-compatible model endpoint.",
  },
};

export function ProvidersPanel() {
  const { models, modelsError, refresh, refreshing } = useOfficeData();
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="providers-view">
      <section className="provider-intro panel">
        <div>
          <span className="eyebrow">YOUR TEAM, YOUR MODELS</span>
          <h2>Bring your own intelligence.</h2>
          <p>
            Choose a provider for each conversation. Your manager and
            specialists share the same safe execution layer.
          </p>
        </div>
        <div className="privacy-badge">
          <Icon name={models?.allow_cloud ? "globe" : "lock"} size={23} />
          <div>
            <strong>
              {models
                ? models.allow_cloud
                  ? "Cloud routing enabled"
                  : "Local-first workspace"
                : "Checking routing policy"}
            </strong>
            <span>
              {models?.allow_cloud
                ? "Configured cloud providers can receive prompts"
                : "Cloud providers are blocked until enabled on the server"}
            </span>
          </div>
        </div>
      </section>
      <div className="section-toolbar">
        <p className="section-description">
          Configuration status comes from your API. A configured key does not
          prove that a provider is reachable.
        </p>
        <button
          type="button"
          className="secondary-button"
          disabled={refreshing}
          onClick={() => void refresh()}
        >
          <Icon name="refresh" size={15} />
          {refreshing ? "Refreshing…" : "Refresh configuration"}
        </button>
      </div>
      {modelsError && (
        <p className="form-notice" role="status">
          {modelsError} Previously received configuration may be out of date.
        </p>
      )}
      {!models && !modelsError && (
        <div className="loading-lines" aria-label="Loading providers">
          <span />
          <span />
        </div>
      )}
      <div className="provider-grid">
        {models?.providers.map((provider) => {
          const info = setup[provider.name];
          const configured = provider.configured !== false;
          const allowed = provider.privacy === "local" || models.allow_cloud;
          return (
            <article className="provider-card panel" key={provider.name}>
              <div className="provider-card-top">
                <span
                  className={`provider-mark provider-mark--${provider.name}`}
                >
                  {provider.name === "self_hosted" ? (
                    <Icon name="terminal" size={25} />
                  ) : (
                    providerName(provider.name).slice(0, 1)
                  )}
                </span>
                <span
                  className={`configuration-tag ${configured ? "configured" : ""}`}
                >
                  {configured ? "Configured" : "Needs setup"}
                </span>
              </div>
              <h3>{providerName(provider.name)}</h3>
              <p>
                {info?.description ??
                  "An inference provider registered by your Jarvis server."}
              </p>
              <div className="provider-model">
                <span>DEFAULT MODEL</span>
                <b>{provider.default_model || "Not specified"}</b>
              </div>
              <div className="provider-facts">
                <span>
                  <Icon
                    name={provider.privacy === "local" ? "lock" : "globe"}
                    size={13}
                  />
                  {provider.privacy === "local" ? "Local / private" : "Cloud"}
                </span>
                <span>
                  {provider.verified ? "Verified by server" : "Not verified"}
                </span>
              </div>
              {configured && !allowed && (
                <p className="provider-policy-note">
                  Cloud routing is disabled by server policy.
                </p>
              )}
              <button
                className="provider-setup-button"
                type="button"
                aria-expanded={open === provider.name}
                onClick={() =>
                  setOpen((current) =>
                    current === provider.name ? null : provider.name,
                  )
                }
              >
                {configured ? "Configuration details" : "How to connect"}
                <Icon name="chevron" size={15} />
              </button>
              {open === provider.name && (
                <div className="provider-setup">
                  <p>
                    Set these variables in your API server’s environment, then
                    restart the API and LLM services:
                  </p>
                  <ul>
                    {(info?.variables ?? []).map((variable) => (
                      <li key={variable}>{variable}</li>
                    ))}
                  </ul>
                  {provider.privacy === "cloud" && (
                    <p>
                      Cloud requests also require LLM_ALLOW_CLOUD=true. Enable
                      it only if you want prompts sent to cloud providers.
                    </p>
                  )}
                  <p>
                    Keep keys on the server. Never put them in NEXT_PUBLIC
                    variables or browser storage.
                  </p>
                  {info?.url && (
                    <a href={info.url} target="_blank" rel="noreferrer">
                      Open {providerName(provider.name)} console{" "}
                      <Icon name="arrow" size={14} />
                    </a>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
      <section className="setup-note">
        <Icon name="shield" size={22} />
        <div>
          <h3>Credentials stay behind the scenes.</h3>
          <p>
            ChatGPT, Claude, Gemini, and Grok app subscriptions do not
            automatically provide API credentials or API billing. This dashboard
            reads non-secret configuration only. Provider keys are managed by
            your deployment environment, not stored in this browser. Changes to
            a self-hosted endpoint’s privacy classification must be made on the
            server.
          </p>
        </div>
      </section>
    </div>
  );
}
