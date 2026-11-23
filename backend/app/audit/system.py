# -*- coding: utf-8 -*-
"""系统运行状态聚合。

口径说明（与部署方式一致）：

- **容器级**：API 容器、Worker 容器各自按自己的 cgroup 限额计算百分比，
  不相加；没有限额时只展示已用字节，不虚构百分比。
- **磁盘**：``/app/output`` 所在文件系统（宿主机数据盘）的使用率，
  不是容器配额。
- **Worker 心跳**：Worker 指标快照超过 15 秒未更新视为失联；Redis 不可用
  时说「指标不可用」，不说「Worker 离线」。
- **任务进程**：来自 Redis 控制面 ``icd:active`` + ``icd:job:{id}:ctl``，
  进程资源来自 Worker 快照里的子进程列表（按 PID 对应）。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from app.audit import catalog
from app.audit.schemas import (
    AuditChildInfo,
    AuditContainerInfo,
    AuditDependencyInfo,
    AuditHealth,
    AuditHistoryPoint,
    AuditHistoryResponse,
    AuditProcessInfo,
    AuditQueueInfo,
    AuditResourceMetric,
    AuditSystemResponse,
    AuditUserResource,
    AuditWorkerSummary,
)
from app.auth.db import ping_db
from app.execution import config as execution_config
from app.execution import control
from app.execution import metrics

WORKER_STALE_S = 15.0
MEMORY_WARN = 75.0
MEMORY_CRITICAL = 90.0
CPU_WARN = 85.0
CPU_CRITICAL = 95.0
DISK_WARN = 80.0
DISK_CRITICAL = 90.0

_LEVEL_ORDER = {"ok": 0, "warning": 1, "critical": 2}
_LEVEL_LABELS = {"ok": "运行正常", "warning": "需要关注", "critical": "存在异常"}


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _utc_iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _age_seconds(value: Any, now: float) -> float | None:
    epoch = _parse_iso(value)
    if epoch is None:
        return None
    return max(0.0, now - epoch)


def _safe_job_dir(job_id: str):
    if not job_id or job_id in (".", "..") or "/" in job_id or "\\" in job_id:
        return None
    from app.v4.config import get_output_root

    return get_output_root() / "v4" / job_id


def _read_manifest(job_id: str) -> dict[str, Any]:
    job_dir = _safe_job_dir(job_id)
    if job_dir is None:
        return {}
    try:
        data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _resource(raw: Any) -> AuditResourceMetric:
    if not isinstance(raw, dict):
        return AuditResourceMetric()
    return AuditResourceMetric(
        used_bytes=_to_int(raw.get("used_bytes")),
        limit_bytes=_to_int(raw.get("limit_bytes")),
        percent=_to_float(raw.get("percent")),
        source=str(raw.get("source") or "unavailable"),
    )


def _child(raw: dict[str, Any]) -> AuditChildInfo | None:
    pid = _to_int(raw.get("pid"))
    if pid is None:
        return None
    return AuditChildInfo(
        pid=pid,
        rss_bytes=_to_int(raw.get("rss_bytes")) or 0,
        cpu_percent=_to_float(raw.get("cpu_percent")),
        status=str(raw.get("status") or "unknown"),
        create_time=raw.get("create_time"),
    )


def _container(raw: dict[str, Any]) -> AuditContainerInfo:
    children: list[AuditChildInfo] = []
    for item in raw.get("children") or []:
        if isinstance(item, dict):
            child = _child(item)
            if child is not None:
                children.append(child)
    return AuditContainerInfo(
        pid=_to_int(raw.get("pid")) or 0,
        hostname=raw.get("hostname"),
        rss_bytes=_to_int(raw.get("rss_bytes")) or 0,
        cpu_percent=_to_float(raw.get("cpu_percent")),
        threads=_to_int(raw.get("threads")),
        started_at=raw.get("started_at"),
        uptime_seconds=_to_int(raw.get("uptime_seconds")),
        memory=_resource(raw.get("memory")),
        cpu_available_cores=_to_float(raw.get("cpu_available_cores")),
        cpu_source=str(raw.get("cpu_source") or "host"),
        updated_at=raw.get("updated_at"),
        pool_size=_to_int(raw.get("pool_size")),
        children=children,
    )


# ---------------------------------------------------------------- 依赖探活


def _redis_ping() -> tuple[bool, float | None]:
    started = time.perf_counter()
    try:
        control.get_client().ping()
    except Exception:  # noqa: BLE001 — 依赖探活只看结果
        return False, None
    return True, round((time.perf_counter() - started) * 1000.0, 1)


async def _collect_dependencies() -> dict[str, AuditDependencyInfo]:
    loop = asyncio.get_running_loop()
    redis_ok, redis_ms = await loop.run_in_executor(None, _redis_ping)

    started = time.perf_counter()
    try:
        await ping_db()
        mongo_ok, mongo_ms = True, round((time.perf_counter() - started) * 1000.0, 1)
    except Exception:  # noqa: BLE001
        mongo_ok, mongo_ms = False, None

    return {
        "redis": AuditDependencyInfo(
            ok=redis_ok, latency_ms=redis_ms, label="Redis 任务队列"
        ),
        "mongo": AuditDependencyInfo(
            ok=mongo_ok, latency_ms=mongo_ms, label="MongoDB 数据库"
        ),
    }


# ---------------------------------------------------------------- 同步采集


def _collect_sync(redis_ok: bool, now: float) -> dict[str, Any]:
    from app.v4.config import get_output_root

    data: dict[str, Any] = {
        "api": None,
        "workers": [],
        "active": [],
        "queue_length": None,
        "disk": metrics.collect_disk_metric(get_output_root()),
    }
    if not redis_ok:
        return data

    api_raw = metrics.read_api_snapshot()
    if not api_raw or (_age_seconds(api_raw.get("updated_at"), now) or 1e9) > metrics.API_TTL_S:
        api_raw = metrics.collect_api_snapshot()
    data["api"] = api_raw

    data["workers"] = metrics.read_worker_snapshots()

    try:
        data["active"] = control.active_snapshots()
    except control.RedisUnavailable:
        data["active"] = []

    try:
        data["queue_length"] = control.queue_length()
    except control.RedisUnavailable:
        data["queue_length"] = None
    return data


def _worker_summary(workers: list[dict[str, Any]], now: float) -> tuple[AuditWorkerSummary, float | None]:
    if not workers:
        return AuditWorkerSummary(), None

    last_seen_epoch = max(
        (_parse_iso(worker.get("updated_at")) or 0.0) for worker in workers
    )
    age = max(0.0, now - last_seen_epoch) if last_seen_epoch else None
    online = age is not None and age <= WORKER_STALE_S

    used = 0
    limit = 0
    limit_complete = True
    cpu_values: list[float] = []
    pool_size = 0
    children = 0
    rss = 0
    for worker in workers:
        memory = worker.get("memory") or {}
        used += _to_int(memory.get("used_bytes")) or 0
        worker_limit = _to_int(memory.get("limit_bytes"))
        if worker_limit is None:
            limit_complete = False
        else:
            limit += worker_limit
        cpu = _to_float(worker.get("cpu_percent"))
        if cpu is not None:
            cpu_values.append(cpu)
        pool_size += _to_int(worker.get("pool_size")) or 0
        children += len(worker.get("children") or [])
        rss += _to_int(worker.get("rss_bytes")) or 0

    summary = AuditWorkerSummary(
        online=online,
        count=len(workers),
        pool_size=pool_size,
        children_count=children,
        running_jobs=0,
        rss_bytes=rss,
        cpu_percent=max(cpu_values) if cpu_values else None,
        memory=AuditResourceMetric(
            used_bytes=used or None,
            limit_bytes=limit if (limit_complete and limit) else None,
            percent=(
                round(used / limit * 100.0, 1)
                if limit_complete and limit
                else None
            ),
            source="cgroup" if workers else "unavailable",
        ),
        last_seen=_utc_iso(last_seen_epoch) if last_seen_epoch else None,
    )
    return summary, age


def _process_rows(
    active: list[dict[str, Any]],
    workers: list[dict[str, Any]],
    now: float,
) -> list[AuditProcessInfo]:
    children: dict[int, dict[str, Any]] = {}
    for worker in workers:
        for child in worker.get("children") or []:
            if isinstance(child, dict):
                pid = _to_int(child.get("pid"))
                if pid is not None:
                    children[pid] = child

    stale_after = execution_config.stale_seconds()
    rows: list[AuditProcessInfo] = []
    for item in active:
        job_id = str(item.get("job_id") or "")
        if not job_id:
            continue
        manifest = _read_manifest(job_id)
        status = str(manifest.get("status") or "unknown")
        progress = manifest.get("progress")
        if not isinstance(progress, dict):
            progress = {}

        pid = _to_int(item.get("pid"))
        heartbeat = _to_float(item.get("heartbeat_at"))
        fresh = heartbeat is not None and (now - heartbeat) <= stale_after
        status_label = catalog.task_status_label(status)
        if status == "running" and not fresh:
            status_label = f"{status_label}（心跳丢失）"

        started_epoch = _to_float(item.get("started_at")) or _parse_iso(
            manifest.get("created_at")
        )
        runtime = (
            max(0, int(now - started_epoch)) if started_epoch is not None else None
        )
        child = children.get(pid) if pid is not None else None

        rows.append(
            AuditProcessInfo(
                job_id=job_id,
                owner_username=manifest.get("owner_username"),
                task_type=str(manifest.get("task_type") or "unknown"),
                task_type_label=catalog.task_type_label(
                    manifest.get("task_type")
                ),
                status=status,
                status_label=status_label,
                pid=pid,
                rss_bytes=(
                    _to_int(child.get("rss_bytes")) if child is not None else None
                ),
                cpu_percent=(
                    _to_float(child.get("cpu_percent"))
                    if child is not None
                    else None
                ),
                started_at=_utc_iso(started_epoch),
                runtime_seconds=runtime,
                stage=progress.get("stage") or progress.get("message"),
                message=manifest.get("message"),
                worker=item.get("worker"),
            )
        )

    rows.sort(
        key=lambda row: (
            0 if row.status == "running" else 1,
            -(row.runtime_seconds or 0),
        )
    )
    return rows


def _concurrency(workers: list[dict[str, Any]]) -> int | None:
    """展示用并发上限：Worker 快照的 pool_size 之和，其次环境变量。"""
    total = sum(int(_to_int(worker.get("pool_size")) or 0) for worker in workers)
    if total > 0:
        return total
    raw = (os.getenv("ICD_WORKER_CONCURRENCY") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 20 if execution_config.use_celery() else None


def _user_rows(processes: list[AuditProcessInfo]) -> list[AuditUserResource]:
    buckets: dict[str, dict[str, Any]] = {}
    for proc in processes:
        if proc.pid is None or proc.status != "running":
            continue
        key = proc.owner_username or "未归属账号"
        bucket = buckets.setdefault(
            key, {"username": key, "running_jobs": 0, "rss_bytes": 0, "cpu": 0.0}
        )
        bucket["running_jobs"] += 1
        bucket["rss_bytes"] += proc.rss_bytes or 0
        if proc.cpu_percent is not None:
            bucket["cpu"] += proc.cpu_percent
    rows = [
        AuditUserResource(
            username=item["username"],
            running_jobs=item["running_jobs"],
            rss_bytes=item["rss_bytes"],
            cpu_percent=round(item["cpu"], 1) if item["cpu"] else None,
        )
        for item in buckets.values()
    ]
    rows.sort(key=lambda row: (-row.running_jobs, -row.rss_bytes))
    return rows


# ---------------------------------------------------------------- 健康判定


def _build_health(
    *,
    api: AuditContainerInfo,
    workers: list[AuditContainerInfo],
    worker_summary: AuditWorkerSummary,
    worker_age: float | None,
    disk: AuditResourceMetric,
    dependencies: dict[str, AuditDependencyInfo],
    queue: AuditQueueInfo,
) -> AuditHealth:
    level = "ok"
    reasons: list[str] = []

    def escalate(target: str, message: str) -> None:
        nonlocal level
        if _LEVEL_ORDER[target] > _LEVEL_ORDER[level]:
            level = target
        reasons.append(message)

    def check_resource(name: str, percent: float | None, warn: float, critical: float) -> None:
        if percent is None:
            return
        if percent >= critical:
            escalate("critical", f"{name}使用率 {percent:.1f}%（阈值 {critical:.0f}%）")
        elif percent >= warn:
            escalate("warning", f"{name}使用率 {percent:.1f}%（阈值 {warn:.0f}%）")

    check_resource("API 容器内存", api.memory.percent, MEMORY_WARN, MEMORY_CRITICAL)
    check_resource("API 容器 CPU", api.cpu_percent, CPU_WARN, CPU_CRITICAL)
    for worker in workers:
        name = f"Worker 容器内存（{worker.hostname or worker.pid}）"
        check_resource(name, worker.memory.percent, MEMORY_WARN, MEMORY_CRITICAL)
        check_resource(
            f"Worker 容器 CPU（{worker.hostname or worker.pid}）",
            worker.cpu_percent,
            CPU_WARN,
            CPU_CRITICAL,
        )
    check_resource("结果目录磁盘", disk.percent, DISK_WARN, DISK_CRITICAL)

    redis_dep = dependencies.get("redis")
    mongo_dep = dependencies.get("mongo")
    if redis_dep is not None and not redis_dep.ok:
        escalate("critical", "Redis 不可用，任务队列与运行指标无法获取")
    if mongo_dep is not None and not mongo_dep.ok:
        escalate("critical", "MongoDB 不可用，登录与审计日志无法读写")

    if execution_config.use_celery() and redis_dep is not None and redis_dep.ok:
        if not worker_summary.online:
            if worker_age is None:
                escalate("critical", "Worker 心跳失联，当前没有可用的执行进程")
            else:
                escalate(
                    "critical",
                    f"Worker 心跳已 {worker_age:.0f} 秒未更新（阈值 {WORKER_STALE_S:.0f} 秒）",
                )

    if queue.length is not None and queue.concurrency:
        if queue.running >= queue.concurrency and queue.length > 0:
            escalate(
                "warning",
                f"执行进程已满（{queue.running}/{queue.concurrency}），"
                f"队列还有 {queue.length} 个任务等待",
            )

    return AuditHealth(
        level=level,
        label=_LEVEL_LABELS.get(level, level),
        reasons=reasons,
    )


# ---------------------------------------------------------------- 入口


async def build_system_payload() -> AuditSystemResponse:
    now = time.time()
    dependencies = await _collect_dependencies()
    redis_ok = dependencies["redis"].ok
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _collect_sync, redis_ok, now)

    api_raw = data.get("api")
    api = _container(api_raw) if isinstance(api_raw, dict) else AuditContainerInfo(pid=0)
    worker_raw = [item for item in data.get("workers") or [] if isinstance(item, dict)]
    workers = [_container(item) for item in worker_raw]
    worker_summary, worker_age = _worker_summary(worker_raw, now)

    processes = _process_rows(data.get("active") or [], worker_raw, now)
    running = sum(1 for row in processes if row.status == "running")
    worker_summary.running_jobs = running
    users = _user_rows(processes)

    queue = AuditQueueInfo(
        length=data.get("queue_length"),
        running=running,
        concurrency=_concurrency(worker_raw) if redis_ok else None,
    )
    disk = _resource(data.get("disk"))
    health = _build_health(
        api=api,
        workers=workers,
        worker_summary=worker_summary,
        worker_age=worker_age,
        disk=disk,
        dependencies=dependencies,
        queue=queue,
    )

    return AuditSystemResponse(
        generated_at=_utc_iso(now) or "",
        mode=execution_config.job_executor(),
        health=health,
        api=api,
        workers=workers,
        worker_summary=worker_summary,
        queue=queue,
        disk=disk,
        processes=processes,
        users=users,
        dependencies=dependencies,
    )


def build_history(minutes: int = 30) -> AuditHistoryResponse:
    points, available = metrics.read_history(minutes)
    if not available:
        return AuditHistoryResponse(
            points=[],
            available=False,
            warning="Redis 不可用，无法读取历史趋势",
        )
    return AuditHistoryResponse(
        points=[AuditHistoryPoint(**point) for point in points],
        available=True,
    )
