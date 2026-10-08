"""Deterministic agent-mode routing and safe tool selection."""

from __future__ import annotations

from dataclasses import dataclass, field

ALLOWED_MODES = {"personal", "coding", "rag", "monitoring"}
MONITORING_TERMS = {
    "cpu", "ram", "memory", "disk", "gpu", "latency", "health", "server",
    "container", "service status", "uptime", "prometheus",
}
CODING_TERMS = {
    "code", "bug", "function", "class", "api", "python", "javascript", "flutter",
    "docker", "sql", "error", "exception", "repository",
}


@dataclass(slots=True)
class AgentDecision:
    mode: str
    tool_name: str | None = None
    tool_args: dict[str, str] = field(default_factory=dict)


def route_agent(
    message: str,
    *,
    project_id: str | None = None,
    requested_mode: str | None = None,
    monitoring_window: str = "15m",
) -> AgentDecision:
    """Select a constrained mode; never accepts arbitrary tool names from the client."""
    normalized = message.casefold()
    if requested_mode in ALLOWED_MODES:
        mode = requested_mode
    elif any(term in normalized for term in MONITORING_TERMS):
        mode = "monitoring"
    elif any(term in normalized for term in CODING_TERMS):
        mode = "coding"
    elif project_id:
        mode = "rag"
    else:
        mode = "personal"

    if mode != "monitoring":
        return AgentDecision(mode=mode)
    if "gpu" in normalized:
        return AgentDecision(
            mode=mode,
            tool_name="get_gpu_summary",
            tool_args={"window": monitoring_window},
        )
    if project_id and any(term in normalized for term in {"health", "uptime", "service status"}):
        return AgentDecision(
            mode=mode,
            tool_name="get_service_health",
            tool_args={"project_id": project_id, "window": monitoring_window},
        )
    if project_id:
        return AgentDecision(
            mode=mode,
            tool_name="get_project_summary",
            tool_args={"project_id": project_id, "window": monitoring_window},
        )
    return AgentDecision(
        mode=mode,
        tool_name="get_host_summary",
        tool_args={"window": monitoring_window},
    )
