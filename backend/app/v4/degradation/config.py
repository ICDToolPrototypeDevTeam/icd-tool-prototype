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
            consecutive_fail_threshold=int(
                os.getenv("DEGRADATION_CONSECUTIVE_FAILURES", "3")
            ),
            unhealthy_ttl=float(
                os.getenv("DEGRADATION_UNHEALTHY_TTL", "300")
            ),
        )
