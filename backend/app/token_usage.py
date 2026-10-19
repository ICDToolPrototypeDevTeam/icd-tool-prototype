# -*- coding: utf-8 -*-
"""LLM token 用量采集（反向/正确性管线）。

采集点放在各 LLM 客户端收到 HTTP 响应之后，而不是业务调用点：客户端内部的
截断重试（``finish_reason == 'length'``）也会各计一次，业务代码零改动。

当前线程绑定的 Job 由 :mod:`app.job_log` 的 thread-local 提供；线程池提交方
已用 ``bind_current_job`` 保留归属。CLI / 单元测试未绑定 Job 时静默 no-op。

本模块刻意**不** import ``app.job_manager``：job_manager 会在构造/落盘时被
调用，反向 import 会形成环。统计失败绝不影响模型调用本身。
"""
from __future__ import annotations

from typing import Any

from app.job_log import current_job


def _as_non_negative_int(value: Any) -> int:
    """把 usage 里的单个计数转成非负整数；bool / None / 非法值一律按 0。"""
    if value is None or isinstance(value, bool):
        return 0
    try:
        number = int(float(value)) if isinstance(value, str) else int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, number)


def extract_total_tokens(usage: Any) -> int:
    """从 OpenAI 兼容的 ``usage`` 字段提取总 token。

    兼容三类形状（按优先级）：

    1. ``total_tokens``；
    2. ``prompt_tokens + completion_tokens``；
    3. ``input_tokens + output_tokens``。

    ``total_tokens`` 为 0/缺失时才用分量求和，避免个别服务端把 total 置 0
    而分量有效。无效输入返回 0。
    """
    if not isinstance(usage, dict):
        return 0

    total = _as_non_negative_int(usage.get('total_tokens'))
    if total > 0:
        return total

    for prompt_key, completion_key in (
        ('prompt_tokens', 'completion_tokens'),
        ('input_tokens', 'output_tokens'),
    ):
        if prompt_key in usage or completion_key in usage:
            summed = _as_non_negative_int(usage.get(prompt_key)) + _as_non_negative_int(
                usage.get(completion_key)
            )
            if summed > 0:
                return summed

    return total


def record_token_usage(usage: Any, provider: str | None = None) -> int:
    """把一次响应的 token 用量计入当前线程绑定的 Job。

    ``provider`` 用于分模型统计（反向三份单模型报告）；不传则只累加总量。
    返回解析出的本次用量（无 Job / Job 不支持统计时同样返回，调用方无须关心
    是否真的记上了）。任何异常都被吞掉 —— 统计失败不能影响模型调用。
    """
    try:
        total = extract_total_tokens(usage)
        job = current_job()
        add = getattr(job, 'add_token_usage', None)
        if callable(add):
            if provider:
                try:
                    add(total, provider=provider)
                except TypeError:
                    # 兼容不接受 provider 的轻量假 Job（测试 / 外部注入）
                    add(total)
            else:
                add(total)
        return total
    except Exception:  # noqa: BLE001 — 统计是旁路，绝不影响主流程
        return 0
