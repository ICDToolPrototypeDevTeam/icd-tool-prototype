# -*- coding: utf-8 -*-
"""运行状态指标采集。

指标分两类：

- **容器指标**：API 容器由 API 进程每 5 秒采集一次；Worker 容器由 Worker 父进程
  的 reporter 线程每 5 秒采集一次。内存优先读 cgroup（当前值减去 inactive_file，
  接近 ``docker stats`` 的口径），CPU 优先读 cgroup ``cpu.stat`` 的累计用量，
  按 cgroup CPU 配额（没有配额则按宿主机核数）归一化为 0–100%。
- **任务进程指标**：Worker 父进程枚举直接子进程（每个 Celery 子进程一次只跑
  一个任务）的 RSS / CPU，任务与 PID 的映射来自 Redis 控制面 ``icd:job:*:ctl``。

数据写入 Redis：

- ``icd:metrics:api``                API 容器快照，TTL 30s；
- ``icd:metrics:worker:{hostname}``  Worker 容器快照，TTL 20s；
- ``icd:metrics:history``            近 30 分钟趋势点（最多 720 个）。

本模块不挂 ``docker.sock``，只从当前容器内部采集，因此不依赖宿主机权限。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.execution import config as execution_config
from app.execution import control

try:  # psutil 缺失时降级为「仅 Redis / 队列指标」，不影响服务启动
    import psutil  # type: ignore
except Exception:  # noqa: BLE001
    psutil = None  # type: ignore

API_KEY = "icd:metrics:api"
WORKER_KEY_PREFIX = "icd:metrics:worker:"
HISTORY_KEY = "icd:metrics:history"

API_TTL_S = 30
WORKER_TTL_S = 20
HISTORY_MAX_POINTS = 720
HISTORY_TTL_S = 3600
SAMPLE_INTERVAL_S = 5.0
WORKER_STALE_S = 20.0

_CGROUP_ROOT = Path("/sys/fs/cgroup")


def _utc_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(
        time.time() if ts is None else ts, timezone.utc
    ).isoformat()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    text = _read_text(path)
    if text is None or not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_kv(text: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not text:
        return result
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            result[parts[0]] = parts[1].strip()
        elif len(parts) == 1:
            result[parts[0]] = ""
    return result


def _percent(used: int | float | None, limit: int | float | None) -> float | None:
    if used is None or not limit or limit <= 0:
        return None
    return round(max(0.0, min(100.0, float(used) / float(limit) * 100.0)), 1)


def _safe_rss(proc) -> int:
    try:
        return int(proc.memory_info().rss)
    except Exception:  # noqa: BLE001 — 进程刚退出 / 无权限
        return 0


def _process_tree_rss(proc) -> int:
    if proc is None:
        return 0
    total = _safe_rss(proc)
    try:
        children = proc.children(recursive=True)
    except Exception:  # noqa: BLE001
        children = []
    for child in children:
        total += _safe_rss(child)
    return total


def read_container_memory(proc=None) -> dict[str, Any]:
    """读容器内存。返回 ``used_bytes`` / ``limit_bytes`` / ``percent`` / ``source``。

    cgroup v2 与 v1 都兼容；内存用 ``current - inactive_file`` 的口径，避免把
    可回收页缓存算成业务占用（否则容器刚启动就可能显示接近 100%）。
    """
    current = _read_int(_CGROUP_ROOT / "memory.current")
    if current is not None:
        max_raw = _read_text(_CGROUP_ROOT / "memory.max")
        limit = None if not max_raw or max_raw == "max" else int(max_raw)
        stat = _parse_kv(_read_text(_CGROUP_ROOT / "memory.stat"))
        try:
            inactive = int(stat.get("inactive_file", "0") or 0)
        except ValueError:
            inactive = 0
        used = max(0, current - inactive)
        return {
            "used_bytes": used,
            "limit_bytes": limit,
            "percent": _percent(used, limit),
            "source": "cgroup",
        }

    usage = _read_int(_CGROUP_ROOT / "memory" / "memory.usage_in_bytes")
    if usage is not None:
        limit = _read_int(_CGROUP_ROOT / "memory" / "memory.limit_in_bytes")
        if limit is not None and limit > (1 << 60):
            limit = None
        stat = _parse_kv(_read_text(_CGROUP_ROOT / "memory" / "memory.stat"))
        try:
            inactive = int(
                stat.get("total_inactive_file", stat.get("inactive_file", "0")) or 0
            )
        except ValueError:
            inactive = 0
        used = max(0, usage - inactive)
        return {
            "used_bytes": used,
            "limit_bytes": limit,
            "percent": _percent(used, limit),
            "source": "cgroup",
        }

    used = _process_tree_rss(proc) if proc is not None else None
    return {
        "used_bytes": used,
        "limit_bytes": None,
        "percent": None,
        "source": "process" if used is not None else "unavailable",
    }


def read_cpu_quota_cores() -> float | None:
    """cgroup CPU 配额（核数）；没有配额返回 None（调用方回退宿主机核数）。"""
    cpu_max = _read_text(_CGROUP_ROOT / "cpu.max")
    if cpu_max:
        parts = cpu_max.split()
        if len(parts) >= 2 and parts[0] != "max":
            try:
                quota = float(parts[0])
                period = float(parts[1])
                if quota > 0 and period > 0:
                    return quota / period
            except ValueError:
                pass

    quota = _read_int(_CGROUP_ROOT / "cpu" / "cpu.cfs_quota_us")
    period = _read_int(_CGROUP_ROOT / "cpu" / "cpu.cfs_period_us")
    if quota is not None and period:
        if quota > 0 and period > 0:
            return float(quota) / float(period)
    return None


def read_cpu_usage_usec() -> float | None:
    """cgroup 累计 CPU 时间（微秒）；读不到返回 None。"""
    stat = _parse_kv(_read_text(_CGROUP_ROOT / "cpu.stat"))
    try:
        if "usage_usec" in stat:
            return float(stat["usage_usec"])
    except (TypeError, ValueError):
        pass
    usage_ns = _read_int(_CGROUP_ROOT / "cpuacct" / "cpuacct.usage")
    if usage_ns is not None:
        return float(usage_ns) / 1000.0
    return None


class ContainerCpuSampler:
    """用 cgroup 累计 CPU 时间计算两次采样之间的容器 CPU 百分比。"""

    def __init__(self) -> None:
        quota = read_cpu_quota_cores()
        self.available_cores = quota or float(os.cpu_count() or 1)
        self.source = "cgroup_quota" if quota else "host"
        self._last_usage: float | None = None
        self._last_ts: float | None = None

    def sample(self) -> float | None:
        usage = read_cpu_usage_usec()
        now = time.monotonic()
        percent: float | None = None
        if usage is not None and self._last_usage is not None and self._last_ts:
            elapsed = now - self._last_ts
            delta = usage - self._last_usage
            if elapsed > 0 and delta >= 0:
                percent = (
                    delta
                    / (elapsed * 1_000_000.0 * max(self.available_cores, 0.01))
                    * 100.0
                )
                percent = round(max(0.0, min(100.0, percent)), 1)
        self._last_usage = usage
        self._last_ts = now
        return percent


def _tree_cpu_percent(proc, cores: float) -> float | None:
    if proc is None or psutil is None:
        return None
    try:
        raw = float(proc.cpu_percent(None))
        for child in proc.children(recursive=True):
            try:
                raw += float(child.cpu_percent(None))
            except Exception:  # noqa: BLE001
                continue
        return round(max(0.0, min(100.0, raw / max(cores, 0.01))), 1)
    except Exception:  # noqa: BLE001
        return None


_api_cpu_sampler = ContainerCpuSampler()


def collect_api_snapshot() -> dict[str, Any]:
    """采集 API 进程 / API 容器的当前快照。"""
    now = time.time()
    snapshot: dict[str, Any] = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "rss_bytes": 0,
        "cpu_percent": None,
        "threads": None,
        "started_at": None,
        "uptime_seconds": None,
        "memory": {
            "used_bytes": None,
            "limit_bytes": None,
            "percent": None,
            "source": "unavailable",
        },
        "cpu_available_cores": _api_cpu_sampler.available_cores,
        "cpu_source": _api_cpu_sampler.source,
        "updated_at": _utc_iso(now),
        "pool_size": None,
        "children": [],
    }
    if psutil is None:
        return snapshot

    try:
        proc = psutil.Process(os.getpid())
    except Exception:  # noqa: BLE001
        return snapshot

    snapshot["rss_bytes"] = _safe_rss(proc)
    snapshot["memory"] = read_container_memory(proc)
    try:
        snapshot["threads"] = int(proc.num_threads())
        created = float(proc.create_time())
        snapshot["started_at"] = _utc_iso(created)
        snapshot["uptime_seconds"] = max(0, int(now - created))
    except Exception:  # noqa: BLE001
        pass

    cpu = _api_cpu_sampler.sample()
    if cpu is None:
        cpu = _tree_cpu_percent(proc, _api_cpu_sampler.available_cores)
    snapshot["cpu_percent"] = cpu
    return snapshot


def collect_worker_snapshot(
    parent,
    children_cache: dict[int, Any],
    sampler: ContainerCpuSampler,
    concurrency: int,
) -> dict[str, Any]:
    """采集 Worker 父进程 + 直接子进程（Celery pool workers）的快照。"""
    now = time.time()
    snapshot: dict[str, Any] = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "rss_bytes": _safe_rss(parent),
        "cpu_percent": None,
        "threads": None,
        "started_at": None,
        "uptime_seconds": None,
        "memory": read_container_memory(parent),
        "cpu_available_cores": sampler.available_cores,
        "cpu_source": sampler.source,
        "updated_at": _utc_iso(now),
        "pool_size": int(concurrency),
        "children": [],
    }
    try:
        snapshot["threads"] = int(parent.num_threads())
        created = float(parent.create_time())
        snapshot["started_at"] = _utc_iso(created)
        snapshot["uptime_seconds"] = max(0, int(now - created))
    except Exception:  # noqa: BLE001
        pass

    try:
        direct = parent.children(recursive=False)
    except Exception:  # noqa: BLE001
        direct = []
    current = {child.pid: child for child in direct}
    for pid in list(children_cache):
        if pid not in current:
            children_cache.pop(pid, None)

    children: list[dict[str, Any]] = []
    for pid, fresh in current.items():
        proc = children_cache.get(pid)
        if proc is None:
            proc = fresh
            children_cache[pid] = proc
            try:
                proc.cpu_percent(None)  # 第一次调用建立基准
            except Exception:  # noqa: BLE001
                pass
        try:
            cpu_raw = float(proc.cpu_percent(None))
            children.append(
                {
                    "pid": pid,
                    "rss_bytes": _safe_rss(proc),
                    "cpu_percent": round(
                        max(
                            0.0,
                            min(100.0, cpu_raw / max(sampler.available_cores, 0.01)),
                        ),
                        1,
                    ),
                    "status": str(proc.status()),
                    "create_time": _utc_iso(float(proc.create_time())),
                }
            )
        except Exception:  # noqa: BLE001 — 子进程刚退出
            continue
    snapshot["children"] = children

    cpu = sampler.sample()
    if cpu is None:
        cpu = _tree_cpu_percent(parent, sampler.available_cores)
    snapshot["cpu_percent"] = cpu
    return snapshot


# ---------------------------------------------------------------- Redis


def _redis_op(operation, default=None):
    try:
        return operation(control.get_client())
    except control.RedisUnavailable:
        return default
    except Exception:  # noqa: BLE001 — 指标写失败不能影响业务/任务
        return default


def _json_set(key: str, payload: dict[str, Any], ttl: int) -> None:
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    _redis_op(lambda client: client.set(key, raw, ex=ttl))


def _json_get(key: str) -> dict[str, Any] | None:
    raw = _redis_op(lambda client: client.get(key))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError):
        return None


def publish_api_snapshot(snapshot: dict[str, Any]) -> None:
    _json_set(API_KEY, snapshot, API_TTL_S)


def read_api_snapshot() -> dict[str, Any] | None:
    return _json_get(API_KEY)


def publish_worker_snapshot(hostname: str, snapshot: dict[str, Any]) -> None:
    _json_set(f"{WORKER_KEY_PREFIX}{hostname}", snapshot, WORKER_TTL_S)


def read_worker_snapshots() -> list[dict[str, Any]]:
    def op(client):
        keys = list(client.scan_iter(match=f"{WORKER_KEY_PREFIX}*", count=50))
        if not keys:
            return []
        values = client.mget(keys)
        result: list[dict[str, Any]] = []
        for raw in values or []:
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict):
                result.append(data)
        return result

    workers = _redis_op(op, default=[]) or []
    workers.sort(key=lambda item: str(item.get("updated_at") or ""))
    return workers


def append_history(point: dict[str, Any]) -> None:
    raw = json.dumps(point, ensure_ascii=False, default=str)

    def op(client):
        pipe = client.pipeline(transaction=True)
        pipe.rpush(HISTORY_KEY, raw)
        pipe.ltrim(HISTORY_KEY, -HISTORY_MAX_POINTS, -1)
        pipe.expire(HISTORY_KEY, HISTORY_TTL_S)
        return pipe.execute()

    _redis_op(op)


def read_history(minutes: int = 30) -> tuple[list[dict[str, Any]], bool]:
    """返回 ``(points, available)``；Redis 不可用时 available=False。"""
    raw = _redis_op(lambda client: client.lrange(HISTORY_KEY, 0, -1))
    if raw is None:
        return [], False
    cutoff = time.time() - max(5, int(minutes)) * 60
    points: list[dict[str, Any]] = []
    for item in raw:
        try:
            data = json.loads(item)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            ts = float(data.get("ts"))
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            points.append(data)
    points.sort(key=lambda item: float(item.get("ts") or 0))
    return points, True


def collect_disk_metric(path: Path) -> dict[str, Any]:
    try:
        if psutil is not None:
            usage = psutil.disk_usage(str(path))
            used, total, percent = usage.used, usage.total, usage.percent
        else:
            usage = shutil.disk_usage(str(path))
            used, total = usage.used, usage.total
            percent = used / total * 100 if total else None
        return {
            "used_bytes": int(used),
            "limit_bytes": int(total),
            "percent": round(float(percent), 1) if percent is not None else None,
            "source": "filesystem",
        }
    except OSError:
        return {
            "used_bytes": None,
            "limit_bytes": None,
            "percent": None,
            "source": "unavailable",
        }


# ---------------------------------------------------------------- 采样循环


_worker_stop: threading.Event | None = None
_worker_thread: threading.Thread | None = None

_api_task: asyncio.Task | None = None
_api_stop: asyncio.Event | None = None


def start_worker_reporter(concurrency: int) -> None:
    """Worker 父进程启动 reporter 线程（``worker_ready`` 信号调用）。"""
    global _worker_stop, _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    if psutil is None:
        print(
            "[metrics] psutil 未安装，Worker 容器指标不可用",
            file=sys.stderr,
        )
        return

    stop = threading.Event()
    _worker_stop = stop
    hostname = socket.gethostname()
    pool_size = max(1, int(concurrency or 1))

    def loop() -> None:
        try:
            parent = psutil.Process(os.getpid())
        except Exception as exc:  # noqa: BLE001
            print(
                f"[metrics] cannot start worker reporter: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return
        sampler = ContainerCpuSampler()
        cache: dict[int, Any] = {}
        while not stop.is_set():
            try:
                snapshot = collect_worker_snapshot(
                    parent, cache, sampler, pool_size
                )
                publish_worker_snapshot(hostname, snapshot)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[metrics] worker sample failed: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
            stop.wait(SAMPLE_INTERVAL_S)

    thread = threading.Thread(
        target=loop, daemon=True, name="worker-metrics-reporter"
    )
    _worker_thread = thread
    thread.start()


def stop_worker_reporter() -> None:
    global _worker_stop, _worker_thread
    if _worker_stop is not None:
        _worker_stop.set()
    if _worker_thread is not None and _worker_thread.is_alive():
        _worker_thread.join(timeout=2.0)
    _worker_stop = None
    _worker_thread = None


def sample_once() -> None:
    """采集一次 API 快照 + 合并趋势点；由 API 采样任务在线程池中调用。"""
    api = collect_api_snapshot()
    publish_api_snapshot(api)

    workers = read_worker_snapshots()
    worker_rss = 0
    worker_cpu = 0.0
    worker_mem_values: list[float] = []
    for worker in workers:
        memory = worker.get("memory") or {}
        worker_rss += int(memory.get("used_bytes") or 0)
        cpu = worker.get("cpu_percent")
        if cpu is not None:
            worker_cpu += float(cpu)
        pct = memory.get("percent")
        if pct is not None:
            worker_mem_values.append(float(pct))

    running: int | None
    try:
        running = control.active_count()
    except control.RedisUnavailable:
        running = None

    append_history(
        {
            "ts": time.time(),
            "api_rss_bytes": api.get("rss_bytes"),
            "api_cpu_percent": api.get("cpu_percent"),
            "api_memory_percent": (api.get("memory") or {}).get("percent"),
            "worker_rss_bytes": worker_rss or None,
            "worker_cpu_percent": round(worker_cpu, 1) if workers else None,
            "worker_memory_percent": (
                round(max(worker_mem_values), 1) if worker_mem_values else None
            ),
            "running_jobs": running,
        }
    )


def start_api_metrics_sampler() -> None:
    global _api_task, _api_stop
    if _api_task is not None and not _api_task.done():
        return
    _api_stop = asyncio.Event()
    _api_task = asyncio.create_task(
        _api_sampler_loop(_api_stop), name="api-metrics-sampler"
    )


async def _api_sampler_loop(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        try:
            await loop.run_in_executor(None, sample_once)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[metrics] api sample failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=SAMPLE_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


async def stop_api_metrics_sampler() -> None:
    global _api_task, _api_stop
    stop = _api_stop
    task = _api_task
    if stop is not None:
        stop.set()
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()
        except Exception:  # noqa: BLE001
            pass
    _api_task = None
    _api_stop = None


def worker_concurrency() -> int | None:
    """展示用并发上限：优先 Worker 快照的 pool_size，其次环境变量。"""
    workers = read_worker_snapshots()
    total = sum(int(worker.get("pool_size") or 0) for worker in workers)
    if total > 0:
        return total
    raw = (os.getenv("ICD_WORKER_CONCURRENCY") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    if execution_config.use_celery():
        return 20
    return None
