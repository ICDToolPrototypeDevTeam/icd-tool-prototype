# -*- coding: utf-8 -*-
"""管线异常 → 面向用户的错误分类。

问题背景：任务失败时后端只写了 ``job.message = "V4 pipeline failed: <类型>: <信息>"``，
且 ``GET /jobs/{id}/result`` 在非 completed 时返回 409，前端既拿不到也不区分
原因，导致「模型 Key 失效」与「输入文件损坏」在 UI 上同形。本模块把异常映射为
稳定的 category + 可执行 hint。

LLM 层细分复用 :func:`app.v4.degradation.fallback.classify_exception`，
不重复实现。
"""
from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Optional

from app.job_manager import JobCancelled
from app.v4.degradation.fallback import AllProvidersUnhealthyError, classify_exception
from app.v4.profiles.base import ProfileLoadError

CATEGORY_TITLES = {
    'INPUT_FILE': '输入文件缺失',
    'INPUT_FORMAT': '文件无法解析',
    'CONFIG': '服务配置缺失',
    'LLM_AUTH': '模型服务认证失败',
    'LLM_NETWORK': '无法连接模型服务',
    'LLM_TIMEOUT': '模型服务超时',
    'LLM_RATE_LIMITED': '模型服务限流',
    'LLM_OUTPUT': '模型返回格式异常',
    'ALL_PROVIDERS_UNHEALTHY': '所有模型服务均不可用',
    'OUTPUT_DISK': '输出写入失败',
    'CANCELLED': '任务已取消',
    'INTERNAL': '内部错误',
}

CATEGORY_HINTS = {
    'INPUT_FILE': '请确认输入文件仍存在，必要时重新上传后重试。',
    'INPUT_FORMAT': '请检查文件是否损坏、格式是否与所选系统类型匹配。',
    'CONFIG': '服务器缺少必要配置（模型 API Key / controller profile）。请联系管理员检查 .env。',
    'LLM_AUTH': '请联系管理员检查模型 API Key 是否有效或已过期。',
    'LLM_NETWORK': '请检查服务器网络出口与模型服务地址是否可达。',
    'LLM_TIMEOUT': '可减少同时裁判的模型数（只选 DeepSeek）后重试。',
    'LLM_RATE_LIMITED': '稍后重试，或减少同时裁判的模型数。',
    'LLM_OUTPUT': '通常是模型返回被截断或不是合法 JSON；可先用 MOCK 模式验证管线本身是否正常。',
    'ALL_PROVIDERS_UNHEALTHY': '检查 API Key 与网络后重试；或切换到 MOCK 模式先验证流程。',
    'OUTPUT_DISK': '请检查服务器磁盘空间与输出目录挂载权限。',
    'CANCELLED': '任务已按你的要求终止；已产出的中间文件保留在输出目录，未被删除。',
    'INTERNAL': '请点「复制诊断信息」并反馈给开发人员。',
}

# classify_exception 的 LLM 层细分结果 → 本模块 category
_LLM_MAP = {
    'TIMEOUT': 'LLM_TIMEOUT',
    'NETWORK': 'LLM_NETWORK',
    'AUTH': 'LLM_AUTH',
    'RATE_LIMITED': 'LLM_RATE_LIMITED',
    'BAD_OUTPUT': 'LLM_OUTPUT',
}

# 允许交给 classify_exception 细分的异常类型名。
#
# 必须按**类型**把关，不能对每个异常都调 classify_exception：后者有两个无界
# 触发点，会把与模型无关的失败贴成「模型故障」——
#   - ``"timeout" in msg`` 子串匹配：网络盘写盘 ``OSError("... timed out")``
#     会被报成 LLM_TIMEOUT，建议用户「减少同时裁判的模型数」；
#   - ``isinstance(exc, (json.JSONDecodeError, KeyError, IndexError, ValueError))``：
#     解析文档数据时的 ``KeyError`` 会被报成 LLM_OUTPUT。
# 把关后这两类落到下方 INPUT_* / OUTPUT_DISK / INTERNAL 分支，提示才对得上根因。
_LLM_EXC_NAMES = frozenset({
    'TimeoutError',     # 内置 TimeoutError（3.10+ 的 socket.timeout 即它）
    'JSONDecodeError',  # json 与 requests 的 JSON 解析失败
    'ValidationError',  # pydantic：模型返回值未通过 schema 校验
})

# 模块名前缀：``requests.exceptions.*`` 全部命中（HTTPError / ConnectionError /
# ConnectTimeout / JSONDecodeError 等），无需逐个枚举类名。
_LLM_EXC_MODULES = frozenset({'requests'})

