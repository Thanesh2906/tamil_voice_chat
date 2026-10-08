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


def test_groq_registers_only_when_configured() -> None:
    router = ModelRouter(_settings(groq_api_key="gsk-test"))
    names = {spec.name for spec in router.available()}
    assert "groq" in names


def test_personal_mode_prefers_groq_for_speed_when_configured() -> None:
    router = ModelRouter(
        _settings(groq_api_key="gsk-test", anthropic_api_key="sk-a", llm_allow_cloud=True)
    )
    decision = router.resolve(mode="personal")
    assert decision.provider == "groq"


def test_coding_mode_still_prefers_quality_over_groq_speed() -> None:
    router = ModelRouter(
        _settings(groq_api_key="gsk-test", anthropic_api_key="sk-a", llm_allow_cloud=True)
    )
    decision = router.resolve(mode="coding")
    assert decision.provider == "anthropic"


def test_catalog_distinguishes_configuration_from_verification() -> None:
    from dataclasses import asdict

    router = ModelRouter(_settings(xai_api_key="unit-fixture", xai_model="model-under-test"))
    catalog = {spec.name: asdict(spec) for spec in router.catalog()}
    assert set(catalog) == {"ollama", "anthropic", "openai", "gemini", "openrouter", "groq", "xai", "self_hosted"}
    assert catalog["xai"]["configured"] is True
    assert catalog["xai"]["verified"] is False
    assert catalog["xai"]["verification_status"] == "not_checked"
    assert catalog["groq"]["configured"] is False
    assert catalog["groq"]["verification_status"] == "not_configured"
    assert catalog["ollama"]["configured"] is True
    assert catalog["ollama"]["verified"] is False
    assert "unit-fixture" not in str(catalog)


def test_xai_and_groq_have_distinct_adapters_and_consent() -> None:
    router = ModelRouter(_settings(xai_api_key="unit-xai", groq_api_key="unit-groq"))
    assert router.adapter("xai").base_url == "https://api.x.ai/v1"
    assert router.adapter("groq").base_url == "https://api.groq.com/openai/v1"
    with pytest.raises(ProviderError, match="cloud models are disabled"):
        router.resolve(mode="personal", requested_provider="xai")
    assert router.resolve(mode="personal", requested_provider="xai", allow_cloud=True).provider == "xai"


def test_self_hosted_requires_url_and_model_but_not_a_key() -> None:
    incomplete = ModelRouter(_settings(self_hosted_base_url="http://runtime:8000/v1"))
    assert "self_hosted" not in {spec.name for spec in incomplete.available()}
    router = ModelRouter(_settings(self_hosted_base_url="http://runtime:8000/v1", self_hosted_model="model-under-test"))
    assert "self_hosted" in {spec.name for spec in router.available()}
    assert router.adapter("self_hosted").api_key is None


def test_self_hosted_is_cloud_until_explicitly_trusted_by_operator() -> None:
    config = dict(self_hosted_base_url="http://127.0.0.1:8000/v1", self_hosted_model="model-under-test")
    router = ModelRouter(_settings(**config))
    with pytest.raises(ProviderError, match="cloud models are disabled"):
        router.resolve(mode="rag", requested_provider="self_hosted")
    local = ModelRouter(_settings(**config, self_hosted_local=True))
    assert local.resolve(mode="coding", requested_provider="self_hosted").provider == "self_hosted"
    assert local.resolve(mode="rag").provider == "ollama"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "https://user:password@example.test/v1",
    "https://example.test/v1?key=private", "https://example.test/v1#private", "http:///missing-host",
    "https://example.test:bad/v1", "http://example.test:99999/v1", "http://example.test/with\nnewline",
])
def test_self_hosted_rejects_unsafe_configuration_urls(url: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(self_hosted_base_url=url)


def test_whitespace_key_is_not_configured() -> None:
    router = ModelRouter(_settings(xai_api_key="   "))
    assert "xai" not in {spec.name for spec in router.available()}


def test_provider_keys_are_not_in_settings_repr() -> None:
    settings = _settings(xai_api_key="unit-xai-private", self_hosted_api_key="unit-runtime-private")
    assert "unit-xai-private" not in repr(settings)
    assert "unit-runtime-private" not in repr(settings)


def test_unsafe_execution_is_off_by_default() -> None:
    assert _settings().tools_unsafe_host_execution is False
