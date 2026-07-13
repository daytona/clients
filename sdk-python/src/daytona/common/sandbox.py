# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from daytona_api_client import SandboxListSortDirection, SandboxListSortField, SandboxState
from daytona_api_client_async import GpuType

TOOLBOX_PORT = 2280


@dataclass
class Resources:
    """Resources configuration for Sandbox.

    Attributes:
        cpu (int | None): Number of CPU cores to allocate.
        memory (int | None): Amount of memory in GiB to allocate.
        disk (int | None): Amount of disk space in GiB to allocate.
        gpu (int | None): Number of GPUs to allocate.
        gpu_type (GpuType | list[GpuType] | None): Preferred GPU type for the Sandbox.

    Example:
        ```python
        resources = Resources(
            cpu=2,
            memory=4,  # 4GiB RAM
            disk=20,   # 20GiB disk
            gpu=1,
            gpu_type=GpuType.H100,
        )
        params = CreateSandboxFromImageParams(
            image=Image.debian_slim("3.12"),
            language="python",
            resources=resources
        )
        ```
    """

    cpu: int | None = None
    memory: int | None = None
    disk: int | None = None
    gpu: int | None = None
    gpu_type: GpuType | list[GpuType] | None = None


@dataclass
class ListSandboxesQuery:
    """Query parameters for filtering and sorting when listing Sandboxes.

    Attributes:
        limit: Per-page fetch size. Does NOT limit the total number of
            Sandboxes returned.
        id: Filter by ID prefix (case-insensitive).
        name: Filter by name prefix (case-insensitive).
        labels: Filter by labels.
        states: Filter by states.
        snapshots: Filter by snapshot names.
        targets: Filter by targets.
        min_cpu: Filter by minimum CPU.
        max_cpu: Filter by maximum CPU.
        min_memory_gib: Filter by minimum memory in GiB.
        max_memory_gib: Filter by maximum memory in GiB.
        min_disk_gib: Filter by minimum disk space in GiB.
        max_disk_gib: Filter by maximum disk space in GiB.
        is_public: Filter by public status.
        is_recoverable: Filter by recoverable status.
        created_at_after (datetime): Include sandboxes created after this timestamp.
        created_at_before (datetime): Include sandboxes created before this timestamp.
        last_activity_after (datetime): Include sandboxes with last activity after this timestamp.
        last_activity_before (datetime): Include sandboxes with last activity before this timestamp.
        sort: Field to sort by.
        order: Sort direction.
    """

    limit: int | None = None
    id: str | None = None
    name: str | None = None
    labels: dict[str, str] | None = None
    states: list[SandboxState] | None = None
    snapshots: list[str] | None = None
    targets: list[str] | None = None
    min_cpu: int | None = None
    max_cpu: int | None = None
    min_memory_gib: int | None = None
    max_memory_gib: int | None = None
    min_disk_gib: int | None = None
    max_disk_gib: int | None = None
    is_public: bool | None = None
    is_recoverable: bool | None = None
    created_at_after: datetime | None = None
    created_at_before: datetime | None = None
    last_activity_after: datetime | None = None
    last_activity_before: datetime | None = None
    sort: SandboxListSortField | None = None
    order: SandboxListSortDirection | None = None


@dataclass
class SandboxMetrics:
    """A single point-in-time sample of historical Sandbox resource usage.

    Each instance corresponds to one aggregation bucket returned by the telemetry
    backend. Use :meth:`Sandbox.get_metrics` to fetch a time-ordered list of these,
    or :meth:`Sandbox.get_metrics_latest` for the current sample.

    Attributes:
        cpu_count (int): Number of CPU cores allocated to the Sandbox.
        cpu_used_pct (float): CPU utilization as a percentage of the allocated limit.
        disk_total (int): Total disk space in bytes.
        disk_used (int): Used disk space in bytes.
        mem_total (int): Total memory in bytes.
        mem_used (int): Used memory in bytes.
        mem_cache (int): Memory used by the page cache in bytes.
        timestamp (datetime): Timestamp of this sample.
    """

    cpu_count: int
    cpu_used_pct: float
    disk_total: int
    disk_used: int
    mem_total: int
    mem_used: int
    mem_cache: int
    timestamp: datetime


_SANDBOX_METRIC_FIELD_BY_NAME: dict[str, str] = {
    "daytona.sandbox.cpu.utilization": "cpu_used_pct",
    "daytona.sandbox.cpu.limit": "cpu_count",
    "daytona.sandbox.memory.usage": "mem_used",
    "daytona.sandbox.memory.limit": "mem_total",
    "daytona.sandbox.memory.cache": "mem_cache",
    "daytona.sandbox.filesystem.usage": "disk_used",
    "daytona.sandbox.filesystem.total": "disk_total",
}

SANDBOX_METRIC_NAMES: list[str] = list(_SANDBOX_METRIC_FIELD_BY_NAME.keys())


def _parse_metric_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class _SystemMetrics(Protocol):
    @property
    def cpu_count(self) -> int | None:
        ...

    @property
    def cpu_used_pct(self) -> float | None:
        ...

    @property
    def disk_total(self) -> int | None:
        ...

    @property
    def disk_used(self) -> int | None:
        ...

    @property
    def mem_total(self) -> int | None:
        ...

    @property
    def mem_used(self) -> int | None:
        ...

    @property
    def mem_cache(self) -> int | None:
        ...

    @property
    def timestamp(self) -> str | None:
        ...


def sandbox_metrics_from_system_metrics(system_metrics: _SystemMetrics) -> SandboxMetrics:
    """Converts a live daemon ``SystemMetrics`` snapshot into a ``SandboxMetrics`` sample."""
    return SandboxMetrics(
        cpu_count=int(system_metrics.cpu_count or 0),
        cpu_used_pct=float(system_metrics.cpu_used_pct or 0.0),
        disk_total=int(system_metrics.disk_total or 0),
        disk_used=int(system_metrics.disk_used or 0),
        mem_total=int(system_metrics.mem_total or 0),
        mem_used=int(system_metrics.mem_used or 0),
        mem_cache=int(system_metrics.mem_cache or 0),
        timestamp=_parse_metric_timestamp(system_metrics.timestamp)
        if system_metrics.timestamp
        else datetime.now(timezone.utc),
    )


def _build_sandbox_metrics(buckets: dict[str, dict[str, float]]) -> list[SandboxMetrics]:
    result: list[SandboxMetrics] = []
    for ts in sorted(buckets):
        values = buckets[ts]
        result.append(
            SandboxMetrics(
                cpu_count=int(values.get("cpu_count", 0)),
                cpu_used_pct=float(values.get("cpu_used_pct", 0.0)),
                disk_total=int(values.get("disk_total", 0)),
                disk_used=int(values.get("disk_used", 0)),
                mem_total=int(values.get("mem_total", 0)),
                mem_used=int(values.get("mem_used", 0)),
                mem_cache=int(values.get("mem_cache", 0)),
                timestamp=_parse_metric_timestamp(ts),
            )
        )
    return result


def pivot_sandbox_metrics(points: Iterable[tuple[str | None, str | None, float | None]]) -> list[SandboxMetrics]:
    """Buckets ``(metric_name, timestamp, value)`` triples by timestamp into ``SandboxMetrics`` samples."""
    buckets: dict[str, dict[str, float]] = {}
    for metric_name, timestamp, value in points:
        if metric_name is None or not timestamp or value is None:
            continue
        field = _SANDBOX_METRIC_FIELD_BY_NAME.get(metric_name)
        if field is None:
            continue
        buckets.setdefault(timestamp, {})[field] = value
    return _build_sandbox_metrics(buckets)
