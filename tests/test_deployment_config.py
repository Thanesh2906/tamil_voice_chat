from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

from packages.common.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_is_loadable_with_json_origins_and_safe_defaults() -> None:
    settings = Settings(_env_file=ROOT / ".env.example")
    assert "http://localhost:3001" in settings.allowed_origins
    assert settings.llm_allow_cloud is False
    assert settings.tools_unsafe_host_execution is False
    assert settings.self_hosted_local is False


def test_compose_forwards_backend_policy_and_keys_explicitly() -> None:
    services = yaml.safe_load((ROOT / "infra/docker-compose.yml").read_text())["services"]
    env = services["api"]["environment"]
    required = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY", "SELF_HOSTED_BASE_URL", "SELF_HOSTED_MODEL", "SELF_HOSTED_API_KEY", "SELF_HOSTED_LOCAL", "LLM_ALLOW_CLOUD", "RAG_ALLOWED_ROOTS", "TOOLS_UNSAFE_HOST_EXECUTION", "GITHUB_ALLOWED_REPOS", "EMAIL_ALLOWED_RECIPIENTS", "SSH_ALLOWED_HOSTS"}
    assert required <= env.keys()
    for key in required:
        assert "${" + key in env[key]
    assert "3001" in env["ALLOWED_ORIGINS"]
    assert services["rag"]["environment"]["RAG_ALLOWED_ROOTS"] == env["RAG_ALLOWED_ROOTS"]
    assert services["rag"]["volumes"] == ["project_workspace:/workspace:ro"]
    assert services["api"]["volumes"] == ["project_workspace:/workspace"]
    for name, service in services.items():
        if name != "api":
            assert not any(key.endswith("API_KEY") for key in service.get("environment", {}))
        assert not service.get("privileged", False)
        assert service.get("pid") != "host"
        assert not any(str(volume).startswith("/:") or "docker.sock" in str(volume) for volume in service.get("volumes", []))


def test_gitleaks_fixture_exception_is_exact_and_combined() -> None:
    config = tomllib.loads((ROOT / ".github/gitleaks.toml").read_text())
    assert config["extend"]["useDefault"] is True
    assert "allowlist" not in config and "allowlists" not in config
    assert len(config["rules"]) == 1
    rule = config["rules"][0]
    assert rule["id"] == "generic-api-key"
    allow = rule["allowlists"][0]
    assert allow["condition"] == "AND"
    assert allow["regexTarget"] == "line"
    assert len(allow["paths"]) == len(allow["regexes"]) == 1
    path = re.compile(allow["paths"][0])
    line = re.compile(allow["regexes"][0])
    assert path.fullmatch("tests/test_api.py")
    assert not path.fullmatch("services/api.py")
    fixture_line = next(value for value in (ROOT / "tests/test_api.py").read_text().splitlines() if value.strip().startswith("password = "))
    assert line.fullmatch(fixture_line)
    assert not line.fullmatch('    password = "an-unexpected-production-value"')


def test_api_image_runs_migrations_before_starting_server() -> None:
    dockerfile = (ROOT / "infra/Dockerfile").read_text()
    assert "COPY alembic.ini ./" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile
    assert "alembic upgrade head && exec uvicorn services.api:app" in dockerfile
