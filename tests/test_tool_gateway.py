from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from packages.common import get_settings
from services.tools import adapters, gateway
from services.tools.adapters import ToolExecutionError
from services.tools.gateway import ToolPolicyError

_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatching


def _mock_client_factory(handler):
    def factory(*, timeout=None, **_ignored):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    return factory


class _FakeProcess:
    """Stands in for asyncio's subprocess handle so docker tests don't need a
    real Docker daemon (or even the docker CLI) available in the test env."""

    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        return None


def test_unknown_tool_is_denied() -> None:
    with pytest.raises(ToolPolicyError):
        gateway.validate("delete_everything", {})


def test_invalid_arguments_are_rejected() -> None:
    with pytest.raises(ToolPolicyError):
        gateway.validate("read_file", {"path": 123})


def test_read_tools_are_classified_read_risk() -> None:
    args_by_tool = {
        "list_dir": {"path": "."},
        "read_file": {"path": "."},
        "search_text": {"path": ".", "query": "jarvis"},
        "git_status": {"path": "."},
        "git_diff": {"path": "."},
    }
    for name, args in args_by_tool.items():
        call = gateway.validate(name, args)
        assert call.spec.risk == "read"


def test_write_and_exec_tools_require_approval() -> None:
    write_call = gateway.validate("write_file", {"path": "x.txt", "content": "hi"})
    assert write_call.spec.risk == "write"
    exec_call = gateway.validate("run_command", {"cwd": ".", "command": "git", "args": ["status"]})
    assert exec_call.spec.risk == "exec"


@pytest.mark.asyncio
async def test_read_file_rejects_path_outside_roots(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    call = gateway.validate("read_file", {"path": str(outside)})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[str(tmp_path)], sensitive_globs=[])


@pytest.mark.asyncio
async def test_read_file_returns_real_content_inside_roots(tmp_path: Path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("hello jarvis", encoding="utf-8")
    call = gateway.validate("read_file", {"path": str(target)})
    result = await gateway.execute(call, roots=[str(tmp_path)], sensitive_globs=[])
    assert result["content"] == "hello jarvis"


@pytest.mark.asyncio
async def test_write_file_rejects_sensitive_glob(tmp_path: Path) -> None:
    call = gateway.validate("write_file", {"path": str(tmp_path / ".env"), "content": "SECRET=1"})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[str(tmp_path)], sensitive_globs=["*.env", ".env*"])


@pytest.mark.asyncio
async def test_write_file_actually_writes_inside_roots(tmp_path: Path) -> None:
    call = gateway.validate("write_file", {"path": str(tmp_path / "out.txt"), "content": "written"})
    result = await gateway.execute(call, roots=[str(tmp_path)], sensitive_globs=[])
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "written"
    assert result["bytes_written"] == len(b"written")


@pytest.mark.asyncio
async def test_run_command_rejects_command_not_on_allowlist(tmp_path: Path) -> None:
    call = gateway.validate("run_command", {"cwd": str(tmp_path), "command": "curl", "args": ["evil.example"]})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[str(tmp_path)], sensitive_globs=[])


@pytest.mark.asyncio
async def test_run_command_executes_allowlisted_binary(tmp_path: Path) -> None:
    call = gateway.validate("run_command", {"cwd": str(tmp_path), "command": "python", "args": ["-c", "print(21*2)"]})
    result = await gateway.execute(call, roots=[str(tmp_path)], sensitive_globs=[])
    assert result["exit_code"] == 0
    assert "42" in result["stdout"]


def test_docker_tools_are_classified_read_risk() -> None:
    assert gateway.validate("docker_ps", {}).spec.risk == "read"
    assert gateway.validate("docker_logs", {"container": "web-1"}).spec.risk == "read"


def test_docker_logs_rejects_a_container_name_that_looks_like_a_flag() -> None:
    with pytest.raises(ToolPolicyError):
        gateway.validate("docker_logs", {"container": "--rm"})


@pytest.mark.asyncio
async def test_docker_ps_parses_the_container_listing(monkeypatch) -> None:
    async def fake_exec(*argv, **_ignored):
        assert argv[:2] == ("docker", "ps")
        return _FakeProcess(b"abc123\tnginx:latest\tUp 2 hours\tweb\n")

    monkeypatch.setattr(adapters.asyncio, "create_subprocess_exec", fake_exec)
    call = gateway.validate("docker_ps", {})
    result = await gateway.execute(call, roots=[], sensitive_globs=[])
    assert result["containers"] == [{"id": "abc123", "image": "nginx:latest", "status": "Up 2 hours", "name": "web"}]


@pytest.mark.asyncio
async def test_docker_ps_includes_all_flag_when_requested(monkeypatch) -> None:
    captured: dict = {}

    async def fake_exec(*argv, **_ignored):
        captured["argv"] = argv
        return _FakeProcess(b"")

    monkeypatch.setattr(adapters.asyncio, "create_subprocess_exec", fake_exec)
    call = gateway.validate("docker_ps", {"all_containers": True})
    await gateway.execute(call, roots=[], sensitive_globs=[])
    assert "--all" in captured["argv"]


