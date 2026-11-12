# -*- coding: utf-8 -*-
"""执行层配置与启动自检。

两种模式：

- ``inline``：API 进程内的 :mod:`app.job_scheduler` 线程池（历史行为）。
  单元测试与 CLI 默认走这里，行为与引入 Celery 前完全一致。
- ``celery``：API 只负责接请求、落盘与入队；反向管线在独立 Worker 容器的
  子进程里执行。Docker 部署统一使用该模式。

环境变量（均有安全默认值）：

=====================  =====================================================
``JOB_EXECUTOR``       ``inline`` / ``celery``；空或非法 → inline（打警告）
``CELERY_BROKER_URL``  默认 ``redis://127.0.0.1:6379/0``
``CELERY_QUEUE``       默认 ``icd.reverse``（只接反向管线）
``FORWARD_PIPELINE_ENABLED``
                       正向管线开关；未设置时 inline 默认开、celery 默认关
``JOB_HEARTBEAT_INTERVAL_S``  Worker 心跳间隔，默认 30 秒
``JOB_STALE_SECONDS``  心跳超过该秒数视为 Worker 失联，默认 300 秒
``JOB_REAPER_INTERVAL_S`` API 侧巡检间隔，默认 60 秒
``JOB_LEASE_TTL_S``    Redis 控制键/心跳记录保留时长，默认 30 天（覆盖 24h+ 任务）
=====================  =====================================================
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit, urlunsplit

EXECUTOR_INLINE = "inline"
EXECUTOR_CELERY = "celery"
_VALID_EXECUTORS = frozenset({EXECUTOR_INLINE, EXECUTOR_CELERY})

DEFAULT_BROKER_URL = "redis://127.0.0.1:6379/0"
DEFAULT_QUEUE = "icd.reverse"

# 24 小时 + 的任务：可见性超时与租约都不能按默认的 1 小时算
DEFAULT_LEASE_TTL_S = 30 * 24 * 3600
DEFAULT_HEARTBEAT_INTERVAL_S = 30.0
DEFAULT_STALE_SECONDS = 300.0
DEFAULT_REAPER_INTERVAL_S = 60.0
DEFAULT_CONTROL_POLL_S = 5.0


def job_executor() -> str:
    """当前执行模式；非法值回退 inline 并打一行警告（不让服务起不来）。"""
    raw = (os.getenv("JOB_EXECUTOR") or "").strip().lower()
    if not raw:
        return EXECUTOR_INLINE
    if raw not in _VALID_EXECUTORS:
        print(
            f"[execution] invalid JOB_EXECUTOR={raw!r}; fallback to {EXECUTOR_INLINE}",
            file=sys.stderr,
        )
        return EXECUTOR_INLINE
    return raw


def use_celery() -> bool:
    return job_executor() == EXECUTOR_CELERY


def broker_url() -> str:
    return (os.getenv("CELERY_BROKER_URL") or "").strip() or DEFAULT_BROKER_URL


def queue_name() -> str:
    return (os.getenv("CELERY_QUEUE") or "").strip() or DEFAULT_QUEUE


def forward_enabled() -> bool:
    """正向管线是否允许提交。

    未显式配置时按执行模式给默认值：inline（测试 / 回滚）保持历史行为开启；
    celery 部署默认只接反向，正向入口保留但提交返回 503。
    """
    raw = os.getenv("FORWARD_PIPELINE_ENABLED")
    if raw is None or not raw.strip():
        return not use_celery()
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float, minimum: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        print(
            f"[execution] invalid {name}={raw!r}; use {default}",
            file=sys.stderr,
        )
        return default
    return max(minimum, value)


def heartbeat_interval_s() -> float:
    return _float_env("JOB_HEARTBEAT_INTERVAL_S", DEFAULT_HEARTBEAT_INTERVAL_S, 5.0)


def stale_seconds() -> float:
    return _float_env("JOB_STALE_SECONDS", DEFAULT_STALE_SECONDS, 60.0)


def reaper_interval_s() -> float:
    return _float_env("JOB_REAPER_INTERVAL_S", DEFAULT_REAPER_INTERVAL_S, 10.0)


def lease_ttl_s() -> int:
    return int(_float_env("JOB_LEASE_TTL_S", float(DEFAULT_LEASE_TTL_S), 3600.0))


def masked_broker_url() -> str:
    """用于日志的 broker 地址：保留 host/port/db，隐去密码。"""
    raw = broker_url()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "(invalid)"
    if not parts.password:
        return raw
    userinfo = parts.username or ""
    host = parts.hostname or ""
    if ":" in host:  # IPv6
        host = f"[{host}]"
    netloc = f"{userinfo}:***@{host}" if userinfo else f":***@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def validate_celery_runtime() -> list[str]:
    """启动自检：返回问题列表（空 = 依赖齐全）。只检查，不抛异常。"""
    problems: list[str] = []
    try:
        import celery  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        problems.append(f"celery 未安装或不可导入: {type(exc).__name__}: {exc}")
    try:
        import redis  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        problems.append(f"redis 未安装或不可导入: {type(exc).__name__}: {exc}")
    if not broker_url():
        problems.append("CELERY_BROKER_URL 为空")
    return problems


def config_snapshot() -> list[str]:
    """启动配置自检：只报「有没有」，绝不打印 key 内容。

    依据：实测中曾因「本地容器未关闭」误判 mock 生效情况，无从核对运行配置。
    本行会写进每个任务的日志头部，让「跑的是哪个模式 / 哪份配置」在 UI 上始终可见。
    在 Worker 进程里也会重新写一遍（运行时设置可能已被前端改过）。
    """
    from app.v4.config import get_output_root

    key_state = " ".join(
        f'{name}={"yes" if (os.getenv(f"{name}_API_KEY") or "").strip() else "no"}'
        for name in ("DEEPSEEK", "MINIMAX", "QWEN")
    )
    models = " / ".join(
        f'{name}={os.getenv(f"{name}_MODEL") or "(未配置)"}'
        for name in ("DEEPSEEK", "MINIMAX", "QWEN")
    )
    return [
        "[config] JOB_EXECUTOR={} | USE_MOCK_LLM={} (容器配置) | {} | "
        "JUDGE_PROVIDERS={} | OUTPUT_DIR={} | STATIC_DIR={}".format(
            job_executor(),
            os.getenv("USE_MOCK_LLM", "0"),
            key_state,
            os.getenv("JUDGE_PROVIDERS", "(默认)"),
            get_output_root(),
            os.getenv("ICD_STATIC_DIR", "(未设置)"),
        ),
        "[config] BROKER={} | QUEUE={} | FORWARD={}".format(
            masked_broker_url() if use_celery() else "(inline)",
            queue_name() if use_celery() else "(inline)",
            "enabled" if forward_enabled() else "disabled",
        ),
        f"[config] MODELS {models}",
    ]
