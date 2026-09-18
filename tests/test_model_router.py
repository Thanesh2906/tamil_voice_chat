from __future__ import annotations

import pytest

from packages.common.config import Settings
from services.llm.providers import ProviderError
from services.llm.router import ModelRouter


def _settings(**overrides) -> Settings:
    return Settings(**{"jwt_secret": "x" * 32, **overrides})


def test_only_ollama_is_available_with_no_cloud_keys_configured() -> None:
    router = ModelRouter(_settings())
    names = {spec.name for spec in router.available()}
    assert names == {"ollama"}


def test_cloud_providers_register_only_when_their_key_is_set() -> None:
    router = ModelRouter(_settings(anthropic_api_key="sk-a", openai_api_key="sk-o"))
    names = {spec.name for spec in router.available()}
    assert names == {"ollama", "anthropic", "openai"}


def test_rag_mode_stays_local_even_when_cloud_is_allowed_by_default() -> None:
    router = ModelRouter(_settings(anthropic_api_key="sk-a", llm_allow_cloud=True))
    decision = router.resolve(mode="rag")
    assert decision.provider == "ollama"


def test_coding_mode_prefers_configured_cloud_model_when_allowed() -> None:
    router = ModelRouter(_settings(anthropic_api_key="sk-a", llm_allow_cloud=True))
    decision = router.resolve(mode="coding")
    assert decision.provider == "anthropic"
    assert decision.reason.startswith("default for mode=")


def test_coding_mode_falls_back_to_ollama_when_cloud_is_not_allowed() -> None:
    router = ModelRouter(_settings(anthropic_api_key="sk-a", llm_allow_cloud=False))
    decision = router.resolve(mode="coding")
    assert decision.provider == "ollama"


def test_explicit_cloud_provider_is_refused_without_allow_cloud() -> None:
    router = ModelRouter(_settings(anthropic_api_key="sk-a", llm_allow_cloud=False))
    with pytest.raises(ProviderError):
        router.resolve(mode="personal", requested_provider="anthropic")


def test_explicit_cloud_provider_succeeds_with_per_request_allow_cloud_override() -> None:
    router = ModelRouter(_settings(anthropic_api_key="sk-a", llm_allow_cloud=False))
    decision = router.resolve(mode="personal", requested_provider="anthropic", allow_cloud=True)
    assert decision.provider == "anthropic"
    assert decision.reason == "explicit request"


def test_unknown_provider_raises() -> None:
    router = ModelRouter(_settings())
    with pytest.raises(ProviderError):
        router.resolve(mode="personal", requested_provider="does-not-exist")


def test_personal_mode_auto_selects_a_cloud_model_once_allowed() -> None:
    router = ModelRouter(_settings(anthropic_api_key="sk-a", openai_api_key="sk-o", llm_allow_cloud=True))
    decision = router.resolve(mode="personal")
    assert decision.provider == "anthropic"  # strongest configured provider wins automatically


def test_personal_mode_falls_back_to_ollama_when_no_cloud_key_is_configured() -> None:
    router = ModelRouter(_settings(llm_allow_cloud=True))
    decision = router.resolve(mode="personal")
    assert decision.provider == "ollama"


def test_explicit_model_overrides_provider_default() -> None:
    router = ModelRouter(_settings())
    decision = router.resolve(mode="personal", requested_provider="ollama", requested_model="custom:tag")
    assert decision.model == "custom:tag"
