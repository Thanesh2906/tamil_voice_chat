"""Configured model routing with an explicit cloud privacy boundary.

Ollama is configured by default; registration does not mean it is reachable.
Remote models require both server configuration and existing cloud consent.
The optional self-hosted endpoint is treated as cloud unless the operator
explicitly attests that it is a private runtime under their control.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from packages.common.config import Settings
from services.llm.providers import (
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
    ProviderAdapter,
    ProviderError,
    ProviderSpec,
    SelfHostedProvider,
    XAIProvider,
)

# Ordered provider preference per mode, used only when the caller does not name
# a provider explicitly. Cloud providers are listed ahead of Ollama for
# "personal"/"coding" so that, once LLM_ALLOW_CLOUD is on, the strongest
# configured model answers automatically rather than always falling to the
# small local model; Ollama stays last as the configured-by-default fallback when
# no cloud provider is configured or cloud is disabled. "rag" and "monitoring"
# stay local-only regardless, because they carry project/infrastructure content.
#
# "personal" puts Groq first: it is the fast-answer provider (see
# GroqProvider), and personal chat/voice is exactly the "live interaction"
# path where perceived latency matters most. "coding" still prefers quality
# (Anthropic/OpenAI/OpenRouter) ahead of raw speed, with Groq as a faster
# fallback before Ollama.
MODE_PREFERENCE: dict[str, list[str]] = {
    "coding": ["anthropic", "openai", "openrouter", "xai", "groq", "self_hosted", "ollama"],
    "personal": ["groq", "anthropic", "openai", "gemini", "xai", "openrouter", "self_hosted", "ollama"],
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
        self._catalog: dict[str, ProviderSpec] = {}
        self._build()

    def _register(self, name: str, adapter: ProviderAdapter, *, privacy: str, default_model: str) -> None:
        self._providers[name] = adapter
        self._specs[name] = ProviderSpec(
            name=name, privacy=privacy, default_model=default_model, configured=True
        )
        self._catalog[name] = self._specs[name]

    def _build(self) -> None:
        s = self.settings
        self._register("ollama", OllamaProvider(s.llm_base_url), privacy="local", default_model=s.llm_model)
        cloud_providers: list[tuple[str, str | None, str, Callable[[str], ProviderAdapter]]] = [
            ("anthropic", s.anthropic_api_key, s.anthropic_model, AnthropicProvider),
            ("openai", s.openai_api_key, s.openai_model, OpenAIProvider),
            ("gemini", s.gemini_api_key, s.gemini_model, GeminiProvider),
            ("openrouter", s.openrouter_api_key, s.openrouter_model, OpenRouterProvider),
            ("groq", s.groq_api_key, s.groq_model, GroqProvider),
            ("xai", s.xai_api_key, s.xai_model, XAIProvider),
        ]
        for name, key, model, adapter_type in cloud_providers:
            if key and key.strip() and model.strip():
                self._register(name, adapter_type(key.strip()), privacy="cloud", default_model=model)
            else:
                self._catalog[name] = ProviderSpec(
                    name=name, privacy="cloud", default_model=model,
                    configured=False, verification_status="not_configured",
                )
        privacy = "local" if s.self_hosted_local else "cloud"
        if s.self_hosted_base_url and s.self_hosted_model and s.self_hosted_model.strip():
            self._register(
                "self_hosted", SelfHostedProvider(s.self_hosted_base_url, s.self_hosted_api_key),
                privacy=privacy, default_model=s.self_hosted_model.strip(),
            )
        else:
            self._catalog["self_hosted"] = ProviderSpec(
                name="self_hosted", privacy=privacy, default_model=s.self_hosted_model or "",
                configured=False, verification_status="not_configured",
            )

    def catalog(self) -> list[ProviderSpec]:
        """All supported providers, including missing configuration, with no secrets.

        No live requests are made by discovery. `verified` must remain false
        until an explicit model-level check proves the particular capability.
        """
        return list(self._catalog.values())

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
