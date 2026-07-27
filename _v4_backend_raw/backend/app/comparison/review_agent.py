# -*- coding: utf-8 -*-
"""Review agent: consensus review of multi-agent judgment results."""

from __future__ import annotations

import json
import sys
import time

from app.llm import get_llm
from app.models import ConsensusOutput, ConsensusResult, MultiJudgeResult
from app.prompts import load_prompt


def review_judgments(
    multi_results: list[MultiJudgeResult],
) -> ConsensusOutput:
    """Review per-case multi-agent judgments and produce consensus.

    Each case with N judgments is sent to the review LLM for
    agreement assessment, star rating, and final coverage status.
    """
    llm = get_llm("deepseek")
    system_prompt = _load_consensus_prompt()
    total = len(multi_results)
    results: list[ConsensusResult] = []

    for idx, mr in enumerate(multi_results):
        user_prompt = _build_review_user_prompt(mr)
        consensus = _call_review_api(
            llm=llm,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            case_id=mr.case_id,
            model_results=mr.judgments,
        )
        results.append(consensus)

        print(
            f"  [review] {mr.case_id} ({idx + 1}/{total}) "
            f"stars={consensus.star_rating} agreement={consensus.agreement_level} "
            f"→ {consensus.final_coverage_status}",
            file=sys.stderr,
        )

        if idx < total - 1:
            time.sleep(0.3)

    summary = _build_summary(results)
    return ConsensusOutput(
        total_cases=total,
        summary=summary,
        results=results,
    )


def _build_review_user_prompt(mr: MultiJudgeResult) -> str:
    """Present all judgments for one case to the review agent."""
    parts = []
    parts.append(f"## 案例: {mr.case_id}")
    parts.append("")
    parts.append("以下是对同一需求案例的多份裁判结果：")
    parts.append("")

    for i, (provider, judgment) in enumerate(mr.judgments.items(), 1):
        parts.append(f"### 裁判 {i}: {provider}")
        parts.append(f"- coverage_status: {judgment.get('coverage_status', 'N/A')}")
        parts.append(f"- difference_type: {judgment.get('difference_type', 'N/A')}")
        parts.append(f"- confidence: {judgment.get('confidence', 0):.2f}")
        parts.append(f"- analysis: {judgment.get('analysis', 'N/A')}")
        missing = judgment.get("missing_points", [])
        if missing:
            parts.append(f"- missing_points: {', '.join(missing)}")
        inconsistent = judgment.get("inconsistent_points", [])
        if inconsistent:
            parts.append(f"- inconsistent_points: {', '.join(inconsistent)}")
        parts.append(f"- suggested_action: {judgment.get('suggested_action', 'N/A')}")
        parts.append("")

    parts.append("请综合以上结果，输出共识判定 JSON。")
    return "\n".join(parts)


def _call_review_api(
    llm,
    system_prompt: str,
    user_prompt: str,
    case_id: str,
    model_results: dict[str, dict],
    max_retries: int = 2,
) -> ConsensusResult:
    """Call LLM and parse JSON response for consensus review."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(max_retries + 1):
        temperature = 0.1 if attempt == 0 else 0.1 + attempt * 0.1
        try:
            response = llm.chat(messages=messages, temperature=temperature)
            from app.comparison.semantic_judge import _extract_json
            content = _extract_json(response["content"])
            data = json.loads(content)
            return ConsensusResult(
                case_id=case_id,
                model_results=model_results,
                agreement_level=data.get("agreement_level", "split"),
                star_rating=int(data.get("star_rating", 1)),
                final_coverage_status=data.get("final_coverage_status", "needs_review"),
                final_analysis=data.get("final_analysis", ""),
                confidence=float(data.get("confidence", 0.5)),
            )
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            if attempt < max_retries:
                time.sleep(1.0)
                continue
        except Exception:
            if attempt < max_retries:
                time.sleep(2.0)
                continue

    return ConsensusResult(
        case_id=case_id,
        model_results=model_results,
        agreement_level="split",
        star_rating=1,
        final_coverage_status="needs_review",
        final_analysis="Review API error after retries",
        confidence=0.0,
    )


def _build_summary(results: list[ConsensusResult]) -> dict:
    """Aggregate review statistics."""
    star_dist = {"1": 0, "2": 0, "3": 0}
    agreement_dist: dict[str, int] = {}
    status_dist: dict[str, int] = {}

    for r in results:
        star_dist[str(r.star_rating)] = star_dist.get(str(r.star_rating), 0) + 1
        agreement_dist[r.agreement_level] = agreement_dist.get(r.agreement_level, 0) + 1
        status_dist[r.final_coverage_status] = status_dist.get(r.final_coverage_status, 0) + 1

    avg_stars = (
        sum(r.star_rating for r in results) / len(results)
        if results else 0.0
    )

    return {
        "total": len(results),
        "star_distribution": star_dist,
        "agreement_distribution": agreement_dist,
        "status_distribution": status_dist,
        "average_star_rating": round(avg_stars, 1),
    }


_consensus_prompt: str | None = None


def _load_consensus_prompt() -> str:
    global _consensus_prompt
    if _consensus_prompt is None:
        _consensus_prompt = load_prompt("consensus")
    return _consensus_prompt
