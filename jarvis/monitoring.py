from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

WINDOWS = {"5m", "15m", "30m", "1h", "6h", "24h"}
PROJECT_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class MetricsBackend(Protocol):
    def query(self, template: str, labels: dict[str, str], window: str) -> float | None: ...


def validate_window(window: str) -> str:
    if window not in WINDOWS:
        raise ValueError(f"window must be one of: {', '.join(sorted(WINDOWS))}")
    return window


def validate_project(project_id: str) -> str:
    if not PROJECT_ID.fullmatch(project_id):
        raise ValueError("invalid project_id")
    return project_id


@dataclass(slots=True)
class MonitoringTools:
    backend: MetricsBackend

    def get_host_summary(self, window: str) -> dict:
        window = validate_window(window)
        return {
            "window": window,
            "cpu_percent": self.backend.query("host_cpu", {}, window),
            "memory_percent": self.backend.query("host_memory", {}, window),
            "disk_percent": self.backend.query("host_disk", {}, window),
        }

    def get_project_summary(self, project_id: str, window: str) -> dict:
        project_id, window = validate_project(project_id), validate_window(window)
        labels = {"project_id": project_id}
        return {
            "project_id": project_id,
            "window": window,
            "container_cpu_percent": self.backend.query("project_container_cpu", labels, window),
            "container_memory_bytes": self.backend.query(
                "project_container_memory", labels, window
            ),
        }

    def get_gpu_summary(self, window: str) -> dict:
        window = validate_window(window)
        return {
            "window": window,
            "gpu_utilization": self.backend.query("gpu_utilization", {}, window),
            "vram_bytes": self.backend.query("gpu_vram", {}, window),
        }

    def get_service_health(self, project_id: str, window: str) -> dict:
        project_id, window = validate_project(project_id), validate_window(window)
        return {
            "project_id": project_id,
            "window": window,
            "up": self.backend.query("service_up", {"project_id": project_id}, window),
        }
