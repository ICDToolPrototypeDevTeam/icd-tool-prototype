# -*- coding: utf-8 -*-
"""内部通道需求识别（R_norm3）。

规则（须同时满足）：
1. 同一句内至少出现 ``min_terms`` 个不同通道词（AMS 默认 3 个）；
2. 同一句内出现数据路径动词（默认 接收/发送/解析/写入，可带 A429）。

可选的告警语境排除由 ``internal_channel_exclude_pattern`` 控制；留空
表示不排除（AMS 当前为空，即不启用告警排除）。

识别前先展开“邻/对侧/对邻通道”这类共用后缀的简写。结果仅供匹配层
做前置分流：命中即按“无匹配 + 内部接口标注”处理，不进入 EoICD
Pub/Sub 接口比对。

该规则由 profile 的 ``matcher`` 配置启用；未配置的 profile 不会走到
这里（``enable_internal_channel_detection=False``）。
"""
from __future__ import annotations

import re

from app.v4.profiles.base import MatcherEnhancementConfig

_CLAUSE_SPLIT_RE = re.compile(r"[。；;\n]")
# “和/以及/或/或者”等连接词也视为简写分隔符；双字词放在前面优先匹配。
_SEP = r"\s*(?:以及|或者|[/、,，和及与或])\s*"


def _common_suffix(terms: tuple[str, ...]) -> str:
    """求词表的公共后缀，例如三个通道词的公共后缀是“通道”。"""
    if not terms:
        return ""
    reversed_terms = [t[::-1] for t in terms]
    prefix = reversed_terms[0]
    for t in reversed_terms[1:]:
        while prefix and not t.startswith(prefix):
            prefix = prefix[:-1]
    return prefix[::-1]


def _expand_abbreviations(text: str, terms: tuple[str, ...]) -> str:
    """把“邻/对侧/对邻通道”展开为“邻通道、对侧通道、对邻通道”。"""
    suffix = _common_suffix(terms)
    if not suffix:
        return text
    prefixes = [
        t[: -len(suffix)]
        for t in terms
        if t.endswith(suffix) and len(t) > len(suffix)
    ]
    if len(prefixes) < 2:
        return text
    prefix_alt = "|".join(
        re.escape(p) for p in sorted(prefixes, key=len, reverse=True)
    )
    pattern = re.compile(
        rf"(?:(?:{prefix_alt}){_SEP})+(?:{prefix_alt})\s*{re.escape(suffix)}"
    )

    def _repl(m: re.Match[str]) -> str:
        body = m.group(0)[: -len(suffix)]
        parts = [p.strip() for p in re.split(_SEP, body) if p.strip()]
        return "、".join(p + suffix for p in parts)

    return pattern.sub(_repl, text)


def _term_regex(terms: tuple[str, ...]) -> re.Pattern[str]:
    # 长词优先，避免“对邻通道”里的“邻通道”被重复计数
    ordered = sorted(set(terms), key=len, reverse=True)
    return re.compile("|".join(re.escape(t) for t in ordered))


def is_internal_channel_requirement(
    text: str,
    config: MatcherEnhancementConfig,
) -> bool:
    """判断一条 HLR 是否属于内部通道数据需求（R_norm3）。"""
    if not config.enable_internal_channel_detection:
        return False
    terms = tuple(config.internal_channel_terms)
    if not text or len(terms) < config.internal_channel_min_terms:
        return False
    normalized = _expand_abbreviations(text, terms)
    term_re = _term_regex(terms)
    data_re = re.compile(config.internal_channel_data_path_pattern, re.IGNORECASE)
    exclude_re = (
        re.compile(config.internal_channel_exclude_pattern, re.IGNORECASE)
        if config.internal_channel_exclude_pattern
        else None
    )
    for clause in _CLAUSE_SPLIT_RE.split(normalized):
        if exclude_re is not None and exclude_re.search(clause):
            continue
        found = {m.group(0) for m in term_re.finditer(clause)}
        if len(found) >= config.internal_channel_min_terms and data_re.search(clause):
            return True
    return False
