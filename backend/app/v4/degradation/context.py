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
        self._failures: dict[str, int] = {}
        self._unhealthy_until: dict[str, float] = {}
        self._case_timeouts: int = 0
        self._review_star_capped: int = 0
        self._drained_late: int = 0
        # Timed-out judgments still running in the drain executor:
        # (case_id, provider, future). Drained by the pipeline after Step 4.
        self.drain: list[tuple[str, str, Future]] = []

    # ── health checks ──────────────────────────────────────

    def is_healthy(self, provider: str) -> bool:
        """Check if provider is healthy. Auto-recovers on TTL expiry."""
        recovered = False
        with self._lock:
            until = self._unhealthy_until.get(provider)
            if until is None:
                return True
            if time.monotonic() >= until:
                del self._unhealthy_until[provider]
                self._failures.pop(provider, None)
                recovered = True
            else:
                return False
        if recovered:
            print(
                f"  [degradation] {provider} recovered (unhealthy TTL expired)",
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

    def surviving_count(self) -> int:
        """Count currently healthy providers (across all tracked)."""
        with self._lock:
            providers = set(self._failures) | set(self._unhealthy_until)
        return sum(1 for p in providers if self.is_healthy(p))

    # ── mutation ───────────────────────────────────────────

    def record_success(self, provider: str) -> None:
        """Reset consecutive failure counter on success."""
        with self._lock:
            self._failures.pop(provider, None)

    def record_failure(self, provider: str, exception: Exception | None = None) -> None:
        """Increment failure counter; trigger unhealthy if threshold reached.

        If exception is provided and its type is AUTH, immediately unhealthy
        (no need to wait for threshold).
        """
        with self._lock:
            if exception is not None:
                error_type = classify_exception(exception)
                if error_type == "AUTH":
                    self._mark_unhealthy(provider, reason=f"auth error: {exception}")
                    return

            count = self._failures.get(provider, 0) + 1
            self._failures[provider] = count

            if count >= self.config.consecutive_fail_threshold:
                self._mark_unhealthy(provider, reason=f"{count} consecutive failures")

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
            all_providers = set(self._failures) | set(self._unhealthy_until)
            provider_status = {
                p: "healthy" if self.is_healthy(p) else "unhealthy"
                for p in sorted(all_providers)
            }
            case_timeouts = self._case_timeouts
            review_star_capped = self._review_star_capped
            drained_late = self._drained_late
        return {
            "provider_status": provider_status,
            "total_case_timeouts": case_timeouts,
            "review_star_capped_count": review_star_capped,
            "drained_late_count": drained_late,
        }

    # ── internal ───────────────────────────────────────────

    def _mark_unhealthy(self, provider: str, reason: str) -> None:
        until = time.monotonic() + self.config.unhealthy_ttl
        self._unhealthy_until[provider] = until
        print(
            f"  [degradation] {provider} unhealthy ({reason}), until T+{self.config.unhealthy_ttl:.0f}s",
            file=sys.stderr,
        )
