# -*- coding: utf-8 -*-
"""Degradation configuration: timeouts, circuit breaker, review caps."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: str) -> int:
    """读取正整数 env；未配置/非法/小于 1 时回落 default，避免 0 并发死锁。"""
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        value = int(default)
    return max(1, value)


def _positive_float(name: str, default: float) -> float:
    """读取正浮点 env；未配置/非法/小于等于 0 时回落 default。"""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return value if value > 0 else default


def _positive_int_default(name: str, default: int) -> int:
    """读取正整数 env；非法或小于等于 0 时回落 default（阈值类配置）。"""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _non_negative_float(name: str, default: float) -> float:
    """读取非负浮点 env；未配置/非法/小于 0 时回落 default（0 合法）。"""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _non_negative_int(name: str, default: int) -> int:
    """读取非负整数 env；未配置/非法/小于 0 时回落 default（0 合法）。"""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


@dataclass
class DegradationConfig:
    """All thresholds have sane defaults; every value is overridable via env."""

    case_total_timeout: float = 300.0       # seconds, extreme fallback ceiling
    extra_wait: float = 120.0              # extra wait for third provider after t2
    drain_budget: float = 300.0            # total wait for late (timed-out) judgments
    drain_max_workers: int = 8             # background thread pool size for drained tasks
    drain_max_tasks: int = 60              # max timed-out tasks kept for draining; excess cancelled
    max_inflight: int = 8                  # max tasks submitted to executor simultaneously
    # 反向管线任务内并行度。同一任务同一时刻只处于一个阶段，因此每模型峰值
    # ≈ ICD_WORKER_CONCURRENCY × max(case_concurrency, label_concurrency)。
    # 首版部署取 2/2（20 × 2 = 40 ≤ 每模型 50）。
    case_concurrency: int = 1              # Step 4 / 5 / 5.5 / 5.6 同时处理的 case 数
    label_concurrency: int = 1             # Step 2 同时标注的 HLR 数
    # 模型服务端「每模型并发上限」的署名声明的值（默认 50）。只用于启动自检：
    # ICD_WORKER_CONCURRENCY × max(case, label) 超过它时打警告，不做运行时限流。
    llm_per_model_limit: int = 50
    consecutive_fail_threshold: int = 3     # N consecutive failures → unhealthy
    unhealthy_ttl: float = 300.0            # seconds, auto-recovery TTL
    # 单次模型调用总预算（秒）：覆盖传输重试 + 截断翻倍；也是 worker 的硬截止
    # 时间上限。默认 900 = CASE_TIMEOUT 300 + EXTRA_WAIT 240 + DRAIN 300 + 余量。
    total_budget: float = 900.0
    timeout_fail_threshold: int = 5         # N consecutive timeouts → unhealthy
    max_unhealthy_ttl: float = 600.0        # exponential cooldown cap (seconds)
    stream_read_timeout: float = 120.0      # SSE first-chunk / inter-chunk read timeout (seconds)
    stream_connect_timeout: float = 5.0     # TCP connect timeout for streaming
    # 5 星体系（ADR-004）：star cap 上限 1-5。0/1/2 存活时硬上限分别 1/1/2。
    # 3 存活时不限制（用户确认不加 confidence cap）。
    zero_provider_star_cap: int = 1          # max stars when 0 providers alive
    zero_provider_agreement: str = "no_consensus"
    single_provider_star_cap: int = 1        # max stars when 1 provider alive
    single_provider_agreement: str = "single_source"
    two_provider_star_cap: int = 2           # max stars when 2 providers alive

    @classmethod
    def from_env(cls) -> DegradationConfig:
        return cls(
            case_total_timeout=_positive_float(
                "DEGRADATION_CASE_TIMEOUT", 300.0
            ),
            extra_wait=_non_negative_float(
                "DEGRADATION_EXTRA_WAIT", 120.0
            ),
            drain_budget=_non_negative_float(
                "DEGRADATION_DRAIN_BUDGET", 300.0
            ),
            drain_max_workers=_positive_int_default(
                "DEGRADATION_DRAIN_WORKERS", 8
            ),
            drain_max_tasks=_non_negative_int(
                "DEGRADATION_DRAIN_MAX_TASKS", 60
            ),
            max_inflight=_positive_int_default(
                "DEGRADATION_MAX_INFLIGHT", 8
            ),
            case_concurrency=_positive_int("REVERSE_CASE_CONCURRENCY", "1"),
            label_concurrency=_positive_int("REVERSE_LABEL_CONCURRENCY", "1"),
            llm_per_model_limit=_positive_int("LLM_PER_MODEL_LIMIT", "50"),
            consecutive_fail_threshold=_positive_int_default(
                "DEGRADATION_CONSECUTIVE_FAILURES", 3
            ),
            unhealthy_ttl=_positive_float(
                "DEGRADATION_UNHEALTHY_TTL", 300.0
            ),
            total_budget=_positive_float("LLM_TOTAL_BUDGET", 900.0),
            timeout_fail_threshold=_positive_int_default(
                "DEGRADATION_TIMEOUT_FAILURES", 5
            ),
            max_unhealthy_ttl=_positive_float(
                "DEGRADATION_MAX_UNHEALTHY_TTL", 600.0
            ),
            stream_read_timeout=_positive_float(
                "LLM_STREAM_READ_TIMEOUT", 120.0
            ),
            stream_connect_timeout=_positive_float(
                "LLM_CONNECT_TIMEOUT", 5.0
            ),
        )
