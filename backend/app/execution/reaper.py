# -*- coding: utf-8 -*-
"""失联 Worker 巡检（只在 API 进程内运行）。

为什么需要它：``acks_late`` + 不自动重投意味着 Worker 子进程被 OOM/强杀后，
队列消息不会再来第二次；如果没人收尾，``job.json`` 会永远停在 ``running``，
用户既不能续跑也不能放弃。巡检按 Redis active 集合找心跳超时的任务：

- 有 cancel/force 意图 → 落 ``canceled``（用户点过终止，只是进程没来得及写）；
- 有 abandon 意图      → 落 ``abandoned``；
- 否则                 → 落 ``interrupted``（可续跑）。

Redis 不可达时**本轮不做任何判断**：不能把「控制面暂时不可用」当成「Worker
已死」，否则一次网络抖动会把所有长任务误标为中断。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.execution import config as execution_config
from app.execution import control
from app.job_manager import MANIFEST_NAME, Job, JobStatus

_ACTIVE_STATUSES = (JobStatus.PENDING.value, JobStatus.RUNNING.value)


def _parse_job(job_dir: Path) -> Optional[Job]:
    try:
        data = json.loads((job_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 目录可能刚被历史页删除
        return None
    try:
        return Job.from_manifest(data, job_dir)
    except Exception:  # noqa: BLE001 — 坏 manifest 不阻断其他任务
        return None


def reap_once(v4_root: Path, *, now: Optional[float] = None) -> int:
    """巡检一轮，返回收尾的任务数；Redis 不可达返回 ``-1``（本轮跳过）。"""
    current = time.time() if now is None else now
    cutoff = current - execution_config.stale_seconds()

    try:
        stale_ids = control.stale_active(cutoff)
    except control.RedisUnavailable as exc:
        print(
            f"[reaper] redis unavailable, skip this round: {exc}",
            file=sys.stderr,
        )
        return -1

    reaped = 0
    for job_id in stale_ids:
        try:
            ctl = control.snapshot(job_id)
        except control.RedisUnavailable as exc:
            print(f"[reaper] redis unavailable mid-scan: {exc}", file=sys.stderr)
            return reaped
        if ctl is None:
            # 与上一处相同：无法判断就不改任何状态
            return reaped

        if not ctl:
            # 控制键已被 TTL 清掉、active 里只剩旧分数；若 Worker 还活着，
            # 下一拍心跳会重新登记，这里先移除旧记录。
            _clear_quietly(job_id)
            continue

        if control.is_fresh(ctl, now=current):
            # 心跳新鲜但 zset 分数落后（例如 Redis 重启后恢复）：只修分数
            try:
                heart = float(ctl.get("heartbeat_at") or current)
                control.refresh_active(job_id, heart)
            except (TypeError, ValueError, control.RedisUnavailable):
                pass
            continue

        job_dir = v4_root / job_id
        job = _parse_job(job_dir)
        if job is None:
            _clear_quietly(job_id)
            continue
        if job.status.value not in _ACTIVE_STATUSES:
            # Worker 已经落了终态（可能只是没来得及 release），不再覆盖
            _clear_quietly(job_id)
            continue

        if ctl.get("abandon"):
            job.status = JobStatus.ABANDONED
            job.message = "Worker 已失联，任务按用户先前的放弃请求收尾"
            job.finished_at = datetime.now(timezone.utc)
        elif ctl.get("cancel") or ctl.get("force"):
            job.status = JobStatus.CANCELED
            job.message = "Worker 已失联，任务按用户先前的终止请求收尾"
            job.finished_at = datetime.now(timezone.utc)
        else:
            job.status = JobStatus.INTERRUPTED
            job.message = (
                f"Worker 心跳超时（>{int(execution_config.stale_seconds())}s），"
                "任务已中断，可继续执行"
            )
            # interrupted 不是终态：保留原 finished_at / updated_at 语义
        try:
            job._persist()
        except Exception as exc:  # noqa: BLE001 — _persist 本身静默，这里只防御
            print(
                f"[reaper] persist {job_id} failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue
        _clear_quietly(job_id)
        reaped += 1

    return reaped


def _clear_quietly(job_id: str) -> None:
    try:
        control.clear(job_id)
    except control.RedisUnavailable:
        pass


def start_reaper(v4_root: Path) -> threading.Event:
    """启动后台巡检线程，返回停止事件（lifespan 结束时 set）。"""
    stop = threading.Event()

    def _loop() -> None:
        interval = execution_config.reaper_interval_s()
        while not stop.wait(interval):
            try:
                count = reap_once(v4_root)
                if count > 0:
                    print(f"[reaper] {count} stale job(s) reaped")
            except Exception as exc:  # noqa: BLE001 — 巡检线程不能死
                print(
                    f"[reaper] unexpected error: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )

    threading.Thread(target=_loop, daemon=True, name="job-reaper").start()
    return stop
