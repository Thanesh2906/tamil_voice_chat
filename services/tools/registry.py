"""Tool registry: the one place that says which tools exist, what arguments
they accept, and how risky each one is (docs/master-build-audit.md, Phase 4).

Nothing here executes anything — see services/tools/adapters.py for that. This
module exists so the gateway can validate arguments with a real schema instead
of trusting whatever shape a model happened to produce, and so "what tools
exist and how dangerous are they" is auditable in one short file.

Risk levels:
  read  - fixed read operation; subject to root, scope and resource allowlists
  write - mutates a file; requires approval before it runs
  exec  - runs a subprocess; requires approval before it runs

There is deliberately no generic "run a shell string" tool. `run_command` takes
a command name plus a structured argument list, never a string handed to a
shell, and the command name must be on ALLOWED_COMMANDS below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from pydantic import BaseModel, EmailStr, Field

# Convenience allowlist only, NOT a sandbox: Python, Node, package managers,
# Git aliases and project scripts can run arbitrary code, read host files and
# use the network. Local/SSH execution is disabled unless an operator explicitly
# opts into unsafe host execution; the API also requires administrator approval.
ALLOWED_COMMANDS = {
    "git", "pytest", "python", "python3", "pip", "ruff", "mypy",
    "npm", "npx", "node", "flutter", "dart",
}


class ListDirArgs(BaseModel):
    path: str


class ReadFileArgs(BaseModel):
    path: str
    max_bytes: int = Field(default=200_000, gt=0, le=2_000_000)


class SearchTextArgs(BaseModel):
    path: str
    query: str = Field(min_length=1, max_length=500)
    recursive: bool = True
    max_results: int = Field(default=200, gt=0, le=2_000)


class GitReadArgs(BaseModel):
    path: str


class WriteFileArgs(BaseModel):
    path: str
    content: str = Field(max_length=2_000_000)


class RunCommandArgs(BaseModel):
    cwd: str
    command: str
    args: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)


class DockerPsArgs(BaseModel):
    all_containers: bool = Field(default=False, description="Include stopped containers too.")


class DockerLogsArgs(BaseModel):
    # Matches real Docker container name/ID characters and forbids a leading
    # "-" so this can never be mistaken for a flag by the docker CLI's own
    # argument parser, even though it is passed as one opaque argv element.
    container: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
                           description="Container name or ID.")
    tail: int = Field(default=200, gt=0, le=5_000)


class DockerActionArgs(BaseModel):
    # Must additionally be present in settings.docker_allowed_containers,
    # checked at execution time in the adapter (an allowlist is runtime
    # config, not a schema constant, so it can't live in this pattern).
    container: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
                           description="Container name or ID.")


class GitHubRepoArg(BaseModel):
    repo: str = Field(min_length=3, max_length=200, pattern=r"^[\w.-]+/[\w.-]+$", description='"owner/repo".')


class GitHubListIssuesArgs(GitHubRepoArg):
    state: str = Field(default="open", pattern=r"^(open|closed|all)$")


class GitHubCreateIssueArgs(GitHubRepoArg):
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=20_000)


class GitHubCommentIssueArgs(GitHubRepoArg):
    issue_number: int = Field(gt=0)
    body: str = Field(min_length=1, max_length=20_000)


class SendEmailArgs(BaseModel):
    to: EmailStr
    subject: str = Field(min_length=1, max_length=256)
    body: str = Field(min_length=1, max_length=50_000)


class SshRunArgs(BaseModel):
    host: str = Field(min_length=1, max_length=256, pattern=r"^(?:[A-Za-z0-9_.-]+@)?(?:[A-Za-z0-9][A-Za-z0-9.-]*|\[[A-Fa-f0-9:]+\])$", description='"user@host" or "host", must be on SSH_ALLOWED_HOSTS.')
    command: str
    args: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=60.0, gt=0, le=300)


@dataclass(slots=True, frozen=True)
class ToolSpec:
    name: str
    risk: str  # "read" | "write" | "exec"
    args_model: Type[BaseModel]
    description: str


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec("list_dir", "read", ListDirArgs, "List files and directories at a path."),
        ToolSpec("read_file", "read", ReadFileArgs, "Read a text file's contents."),
        ToolSpec("search_text", "read", SearchTextArgs, "Search files under a path for matching text."),
        ToolSpec("git_status", "read", GitReadArgs, "Show working tree status (git status --porcelain)."),
        ToolSpec("git_diff", "read", GitReadArgs, "Show uncommitted changes (git diff)."),
        ToolSpec("write_file", "write", WriteFileArgs, "Create or overwrite a text file."),
        ToolSpec(
            "run_command", "exec", RunCommandArgs,
            f"Run an allowlisted dev command ({', '.join(sorted(ALLOWED_COMMANDS))}) "
            "with structured arguments. UNSAFE host execution; admin opt-in required.",
        ),
        # Fixed Docker reads; logs are also resource-allowlisted. Docker socket
        # access is privileged and must only be enabled deliberately by operators.
        ToolSpec("docker_ps", "read", DockerPsArgs, "List running (or all) Docker containers."),
        ToolSpec("docker_logs", "read", DockerLogsArgs, "Show the tail of an allowlisted container's logs."),
        # Write-capable Docker: container must be on DOCKER_ALLOWED_CONTAINERS
        # (checked in the adapter). No `docker run`/`docker exec` yet -- those
        # are arbitrary code execution inside a container and need more design
        # than a same-shape addition here.
        ToolSpec("docker_start", "exec", DockerActionArgs, "Start a stopped, allowlisted container."),
        ToolSpec("docker_stop", "exec", DockerActionArgs, "Stop a running, allowlisted container."),
        ToolSpec("docker_restart", "exec", DockerActionArgs, "Restart an allowlisted container."),
        # GitHub: repo must be on GITHUB_ALLOWED_REPOS (checked in the adapter,
        # for reads too -- the allowlist bounds what's reachable regardless of
        # whether a human has to approve it). Requires GITHUB_TOKEN.
        ToolSpec("github_list_issues", "read", GitHubListIssuesArgs, "List issues on an allowlisted repo."),
        ToolSpec("github_create_issue", "write", GitHubCreateIssueArgs, "Open a new issue on an allowlisted repo."),
        ToolSpec("github_comment_issue", "write", GitHubCommentIssueArgs, "Comment on an existing issue."),
        # Email: recipient must match EMAIL_ALLOWED_RECIPIENTS (exact address or
        # "*@domain" wildcard). Requires SMTP_HOST and credentials.
        ToolSpec("send_email", "write", SendEmailArgs, "Send an email to an allowlisted recipient."),
        # SSH: host must be on SSH_ALLOWED_HOSTS; the command allowlist is not
        # isolation. Requires the explicit unsafe-host-execution opt-in and
        # working, already-trusted SSH host-key/known_hosts setup on this
        # machine (StrictHostKeyChecking=yes; no first-use trust-on-connect).
        ToolSpec(
            "ssh_run", "exec", SshRunArgs,
            f"Run an allowlisted dev command ({', '.join(sorted(ALLOWED_COMMANDS))}) on an "
            "allowlisted remote host over SSH. UNSAFE execution; admin opt-in required.",
        ),
    ]
}


def get_tool(name: str) -> ToolSpec | None:
    return TOOLS.get(name)


# Offered to the model during an agentic run (services/manager). Every tool is
# offered regardless of risk -- the gateway still decides at call time whether
# a "read" runs immediately or a "write"/"exec" pauses the run for approval, so
# offering more tools never widens what the model can do without a human.
AGENT_OFFERED_TOOLS = list(TOOLS.keys())


def tool_schema(spec: ToolSpec) -> dict:
    """Provider-agnostic {name, description, parameters} shape. Each provider
    adapter reshapes this into its own tool-calling wire format."""
    schema = spec.args_model.model_json_schema()
    schema.pop("title", None)
    return {"name": spec.name, "description": spec.description, "parameters": schema}
