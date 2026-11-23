# -*- coding: utf-8 -*-
"""审计中心接口的请求/响应模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditLogItem(BaseModel):
    id: str
    ts: datetime
    category: str
    category_label: str
    module: str
    module_label: str
    action: str
    action_label: str
    action_type: str
    action_type_label: str
    actor_username: str | None = None
    actor_role: str
    actor_role_label: str
    target_type: str | None = None
    target_id: str | None = None
    target_label: str | None = None
    summary: str
    result: str
    result_label: str
    error: str | None = None
    duration_ms: int | None = None
    ip: str | None = None
    client: str | None = None
    request_method: str | None = None
    request_path: str | None = None


class AuditLogDetail(AuditLogItem):
    detail: dict[str, Any] = Field(default_factory=dict)
    user_agent: str | None = None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    page_size: int


class AuditOption(BaseModel):
    value: str
    label: str
    module: str | None = None
    module_label: str | None = None


class AuditMetaResponse(BaseModel):
    categories: list[AuditOption]
    modules: list[AuditOption]
    actions: list[AuditOption]
    roles: list[AuditOption]
    results: list[AuditOption]
    time_presets: list[AuditOption]


class AuditTotals(BaseModel):
    total: int = 0
    login_success: int = 0
    login_failure: int = 0
    operation_success: int = 0
    operation_failure: int = 0
    unique_actors: int = 0
    active_users_30m: int = 0
    running_jobs: int | None = None


class AuditHourPoint(BaseModel):
    hour: str
    total: int
    failure: int


class AuditCountItem(BaseModel):
    value: str
    label: str
    count: int


class AuditOverviewResponse(BaseModel):
    range_hours: int
    totals: AuditTotals
    by_hour: list[AuditHourPoint]
    by_module: list[AuditCountItem]
    by_role: list[AuditCountItem]
    recent: list[AuditLogItem]


# ---------------------------------------------------------------- 系统运行


class AuditResourceMetric(BaseModel):
    used_bytes: int | None = None
    limit_bytes: int | None = None
    percent: float | None = None
    source: str = "unavailable"


class AuditChildInfo(BaseModel):
    pid: int
    rss_bytes: int = 0
    cpu_percent: float | None = None
    status: str = "unknown"
    create_time: str | None = None


class AuditContainerInfo(BaseModel):
    pid: int
    hostname: str | None = None
    rss_bytes: int = 0
    cpu_percent: float | None = None
    threads: int | None = None
    started_at: str | None = None
    uptime_seconds: int | None = None
    memory: AuditResourceMetric = Field(default_factory=AuditResourceMetric)
    cpu_available_cores: float | None = None
    cpu_source: str = "host"
    updated_at: str | None = None
    pool_size: int | None = None
    children: list[AuditChildInfo] = Field(default_factory=list)


class AuditWorkerSummary(BaseModel):
    online: bool = False
    count: int = 0
    pool_size: int = 0
    children_count: int = 0
    running_jobs: int = 0
    rss_bytes: int = 0
    cpu_percent: float | None = None
    memory: AuditResourceMetric = Field(default_factory=AuditResourceMetric)
    last_seen: str | None = None


class AuditQueueInfo(BaseModel):
    length: int | None = None
    running: int = 0
    concurrency: int | None = None


class AuditDependencyInfo(BaseModel):
    ok: bool
    latency_ms: float | None = None
    label: str


class AuditProcessInfo(BaseModel):
    job_id: str
    owner_username: str | None = None
    task_type: str
    task_type_label: str
    status: str
    status_label: str
    pid: int | None = None
    rss_bytes: int | None = None
    cpu_percent: float | None = None
    started_at: str | None = None
    runtime_seconds: int | None = None
    stage: str | None = None
    message: str | None = None
    worker: str | None = None


class AuditUserResource(BaseModel):
    username: str
    running_jobs: int
    rss_bytes: int = 0
    cpu_percent: float | None = None


class AuditHealth(BaseModel):
    level: str
    label: str
    reasons: list[str] = Field(default_factory=list)


class AuditSystemResponse(BaseModel):
    generated_at: str
    mode: str
    health: AuditHealth
    api: AuditContainerInfo
    workers: list[AuditContainerInfo]
    worker_summary: AuditWorkerSummary
    queue: AuditQueueInfo
    disk: AuditResourceMetric
    processes: list[AuditProcessInfo]
    users: list[AuditUserResource]
    dependencies: dict[str, AuditDependencyInfo]


class AuditHistoryPoint(BaseModel):
    ts: float
    api_rss_bytes: int | None = None
    api_cpu_percent: float | None = None
    api_memory_percent: float | None = None
    worker_rss_bytes: int | None = None
    worker_cpu_percent: float | None = None
    worker_memory_percent: float | None = None
    running_jobs: int | None = None


class AuditHistoryResponse(BaseModel):
    points: list[AuditHistoryPoint]
    available: bool = True
    warning: str | None = None
