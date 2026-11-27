# -*- coding: utf-8 -*-
"""Per-case wait/hard deadline shared by one case controller and its workers."""

from __future__ import annotations

import threading
import time


class CaseBudget:
    """Thread-safe deadlines for a single case.

    ``wait_deadline`` is when the case controller stops waiting and writes a
    TIMEOUT placeholder; the worker may still produce a late result until
    ``hard_deadline``.  Updating either field always writes both under the
    lock so a worker never observes a wait deadline paired with a stale hard
    deadline.
    """

    def __init__(self, start: float, wait_deadline: float, hard_deadline: float) -> None:
        self._lock = threading.Lock()
        self._start = start
        self._wait = wait_deadline
        self._hard = hard_deadline

    @property
    def start(self) -> float:
        with self._lock:
            return self._start

    @property
    def wait_deadline(self) -> float:
        with self._lock:
            return self._wait

    @property
    def hard_deadline(self) -> float:
        with self._lock:
            return self._hard

    def rebase(self, start: float, ceiling: float, drain_budget: float, total_budget: float) -> None:
        with self._lock:
            self._start = start
            self._wait = start + ceiling
            self._hard = start + min(ceiling + drain_budget, total_budget)

    def update(self, wait_deadline: float, hard_deadline: float) -> None:
        with self._lock:
            self._wait = wait_deadline
            self._hard = hard_deadline

    def wait_expired(self, now: float | None = None) -> bool:
        return (time.monotonic() if now is None else now) >= self.wait_deadline

    def hard_expired(self, now: float | None = None) -> bool:
        return (time.monotonic() if now is None else now) >= self.hard_deadline

    def remaining_hard(self, now: float | None = None) -> float:
        return max(0.0, self.hard_deadline - (time.monotonic() if now is None else now))
