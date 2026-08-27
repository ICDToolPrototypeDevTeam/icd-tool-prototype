# -*- coding: utf-8 -*-
"""Mock LLM client for offline development — schema-aware preset JSON responses.

正向缺陷修正 #3：MockLLMClient 现在按请求类型（prompt 特征）分派，返回对应 schema，
而不是一律返回反向的 coverage_status：

  - label          → HLRLabel JSON（bus_types/labels/devices/signal_keywords/...）
  - forward_review → 三态判定 JSON（review_verdict/matched_hlr_ids/rejected_hlr_ids/...）
  - reverse        → coverage_status JSON（保持原有 MOCK_JUDGE_RESULT 行为不变）

可通过环境变量控制：
  MOCK_JUDGE_RESULT     反向裁判（covered/inconsistent/needs_review），保持兼容。
  MOCK_FORWARD_VERDICT  正向三态复核（covered/not_same_object/unconfirmed/exception/bad_json）。
"""

from __future__ import annotations

import json
import os
import re
import threading


# ── Forward-labeling context (正向缺陷修正 #3 / 反向基线保护) ────────────────
#
# label_hlrs() 同时被反向与正向管线调用，且二者 prompt 完全一致，mock 无法按
# prompt 区分。为满足「正向 llm_label tokens 非空」同时「反向基线 5/7/4/12 不变」，
# 正向管线在调用 label_hlrs 前进入 forward_label_context()，让 mock 返回非空标签；
# 反向管线不进入，mock 返回空标签（等价于旧版 mock 行为，反向匹配结果不变）。
_FORWARD_LABELING = threading.local()


def _in_forward_labeling() -> bool:
    return bool(getattr(_FORWARD_LABELING, "active", False))


class forward_label_context:
    """Thread-local context: mark label calls as forward-pipeline labeling."""

    def __enter__(self) -> "forward_label_context":
        _FORWARD_LABELING.active = True
        return self

    def __exit__(self, *exc) -> bool:
        _FORWARD_LABELING.active = False
        return False


class MockLLMClient:
    """Returns preset JSON based on the request kind + env overrides."""

    @property
    def model(self) -> str:
        return "mock"

    def chat(self, messages: list[dict], **kwargs) -> "ChatResponse":
        from app.v4.llm.factory import ChatResponse

        mode = _detect_mode(messages)
        if mode == "label":
            data = _mock_label(messages)
        elif mode == "forward_review":
            data = _mock_forward_review(messages)
        else:
            data = _mock_reverse()

        return ChatResponse(
            content=json.dumps(data, ensure_ascii=False),
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )


# ── Request detection ───────────────────────────────────────────────────────


def _detect_mode(messages: list[dict]) -> str:
    """Dispatch on prompt markers: label / forward_review / reverse."""
    joined = "\n".join(m.get("content", "") or "" for m in messages)
    if "提取以下标签" in joined or '"bus_types"' in joined:
        return "label"
    if "review_verdict" in joined or "正向完整性分析" in joined or "候选 HLR" in joined:
        return "forward_review"
    return "reverse"


# ── Reverse (preserve original MOCK_JUDGE_RESULT behavior) ─────────────────


def _mock_reverse() -> dict:
    preset = os.getenv("MOCK_JUDGE_RESULT", "covered")
    templates = {
        "covered": {
            "coverage_status": "covered",
            "analysis": "Mock: ICD 接口要求在 HLR 中正确落实。",
            "confidence": 0.92,
        },
        "inconsistent": {
            "coverage_status": "inconsistent",
            "analysis": "Mock: HLR 与 ICD 定义存在矛盾（数据类型/方向等不一致）。",
            "confidence": 0.80,
        },
        "needs_review": {
            "coverage_status": "needs_review",
            "analysis": "Mock: 匹配的 ICD Block 与 HLR 不相关，或无法判断覆盖关系。",
            "confidence": 0.40,
        },
    }
    return templates.get(preset, templates["covered"])


# ── Label (HLR AI pre-labeling) ─────────────────────────────────────────────


