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
            case_total_timeout=float(
                os.getenv("DEGRADATION_CASE_TIMEOUT", "300")
            ),
            extra_wait=float(
                os.getenv("DEGRADATION_EXTRA_WAIT", "120")
            ),
            drain_budget=float(
                os.getenv("DEGRADATION_DRAIN_BUDGET", "300")
            ),
            drain_max_workers=int(
                os.getenv("DEGRADATION_DRAIN_WORKERS", "8")
            ),
            drain_max_tasks=int(
                os.getenv("DEGRADATION_DRAIN_MAX_TASKS", "60")
            ),
            max_inflight=int(
                os.getenv("DEGRADATION_MAX_INFLIGHT", "8")
            ),
            case_concurrency=_positive_int("REVERSE_CASE_CONCURRENCY", "1"),
            label_concurrency=_positive_int("REVERSE_LABEL_CONCURRENCY", "1"),
            llm_per_model_limit=_positive_int("LLM_PER_MODEL_LIMIT", "50"),
            consecutive_fail_threshold=int(
                os.getenv("DEGRADATION_CONSECUTIVE_FAILURES", "3")
            ),
            unhealthy_ttl=float(
                os.getenv("DEGRADATION_UNHEALTHY_TTL", "300")
            ),
        )