@pytest.mark.asyncio
async def test_docker_logs_passes_tail_and_container_as_separate_argv_elements(monkeypatch) -> None:
    captured: dict = {}

    async def fake_exec(*argv, **_ignored):
        captured["argv"] = argv
        return _FakeProcess(b"log line 1\nlog line 2\n")

    monkeypatch.setattr(adapters.asyncio, "create_subprocess_exec", fake_exec)
    call = gateway.validate("docker_logs", {"container": "web-1", "tail": 50})
    result = await gateway.execute(call, roots=[], sensitive_globs=[])
    assert captured["argv"] == ("docker", "logs", "--tail", "50", "web-1")
    assert "log line 1" in result["logs"]


@pytest.mark.asyncio
async def test_docker_logs_reports_a_clear_error_when_docker_cli_is_missing(monkeypatch) -> None:
    async def fake_exec(*argv, **_ignored):
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(adapters.asyncio, "create_subprocess_exec", fake_exec)
    call = gateway.validate("docker_logs", {"container": "web-1"})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[], sensitive_globs=[])


# ---------------------------------------------------------------------------
# Docker write actions -- allowlist-gated, exec risk.
# ---------------------------------------------------------------------------

def test_docker_action_tools_are_classified_exec_risk() -> None:
    for name in ("docker_start", "docker_stop", "docker_restart"):
        assert gateway.validate(name, {"container": "web"}).spec.risk == "exec"


@pytest.mark.asyncio
async def test_docker_start_rejects_container_not_on_allowlist(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "docker_allowed_containers", [])
    call = gateway.validate("docker_start", {"container": "web"})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[], sensitive_globs=[])


@pytest.mark.asyncio
async def test_docker_stop_executes_when_container_is_allowlisted(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "docker_allowed_containers", ["web"])
    captured: dict = {}

    async def fake_exec(*argv, **_ignored):
        captured["argv"] = argv
        return _FakeProcess(b"web\n", returncode=0)

    monkeypatch.setattr(adapters.asyncio, "create_subprocess_exec", fake_exec)
    call = gateway.validate("docker_stop", {"container": "web"})
    result = await gateway.execute(call, roots=[], sensitive_globs=[])
    assert captured["argv"] == ("docker", "stop", "web")
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_docker_restart_raises_when_docker_reports_failure(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "docker_allowed_containers", ["web"])

    async def fake_exec(*argv, **_ignored):
        return _FakeProcess(b"", stderr=b"no such container: web", returncode=1)

    monkeypatch.setattr(adapters.asyncio, "create_subprocess_exec", fake_exec)
    call = gateway.validate("docker_restart", {"container": "web"})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[], sensitive_globs=[])


# ---------------------------------------------------------------------------
# GitHub -- allowlist-gated on every risk tier, not just writes.
# ---------------------------------------------------------------------------

def test_github_tools_risk_classification() -> None:
    assert gateway.validate("github_list_issues", {"repo": "me/proj"}).spec.risk == "read"
    assert gateway.validate("github_create_issue", {"repo": "me/proj", "title": "x"}).spec.risk == "write"
    assert gateway.validate("github_comment_issue", {"repo": "me/proj", "issue_number": 1, "body": "x"}).spec.risk == "write"


@pytest.mark.asyncio
async def test_github_list_issues_rejects_repo_not_on_allowlist(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "github_token", "gh-test")
    monkeypatch.setattr(get_settings(), "github_allowed_repos", [])
    call = gateway.validate("github_list_issues", {"repo": "me/proj"})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[], sensitive_globs=[])


@pytest.mark.asyncio
async def test_github_list_issues_rejects_when_token_missing(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "github_token", None)
    monkeypatch.setattr(get_settings(), "github_allowed_repos", ["me/proj"])
    call = gateway.validate("github_list_issues", {"repo": "me/proj"})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[], sensitive_globs=[])