# docx / xlsx 解析失败时第三方库抛出的异常类型名（按名字匹配，不引入额外 import）
_FORMAT_EXC_NAMES = frozenset({
    'BadZipFile',            # zipfile：xlsx / docx 损坏
    'PackageNotFoundError',  # python-docx：不是合法的 docx 包
    'InvalidXmlError',       # python-docx：XML 结构非法
    'InvalidFileException',  # openpyxl：后缀不受支持
})

_TRACEBACK_TAIL_LINES = 20


def _is_llm_attributable(exc: BaseException) -> bool:
    """裁定该异常是否允许交给 ``classify_exception`` 细分。

    只有确实来自模型调用层的异常才进得来；其余一律交给下方按类型的
    INPUT_* / OUTPUT_DISK / INTERNAL 分支，避免「模型无关的失败被贴成
    模型故障」（理由见 ``_LLM_EXC_NAMES`` 上方注释）。

    pydantic 的 ``ValidationError`` 只能用类名匹配：它的
    ``__module__`` 是 ``pydantic_core._pydantic_core``，模块前缀抓不到。
    """
    if isinstance(exc, TimeoutError):               # 含 TimeoutError 子类
        return True
    if type(exc).__name__ in _LLM_EXC_NAMES:
        return True
    return type(exc).__module__.split('.')[0] in _LLM_EXC_MODULES


def _match_category(exc: BaseException) -> str:
    """判定顺序即优先级，改动前请先读测试。

    关键点：
    - ``TimeoutError`` 是 ``OSError`` 子类，LLM 层必须先于磁盘层判定，
      否则「模型超时」会被误报成「磁盘写入失败」。
    - 缺 API Key 抛的是 ``ValueError``，而 ``classify_exception`` 把
      ``ValueError`` 归为 BAD_OUTPUT，因此 CONFIG 也必须先于 LLM 层判定。
    - LLM 层（``_is_llm_attributable``）只在异常确实来自模型调用层时
      才判定，见该函数与 ``_LLM_EXC_NAMES``。
    """
    if isinstance(exc, JobCancelled):
        return 'CANCELLED'
    if isinstance(exc, AllProvidersUnhealthyError):
        return 'ALL_PROVIDERS_UNHEALTHY'
    if isinstance(exc, (ProfileLoadError, ImportError)):
        return 'CONFIG'
    if isinstance(exc, ValueError) and 'API_KEY' in str(exc):
        return 'CONFIG'

    if _is_llm_attributable(exc):
        llm = _LLM_MAP.get(classify_exception(exc))   # type: ignore[arg-type]
        if llm is not None:
            return llm

    if isinstance(exc, FileNotFoundError):
        return 'INPUT_FILE'
    if type(exc).__name__ in _FORMAT_EXC_NAMES:
        return 'INPUT_FORMAT'
    if isinstance(exc, OSError):                 # FileNotFoundError 已在上方返回
        return 'OUTPUT_DISK'
    return 'INTERNAL'


def _http_status(exc: BaseException) -> Optional[int]:
    """取异常携带的 HTTP 状态码（requests 的 HTTPError 等），没有则 None。"""
    return getattr(getattr(exc, 'response', None), 'status_code', None)


def classify_pipeline_error(
    exc: BaseException,
    stage: str = '',
    stage_index: Optional[int] = None,
) -> dict:
    """把管线异常分类为可直接展示给用户的错误字典。"""
    category = _match_category(exc)
    detail = f'{type(exc).__name__}: {exc}'
    # HTTPError 自身的 message 为空（错误信息在 response 里），401/429 若不补
    # 状态码，诊断文本就只剩「HTTPError: 」，看不出失败原因。
    status = _http_status(exc)
    if status:
        detail = f'{detail.rstrip()} (HTTP {status})'
    tail = ''.join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    ).strip().splitlines()[-_TRACEBACK_TAIL_LINES:]
    return {
        'category': category,
        'title': CATEGORY_TITLES.get(category, CATEGORY_TITLES['INTERNAL']),
        'stage': stage or '',
        'stage_index': stage_index,
        'error_type': type(exc).__name__,
        'message': str(exc),
        'detail': detail,
        'traceback_tail': '\n'.join(tail),
        'hint': CATEGORY_HINTS.get(category, CATEGORY_HINTS['INTERNAL']),
        'at': datetime.now(timezone.utc).isoformat(),
    }
