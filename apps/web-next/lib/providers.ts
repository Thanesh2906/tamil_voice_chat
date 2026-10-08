import type { ProviderInfo } from "./contracts";

export const providerNames: Record<string, string> = {
  ollama: "Ollama",
  openai: "OpenAI",
  anthropic: "Claude",
  gemini: "Gemini",
  openrouter: "OpenRouter",
  groq: "Groq",
  xai: "Grok",
  self_hosted: "Self-hosted",
};
export const providerName = (name: string) => providerNames[name] ?? name;
export const providerAvailable = (
  provider: ProviderInfo,
  allowCloud: boolean,
) =>
  provider.configured !== false && (provider.privacy === "local" || allowCloud);
