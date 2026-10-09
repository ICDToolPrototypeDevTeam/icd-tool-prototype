# -*- coding: utf-8 -*-
"""Mock LLM client for offline development — schema-aware preset JSON responses.

MockLLMClient 按请求类型（prompt 特征）分派，返回对应 schema：

  - label   → HLRLabel JSON（反向管线走 mock 时恒返回空标签，见 _mock_label）
  - reverse → coverage_status JSON（保持原有 MOCK_JUDGE_RESULT 行为不变）

可通过环境变量控制：
  MOCK_JUDGE_RESULT     反向裁判（covered/inconsistent/needs_review），保持兼容。
"""

from __future__ import annotations

import json
import os


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
        else:
            data = _mock_reverse()

        return ChatResponse(
            content=json.dumps(data, ensure_ascii=False),
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )


# ── Request detection ───────────────────────────────────────────────────────


def _detect_mode(messages: list[dict]) -> str:
    """Dispatch on prompt markers: label / reverse."""
    joined = "\n".join(m.get("content", "") or "" for m in messages)
    if "提取以下标签" in joined or '"bus_types"' in joined:
        return "label"
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


def _mock_label(messages: list[dict]) -> dict:
    """反向管线 label 调用 → 空标签（等价旧版 mock 行为，反向基线不变）。"""
    return {
        "bus_types": [],
        "labels": [],
        "devices": [],
        "signal_keywords": [],
        "attr_categories": [],
        "direction_keywords": [],
    }
