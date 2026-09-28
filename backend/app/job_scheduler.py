# -*- coding: utf-8 -*-
"""进程内任务队列调度器（多输入批量并行 Step 2）。

背景：任务原先「登记后立即起线程」——同一时刻可以有任意多个管线在跑。批量提交
（同一控制器的多份输入一次上传）时，2C2G 的部署机上同时跑 2 个以上任务就会把
整机拖进 swap 抖动（单任务峰值 300~500MB，见 BUG-20260923-008）。运行参数已按
任务隔离（app.runtime_context）解决「串台」，本模块解决「并发数不受控」。

职责：
- :func:`submit` 把已登记、已落盘参数快照的任务执行函数放进 FIFO 队列，由固定
  ``MAX_CONCURRENT_JOBS``（默认 2）个常驻 daemon 工作线程顺序领取执行；
- 出队时做两项裁决：
  1. **票据比对**——队列里可能留着同一任务的旧条目（排队期间被终止、之后又被
     「继续」）：只有 ``job.queue_token`` 与条目一致才执行，防止同一任务被两张
     票跑两遍（甚至被两个工作线程同时跑）；
  2. **跳过裁决**——排队期间被终止 / 强制终止 / 放弃的任务不执行，落 canceled
     终态并往该任务日志写一行原因，用户在前端能看到「为什么没跑」。

工作线程常驻且会被下一个任务复用，因此本模块要求执行函数返回前清理干净自己的
线程局部状态——``run_*_pipeline_thread`` 的 finally 已做（job_log / runtime_context
/ thread ident 三处），本模块自身不绑定任何 thread-local。CLI 不经过本模块。
"""
from __future__ import annotations

import os
import queue
import threading
import traceback
from pathlib import Path
from typing import Callable, Optional

from app.job_log import (
    LOG_FILE_NAME,
    job_log_context,
    job_log_store,
)
from app.job_manager import Job, JobStatus


# 2C2G 部署机上单任务峰值 300~500MB，默认并发 2；内存更小可调成 1
DEFAULT_MAX_CONCURRENT_JOBS = 2


def _max_concurrent_jobs() -> int:
    """读取并发上限：非法值（空 / 非数字 / <1）一律回落默认 2。"""
    raw = os.getenv('MAX_CONCURRENT_JOBS', '')
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONCURRENT_JOBS
    return n if n >= 1 else DEFAULT_MAX_CONCURRENT_JOBS


# (job, 票据, 执行函数)；票据是 submit 时生成的哨兵对象，见模块 docstring
_queue: queue.Queue = queue.Queue()
_start_lock = threading.Lock()
_workers_started = False
_worker_count = 0


def max_concurrent_jobs() -> int:
    """当前生效的并发上限（工作线程启动后即固定；未启动时按 env 计算）。"""
    return _worker_count or _max_concurrent_jobs()


def _ensure_workers() -> None:
    global _workers_started, _worker_count
    with _start_lock:
        if _workers_started:
            return
        _worker_count = _max_concurrent_jobs()
        for i in range(_worker_count):
            threading.Thread(
                target=_worker_loop, name=f'job-worker-{i + 1}', daemon=True
            ).start()
        _workers_started = True
        print(f'[queue] 任务队列已启动：并发上限 {_worker_count}（MAX_CONCURRENT_JOBS）')


def submit(job: Job, fn: Callable[[], None]) -> None:
    """入队一个任务的执行函数（登记与参数快照落盘由调用方在此之前完成）。

    每次 submit 生成新票据并写入 ``job.queue_token``：同一任务若因「排队期间被
    终止 → 又被继续」而二次入队，旧条目出队时会因票据不符被丢弃。
    """
    _ensure_workers()
    token = object()
    job.queue_token = token
    # 入队即如实落「排队中」：此前只改 fresh 任务的 message，续跑（relaunch）在
    # 入队前已把状态置成 running（“任务继续执行中”），于是排队等待时列表显示成
    # 「正在分析」。任务真正开跑时由管线线程入口落 running + Step 1/N。
    job.update(
        JobStatus.PENDING,
        f'任务已排队，等待空闲执行槽位（并发上限 {_worker_count}）',
    )
    _queue.put((job, token, fn))


def _skip_reason(job: Job) -> Optional[str]:
    """出队裁决：返回跳过执行的原因（None = 正常执行）。"""
    if job.hard_killed:
        return '已被强制终止'
    if job.status == JobStatus.ABANDONED:
        return '已被放弃'
    if job.status == JobStatus.CANCELED or job.cancel_event.is_set():
        return '已被终止'
    return None


def _mark_skipped(job: Job, reason: str) -> None:
    """跳过执行：落 canceled（若非终态）+ 往本任务日志写一行原因。"""
    if job.status not in (JobStatus.CANCELED, JobStatus.ABANDONED):
        # 走到这里说明 cancel 标志置位但状态还没落终态（如端点侧未覆盖的竞态），
        # 补一次，保证「跳过」不会给用户留下一条永远 pending 的幽灵任务
        job.update(JobStatus.CANCELED, f'任务在排队等待期间{reason}，未开始执行')
    try:
        job_dir = getattr(job, 'job_dir', None)
        if job_dir is not None:
            # 先带磁盘路径注册，让这行同时落进 job.log（日志面板与历史页都从它读）
            job_log_store.register(job.job_id, Path(job_dir) / LOG_FILE_NAME)
    except Exception:  # noqa: BLE001 — 日志缓冲注册失败不得影响裁决
        pass
    with job_log_context(job.job_id, job):
        print(f'[queue] 任务在排队等待期间{reason}，跳过执行（未开始任何步骤）')


def _worker_loop() -> None:
    while True:
        job, token, fn = _queue.get()
        try:
            if job.queue_token is not token:
                # 旧票据：该任务排队期间被终止后又「继续」，本次重新入队已取代
                # 这张票；两张票都执行会让同一任务跑两遍（甚至并发跑两遍）。
                continue
            reason = _skip_reason(job)
            if reason is not None:
                _mark_skipped(job, reason)
                continue
            fn()
        except BaseException:  # noqa: BLE001 — 含逃逸的 JobCancelled 与任何管线异常
            # 单任务异常绝不能杀死工作线程，否则后续排队任务全部饿死
            with job_log_context(job.job_id, job):
                traceback.print_exc()
        finally:
            _queue.task_done()
