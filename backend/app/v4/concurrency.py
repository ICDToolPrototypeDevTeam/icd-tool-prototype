# -*- coding: utf-8 -*-
"""Generic bounded, order-preserving parallel execution for the V4 pipeline.

只负责把一批互相独立的条目分给 N 个线程执行并保持输入顺序，不涉及降级、
熔断或 provider 健康状态；降级专用的后台线程池与背压信号量仍在
``app.v4.degradation.concurrency``。
"""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Callable, Optional, Sequence, TypeVar

from app.job_manager import JobCancelled, raise_if_cancelled
from app.runtime_context import bind_thread_context

T = TypeVar("T")
R = TypeVar("R")
_MISSING = object()


def run_parallel_ordered(
    items: Sequence[T],
    fn: Callable[[T], R],
    *,
    max_workers: int,
    thread_name_prefix: str = "reverse-parallel",
    serial_delay: float = 0.0,
    on_result: Optional[Callable[[int, T, R], None]] = None,
) -> list[R]:
    """Run ``fn`` over ``items`` with bounded concurrency, preserving input order.

    ``max_workers <= 1`` 时走原串行路径：逐项取消检查 + 可选的 ``serial_delay``，
    保证单测、CLI 和未开启并行的部署与改动前行为一致。

    并行路径：
    - 固定窗口提交（最多 ``max_workers`` 个 future 在飞），不一次性提交全部；
    - 每个 future 都经 ``bind_thread_context`` 包装，池线程仍归属当前 job，
      日志、token、mock 模式不串台；
    - 结果按输入下标写入，完成顺序不影响返回顺序；
    - 主线程在每次提交前检查取消；任一任务抛出 ``JobCancelled`` 或普通异常时，
      取消尚未开始的 future 并原样抛出（不吞异常）。
    """
    total = len(items)
    if total == 0:
        return []

    workers = max(1, int(max_workers))
    if workers == 1 or total == 1:
        results: list[R] = []
        for idx, item in enumerate(items):
            raise_if_cancelled()
            result = fn(item)
            results.append(result)
            if on_result is not None:
                on_result(idx, item, result)
            if serial_delay and idx < total - 1:
                time.sleep(serial_delay)
        return results

    slots: list[object] = [_MISSING] * total
    futures: dict[Future, tuple[int, T]] = {}
    next_idx = 0
    aborted = False
    executor = ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix=thread_name_prefix,
    )
    try:
        while next_idx < total or futures:
            while next_idx < total and len(futures) < workers:
                raise_if_cancelled()
                item = items[next_idx]
                future = executor.submit(bind_thread_context(fn), item)
                futures[future] = (next_idx, item)
                next_idx += 1
            if not futures:
                break
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                idx, item = futures.pop(future)
                result = future.result()
                slots[idx] = result
                if on_result is not None:
                    on_result(idx, item, result)
    except JobCancelled:
        aborted = True
        raise
    except Exception:
        aborted = True
        raise
    finally:
        # 正常结束时所有 future 都已收完；异常时取消排队任务，运行中的 HTTP
        # 请求无法中断，交给进程退出/降级逻辑处理。
        executor.shutdown(wait=not aborted, cancel_futures=aborted)

    if any(result is _MISSING for result in slots):
        raise RuntimeError("run_parallel_ordered: missing result for submitted item")
    return slots  # type: ignore[return-value]