@pytest.mark.asyncio
async def test_github_list_issues_filters_out_pull_requests(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "github_token", "gh-test")
    monkeypatch.setattr(get_settings(), "github_allowed_repos", ["me/proj"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer gh-test"
        return httpx.Response(200, json=[
            {"number": 1, "title": "bug", "state": "open", "html_url": "https://x/1"},
            {"number": 2, "title": "a pr", "state": "open", "html_url": "https://x/2", "pull_request": {}},
        ])

    monkeypatch.setattr(adapters.httpx, "AsyncClient", _mock_client_factory(handler))
    call = gateway.validate("github_list_issues", {"repo": "me/proj"})
    result = await gateway.execute(call, roots=[], sensitive_globs=[])
    assert [issue["number"] for issue in result["issues"]] == [1]


@pytest.mark.asyncio
async def test_github_create_issue_posts_and_returns_url(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "github_token", "gh-test")
    monkeypatch.setattr(get_settings(), "github_allowed_repos", ["me/proj"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(201, json={"number": 42, "html_url": "https://github.com/me/proj/issues/42"})

    monkeypatch.setattr(adapters.httpx, "AsyncClient", _mock_client_factory(handler))
    call = gateway.validate("github_create_issue", {"repo": "me/proj", "title": "Bug", "body": "details"})
    result = await gateway.execute(call, roots=[], sensitive_globs=[])
    assert result == {"repo": "me/proj", "number": 42, "url": "https://github.com/me/proj/issues/42"}


# ---------------------------------------------------------------------------
# Email -- allowlist-gated recipients, exact address or "*@domain" wildcard.
# ---------------------------------------------------------------------------

def test_send_email_tool_is_write_risk() -> None:
    call = gateway.validate("send_email", {"to": "a@example.com", "subject": "hi", "body": "hello"})
    assert call.spec.risk == "write"


@pytest.mark.asyncio
async def test_send_email_rejects_recipient_not_on_allowlist(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")
    monkeypatch.setattr(get_settings(), "email_allowed_recipients", [])
    call = gateway.validate("send_email", {"to": "a@example.com", "subject": "hi", "body": "hello"})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[], sensitive_globs=[])


@pytest.mark.asyncio
async def test_send_email_rejects_when_smtp_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "smtp_host", None)
    call = gateway.validate("send_email", {"to": "a@example.com", "subject": "hi", "body": "hello"})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[], sensitive_globs=[])


@pytest.mark.asyncio
async def test_send_email_accepts_wildcard_domain_recipient_and_sends(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")
    monkeypatch.setattr(get_settings(), "smtp_port", 587)
    monkeypatch.setattr(get_settings(), "smtp_username", "bot@example.com")
    monkeypatch.setattr(get_settings(), "smtp_password", "secret")
    monkeypatch.setattr(get_settings(), "smtp_from", "bot@example.com")
    monkeypatch.setattr(get_settings(), "email_allowed_recipients", ["*@example.com"])

    sent = {}

    def fake_send_sync(host, port, username, password, sender, to, subject, body):
        sent.update(host=host, port=port, to=to, subject=subject, body=body)

    monkeypatch.setattr(adapters, "_send_email_sync", fake_send_sync)
    call = gateway.validate("send_email", {"to": "someone@example.com", "subject": "hi", "body": "hello"})
    result = await gateway.execute(call, roots=[], sensitive_globs=[])
    assert result["sent"] is True
    assert sent["to"] == "someone@example.com"
    assert sent["host"] == "smtp.example.com"


@pytest.mark.asyncio
async def test_send_email_rejects_exact_mismatch_even_with_similar_domain(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")
    monkeypatch.setattr(get_settings(), "email_allowed_recipients", ["me@example.com"])
    call = gateway.validate("send_email", {"to": "notme@example.com", "subject": "hi", "body": "hello"})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[], sensitive_globs=[])


# ---------------------------------------------------------------------------
# SSH -- allowlist-gated hosts, command restricted to the same set as local
# run_command, arguments shell-quoted into one remote command string.
# ---------------------------------------------------------------------------

def test_ssh_run_tool_is_exec_risk() -> None:
    call = gateway.validate("ssh_run", {"host": "deploy@example.com", "command": "git", "args": ["status"]})
    assert call.spec.risk == "exec"


@pytest.mark.asyncio
async def test_ssh_run_rejects_host_not_on_allowlist(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ssh_allowed_hosts", [])
    call = gateway.validate("ssh_run", {"host": "deploy@example.com", "command": "git", "args": ["status"]})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[], sensitive_globs=[])


@pytest.mark.asyncio
async def test_ssh_run_rejects_command_not_on_allowlist(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ssh_allowed_hosts", ["deploy@example.com"])
    call = gateway.validate("ssh_run", {"host": "deploy@example.com", "command": "curl", "args": []})
    with pytest.raises(ToolExecutionError):
        await gateway.execute(call, roots=[], sensitive_globs=[])


@pytest.mark.asyncio
async def test_ssh_run_shell_quotes_arguments_into_one_remote_command_string(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ssh_allowed_hosts", ["deploy@example.com"])
    captured: dict = {}

    async def fake_exec(*argv, **_ignored):
        captured["argv"] = argv
        return _FakeProcess(b"ok\n")

    monkeypatch.setattr(adapters.asyncio, "create_subprocess_exec", fake_exec)
    call = gateway.validate(
        "ssh_run",
        {"host": "deploy@example.com", "command": "git", "args": ["commit", "-m", "hello; rm -rf /"]},
    )
    await gateway.execute(call, roots=[], sensitive_globs=[])
    argv = captured["argv"]
    assert argv[0] == "ssh"
    assert argv[-2] == "deploy@example.com"
    remote_command = argv[-1]
    # The dangerous-looking argument must be a single shell-quoted token, not
    # something that lets the remote shell see an unescaped ";".
    assert "'hello; rm -rf /'" in remote_command
