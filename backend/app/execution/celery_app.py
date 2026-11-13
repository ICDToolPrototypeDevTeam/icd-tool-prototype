# -*- coding: utf-8 -*-
"""Celery 应用定义（Worker 进程入口：``celery -A app.execution.celery_app worker``）。

关键配置及原因：

- ``task_ignore_result=True`` / 无 result backend：任务状态权威来源是共享卷上的
  ``job.json``，不需要 Celery 再存一份结果，也避免 Redis 里堆积 24h 任务的结果。
- ``task_acks_late=True`` + ``task_reject_on_worker_lost=False``：任务在子进程
  真正结束后才 ack；子进程被 OOM/强杀时不自动重投（否则同一任务可能被跑两遍）。
  需要续跑时由用户在页面上点「继续」，生成新的 ``run_token`` 重新入队。
- ``worker_prefetch_multiplier=1``：一个子进程一次只预取一个任务，避免 20 个
  长任务被少数子进程囤住、其他子进程空转。
- ``worker_max_tasks_per_child=1``：每个子进程跑完一个任务就退出，24h+ 任务
  不会把内存碎片/泄漏带进下一个任务；强制终止时 ``os._exit`` 也只影响本任务。
- ``worker_redirect_stdouts=False``：不能把 ``print`` 重定向到 Celery 自己的
  logger —— 项目用 stdout Tee 采集任务日志（app/job_log.py），重定向会丢日志。
- ``visibility_timeout=30 天``：默认 1 小时会把运行超过 1 小时的任务重新投递，
  这里必须大于最长任务时长。
"""
from __future__ import annotations

from celery import Celery

from app.execution import config

celery_app = Celery("icd", broker=config.broker_url())

celery_app.conf.update(
    task_ignore_result=True,
    result_backend=None,
    task_acks_late=True,
    task_reject_on_worker_lost=False,
    task_acks_on_failure_or_timeout=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1,
    worker_redirect_stdouts=False,
    worker_hijack_root_logger=False,
    worker_send_task_events=False,
    task_send_sent_event=False,
    worker_cancel_long_running_tasks_on_connection_loss=False,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": config.lease_ttl_s(),
        "socket_connect_timeout": 5,
        "socket_timeout": 30,
    },
    task_default_queue=config.queue_name(),
    task_default_exchange=config.queue_name(),
    task_default_routing_key=config.queue_name(),
    imports=("app.execution.tasks",),
    timezone="UTC",
    enable_utc=True,
)

# ``celery -A app.execution.celery_app`` 会依次找模块里的 app / celery / 模块名
# 属性；显式别名保证三种写法都能拿到同一个实例。
app = celery_app
celery = celery_app
