# -*- coding: utf-8 -*-
"""Degradation configuration: timeouts, circuit breaker, review caps."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class DegradationConfig:
    """All thresholds have sane defaults; every value is overridable via env."""

    case_total_timeout: float = 300.0       # seconds, extreme fallback ceiling
    extra_wait: float = 120.0              # extra wait for third provider after t2
    drain_budget: float = 300.0            # total wait for late (timed-out) judgments
    drain_max_workers: int = 6             # background thread pool size for drained tasks
    drain_max_tasks: int = 60              # max timed-out tasks kept for draining; excess cancelled
    max_inflight: int = 6                  # max tasks submitted to executor simultaneously
    consecutive_fail_threshold: int = 3     # N consecutive failures → unhealthy
    unhealthy_ttl: float = 300.0            # seconds, auto-recovery TTL
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
                os.getenv("DEGRADATION_DRAIN_WORKERS", "6")
            ),
            drain_max_tasks=int(
                os.getenv("DEGRADATION_DRAIN_MAX_TASKS", "60")
            ),
            max_inflight=int(
                os.getenv("DEGRADATION_MAX_INFLIGHT", "6")
            ),
            consecutive_fail_threshold=int(
                os.getenv("DEGRADATION_CONSECUTIVE_FAILURES", "3")
            ),
            unhealthy_ttl=float(
                os.getenv("DEGRADATION_UNHEALTHY_TTL", "300")
            ),
        )
