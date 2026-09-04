# -*- coding: utf-8 -*-
"""Script-based HLR classifier: 4-path decision tree + structured field extraction.

Profile-driven: keyword sets can be overridden by ClassifierKeywords.
Default (None) falls back to AMS-equivalent keywords for backward compatibility.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.v4.models import HLRLabel, HLRRequirement

if TYPE_CHECKING:
    from app.v4.profiles.base import ClassifierKeywords


# ——— Default keywords (AMS-equivalent) ———

_DEFAULT_ANALOG = ("ADC", "模拟量", "电压", "电流", "传感器", "采样", "量程",
                   "[Vv]oltage", "[Cc]urrent", "[Ss]ensor", "A/D", "AD转换", "ADCIN")
_DEFAULT_DISCRETE = ("微动", "开关", "[Tt]rue", "[Ff]alse", "故障", "跳变", "触点",
                     "离散量", "开关量", "BOOL", "BOOLEAN", "DISCRETE", "状态位", "有效位")
_DEFAULT_BUS = ("CAN", "A825", "A664", "A429", "AFDX", "总线", "ARINC",
                "通信", "接收", "发送", "写入", "采集", "解析")
_DEFAULT_SEND = ("发送", "写入", "输出", "上报", "发布", "驱动", "设置", "控制")
_DEFAULT_RECEIVE = ("接收", "采集", "解析", "获取", "读取", "监测", "判断", "检测", "输入")
_DEFAULT_STRONG_SEND = ("发送", "写入", "输出", "发布")
_DEFAULT_STRONG_RECEIVE = ("接收", "解析", "采集")


def _build_keyword_regex(keywords) -> re.Pattern | None:
    """Build a single regex from keyword tuple (each becomes | alternation)."""
    if not keywords:
        return None
    return re.compile("|".join(keywords))


def _resolve_keywords(keywords: "ClassifierKeywords | None") -> tuple:
    """Return (analog_pat, discrete_pat, bus_pat, send_pat, receive_pat, strong_send_pat, strong_receive_pat)."""
    if keywords is None:
        return (
            _build_keyword_regex(_DEFAULT_ANALOG),
            _build_keyword_regex(_DEFAULT_DISCRETE),
            _build_keyword_regex(_DEFAULT_BUS),
            _build_keyword_regex(_DEFAULT_SEND),
            _build_keyword_regex(_DEFAULT_RECEIVE),
            _build_keyword_regex(_DEFAULT_STRONG_SEND),
            _build_keyword_regex(_DEFAULT_STRONG_RECEIVE),
        )
    return (
        _build_keyword_regex(keywords.analog),
        _build_keyword_regex(keywords.discrete),
        _build_keyword_regex(keywords.bus),
        _build_keyword_regex(keywords.direction_send),
        _build_keyword_regex(keywords.direction_receive),
        _build_keyword_regex(tuple(k for k in keywords.direction_send if k in _DEFAULT_STRONG_SEND)),
        _build_keyword_regex(tuple(k for k in keywords.direction_receive if k in _DEFAULT_STRONG_RECEIVE)),
    )


_LABEL_RE = re.compile(r"L(\d+)", re.IGNORECASE)
_BIT_RANGE_RE = re.compile(r"bit(\d+)\s*[至~\-]\s*bit(\d+)", re.IGNORECASE)
_BIT_SINGLE_RE = re.compile(r"bit(\d+)\s*[为是]\s*(.+?)(?:[，,;；]|$)", re.IGNORECASE)
_BIT_ASSIGN_RE = re.compile(r"bit(\d+)\s*=", re.IGNORECASE)
_BIT_POSSESSIVE_RE = re.compile(r"bit(\d+)\s*的", re.IGNORECASE)
_SDI_RE = re.compile(r"SDI\s*[=为]?\s*(\d+)", re.IGNORECASE)

# —— 中文位号（物理 1 基 → ICD 0 基换算）——
# HLR 常以"第N位"描述 A429 字内位布局（"第9和10位" = SDI 字段、
# "第18到28位" = 数据位、"第29位符号位" = BNR 符号位）。ICD 的
# BitOffsetWithinDS 是 0 基偏移（offset=8 即物理第 9 位），故提取时统一
# N-1 换算，使位匹配与裁判输入在同一基制下与 ICD 比较。英文 bitN 形式
# 保持原语义不变（历史行为）。
_CN_BIT_RANGE_RE = re.compile(r"第\s*(\d+)\s*(?:到|至|~|～|-|—)\s*第?\s*(\d+)\s*位")
_CN_BIT_AND_RE = re.compile(r"第\s*(\d+)\s*[和、与]\s*(?:第)?\s*(\d+)\s*位")
_CN_BIT_SINGLE_RE = re.compile(r"第\s*(\d+)\s*位")


def classify_hlr(
    text: str, keywords: "ClassifierKeywords | None" = None
) -> str:
    """Classify an HLR requirement into one of 5 categories.

    Returns one of: "A429显式" | "模拟量" | "离散量" | "A429隐式" | "逻辑/非通信"
    """
    analog_pat, discrete_pat, bus_pat, _, _, _, _ = _resolve_keywords(keywords)

    has_label = bool(_LABEL_RE.search(text))

    if has_label:
        return "A429显式"
    if analog_pat and analog_pat.search(text):
        return "模拟量"
    if discrete_pat and discrete_pat.search(text):
        return "离散量"
    if bus_pat and bus_pat.search(text):
        return "A429隐式"
    return "逻辑/非通信"


def extract_labels(text: str) -> list[str]:
    return [f"L{m.group(1)}" for m in _LABEL_RE.finditer(text)]


def extract_bit_fields(text: str) -> list[dict]:
    fields: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for m in _BIT_RANGE_RE.finditer(text):
        start = int(m.group(1))
        end = int(m.group(2))
        offset = min(start, end)
        size = abs(end - start) + 1
        key = (offset, size)
        if key not in seen:
            seen.add(key)
            fields.append({"offset": offset, "size": size, "text": m.group(0)})
    for m in _BIT_SINGLE_RE.finditer(text):
        offset = int(m.group(1))
        key = (offset, 1)
        if key not in seen:
            seen.add(key)
            fields.append({"offset": offset, "size": 1, "text": m.group(0)})
    for m in _BIT_ASSIGN_RE.finditer(text):
        offset = int(m.group(1))
        key = (offset, 1)
        if key not in seen:
            seen.add(key)
            fields.append({"offset": offset, "size": 1, "text": m.group(0)})
    for m in _BIT_POSSESSIVE_RE.finditer(text):
        offset = int(m.group(1))
        key = (offset, 1)
        if key not in seen:
            seen.add(key)
            fields.append({"offset": offset, "size": 1, "text": m.group(0)})

    # 中文位号：先范围、再并列（"第9和10位"相邻合并为 2 位）、后单 bit。
    # offset 一律 0 基（物理位号 - 1）；单 bit 若落在已提取范围内则跳过，
    # 避免"第18到28位、第29位符号位"这类文本把边界位重复计数。
    cn_ranges: list[tuple[int, int]] = []
    for m in _CN_BIT_RANGE_RE.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = min(a, b), max(a, b)
        offset = lo - 1
        size = hi - lo + 1
        key = (offset, size)
        if key not in seen:
            seen.add(key)
            fields.append({
                "offset": offset, "size": size,
                "text": m.group(0), "convention": "cn-physical",
            })
        cn_ranges.append((offset, offset + size))
    for m in _CN_BIT_AND_RE.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = min(a, b), max(a, b)
        if hi - lo == 1:
            # 相邻并列（第9和10位）→ 合并为连续位段
            offset = lo - 1
            key = (offset, 2)
            if key not in seen:
                seen.add(key)
                fields.append({
                    "offset": offset, "size": 2,
                    "text": m.group(0), "convention": "cn-physical",
                })
            cn_ranges.append((offset, offset + 2))
        else:
            # 不相邻并列（第3和5位）→ 两个独立单 bit
            for bit in (lo, hi):
                offset = bit - 1
                key = (offset, 1)
                if key not in seen:
                    seen.add(key)
                    fields.append({
                        "offset": offset, "size": 1,
                        "text": m.group(0), "convention": "cn-physical",
                    })
    for m in _CN_BIT_SINGLE_RE.finditer(text):
        n = int(m.group(1))
        offset = n - 1
        if any(r_lo <= offset < r_hi for r_lo, r_hi in cn_ranges):
            continue
        key = (offset, 1)
        if key not in seen:
            seen.add(key)
            fields.append({
                "offset": offset, "size": 1,
                "text": m.group(0), "convention": "cn-physical",
            })
    return fields


def extract_sdi(text: str) -> str:
    m = _SDI_RE.search(text)
    return m.group(1) if m else ""


def extract_direction(
    text: str, keywords: "ClassifierKeywords | None" = None
) -> str:
    """Determine signal direction from HLR text keywords.

    Returns "发送" | "接收" | "" (unknown/ambiguous).
    """
    _, _, _, send_pat, receive_pat, strong_send_pat, strong_receive_pat = _resolve_keywords(keywords)

    has_strong_send = bool(strong_send_pat and strong_send_pat.search(text))
    has_strong_receive = bool(strong_receive_pat and strong_receive_pat.search(text))
    has_any_send = has_strong_send or bool(send_pat and send_pat.search(text))
    has_any_receive = has_strong_receive or bool(receive_pat and receive_pat.search(text))

    if has_strong_send and not has_strong_receive:
        return "发送"
    if has_strong_receive and not has_strong_send:
        return "接收"
    if has_any_send and not has_any_receive:
        return "发送"
    if has_any_receive and not has_any_send:
        return "接收"
    return ""


def enrich_label(
    hlr: HLRRequirement,
    label: HLRLabel,
    keywords: "ClassifierKeywords | None" = None,
) -> HLRLabel:
    text = hlr.content
    label.signal_category = classify_hlr(text, keywords=keywords)
    label.bit_fields = extract_bit_fields(text)
    label.sdi_value = extract_sdi(text)
    label.extracted_direction = extract_direction(text, keywords=keywords)

    classifier_labels = extract_labels(text)
    existing = set(label.labels)
    for cl in classifier_labels:
        if cl not in existing:
            label.labels.append(cl)

    if label.extracted_direction == "发送":
        for kw in ["发送", "写入", "输出"]:
            if kw not in label.direction_keywords:
                label.direction_keywords.append(kw)
    elif label.extracted_direction == "接收":
        for kw in ["接收", "采集", "解析"]:
            if kw not in label.direction_keywords:
                label.direction_keywords.append(kw)

    return label


def enrich_all_labels(
    hlr_reqs: list[HLRRequirement],
    ai_labels: dict[str, HLRLabel],
    keywords: "ClassifierKeywords | None" = None,
) -> dict[str, HLRLabel]:
    for hlr in hlr_reqs:
        lbl = ai_labels.get(hlr.requirement_id)
        if lbl is not None:
            enrich_label(hlr, lbl, keywords=keywords)
    return ai_labels
