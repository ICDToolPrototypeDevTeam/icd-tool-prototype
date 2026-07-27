# -*- coding: utf-8 -*-
"""Multi-agent judging panel: call N LLM providers in parallel per case."""

from __future__ import annotations

import json
import sys
import time

from app.comparison.semantic_judge import (
    _build_reverse_user_prompt,
    _call_reverse_judge_api,
)
from app.config import JUDGE_PROVIDERS
from app.llm import get_llm
from app.models import MultiJudgeOutput, MultiJudgeResult, ReverseCase


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

    for idx, case in enumerate(cases):
        case_judgments: dict[str, dict] = {}

        for provider in providers:
            llm = get_llm(provider)
            # semantic_judge internals: build prompt once, call per provider
            user_prompt = _build_reverse_user_prompt(case)
            result = _call_reverse_judge_api(
                llm=llm,
                system_prompt=_load_reverse_prompt(),
                user_prompt=user_prompt,
                case=case,
            )
            case_judgments[provider] = {
                "coverage_status": result.coverage_status,
                "difference_type": result.difference_type,
                "missing_points": result.missing_points,
                "inconsistent_points": result.inconsistent_points,
                "analysis": result.analysis,
                "suggested_action": result.suggested_action,
                "confidence": result.confidence,
                "source": provider,
            }

        results.append(MultiJudgeResult(
            case_id=case.case_id,
            judgments=case_judgments,
        ))

        statuses = {p: j["coverage_status"] for p, j in case_judgments.items()}
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
        from app.prompts import load_prompt
        _reverse_prompt = load_prompt("reverse_judge")
    return _reverse_prompt
