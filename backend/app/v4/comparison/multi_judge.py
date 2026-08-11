# -*- coding: utf-8 -*-
"""Multi-agent judging panel: call N LLM providers in parallel per case."""

from __future__ import annotations

import asyncio
import json
import sys
import time

from app.v4.comparison.semantic_judge import (
    _build_reverse_user_prompt,
    _call_reverse_judge_api,
)
from app.v4.config import JUDGE_PROVIDERS
from app.v4.llm import get_llm
from app.v4.models import MultiJudgeOutput, MultiJudgeResult, ReverseCase


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


def judge_with_panel(
    cases: list[ReverseCase],
    providers: list[str] | None = None,
) -> MultiJudgeOutput:
    """Judge each ReverseCase with multiple LLM providers.

    Each provider gets the same system prompt and user prompt.
    Only the LLM backend differs.

    Returns MultiJudgeOutput with per-provider judgments nested under each case.
    """
    if providers is None:
        providers = JUDGE_PROVIDERS

    total = len(cases)
    results: list[MultiJudgeResult] = []

    system_prompt = _load_reverse_prompt()

    for idx, case in enumerate(cases):
        # Phase 1: 并行调用所有 provider（asyncio.gather）
        async def _gather():
            return await asyncio.gather(
                *[_judge_with_provider(case, p, system_prompt) for p in providers],
                return_exceptions=True,
            )

        gathered = asyncio.run(_gather())
        case_judgments: dict[str, dict] = {}
        for provider, result in zip(providers, gathered):
            if isinstance(result, Exception):
                case_judgments[provider] = {
                    "agent_name": provider,
                    "coverage_status": "error",
                    "confidence": 0.0,
                    "analysis": f"gather 异常: {str(result)}",
                }
            else:
                case_judgments[provider] = result

        results.append(MultiJudgeResult(
            case_id=case.case_id,
            judgments=case_judgments,
        ))

        statuses = {p: j.get("coverage_status", "?") for p, j in case_judgments.items()}
        print(
            f"  [multi] {case.case_id} ({idx + 1}/{total}) {statuses}",
            file=sys.stderr,
        )

        if idx < total - 1:
            time.sleep(0.3)

    return MultiJudgeOutput(
        total_cases=total,
        providers=providers,
        results=results,
    )


# Cached prompt load (module-level, read once per process)
_reverse_prompt: str | None = None


def _load_reverse_prompt() -> str:
    global _reverse_prompt
    if _reverse_prompt is None:
        from app.v4.prompts import load_prompt
        _reverse_prompt = load_prompt("reverse_judge")
    return _reverse_prompt
