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

from app.runtime_context import bind_thread_context
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

    池内工作线程不继承 thread-local，因此提交前用 bind_thread_context 把 fn 包成
    「在提交方的 job 归属 + 运行上下文内执行」：池内日志/进度归到同一个任务，
    且池内构造的 LLM 客户端读到的仍是本任务的 mock 模式（不写进程 env）。
    无任何绑定时原样返回 fn，CLI 路径行为不变。
    """
    sema = _get_inflight_sema()
    sema.acquire()
    future = executor.submit(bind_thread_context(fn), *args)
    future.add_done_callback(lambda _: sema.release())
    return future