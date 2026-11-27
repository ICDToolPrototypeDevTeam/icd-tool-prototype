# -*- coding: utf-8 -*-
"""DegradationContext: cross-case provider health tracking."""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import Future

from app.v4.degradation.config import DegradationConfig
from app.v4.degradation.fallback import AllProvidersUnhealthyError, classify_exception


class DegradationContext:
    """Tracks per-provider health across cases in Step 4 and Step 5.

    并发约束：Step 4 / 5.5 的 case 可并行，多个 case 线程会同时读写
    provider 健康状态、统计计数和 drain 列表。所有状态变更都在同一把
    RLock 内完成；``is_healthy`` 会在 ``filter_healthy`` / ``to_summary``
    内部重入，因此用 RLock 而不是普通 Lock。
    """

    def __init__(self, config: DegradationConfig | None = None):
        self.config = config or DegradationConfig.from_env()
        self._lock = threading.RLock()
        self._network_failures: dict[str, int] = {}
        self._timeout_failures: dict[str, int] = {}
        self._soft_failures: dict[str, int] = {}
        self._unhealthy_until: dict[str, float] = {}
        self._unhealthy_kind: dict[str, str] = {}
        self._cooldown_level: dict[str, int] = {}
        self._case_timeouts: int = 0
        self._review_star_capped: int = 0
        self._drained_late: int = 0
        # Timed-out judgments still running in the drain executor:
        # (case_id, provider, future). Drained by the pipeline after Step 4.
        self.drain: list[tuple[str, str, Future]] = []

    # ── health checks ──────────────────────────────────────

    def is_healthy(self, provider: str) -> bool:
        """Check if provider is healthy.

        TTL expiry enters half-open: the counter for the failure kind that
        tripped the breaker is preloaded to ``threshold - 1`` so the next
        failure of that kind re-trips immediately.
        """
        recovered = False
        with self._lock:
            until = self._unhealthy_until.get(provider)
            if until is None:
                return True
            if time.monotonic() >= until:
                kind = self._unhealthy_kind.pop(provider, "network")
                del self._unhealthy_until[provider]
                if kind == "timeout":
                    self._timeout_failures[provider] = (
                        self.config.timeout_fail_threshold - 1
                    )
                    self._network_failures.pop(provider, None)
                elif kind == "network":
                    self._network_failures[provider] = (
                        self.config.consecutive_fail_threshold - 1
                    )
                    self._timeout_failures.pop(provider, None)
                recovered = True
            else:
                return False
        if recovered:
            print(
                f"  [degradation] {provider} recovered (half-open, kind={kind})",
                file=sys.stderr,
            )
        return True

    def filter_healthy(self, providers: list[str]) -> list[str]:
        """Return only healthy providers. Raise if none remain."""
        healthy = [p for p in providers if self.is_healthy(p)]
        if not healthy:
            raise AllProvidersUnhealthyError(
                [p for p in providers if not self.is_healthy(p)]
            )
        return healthy

    def _tracked_providers(self) -> set[str]:
        return (
            set(self._network_failures)
            | set(self._timeout_failures)
            | set(self._soft_failures)
            | set(self._unhealthy_until)
        )

    def surviving_count(self) -> int:
        """Count currently healthy providers (across all tracked)."""
        with self._lock:
            providers = self._tracked_providers()
        return sum(1 for p in providers if self.is_healthy(p))

    # ── mutation ───────────────────────────────────────────

    def record_success(self, provider: str) -> None:
        """Reset consecutive network/timeout counters and cooldown level."""
        with self._lock:
            self._network_failures.pop(provider, None)
            self._timeout_failures.pop(provider, None)
            self._cooldown_level.pop(provider, None)

    def record_failure(
        self,
        provider: str,
        error_type: str = "UNKNOWN",
        exception: Exception | None = None,
    ) -> None:
        """Record one classified failure.

        AUTH trips immediately; NETWORK uses ``consecutive_fail_threshold``;
        TIMEOUT uses ``timeout_fail_threshold``. Soft failures (bad output,
        truncation, rate limits, unknown) are counted but never trip.
        """
        if exception is not None and error_type == "UNKNOWN":
            error_type = classify_exception(exception)

        with self._lock:
            if error_type == "AUTH":
                self._mark_unhealthy(provider, "auth error", kind="auth")
                return
            if error_type == "NETWORK":
                count = self._network_failures.get(provider, 0) + 1
                self._network_failures[provider] = count
                if count >= self.config.consecutive_fail_threshold:
                    self._mark_unhealthy(
                        provider,
                        f"{count} consecutive network failures",
                        kind="network",
                    )
                return
            if error_type in {"TIMEOUT", "STREAM_STALL"}:
                count = self._timeout_failures.get(provider, 0) + 1
                self._timeout_failures[provider] = count
                if count >= self.config.timeout_fail_threshold:
                    self._mark_unhealthy(
                        provider,
                        f"{count} consecutive timeouts",
                        kind="timeout",
                    )
                return
            # BAD_OUTPUT / TRUNCATED / STREAM_INTERRUPTED / STREAM_ABORTED /
            # RATE_LIMITED / UNKNOWN: statistics only, never trip.
            self._soft_failures[provider] = (
                self._soft_failures.get(provider, 0) + 1
            )

    def record_case_timeout(self) -> None:
        """Increment the case-timeout counter (stats only)."""
        with self._lock:
            self._case_timeouts += 1

    def record_review_star_capped(self) -> None:
        """Increment review star cap counter (stats only)."""
        with self._lock:
            self._review_star_capped += 1

    def record_drained_late(self) -> None:
        """Increment counter of late judgments that arrived valid during drain."""
        with self._lock:
            self._drained_late += 1

    # ── drain list ─────────────────────────────────────────

    def add_drain(self, case_id: str, provider: str, future: Future) -> bool:
        """登记一个超时后仍在后台执行的裁决；超过上限返回 False。

        并发 case 可能同时走到这里，len + append 必须是一个整体，否则会突破
        ``drain_max_tasks``。
        """
        with self._lock:
            if len(self.drain) >= self.config.drain_max_tasks:
                return False
            self.drain.append((case_id, provider, future))
            return True

    def drain_snapshot(self) -> list[tuple[str, str, Future]]:
        """返回 drain 列表副本；调用方 join 期间列表不再被并发修改。"""
        with self._lock:
            return list(self.drain)

    def clear_drain(self) -> None:
        with self._lock:
            self.drain.clear()

    # ── summary ────────────────────────────────────────────

    def to_summary(self) -> dict:
        """Export summary dict for job.result.degradation."""
        with self._lock:
            all_providers = self._tracked_providers()
            provider_status = {
                p: "healthy" if self.is_healthy(p) else "unhealthy"
                for p in sorted(all_providers)
            }
            provider_stats = {
                p: {
                    "network_failures": self._network_failures.get(p, 0),
                    "timeout_failures": self._timeout_failures.get(p, 0),
                    "soft_failures": self._soft_failures.get(p, 0),
                    "cooldown_level": self._cooldown_level.get(p, 0),
                }
                for p in sorted(all_providers)
            }
            case_timeouts = self._case_timeouts
            review_star_capped = self._review_star_capped
            drained_late = self._drained_late
        return {
            "provider_status": provider_status,
            "provider_stats": provider_stats,
            "total_case_timeouts": case_timeouts,
            "review_star_capped_count": review_star_capped,
            "drained_late_count": drained_late,
        }

    # ── internal ───────────────────────────────────────────

    def _mark_unhealthy(self, provider: str, reason: str, kind: str) -> None:
        level = self._cooldown_level.get(provider, 0)
        ttl = min(
            self.config.unhealthy_ttl * (2 ** level),
            self.config.max_unhealthy_ttl,
        )
        self._unhealthy_until[provider] = time.monotonic() + ttl
        self._unhealthy_kind[provider] = kind
        self._cooldown_level[provider] = level + 1
        print(
            f"  [degradation] {provider} unhealthy ({reason}), "
            f"kind={kind}, until T+{ttl:.0f}s",
            file=sys.stderr,
        )
