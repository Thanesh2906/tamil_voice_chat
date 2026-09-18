"""Tool execution. Every function here is what actually runs on this machine
once the gateway has validated arguments and (for write/exec tools) a human
has approved the exact invocation. No tool may claim success from model text:
every result here comes from a real filesystem or subprocess call.
"""

from __future__ import annotations

import asyncio
import shlex
import smtplib
from email.message import EmailMessage
from pathlib import Path

import httpx

from packages.common import get_settings
from packages.common.safe_paths import (
    PathSecurityError,
    is_sensitive,
    resolve_authorized,
    resolve_authorized_for_write,
)
from services.tools.registry import (
    ALLOWED_COMMANDS,
    DockerActionArgs,
    DockerLogsArgs,
    DockerPsArgs,
    GitHubCommentIssueArgs,
    GitHubCreateIssueArgs,
    GitHubListIssuesArgs,
    GitReadArgs,
    ListDirArgs,
    ReadFileArgs,
    RunCommandArgs,
    SearchTextArgs,
    SendEmailArgs,
    SshRunArgs,
    WriteFileArgs,
)

GITHUB_API = "https://api.github.com"

MAX_OUTPUT_BYTES = 200_000
TEXT_EXTENSIONS = {
    ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yml", ".yaml",
    ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb",
    ".php", ".sql", ".sh", ".toml", ".ini", ".cfg", ".dart", ".html", ".css",
}


class ToolExecutionError(RuntimeError):
    """A tool could not complete (bad path, disallowed command, subprocess failure)."""


def _truncate(text: str, limit: int = MAX_OUTPUT_BYTES) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n...[truncated]"


async def list_dir(args: ListDirArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    try:
        target = resolve_authorized(args.path, roots, sensitive_globs)
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    if not target.is_dir():
        raise ToolExecutionError("path is not a directory")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "size": stat.st_size if child.is_file() else None,
        })
    return {"path": str(target), "entries": entries}


