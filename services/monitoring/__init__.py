"""Read-only Prometheus query adapter for the agent.

Implements blueprint §6: host + container + GPU + app metrics through
a single tool surface. The agent never sees raw PromQL.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx

from packages.common import configure_logging, get_logger, get_settings
from packages.schemas.monitoring import MetricSeries, MonitoringSummary

configure_logging()
log = get_logger("monitoring")

# PromQL templates — names match the exporters listed in blueprint §6.
QUERIES = {
    "host_cpu": "100 - (avg by (instance) (rate(node_cpu_seconds_total{mode=\"idle\"}[5m])) * 100)",
    "host_ram": "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100",
    "host_disk": "(1 - (node_filesystem_avail_bytes{fstype!~\"tmpfs|overlay\"} / node_filesystem_size_bytes)) * 100",
    "host_net_in": "rate(node_network_receive_bytes_total[5m])",
    "host_net_out": "rate(node_network_transmit_bytes_total[5m])",
    "container_cpu": "sum by (name) (rate(container_cpu_usage_seconds_total{name=~\".+\"}[5m]) * 100)",
    "container_ram": "sum by (name) (container_memory_usage_bytes{name=~\".+\"})",
    "gpu_util": "DCGM_FI_DEV_GPU_UTIL",
    "gpu_vram": "DCGM_FI_DEV_FB_USED",
    "gpu_temp": "DCGM_FI_DEV_GPU_TEMP",
}

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
WINDOW_RE = re.compile(r"^(?:[1-9]|[1-5][0-9]|60)(?:m|h)$|^[1-7]d$")


async def _query_range(client: httpx.AsyncClient, base: str, query: str, start: int, end: int, step: int = 30) -> Dict:
    url = f"{base.rstrip('/')}/api/v1/query_range"
    r = await client.get(url, params={"query": query, "start": start, "end": end, "step": step})
    r.raise_for_status()
    return r.json()


async def _query_instant(client: httpx.AsyncClient, base: str, query: str) -> Dict:
    url = f"{base.rstrip('/')}/api/v1/query"
    r = await client.get(url, params={"query": query})
    r.raise_for_status()
    return r.json()


def _series(metric: str, labels: dict, values: List[List]) -> MetricSeries:
    pts = [(datetime.utcfromtimestamp(ts), float(v)) for ts, v in values]
    return MetricSeries(metric=metric, labels=labels, points=pts)


async def summary(
    *,
    project_id: Optional[str] = None,
    window: str = "15m",
) -> MonitoringSummary:
    s = get_settings()
    delta = _parse_window(window)
    if project_id and not PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError("invalid project_id")
    end = datetime.utcnow()
    start = end - delta
    series: List[MetricSeries] = []
    notes: List[str] = []
    averages: Dict[str, Optional[float]] = {}

    async with httpx.AsyncClient(timeout=20.0) as client:
        for key, q in QUERIES.items():
            try:
                q_proj = q
                if "container" in key:
                    if not project_id:
                        continue
                    # project_id passed validation above; only this controlled label is substituted.
                    q_proj = q.replace('name=~".+"', f'name="{project_id}"')
                elif project_id and key.startswith("host_"):
                    continue
                res = await _query_range(
                    client, s.prometheus_url, q_proj,
                    int(start.timestamp()), int(end.timestamp()), 30,
                )
                for r in res.get("data", {}).get("result", []):
                    metric = r.get("metric", {})
                    values = r.get("values", [])
                    if not values:
                        continue
                    series.append(_series(key, metric, values))
                    if values:
                        avg = sum(float(v) for _, v in values) / len(values)
                        averages[key] = avg
            except httpx.HTTPError as e:
                notes.append(f"{key}: {e.__class__.__name__}")

    return MonitoringSummary(
        project_id=project_id,
        scope="project" if project_id else "host",
        window_start=start,
        window_end=end,
        cpu_avg=averages.get("container_cpu" if project_id else "host_cpu"),
        ram_avg=averages.get("container_ram" if project_id else "host_ram"),
        disk_avg=None if project_id else averages.get("host_disk"),
        net_in_avg=None if project_id else averages.get("host_net_in"),
        net_out_avg=None if project_id else averages.get("host_net_out"),
        gpu_util=averages.get("gpu_util"),
        gpu_vram=averages.get("gpu_vram"),
        series=series,
        notes=notes,
    )


def _parse_window(window: str) -> timedelta:
    window = window.strip().lower()
    if not WINDOW_RE.fullmatch(window):
        raise ValueError("window must be 1-60m, 1-60h, or 1-7d")
    if window.endswith("m"):
        return timedelta(minutes=int(window[:-1] or 15))
    if window.endswith("h"):
        return timedelta(hours=int(window[:-1] or 1))
    if window.endswith("d"):
        return timedelta(days=int(window[:-1] or 1))
    raise ValueError("invalid window")


from fastapi import FastAPI, HTTPException  # noqa: E402
from prometheus_client import make_asgi_app  # noqa: E402

app = FastAPI(title="Jarvis Monitoring", version="1.0.0")
app.mount("/metrics", make_asgi_app())


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/monitoring/summary", response_model=MonitoringSummary)
async def monitoring_summary(project_id: Optional[str] = None, window: str = "15m") -> MonitoringSummary:
    try:
        return await summary(project_id=project_id, window=window)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/monitoring/projects/{project_id}", response_model=MonitoringSummary)
async def project_summary(project_id: str, window: str = "15m") -> MonitoringSummary:
    try:
        return await summary(project_id=project_id, window=window)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def get_host_summary(window: str = "15m") -> MonitoringSummary:
    return await summary(window=window)


async def get_project_summary(project_id: str, window: str = "15m") -> MonitoringSummary:
    return await summary(project_id=project_id, window=window)


async def get_gpu_summary(window: str = "15m") -> MonitoringSummary:
    result = await summary(window=window)
    result.series = [item for item in result.series if item.metric.startswith("gpu_")]
    return result


async def get_service_health(project_id: str, window: str = "15m") -> dict:
    result = await summary(project_id=project_id, window=window)
    return {"project_id": project_id, "healthy": not result.notes, "notes": result.notes}
