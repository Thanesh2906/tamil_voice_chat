"""Tool execution. Every function here is what actually runs on this machine
once the gateway has validated arguments and (for write/exec tools) a human
has approved the exact invocation. No tool may claim success from model text:
every result here comes from a real filesystem or subprocess call.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import smtplib
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Awaitable, Callable

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
STREAM_CHUNK_BYTES = 8192
MAX_DIRECTORY_ENTRIES = 1000
MAX_SEARCH_ENTRIES = 10_000
MAX_SEARCH_FILES = 2000
MAX_SEARCH_FILE_BYTES = 1_000_000
MAX_SEARCH_BYTES = 10_000_000
MAX_SEARCH_SECONDS = 5.0
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


@contextmanager
def _open_regular_file(target: Path, *, write: bool = False):
    """Open an already-authorized canonical path without following swapped links.

    Walk directory descriptors with O_NOFOLLOW too, so replacing a parent with
    a symlink between validation and open cannot redirect file reads/writes.
    This is defense in depth for filesystem tools, not a host-code sandbox.
    """
    if os.name != "posix":
        raise ToolExecutionError("safe file access requires POSIX no-follow opens")
    directory = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY)
    descriptor = None
    try:
        for part in target.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        flags = (os.O_WRONLY | os.O_CREAT) if write else os.O_RDONLY
        descriptor = os.open(
            target.name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ToolExecutionError("path is not a regular file")
        if write and metadata.st_nlink != 1:
            raise ToolExecutionError("writing multiply-linked files is not allowed")
        if write:
            os.ftruncate(descriptor, 0)
        with os.fdopen(descriptor, "wb" if write else "rb") as handle:
            descriptor = None
            yield handle
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


async def list_dir(args: ListDirArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    try:
        target = resolve_authorized(args.path, roots, sensitive_globs)
        if not target.is_dir():
            raise ToolExecutionError("path is not a directory")
        entries: list[dict[str, Any]] = []
        truncated = False
        with os.scandir(target) as children:
            for visited, child in enumerate(children):
                if visited >= MAX_SEARCH_ENTRIES or len(entries) >= MAX_DIRECTORY_ENTRIES:
                    truncated = True
                    break
                try:
                    path = resolve_authorized(child.path, roots, sensitive_globs)
                    metadata = path.stat()
                except (PathSecurityError, OSError):
                    continue
                if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                    continue
                entries.append({
                    "name": child.name,
                    "type": "dir" if stat.S_ISDIR(metadata.st_mode) else "file",
                    "size": metadata.st_size if stat.S_ISREG(metadata.st_mode) else None,
                })
        entries.sort(key=lambda item: item["name"].lower())
        return {"path": str(target), "entries": entries, "truncated": truncated}
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc


async def read_file(args: ReadFileArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    try:
        target = resolve_authorized(args.path, roots, sensitive_globs)
        if target.suffix.lower() not in TEXT_EXTENSIONS:
            raise ToolExecutionError(f"unsupported file type: {target.suffix or '(none)'}")
        with _open_regular_file(target) as handle:
            raw = handle.read(args.max_bytes + 1)
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    return {
        "path": str(target),
        "content": raw[:args.max_bytes].decode("utf-8", errors="replace"),
        "truncated": len(raw) > args.max_bytes,
    }


def _search_candidates(target: Path, recursive: bool, sensitive_globs: list[str], budget: dict):
    if target.is_file():
        yield target
        return
    pending = [target]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as children:
                for child in children:
                    budget["entries"] += 1
                    if budget["entries"] > MAX_SEARCH_ENTRIES or time.monotonic() > budget["deadline"]:
                        budget["truncated"] = True
                        return
                    path = Path(child.path)
                    # Never recurse through symlinks or excluded dependency/VCS/secret trees.
                    if child.is_symlink() or is_sensitive(path, sensitive_globs):
                        continue
                    if child.is_dir(follow_symlinks=False):
                        if recursive:
                            pending.append(path)
                    elif child.is_file(follow_symlinks=False):
                        yield path
        except OSError:
            continue


async def search_text(args: SearchTextArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    try:
        target = resolve_authorized(args.path, roots, sensitive_globs)
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    query = args.query.lower()
    matches: list[dict] = []
    budget = {"entries": 0, "truncated": False, "deadline": time.monotonic() + MAX_SEARCH_SECONDS}
    bytes_read = files_read = output_bytes = 0
    candidates = _search_candidates(target, args.recursive, sensitive_globs, budget)
    try:
        for path in candidates:
            await asyncio.sleep(0)  # bounded scans must remain cancellable
            if (files_read >= MAX_SEARCH_FILES or bytes_read >= MAX_SEARCH_BYTES
                    or time.monotonic() > budget["deadline"]):
                budget["truncated"] = True
                break
            try:
                resolved = resolve_authorized(str(path), roots, sensitive_globs)
                if not resolved.is_relative_to(target) and resolved != target:
                    continue
                if resolved.suffix.lower() not in TEXT_EXTENSIONS:
                    continue
                limit = min(MAX_SEARCH_FILE_BYTES, MAX_SEARCH_BYTES - bytes_read)
                with _open_regular_file(resolved) as handle:
                    raw = handle.read(limit)
                    if os.fstat(handle.fileno()).st_size > limit:
                        budget["truncated"] = True
            except (PathSecurityError, OSError, ToolExecutionError):
                continue
            files_read += 1
            bytes_read += len(raw)
            for line_no, line in enumerate(raw.decode("utf-8", errors="ignore").splitlines(), start=1):
                if query in line.lower():
                    match = {"path": str(resolved), "line": line_no, "text": line.strip()[:300]}
                    output_bytes += len(str(match).encode("utf-8"))
                    if output_bytes > MAX_OUTPUT_BYTES:
                        budget["truncated"] = True
                        break
                    matches.append(match)
                    if len(matches) >= args.max_results:
                        budget["truncated"] = True
                        break
            if len(matches) >= args.max_results or output_bytes > MAX_OUTPUT_BYTES:
                break
    finally:
        candidates.close()
    return {"query": args.query, "matches": matches, "truncated": budget["truncated"]}


def _git_repository(target: Path, roots: list[str], sensitive_globs: list[str]) -> Path:
    """Find metadata without letting Git discover a repository above authorization."""
    for directory in [target, *target.parents]:
        try:
            resolve_authorized(str(directory), roots, sensitive_globs)
        except PathSecurityError:
            break
        metadata = directory / ".git"
        if metadata.exists() or metadata.is_symlink():
            # Linked worktrees, submodules and symlinked metadata can point at
            # objects/config outside the authorized tree; fail closed for now.
            if metadata.is_symlink() or not metadata.is_dir():
                raise ToolExecutionError("Git metadata must be a directory inside the authorized repository")
            return directory
    raise ToolExecutionError("no Git repository exists inside the authorized roots")


async def _run_git(args: GitReadArgs, *, roots: list[str], sensitive_globs: list[str], diff: bool) -> dict:
    try:
        target = resolve_authorized(args.path, roots, sensitive_globs)
        if not target.is_dir():
            raise ToolExecutionError("path is not a directory")
        repository = _git_repository(target, roots, sensitive_globs)
        # Explicit worktree/git-dir prevents core.worktree or repository discovery
        # from redirecting these reads. Suppress helpers that can execute code.
        git = [
            "git", "--no-pager", "--git-dir", str(repository / ".git"),
            "--work-tree", str(repository), "-c", "core.bare=false",
            "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
            "-c", "core.quotePath=true", "-c", "diff.renames=false",
            "-c", "status.renames=false", "-c", "core.hooksPath=/dev/null",
            "-c", "core.attributesFile=/dev/null", "-c", "core.excludesFile=/dev/null",
        ]
        # --no-ext-diff/--no-textconv do not disable clean/process filters:
        # Git can invoke them merely to compare a working file with its index.
        # Inspect key names only, then override every configured filter. Do not
        # follow include files or worktree config outside this verified config.
        config = repository / ".git" / "config"
        if config.is_symlink():
            raise ToolExecutionError("symlinked Git configuration is not allowed")
        keys = await _exec(
            ["git", "config", "--file", str(config), "--no-includes", "--null", "--name-only", "--list"],
            cwd=repository, timeout_seconds=30.0,
        )
        filters = set()
        for key in keys["stdout"].split("\0"):
            lowered = key.lower()
            if lowered == "include.path" or lowered.startswith("includeif.") or lowered == "extensions.worktreeconfig":
                raise ToolExecutionError("Git include/worktree configuration is not supported by read tools")
            if lowered.startswith("filter.") and "." in key[7:]:
                filters.add(key.rsplit(".", 1)[0])
        for driver in sorted(filters):
            git.extend(["-c", f"{driver}.clean=", "-c", f"{driver}.process=", "-c", f"{driver}.required=false"])
        relative = target.relative_to(repository).as_posix()
        scope = ":(top,literal)" + relative if relative != "." else ":(top)**"
        listing = await _exec(
            [*git, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", scope],
            cwd=repository, timeout_seconds=30.0,
        )
        paths = []
        for name in listing["stdout"].split("\0"):
            if not name or "\ufffd" in name:
                continue
            lexical = repository / name
            if is_sensitive(lexical, sensitive_globs):
                continue
            resolved = lexical.resolve(strict=False)
            if not resolved.is_relative_to(target) or is_sensitive(resolved, sensitive_globs):
                continue
            # Do not return symlink targets or submodule content, even when the
            # link currently resolves inside the permitted tree.
            if lexical.is_symlink() or (lexical.exists() and not lexical.is_file()):
                continue
            paths.append(":(top,literal)" + name)
        paths = sorted(set(paths))
        if not paths:
            return {"command": ["git", "diff" if diff else "status"], "cwd": str(target),
                    "exit_code": 0, "stdout": "", "stderr": ""}
        subcommand = (
            ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--ignore-submodules=all"]
            if diff else ["status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=all"]
        )
        return await _exec([*git, *subcommand, "--", *paths], cwd=repository, timeout_seconds=30.0)
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc


async def git_status(args: GitReadArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    return await _run_git(args, roots=roots, sensitive_globs=sensitive_globs, diff=False)


async def git_diff(args: GitReadArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    return await _run_git(args, roots=roots, sensitive_globs=sensitive_globs, diff=True)


async def write_file(args: WriteFileArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    try:
        target = resolve_authorized_for_write(args.path, roots, sensitive_globs)
        existed = target.exists()
        raw = args.content.encode("utf-8")
        with _open_regular_file(target, write=True) as handle:
            handle.write(raw)
    except (PathSecurityError, OSError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    return {"path": str(target), "bytes_written": len(raw), "overwrote": existed}


def _require_unsafe_host_execution() -> None:
    if not get_settings().tools_unsafe_host_execution:
        raise ToolExecutionError(
            "host execution is disabled; TOOLS_UNSAFE_HOST_EXECUTION=true is required "
            "for trusted administrators (command allowlists are not a sandbox)"
        )


async def run_command(args: RunCommandArgs, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    _require_unsafe_host_execution()
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
    if args.container not in get_settings().docker_allowed_containers:
        raise ToolExecutionError(f"container '{args.container}' is not on DOCKER_ALLOWED_CONTAINERS")
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
    _require_unsafe_host_execution()
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
        "--", args.host, remote_command,
    ]
    return await _exec(argv, cwd=Path.cwd(), timeout_seconds=args.timeout_seconds)


def _subprocess_environment(home: str) -> dict[str, str]:
    """Do not forward API credentials, cloud keys, agents, proxies or Git overrides."""
    return {
        "PATH": os.pathsep.join(dict.fromkeys([str(Path(sys.executable).parent), "/usr/local/bin", "/usr/bin", "/bin"])),
        "HOME": home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_PAGER": "cat",
    }


async def _stop_process(process, tasks: list[asyncio.Task]) -> None:
    # start_new_session makes the PID the process-group ID. Kill the group even
    # if its leader already exited: descendants may still hold stdout/stderr.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    async def discard(stream):
        while await stream.read(STREAM_CHUNK_BYTES):
            pass

    cleanup = asyncio.gather(discard(process.stdout), discard(process.stderr), process.wait())
    try:
        await asyncio.wait_for(cleanup, timeout=5.0)
    except asyncio.TimeoutError:
        # A deliberately detached descendant is beyond process-group cleanup.
        # Close our pipe transports rather than retaining an unbounded task.
        transport = getattr(process, "_transport", None)
        if transport is not None:
            transport.close()


async def _exec(argv: list[str], *, cwd: Path, timeout_seconds: float) -> dict:
    """Execute bounded argv with a clean environment and process-group cleanup.

    Structured argv prevents shell interpolation; it does NOT constrain what
    interpreters, package managers, Git aliases or project scripts can execute.
    Arbitrary dev commands therefore additionally require the explicit unsafe
    host-execution opt-in and administrator approval at the API boundary.
    """
    if os.name != "posix":
        raise ToolExecutionError("subprocess tools require POSIX process-group cleanup")
    with tempfile.TemporaryDirectory(prefix="jarvis-tool-home-") as home:
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env=_subprocess_environment(home),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=STREAM_CHUNK_BYTES,
        ))
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # Cancellation while spawning must not lose ownership of the PID.
            try:
                process = await spawn
            except OSError:
                raise asyncio.CancelledError from None
            await asyncio.shield(_stop_process(process, []))
            raise
        except OSError as exc:
            raise ToolExecutionError(f"could not start {argv[0]}: {exc}") from exc
        total = 0

        async def read_bounded(stream):
            nonlocal total
            output = bytearray()
            while chunk := await stream.read(STREAM_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    raise ToolExecutionError(f"command output exceeded {MAX_OUTPUT_BYTES} bytes")
                output.extend(chunk)
            return bytes(output)

        tasks = [asyncio.create_task(read_bounded(process.stdout)),
                 asyncio.create_task(read_bounded(process.stderr)),
                 asyncio.create_task(process.wait())]
        try:
            stdout, stderr, _ = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout_seconds)
        except BaseException as exc:
            await asyncio.shield(_stop_process(process, tasks))
            if isinstance(exc, asyncio.TimeoutError):
                raise ToolExecutionError(f"command timed out after {timeout_seconds}s") from exc
            raise
        # Do not leave a background child behind after a successful leader exit.
        await asyncio.shield(_stop_process(process, tasks))
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if process.returncode != 0:
            detail = _truncate((err or out).strip(), 2000)
            raise ToolExecutionError(f"{argv[0]} exited with code {process.returncode}: {detail}")
        return {"command": argv, "cwd": str(cwd), "exit_code": process.returncode,
                "stdout": out, "stderr": err}


EXECUTORS: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
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
