# -*- coding: utf-8 -*-
"""Multi-agent judging panel: call N LLM providers in parallel per case."""

from __future__ import annotations

import asyncio

from app.v4.comparison.semantic_judge import (
    _build_reverse_user_prompt,
    _call_reverse_judge_api,
)
from app.v4.llm import get_llm
from app.v4.models import ReverseCase


async def _judge_with_provider(
    case: ReverseCase,
    provider: str,
    system_prompt: str,
) -> dict:
    """Single provider judging one case asynchronously. Returns judgment dict on success,
    or error dict on failure (never raises)."""
    llm = get_llm(provider)
    user_prompt = _build_reverse_user_prompt(case)
    try:
        result = await asyncio.to_thread(
            _call_reverse_judge_api,
            llm=llm,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            case=case,
        )
        return {
            "agent_name": provider,
            "coverage_status": result.coverage_status,
            "difference_type": result.difference_type,
            "missing_points": result.missing_points,
            "inconsistent_points": result.inconsistent_points,
            "analysis": result.analysis,
            "suggested_action": result.suggested_action,
            "confidence": result.confidence,
            "raw_response": "",
        }
    except Exception as e:
        return {
            "agent_name": provider,
            "coverage_status": "error",
            "difference_type": "",
            "missing_points": [],
            "inconsistent_points": [],
            "analysis": f"调用失败: {str(e)}",
            "suggested_action": "",
            "confidence": 0.0,
            "raw_response": "",
        }


# Cached prompt load (module-level, read once per process)
_reverse_prompt: str | None = None


def _load_reverse_prompt() -> str:
    global _reverse_prompt
    if _reverse_prompt is None:
        from app.v4.prompts import load_prompt
        _reverse_prompt = load_prompt("reverse_judge")
    return _reverse_prompt
