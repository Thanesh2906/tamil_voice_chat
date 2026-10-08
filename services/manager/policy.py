"""One authorization boundary shared by direct tools and model-proposed tools.

A configured server root is an outer deployment limit, not a per-user grant.
Only a project's administrator-assigned root grants filesystem access. Host-wide
adapters and unsandboxed commands require administrator privileges as well.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common import get_settings
from packages.common.safe_paths import resolve_authorized, resolve_authorized_for_write
from packages.db import Project, User, project_for_user
from services.tools.gateway import ToolPolicyError, ValidatedCall
from services.tools.registry import TOOLS

FILE_TOOLS = {"list_dir", "read_file", "search_text", "git_status", "git_diff", "write_file", "run_command"}
PROJECT_TOOLS = FILE_TOOLS - {"run_command"}


def current_scopes(session: Session, user_id: str) -> set[str]:
    # Select the scalar value, not an ORM identity-map copy fetched before a
    # potentially slow model call. Revocations take effect before each action.
    scopes = session.scalar(select(User.scopes_csv).where(User.id == user_id))
    return set(scopes.split(",")) if scopes else set()


def has_scope(scopes: set[str], scope: str) -> bool:
    return scope in scopes or "admin" in scopes


def project_roots(project: Project | None) -> list[str]:
    if project is None or not project.root_path:
        raise ToolPolicyError("project has no administrator-assigned filesystem root")
    settings = get_settings()
    try:
        root = resolve_authorized(project.root_path, settings.rag_allowed_roots, settings.rag_sensitive_globs)
        if not root.is_dir():
            raise ToolPolicyError("project root is not a directory")
        return [str(root)]
    except (OSError, PermissionError) as exc:
        raise ToolPolicyError("project filesystem root is not authorized") from exc


def available_tool_names(session: Session, user_id: str, project: Project | None) -> set[str]:
    scopes = current_scopes(session, user_id)
    if not has_scope(scopes, "tools"):
        return set()
    allowed = set(TOOLS) - FILE_TOOLS if "admin" in scopes else set()
    if project and project_for_user(session, project.id, user_id):
        try:
            project_roots(project)
            allowed |= FILE_TOOLS if "admin" in scopes else PROJECT_TOOLS
        except ToolPolicyError:
            pass
    if not getattr(get_settings(), "tools_unsafe_host_execution", False):
        allowed -= {"run_command", "ssh_run"}
    return allowed


def authorize_tool(session: Session, user_id: str, project_id: str | None, call: ValidatedCall) -> list[str]:
    scopes = current_scopes(session, user_id)
    if not has_scope(scopes, "tools"):
        raise ToolPolicyError("missing scope: tools")
    project = None
    if project_id:
        project = project_for_user(session, project_id, user_id)
        if not project:
            raise ToolPolicyError("project membership is no longer authorized")
        session.refresh(project)
    if call.spec.name in {"run_command", "ssh_run"} and not getattr(get_settings(), "tools_unsafe_host_execution", False):
        raise ToolPolicyError("unsandboxed host execution is disabled by the administrator")
    if call.spec.name not in PROJECT_TOOLS and "admin" not in scopes:
        raise ToolPolicyError("this server-wide or unsandboxed tool requires admin scope")
    if call.spec.name not in FILE_TOOLS:
        return []
    roots = project_roots(project)
    settings = get_settings()
    path_key = "cwd" if call.spec.name == "run_command" else "path"
    try:
        resolver = resolve_authorized_for_write if call.spec.name == "write_file" else resolve_authorized
        path = resolver(call.args[path_key], roots, settings.rag_sensitive_globs)
        # Pin the canonical path in the persisted approval instead of re-resolving
        # a relative path against a potentially different process cwd.
        call.args[path_key] = str(path)
    except (OSError, PermissionError) as exc:
        raise ToolPolicyError("tool path is outside the authorized project or is excluded") from exc
    return roots
