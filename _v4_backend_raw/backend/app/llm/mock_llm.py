# -*- coding: utf-8 -*-
"""Mock LLM client for offline development — returns preset JSON responses."""

from __future__ import annotations

import os
import json


class MockLLMClient:
    """Returns preset JSON based on MOCK_JUDGE_RESULT env var.

    MOCK_JUDGE_RESULT options:
      "covered"       -> coverage_status=covered, confidence=0.92
      "inconsistent"  -> coverage_status=inconsistent, confidence=0.80
      "needs_review"  -> coverage_status=needs_review, confidence=0.40
      (unset/default) -> coverage_status=covered, confidence=0.90
    """

    @property
    def model(self) -> str:
        return "mock"

    def chat(self, messages: list[dict], **kwargs) -> "ChatResponse":
        from app.llm.factory import ChatResponse

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
        data = templates.get(preset, templates["covered"])
        return ChatResponse(
            content=json.dumps(data, ensure_ascii=False),
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
