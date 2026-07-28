# -*- coding: utf-8 -*-
"""Script-based HLR classifier: 4-path decision tree + structured field extraction.

Supplements AI labeling with deterministic regex-based extraction for:
- Signal category classification (4 paths)
- Label numbers (Lxxx)
- Bit ranges (bitX至bitY)
- SDI values
- Direction (发送/接收)
"""

from __future__ import annotations

import re

from app.v4.models import HLRLabel, HLRRequirement

# ── Regex patterns ──────────────────────────────────────────
_LABEL_RE = re.compile(r"L(\d+)", re.IGNORECASE)
_BIT_RANGE_RE = re.compile(r"bit(\d+)\s*[至~\-]\s*bit(\d+)", re.IGNORECASE)
_BIT_SINGLE_RE = re.compile(r"bit(\d+)\s*[为是]\s*(.+?)(?:[，,;；]|$)", re.IGNORECASE)
_BIT_ASSIGN_RE = re.compile(r"bit(\d+)\s*=", re.IGNORECASE)
_BIT_POSSESSIVE_RE = re.compile(r"bit(\d+)\s*的", re.IGNORECASE)
_SDI_RE = re.compile(r"SDI\s*[=为]?\s*(\d+)", re.IGNORECASE)

# ── Classification keywords ─────────────────────────────────
_ANALOG_KW = re.compile(
    r"ADC|模拟量|电压|电流|传感器|采样|量程|[Vv]oltage|[Cc]urrent|[Ss]ensor|A/D|AD转换|ADCIN",
)
_DISCRETE_KW = re.compile(
    r"微动|开关|[Tt]rue|[Ff]alse|故障|跳变|触点|离散量|开关量|BOOL|BOOLEAN|DISCRETE|状态位|有效位",
)
_BUS_KW = re.compile(r"CAN|A825|A664|A429|AFDX|总线|ARINC|通信|接收|发送|写入|采集|解析")

# ── Direction keywords ──────────────────────────────────────
_SEND_KW = re.compile(r"发送|写入|输出|上报|发布|驱动|设置|控制")
_RECEIVE_KW = re.compile(r"接收|采集|解析|获取|读取|监测|判断|检测|输入")


def classify_hlr(text: str) -> str:
    """Classify an HLR requirement into one of 5 categories.

    Returns one of: "A429显式" | "模拟量" | "离散量" | "A429隐式" | "逻辑/非通信"
    """
    has_label = bool(_LABEL_RE.search(text))

    if has_label:
        return "A429显式"

    if _ANALOG_KW.search(text):
        return "模拟量"

    if _DISCRETE_KW.search(text):
        return "离散量"

    if _BUS_KW.search(text):
        return "A429隐式"

    return "逻辑/非通信"


def extract_labels(text: str) -> list[str]:
    """Extract Label numbers from HLR text. Returns e.g. ['L203', 'L30']."""
    return [f"L{m.group(1)}" for m in _LABEL_RE.finditer(text)]


def extract_bit_fields(text: str) -> list[dict]:
    """Extract bit field definitions from HLR text.

    Returns list of {offset: int, size: int, text: str}.
    """
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

    return fields


def extract_sdi(text: str) -> str:
    """Extract SDI value from HLR text. Returns '' if not found."""
    m = _SDI_RE.search(text)
    return m.group(1) if m else ""


_STRONG_SEND = re.compile(r"发送|写入|输出|发布")
_STRONG_RECEIVE = re.compile(r"接收|解析|采集")


def extract_direction(text: str) -> str:
    """Determine signal direction from HLR text keywords.

    Returns "发送" | "接收" | "" (unknown/ambiguous).

    Strong keywords (发送/写入/接收/解析) take priority over weak ones.
    """
    has_strong_send = bool(_STRONG_SEND.search(text))
    has_strong_receive = bool(_STRONG_RECEIVE.search(text))
    has_any_send = has_strong_send or bool(_SEND_KW.search(text))
    has_any_receive = has_strong_receive or bool(_RECEIVE_KW.search(text))

    # Strong send beats any receive
    if has_strong_send and not has_strong_receive:
        return "发送"
    # Strong receive beats any send
    if has_strong_receive and not has_strong_send:
        return "接收"
    # Both or neither strong keywords: use any keyword match
    if has_any_send and not has_any_receive:
        return "发送"
    if has_any_receive and not has_any_send:
        return "接收"
    return ""


def enrich_label(hlr: HLRRequirement, label: HLRLabel) -> HLRLabel:
    """Enrich an HLRLabel with script-extracted classifier data.

    Classifier results supplement AI labels; they never replace.
    Deterministic fields (signal_category, bit_fields, sdi_value, extracted_direction)
    are set from regex. AI labels remain as-is for semantic fields.
    """
    text = hlr.content

    label.signal_category = classify_hlr(text)
    label.bit_fields = extract_bit_fields(text)
    label.sdi_value = extract_sdi(text)
    label.extracted_direction = extract_direction(text)

    # Merge classifier labels into AI labels (union, no duplicates)
    classifier_labels = extract_labels(text)
    existing = set(label.labels)
    for cl in classifier_labels:
        if cl not in existing:
            label.labels.append(cl)

    # Merge direction keywords
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
) -> dict[str, HLRLabel]:
    """Run classifier enrichment on all HLR labels."""
    for hlr in hlr_reqs:
        lbl = ai_labels.get(hlr.requirement_id)
        if lbl is not None:
            enrich_label(hlr, lbl)
    return ai_labels