async def read_file(args: ReadFileArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    try:
        target = resolve_authorized(args.path, roots, sensitive_globs)
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    if not target.is_file():
        raise ToolExecutionError("path is not a file")
    if target.suffix.lower() not in TEXT_EXTENSIONS:
        raise ToolExecutionError(f"unsupported file type: {target.suffix or '(none)'}")
    raw = target.read_bytes()[: args.max_bytes]
    return {
        "path": str(target),
        "content": raw.decode("utf-8", errors="replace"),
        "truncated": target.stat().st_size > args.max_bytes,
    }


async def search_text(args: SearchTextArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    try:
        target = resolve_authorized(args.path, roots, sensitive_globs)
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    query = args.query.lower()
    matches: list[dict] = []
    candidates: list[Path]
    if target.is_file():
        candidates = [target]
    else:
        iterator = target.rglob("*") if args.recursive else target.iterdir()
        candidates = [p for p in iterator if p.is_file()]
    for path in candidates:
        if len(matches) >= args.max_results:
            break
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_relative_to(target) and resolved != target:
            continue
        if is_sensitive(resolved, sensitive_globs) or resolved.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            text = resolved.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if query in line.lower():
                matches.append({"path": str(resolved), "line": line_no, "text": line.strip()[:300]})
                if len(matches) >= args.max_results:
                    break
    return {"query": args.query, "matches": matches, "truncated": len(matches) >= args.max_results}


async def _run_git(args: GitReadArgs, *, roots: list[str], sensitive_globs: list[str], subcommand: list[str]) -> dict:
    try:
        target = resolve_authorized(args.path, roots, sensitive_globs)
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    if not target.is_dir():
        raise ToolExecutionError("path is not a directory")
    return await _exec(["git", *subcommand], cwd=target, timeout_seconds=30.0)


async def git_status(args: GitReadArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    return await _run_git(args, roots=roots, sensitive_globs=sensitive_globs, subcommand=["status", "--porcelain=v1"])


async def git_diff(args: GitReadArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    return await _run_git(args, roots=roots, sensitive_globs=sensitive_globs, subcommand=["diff"])


async def write_file(args: WriteFileArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    try:
        target = resolve_authorized_for_write(args.path, roots, sensitive_globs)
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    existed = target.exists()
    target.write_text(args.content, encoding="utf-8")
    return {"path": str(target), "bytes_written": len(args.content.encode("utf-8")), "overwrote": existed}


async def run_command(args: RunCommandArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    if args.command not in ALLOWED_COMMANDS:
        raise ToolExecutionError(
            f"command '{args.command}' is not on the allowlist ({', '.join(sorted(ALLOWED_COMMANDS))})"
        )
    try:
        cwd = resolve_authorized(args.cwd, roots, sensitive_globs)
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    if not cwd.is_dir():
        raise ToolExecutionError("cwd is not a directory")
    return await _exec([args.command, *args.args], cwd=cwd, timeout_seconds=args.timeout_seconds)


async def docker_ps(args: DockerPsArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    """Read-only container listing. `roots`/`sensitive_globs` are accepted but
    unused -- this tool has no filesystem path to bound, unlike the others."""
    argv = ["docker", "ps", "--format", "{{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"]
    if args.all_containers:
        argv.insert(2, "--all")
    result = await _exec(argv, cwd=Path.cwd(), timeout_seconds=15.0)
    containers = []
    for line in result["stdout"].splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            containers.append({"id": parts[0], "image": parts[1], "status": parts[2], "name": parts[3]})
    return {"containers": containers, "exit_code": result["exit_code"], "stderr": result["stderr"]}


async def docker_logs(args: DockerLogsArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    result = await _exec(
        ["docker", "logs", "--tail", str(args.tail), args.container], cwd=Path.cwd(), timeout_seconds=15.0
    )
    return {"container": args.container, "logs": result["stdout"] + result["stderr"], "exit_code": result["exit_code"]}


async def _docker_action(args: DockerActionArgs, action: str) -> dict:
    settings = get_settings()
    if args.container not in settings.docker_allowed_containers:
        raise ToolExecutionError(f"container '{args.container}' is not on DOCKER_ALLOWED_CONTAINERS")
    result = await _exec(["docker", action, args.container], cwd=Path.cwd(), timeout_seconds=30.0)
    if result["exit_code"] != 0:
        raise ToolExecutionError(f"docker {action} failed: {(result['stderr'] or result['stdout']).strip()}")
    return result


async def docker_start(args: DockerActionArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    return await _docker_action(args, "start")


async def docker_stop(args: DockerActionArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    return await _docker_action(args, "stop")


async def docker_restart(args: DockerActionArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    return await _docker_action(args, "restart")


def _require_allowed_github_repo(repo: str) -> str:
    """Returns the token once repo + configuration are both verified, so
    callers can't accidentally make the request before checking either."""
    settings = get_settings()
    if not settings.github_token:
        raise ToolExecutionError("GitHub is not configured: set GITHUB_TOKEN")
    if repo not in settings.github_allowed_repos:
        raise ToolExecutionError(f"repo '{repo}' is not on GITHUB_ALLOWED_REPOS")
    return settings.github_token


def _github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _github_request(method: str, url: str, token: str, *, json: dict | None = None, params: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(method, url, headers=_github_headers(token), json=json, params=params)
            if response.status_code >= 400:
                raise ToolExecutionError(f"GitHub error {response.status_code}: {response.text[:300]!r}")
    except httpx.HTTPError as exc:
        raise ToolExecutionError(f"GitHub request failed: {exc}") from exc
    return response.json()


async def github_list_issues(args: GitHubListIssuesArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    token = _require_allowed_github_repo(args.repo)
    body = await _github_request(
        "GET", f"{GITHUB_API}/repos/{args.repo}/issues", token,
        params={"state": args.state, "per_page": 30},
    )
    issues = [
        {"number": item["number"], "title": item["title"], "state": item["state"], "url": item["html_url"]}
        for item in body if "pull_request" not in item
    ]
    return {"repo": args.repo, "issues": issues}


async def github_create_issue(args: GitHubCreateIssueArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    token = _require_allowed_github_repo(args.repo)
    created = await _github_request(
        "POST", f"{GITHUB_API}/repos/{args.repo}/issues", token,
        json={"title": args.title, "body": args.body},
    )
    return {"repo": args.repo, "number": created["number"], "url": created["html_url"]}


async def github_comment_issue(args: GitHubCommentIssueArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    token = _require_allowed_github_repo(args.repo)
    created = await _github_request(
        "POST", f"{GITHUB_API}/repos/{args.repo}/issues/{args.issue_number}/comments", token,
        json={"body": args.body},
    )
    return {"repo": args.repo, "issue_number": args.issue_number, "url": created["html_url"]}


def _recipient_allowed(to: str, allowed: list[str]) -> bool:
    to_lower = to.lower()
    for pattern in allowed:
        pattern = pattern.lower()
        if pattern.startswith("*@") and to_lower.endswith(pattern[1:]):
            return True
        if to_lower == pattern:
            return True
    return False


def _send_email_sync(host: str, port: int, username: str | None, password: str | None,
                     sender: str, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)


async def send_email(args: SendEmailArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    settings = get_settings()
    if not settings.smtp_host:
        raise ToolExecutionError("Email is not configured: set SMTP_HOST (and credentials)")
    to = str(args.to)
    if not _recipient_allowed(to, settings.email_allowed_recipients):
        raise ToolExecutionError(f"recipient '{to}' is not on EMAIL_ALLOWED_RECIPIENTS")
    sender = settings.smtp_from or settings.smtp_username or ""
    try:
        await asyncio.to_thread(
            _send_email_sync, settings.smtp_host, settings.smtp_port,
            settings.smtp_username, settings.smtp_password, sender, to, args.subject, args.body,
        )
    except (OSError, smtplib.SMTPException) as exc:
        raise ToolExecutionError(f"email send failed: {exc}") from exc
    return {"to": to, "subject": args.subject, "sent": True}


async def ssh_run(args: SshRunArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    settings = get_settings()
    if args.host not in settings.ssh_allowed_hosts:
        raise ToolExecutionError(f"host '{args.host}' is not on SSH_ALLOWED_HOSTS")
    if args.command not in ALLOWED_COMMANDS:
        raise ToolExecutionError(
            f"command '{args.command}' is not on the allowlist ({', '.join(sorted(ALLOWED_COMMANDS))})"
        )
    # Each token is shell-quoted before being joined into the single command
    # string ssh hands to the remote shell -- ssh itself re-interprets a
    # trailing argument list through $SHELL -c on the far end, so without this
    # an argument containing e.g. ";" would be remote-shell-injectable even
    # though no local shell is ever involved.
    remote_command = " ".join(shlex.quote(part) for part in [args.command, *args.args])
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        args.host, remote_command,
    ]
    return await _exec(argv, cwd=Path.cwd(), timeout_seconds=args.timeout_seconds)


async def _exec(argv: list[str], *, cwd: Path, timeout_seconds: float) -> dict:
    """Run argv as a real subprocess. Never through a shell: argv[0] is a fixed
    binary name and every other element is an opaque argument, so there is no
    string a model can produce that changes which program runs."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ToolExecutionError(f"command not found: {argv[0]}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ToolExecutionError(f"command timed out after {timeout_seconds}s") from exc
    return {
        "command": argv,
        "cwd": str(cwd),
        "exit_code": process.returncode,
        "stdout": _truncate(stdout.decode("utf-8", errors="replace")),
        "stderr": _truncate(stderr.decode("utf-8", errors="replace")),
    }


EXECUTORS = {
    "list_dir": list_dir,
    "read_file": read_file,
    "search_text": search_text,
    "git_status": git_status,
    "git_diff": git_diff,
    "write_file": write_file,
    "run_command": run_command,
    "docker_ps": docker_ps,
    "docker_logs": docker_logs,
    "docker_start": docker_start,
    "docker_stop": docker_stop,
    "docker_restart": docker_restart,
    "github_list_issues": github_list_issues,
    "github_create_issue": github_create_issue,
    "github_comment_issue": github_comment_issue,
    "send_email": send_email,
    "ssh_run": ssh_run,
}
