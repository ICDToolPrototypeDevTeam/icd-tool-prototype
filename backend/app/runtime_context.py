# -*- coding: utf-8 -*-
"""运行时上下文（thread-local）：让前端传入的运行参数按「任务」生效，而非按进程。

背景：``USE_MOCK_LLM`` 原由 runner 直接写入 ``os.environ``，而它是进程级状态
——两个任务重叠时，后提交者会改写先提交者的 mock 模式，两边的判定、日志头与
结果页警告都会失真（I-2）。本模块提供与 :mod:`app.job_log` 同范式的线程局部
绑定：任务在自己的线程内绑定参数，LLM 工厂按当前线程读取；进程 env 只作为
「未绑定时的回落」，因此 CLI / 测试路径行为完全不变。

线程池注意：池内工作线程不继承 thread-local，提交方必须用
:func:`bind_thread_context` 包装（同时带上 job 日志归属与运行上下文）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from app.job_log import bind_current_job


@dataclass(frozen=True)
class RuntimeContext:
    """一次任务运行的参数快照；``None`` 字段 = 未指定，回落到进程 env。"""

    use_mock_llm: Optional[bool] = None


_local = threading.local()


def current_runtime() -> Optional[RuntimeContext]:
    """当前线程绑定的运行上下文；未绑定返回 ``None``（调用方据此回落 env）。"""
    return getattr(_local, 'context', None)


def bind_runtime(context: RuntimeContext) -> Optional[RuntimeContext]:
    """绑定当前线程的运行上下文；返回上一层绑定，供 :func:`restore_runtime`。"""
    prev = current_runtime()
    _local.context = context
    return prev


def restore_runtime(prev: Optional[RuntimeContext]) -> None:
    _local.context = prev


def bind_thread_context(fn: Callable) -> Callable:
    """把 ``fn`` 包成「在提交方的 job 归属 + 运行上下文内执行」，供线程池提交。

    池内工作线程既不继承 job 归属也不继承运行上下文，两者都必须在**提交瞬间**
    捕获并在池内重绑；否则池内的 LLM 客户端会按进程 env 构造，mock 判定串台。
    无任何绑定时原样返回 ``fn``（CLI 路径零开销）。
    """
    job_bound = bind_current_job(fn)
    context = current_runtime()
    if context is None:
        return job_bound

    def _wrapped(*args, **kwargs):
        prev = bind_runtime(context)
        try:
            return job_bound(*args, **kwargs)
        finally:
            restore_runtime(prev)

    return _wrapped
