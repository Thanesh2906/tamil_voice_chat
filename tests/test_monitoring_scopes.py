from __future__ import annotations

import httpx
import pytest

from services import monitoring

_REAL_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def offline_client(monkeypatch):
    def unexpected_request(_request):
        raise AssertionError("A unit test attempted a real monitoring request")

    monkeypatch.setattr(monitoring.httpx, "AsyncClient", lambda **kwargs: _REAL_CLIENT(
        transport=httpx.MockTransport(unexpected_request), **kwargs,
    ))


def test_every_host_query_requires_verified_host_scope() -> None:
    for key, query in monitoring.QUERIES.items():
        if key.startswith("host_"):
            assert 'scope="host"' in query
    assert "avg by (instance, scope)" in monitoring.QUERIES["host_cpu"]


@pytest.mark.asyncio
async def test_container_exporter_metrics_are_not_labeled_as_host(monkeypatch) -> None:
    async def query(*_args, **_kwargs):
        return {"data": {"result": [{
            "metric": {"scope": "exporter_container", "instance": "node_exporter:9100"},
            "values": [[1700000000, "92.0"]],
        }]}}

    monkeypatch.setattr(monitoring, "_query_range", query)
    result = await monitoring.summary()
    assert result.scope == "host"
    assert result.cpu_avg is None
    assert result.ram_avg is None
    assert result.disk_avg is None
    assert result.net_in_avg is None
    assert result.net_out_avg is None
    assert not any(series.metric.startswith("host_") for series in result.series)
    assert "host_cpu: no verified samples available" in result.notes


@pytest.mark.asyncio
async def test_verified_host_metrics_are_reported(monkeypatch) -> None:
    async def query(*_args, **_kwargs):
        return {"data": {"result": [{
            "metric": {"scope": "host", "instance": "approved-host:9100"},
            "values": [[1700000000, "10.0"], [1700000030, "20.0"]],
        }]}}

    monkeypatch.setattr(monitoring, "_query_range", query)
    result = await monitoring.summary()
    assert result.cpu_avg == 15.0
    assert result.ram_avg == 15.0
    assert result.notes == []


@pytest.mark.asyncio
async def test_empty_telemetry_is_unavailable_not_healthy(monkeypatch) -> None:
    async def query(*_args, **_kwargs):
        return {"data": {"result": []}}

    monkeypatch.setattr(monitoring, "_query_range", query)
    result = await monitoring.get_service_health("project-1")
    assert result["healthy"] is False
    assert "container_cpu: no verified samples available" in result["notes"]
