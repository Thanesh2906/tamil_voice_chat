"""Model router: picks a provider/model per request without crossing a privacy
boundary silently (docs/master-build-audit.md, Phase 3 security invariant).

Ollama is always registered because it needs no credential and is the private,
on-host default. Cloud providers (Anthropic/Claude, OpenAI/ChatGPT, Gemini,
OpenRouter) register only when their API key is configured. RAG and monitoring
modes carry project data and infrastructure data respectively, so by default
they never leave the host: `llm_allow_cloud` (or an explicit per-request
override) must be turned on before a cloud provider is even considered for them.
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.common.config import Settings
from services.llm.providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
    ProviderAdapter,
    ProviderError,
    ProviderSpec,
)

# Ordered provider preference per mode, used only when the caller does not name
# a provider explicitly. Cloud providers are listed ahead of Ollama for
# "personal"/"coding" so that, once LLM_ALLOW_CLOUD is on, the strongest
# configured model answers automatically rather than always falling to the
# small local model; Ollama stays last as the always-available fallback when
# no cloud provider is configured or cloud is disabled. "rag" and "monitoring"
# stay local-only regardless, because they carry project/infrastructure content.
MODE_PREFERENCE: dict[str, list[str]] = {
    "coding": ["anthropic", "openai", "openrouter", "ollama"],
    "personal": ["anthropic", "openai", "gemini", "openrouter", "ollama"],
    "rag": ["ollama"],
    "monitoring": ["ollama"],
}


@dataclass(slots=True)
class RoutingDecision:
    provider: str
    model: str
    reason: str


class ModelRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._providers: dict[str, ProviderAdapter] = {}
        self._specs: dict[str, ProviderSpec] = {}
        self._build()

    def _register(self, name: str, adapter: ProviderAdapter, *, privacy: str, default_model: str) -> None:
        self._providers[name] = adapter
        self._specs[name] = ProviderSpec(
            name=name, privacy=privacy, default_model=default_model, configured=True
        )

    def _build(self) -> None:
        s = self.settings
        self._register("ollama", OllamaProvider(s.llm_base_url), privacy="local", default_model=s.llm_model)
        if s.anthropic_api_key:
            self._register(
                "anthropic", AnthropicProvider(s.anthropic_api_key), privacy="cloud",
                default_model=s.anthropic_model,
            )
        if s.openai_api_key:
            self._register(
                "openai", OpenAIProvider(s.openai_api_key), privacy="cloud",
                default_model=s.openai_model,
            )
        if s.gemini_api_key:
            self._register(
                "gemini", GeminiProvider(s.gemini_api_key), privacy="cloud",
                default_model=s.gemini_model,
            )
        if s.openrouter_api_key:
            self._register(
                "openrouter", OpenRouterProvider(s.openrouter_api_key), privacy="cloud",
                default_model=s.openrouter_model,
            )

    def available(self) -> list[ProviderSpec]:
        """Non-secret metadata for a client-facing model picker."""
        return list(self._specs.values())

    def resolve(
        self,
        *,
        mode: str,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        allow_cloud: bool | None = None,
    ) -> RoutingDecision:
        cloud_allowed = self.settings.llm_allow_cloud if allow_cloud is None else allow_cloud

        if requested_provider:
            spec = self._specs.get(requested_provider)
            if not spec:
                raise ProviderError(
                    f"unknown or unconfigured provider '{requested_provider}'"
                )
            if spec.privacy == "cloud" and not cloud_allowed:
                raise ProviderError(
                    f"provider '{requested_provider}' would send this conversation off this "
                    "host; cloud models are disabled for this request"
                )
            return RoutingDecision(
                provider=requested_provider,
                model=requested_model or spec.default_model,
                reason="explicit request",
            )

        for name in MODE_PREFERENCE.get(mode, ["ollama"]):
            spec = self._specs.get(name)
            if not spec:
                continue
            if spec.privacy == "cloud" and not cloud_allowed:
                continue
            return RoutingDecision(
                provider=name,
                model=requested_model or spec.default_model,
                reason=f"default for mode={mode}",
            )
        raise ProviderError("no configured model provider is available for this mode")

    def adapter(self, provider: str) -> ProviderAdapter:
        return self._providers[provider]