def _extract_user_content(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            return m.get("content", "") or ""
    return ""


_EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_LABEL_RE = re.compile(r"L(\d+)", re.IGNORECASE)


def _mock_label(messages: list[dict]) -> dict:
    """Deterministic label JSON: extract L-numbers + English identifiers + bus/direction.

    - Forward pipeline（进入 forward_label_context）→ 返回非空标签，使 llm_label_tokens 非空；
    - Reverse pipeline（未进入）→ 返回空标签，等价于旧版 mock 行为，反向基线 5/7/4/12 不变。
    """
    content = _extract_user_content(messages)

    if not _in_forward_labeling():
        return {
            "bus_types": [],
            "labels": [],
            "devices": [],
            "signal_keywords": [],
            "attr_categories": [],
            "direction_keywords": [],
        }

    labels = [f"L{n}" for n in _LABEL_RE.findall(content)]

    upper = content.upper()
    bus_types: list[str] = []
    for keyword, bt in (
        ("ARINC429", "A429"), ("A429", "A429"),
        ("CAN", "A825"), ("A825", "A825"),
        ("AFDX", "A664"), ("A664", "A664"),
        ("模拟量", "Analog"), ("ANALOG", "Analog"), ("ADC", "Analog"),
        ("离散量", "Discrete"), ("DISCRETE", "Discrete"), ("开关量", "Discrete"),
    ):
        if keyword.upper() in upper and bt not in bus_types:
            bus_types.append(bt)

    signal_keywords: list[str] = []
    for tok in _EN_TOKEN_RE.findall(content):
        up = tok.upper()
        if up in ("THE", "AND", "FOR", "A429", "A825", "A664", "CAN", "AFDX", "ADC", "BOOL", "DISCRETE"):
            continue
        if up not in signal_keywords:
            signal_keywords.append(up)
        if len(signal_keywords) >= 12:
            break

    direction_keywords: list[str] = []
    for kw, d in (("发送", "发送"), ("写入", "发送"), ("输出", "发送"),
                  ("接收", "接收"), ("采集", "接收"), ("解析", "接收")):
        if kw in content and d not in direction_keywords:
            direction_keywords.append(d)

    return {
        "bus_types": bus_types,
        "labels": labels,
        "devices": [],
        "signal_keywords": signal_keywords,
        "attr_categories": [],
        "direction_keywords": direction_keywords,
    }


# ── Forward three-state review ──────────────────────────────────────────────


def _extract_candidate_ids(messages: list[dict]) -> list[str]:
    """Extract candidate HLR ids from the review user prompt ('候选 N: HLRxxx')."""
    content = _extract_user_content(messages)
    ids: list[str] = []
    for m in re.finditer(r"候选\s*\d+\s*[:：]\s*([A-Za-z0-9_\-]+)", content):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def _mock_forward_review(messages: list[dict]) -> dict:
    verdict = os.getenv("MOCK_FORWARD_VERDICT", "covered")
    if verdict == "exception":
        raise RuntimeError("Mock forward review: simulated exception")

    candidate_ids = _extract_candidate_ids(messages)

    if verdict == "bad_json":
        return {"review_verdict": 12345, "confidence": "not-a-float"}  # will fail json parse
    if verdict == "not_same_object":
        return {
            "review_verdict": "not_same_object",
            "matched_hlr_ids": [],
            "rejected_hlr_ids": candidate_ids,
            "confidence": 0.85,
            "rationale": "Mock: 候选 HLR 描述的是别的对象（Label/协议/设备冲突），判定非同一对象。",
        }
    if verdict == "unconfirmed":
        return {
            "review_verdict": "unconfirmed",
            "matched_hlr_ids": [],
            "rejected_hlr_ids": [],
            "confidence": 0.5,
            "rationale": "Mock: 信息不足，无法确定是否描述了该对象。",
        }
    # default: covered
    return {
        "review_verdict": "covered",
        "matched_hlr_ids": candidate_ids,
        "rejected_hlr_ids": [],
        "confidence": 0.9,
        "rationale": "Mock: 候选 HLR 描述了该 EoICD 业务对象。",
    }
