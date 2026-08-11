# -*- coding: utf-8 -*-
"""DegradationContext: cross-case provider health tracking."""

from __future__ import annotations

import sys
import time

from app.v4.degradation.config import DegradationConfig
from app.v4.degradation.fallback import AllProvidersUnhealthyError, classify_exception


class DegradationContext:
    """Tracks per-provider health across cases in Step 4 and Step 5."""

    def __init__(self, config: DegradationConfig | None = None):
        self.config = config or DegradationConfig.from_env()
        self._failures: dict[str, int] = {}
        self._unhealthy_until: dict[str, float] = {}
        self._case_timeouts: int = 0
        self._review_star_capped: int = 0

    # ── health checks ──────────────────────────────────────

    def is_healthy(self, provider: str) -> bool:
        """Check if provider is healthy. Auto-recovers on TTL expiry."""
        until = self._unhealthy_until.get(provider)
        if until is None:
            return True
        if time.monotonic() >= until:
            del self._unhealthy_until[provider]
            self._failures.pop(provider, None)
            print(
                f"  [degradation] {provider} recovered (unhealthy TTL expired)",
                file=sys.stderr,
            )
            return True
        return False

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
        return sum(
            1 for p in set(self._failures) | set(self._unhealthy_until)
            if self.is_healthy(p)
        )

    # ── mutation ───────────────────────────────────────────

    def record_success(self, provider: str) -> None:
        """Reset consecutive failure counter on success."""
        self._failures.pop(provider, None)

    def record_failure(self, provider: str, exception: Exception | None = None) -> None:
        """Increment failure counter; trigger unhealthy if threshold reached.

        If exception is provided and its type is AUTH, immediately unhealthy
        (no need to wait for threshold).
        """
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
        self._case_timeouts += 1

    def record_review_star_capped(self) -> None:
        """Increment review star cap counter (stats only)."""
        self._review_star_capped += 1

    # ── summary ────────────────────────────────────────────

    def to_summary(self) -> dict:
        """Export summary dict for job.result.degradation."""
        all_providers = set(self._failures) | set(self._unhealthy_until)
        return {
            "provider_status": {
                p: "healthy" if self.is_healthy(p) else "unhealthy"
                for p in sorted(all_providers)
            },
            "total_case_timeouts": self._case_timeouts,
            "review_star_capped_count": self._review_star_capped,
        }

    # ── internal ───────────────────────────────────────────

    def _mark_unhealthy(self, provider: str, reason: str) -> None:
        until = time.monotonic() + self.config.unhealthy_ttl
        self._unhealthy_until[provider] = until
        print(
            f"  [degradation] {provider} unhealthy ({reason}), until T+{self.config.unhealthy_ttl:.0f}s",
            file=sys.stderr,
        )
