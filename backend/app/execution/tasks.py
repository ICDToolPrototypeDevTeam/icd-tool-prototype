# -*- coding: utf-8 -*-
"""Celery 任务：反向管线在 Worker 子进程中执行。

执行顺序（每一步都是必要的）：

1. 安装 stdout Tee（Celery 可能替换过 ``sys.stdout``，否则 Worker 里的 print
   不会进入任务日志）；
2. 回读前端保存的运行时模型设置（Worker 启动后用户可能改过模型名/思考开关）；
3. 读 ``job.json`` 并校验 ``run_token`` —— 队列里的旧消息不得执行新任务；
4. Redis 原子 claim —— 被取消/放弃/已被别的子进程占用的消息直接丢弃；
5. 起心跳线程（续租 + 消费 cancel/force）；
6. 按 manifest 重建参数，复用 API 进程同一个 ``run_v4_pipeline_thread``；
7. finally 释放租约（被新运行接管时不删新租约）。

任务状态仍由 ``run_v4_pipeline_thread`` 写 ``job.json``；本模块只在「执行入口
之外」的异常上兜底，避免任务永远停在 running。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from app.execution import config as execution_config
from app.execution import control
from app.execution.celery_app import celery_app
from app.job_log import (
    bind_job_log,
    ensure_log_tee,
    restore_job_log,
    set_config_lines,
)
from app.job_manager import MANIFEST_NAME, Job, JobCancelled, JobStatus

_TERMINAL = frozenset({
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELED,
    JobStatus.ABANDONED,
})


def _safe_job_dir(job_id: str) -> Optional[Path]:
    """任务目录必须落在 OUTPUT_DIR/v4 下；消息里的 job_id 只允许单段目录名。"""
    if not job_id or job_id in (".", "..") or "/" in job_id or "\\" in job_id:
        return None
    from app.v4.config import get_output_root

    return get_output_root() / "v4" / job_id


def load_worker_job(job_dir: Path, run_token: str) -> Optional[Job]:
    """按 manifest 重建任务；代次不符 / 已终态 / 文件损坏 → None（消息丢弃）。"""
    manifest = job_dir / MANIFEST_NAME
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — 消息先于 manifest / 文件损坏
        print(
            f"[worker] cannot read manifest {manifest}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("run_token") or "") != run_token:
        # 旧代次的队列消息：任务已被续跑/终止，不能执行
        return None
    try:
        job = Job.from_manifest(data, job_dir)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[worker] invalid manifest {manifest}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None
    if job.status in _TERMINAL:
        return None
    return job


def _fail_if_not_terminal(job: Job, exc: BaseException) -> None:
    """执行入口之外的异常兜底：任务尚未落终态时按统一规则落 failed。"""
    if job.status in _TERMINAL:
        return
    try:
        from app.api.v4.runner import _fail_job

        _fail_job(job, exc, "V4 pipeline")
    except Exception as inner:  # noqa: BLE001 — 兜底失败也不能让异常消失
        print(
            f"[worker] failed to mark job {job.job_id} failed: "
            f"{type(inner).__name__}: {inner}",
            file=sys.stderr,
        )


@celery_app.task(bind=True, name="icd.reverse.run", acks_late=True)
def run_reverse_job(self, job_id: str, run_token: str) -> None:
    """反向管线任务。参数只有 id 与代次票据，所有运行参数从 manifest 重建。"""
    ensure_log_tee()

    try:
        from app.runtime_settings import load_persisted_settings

        load_persisted_settings()
    except Exception as exc:  # noqa: BLE001 — 设置损坏时回落部署配置，不阻断任务
        print(
            f"[worker] load persisted settings failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    set_config_lines(execution_config.config_snapshot())

    job_dir = _safe_job_dir(job_id)
    if job_dir is None:
        print(f"[worker] invalid job_id from queue: {job_id!r}", file=sys.stderr)
        return

    job = load_worker_job(job_dir, run_token)
    if job is None:
        print(
            f"[worker] skip stale or finished job {job_id} token={run_token[:8]}",
            file=sys.stderr,
        )
        return

    ctl = control.JobControl(job_id)
    try:
        claimed = ctl.claim(run_token)
    except control.RedisUnavailable as exc:
        # 控制面不可用：任务尚未执行。落 interrupted（可续跑），不要留 running。
        job.update(
            JobStatus.INTERRUPTED,
            f"任务队列控制服务不可用，未开始执行：{exc}",
        )
        return
    if not claimed:
        print(
            f"[worker] claim refused for {job_id} "
            f"(canceled/abandoned/already claimed)",
            file=sys.stderr,
        )
        return

    def _on_cancel() -> None:
        job.cancel_requested = True
        job.cancel_event.set()

    prev_binding = bind_job_log(job_id, job)
    heartbeat = ctl.start_heartbeat(run_token, on_cancel=_on_cancel)
    try:
        from app.api.v4.runner import execute_reverse_job

        execute_reverse_job(job, job_dir)
    except JobCancelled:
        # run_v4_pipeline_thread 已落 canceled；这里只需让 Celery 正常 ack
        pass
    except Exception as exc:
        _fail_if_not_terminal(job, exc)
        raise
    finally:
        heartbeat.stop()
        restore_job_log(prev_binding)
        try:
            ctl.release(run_token)
        except control.RedisUnavailable as exc:
            # 租约会随 TTL 过期；reaper 也能按 active 分数收尾
            print(
                f"[worker] release lease failed for {job_id}: {exc}",
                file=sys.stderr,
            )
