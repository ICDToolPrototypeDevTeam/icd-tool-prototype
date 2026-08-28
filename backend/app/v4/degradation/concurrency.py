# -*- coding: utf-8 -*-
"""Shared concurrency primitives for the V4 pipeline.

Process-wide thread pool executor + inflight semaphore + a gate wrapper that
submits callables through the semaphore. Both ``app.v4.pipeline`` (Step 4
multi-agent judging) and ``app.v4.comparison.re_review`` (Step 5.5 peer-aware
re-review) consume these so they share the same backpressure budget without
introducing a circular import between pipeline ↔ re_review.

Moved out of ``pipeline.py`` so that ``re_review.py`` can import these without
triggering pipeline.py's own import of re_review_judgments at module load.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor

from app.v4.degradation.config import DegradationConfig


_drain_executor: ThreadPoolExecutor | None = None


def _get_drain_executor() -> ThreadPoolExecutor:
    """Shared background executor for timed-out judgments (process-wide)."""
    global _drain_executor
    if _drain_executor is None:
        _drain_executor = ThreadPoolExecutor(
            max_workers=DegradationConfig.from_env().drain_max_workers,
            thread_name_prefix="degradation-drain",
        )
    return _drain_executor


_inflight_sema: threading.Semaphore | None = None


def _get_inflight_sema() -> threading.Semaphore:
    """Gate limiting tasks submitted to executor simultaneously (process-wide)."""
    global _inflight_sema
    if _inflight_sema is None:
        _inflight_sema = threading.Semaphore(
            DegradationConfig.from_env().max_inflight
        )
    return _inflight_sema


def _submit_with_gate(executor: ThreadPoolExecutor, fn, *args) -> Future:
    """Submit fn to executor, blocking until an inflight slot is available.

    The semaphore is released when the future completes (success or failure).
    This prevents unbounded task accumulation when many cases are queued.
    """
    sema = _get_inflight_sema()
    sema.acquire()
    future = executor.submit(fn, *args)
    future.add_done_callback(lambda _: sema.release())
    return future