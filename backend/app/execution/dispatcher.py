# -*- coding: utf-8 -*-
"""API → Celery 的入队 / 撤销入口。

只在 ``JOB_EXECUTOR=celery`` 时被调用。Celery 与 Redis 的导入都推迟到函数内部：
inline 模式（单元测试 / 回滚）不应因为没装执行依赖而无法启动 API。
"""
from __future__ import annotations

import sys

from app.execution import config
from app.job_manager import Job, JobStatus


class QueueUnavailable(RuntimeError):
    """队列不可用（Redis 连不上 / Celery 未安装 / 发送失败）。路由据此返回 503。"""


def revoke_task_id(job: Job) -> str:
    """Celery 任务 id：``{job_id}:{run_token}``。

    不能直接用 ``job_id``：Celery 的 revoked 集合按 task id 记录，一次「终止」
    之后用同一个 job_id 续跑，Worker 会把新任务当成已撤销而直接丢弃。
    """
    if job.run_token:
        return f"{job.job_id}:{job.run_token}"
    # 旧 manifest 没有 run_token（理论上只有升级前的任务）：退回裸 id
    return job.job_id


def submit_reverse(job: Job) -> str:
    """把反向任务放进 Celery 队列，返回 task id。失败抛 :class:`QueueUnavailable`。"""
    if not job.run_token:
        raise QueueUnavailable("任务缺少 run_token，无法入队")

    from app.execution import control

    try:
        control.prepare(job.job_id, job.run_token)
    except control.RedisUnavailable as exc:
        raise QueueUnavailable(f"任务队列不可用: {exc}") from exc

    # manifest 在 send 之前落 pending：POST 返回时前端轮询就能看到排队状态。
    job.update(JobStatus.PENDING, "任务已排队，等待 Worker 执行…")

    task_id = revoke_task_id(job)
    try:
        from app.execution.celery_app import celery_app

        celery_app.send_task(
            "icd.reverse.run",
            args=[job.job_id, job.run_token],
            task_id=task_id,
            queue=config.queue_name(),
        )
    except Exception as exc:  # noqa: BLE001 — 连接失败 / 序列化失败都归为队列不可用
        try:
            control.clear(job.job_id)
        except Exception:  # noqa: BLE001 — 回滚失败不改变「入队失败」这个事实
            pass
        raise QueueUnavailable(
            f"任务入队失败: {type(exc).__name__}: {exc}"
        ) from exc
    return task_id


def revoke_reverse(job: Job) -> None:
    """通知 Celery 立即杀掉该任务的 Worker 子进程（强制终止的快速路径）。

    控制面已先写入 force 意图；即使这条广播丢失，Worker 心跳也会在下一周期
    读到 force 并自行退出，因此这里失败只记日志、不抛异常。
    """
    try:
        from app.execution.celery_app import celery_app

        celery_app.control.revoke(
            revoke_task_id(job), terminate=True, signal="SIGKILL"
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"[execution] revoke failed for {job.job_id}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
