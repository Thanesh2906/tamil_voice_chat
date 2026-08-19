from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class MetricSeries(BaseModel):
    metric: str
    labels: dict = Field(default_factory=dict)
    points: List[tuple] = Field(default_factory=list)  # (timestamp, value)


class MonitoringSummary(BaseModel):
    project_id: Optional[str] = None
    scope: str
    window_start: datetime
    window_end: datetime
    cpu_avg: Optional[float] = None
    ram_avg: Optional[float] = None
    disk_avg: Optional[float] = None
    net_in_avg: Optional[float] = None
    net_out_avg: Optional[float] = None
    gpu_util: Optional[float] = None
    gpu_vram: Optional[float] = None
    series: List[MetricSeries] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
