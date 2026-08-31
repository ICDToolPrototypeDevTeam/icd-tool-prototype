# -*- coding: utf-8 -*-
"""Review agent: consensus review of multi-agent judgment results."""

from __future__ import annotations

import json
import sys
import time

from app.v4.llm import get_llm
from app.v4.models import (
    ConsensusOutput,
    ConsensusResult,
    FieldDisagreement,
    InconsistentAttribute,
    MultiJudgeResult,
)
from app.v4.prompts import load_prompt


# ADR-004 v2: 12 个关键字段，minority 涉及则触发 2★
KEY_FIELDS = frozenset({
    "Direction", "DataFormatType", "BitOffset", "ParameterSize",
    "OneState", "ZeroState", "Label", "FuncRngMin", "FuncRngMax",
    "Units", "Period", "SDIExpected",
})


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
    """Call LLM and parse JSON response for consensus review.

    5 星体系（ADR-004 v3 fusion）：star_rating 由 agreement_level +
    field_disagreements 联合映射。LLM 另行输出 inconsistent_attributes
    （HLR 与 ICD 的事实差异，主字段，供 Word 报告渲染），与
    field_disagreements（provider 间字段级分歧，辅助字段）互相独立。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(max_retries + 1):
        try:
            from app.v4.comparison.semantic_judge import _extract_json
            response = llm.chat(messages=messages, temperature=0.1, max_tokens=8192)
            content = _extract_json(response["content"])
            data = json.loads(content)
            consistent, divergent = _derive_consensus_details(data, model_results)
            agreement = data.get("agreement_level", "split")
            inconsistent_attributes = _parse_inconsistent_attributes(
                data.get("inconsistent_attributes", [])
            )
            field_disagreements = _parse_field_disagreements(
                data.get("field_disagreements", [])
            )
            star = _map_star_rating(agreement, field_disagreements)
            # 5★/4★/3★/2★ → 取 majority 的 coverage_status；1★ → 待确认
            if agreement in ("full", "majority") and star >= 2:
                final_status = _majority_status(model_results, consistent)
            else:
                final_status = "待确认"
            return ConsensusResult(
                case_id=case_id,
                model_results=model_results,
                agreement_level=agreement,
                star_rating=star,
                inconsistent_attributes=inconsistent_attributes,
                field_disagreements=field_disagreements,
                final_coverage_status=final_status,
                final_analysis=data.get("final_analysis", ""),
                confidence=float(data.get("confidence", 0.5)),
                consistent_agents=consistent,
                divergent_agents=divergent,
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
        inconsistent_attributes=[],
        field_disagreements=[],
        final_coverage_status="待确认",
        final_analysis="Review API error after retries",
        confidence=0.0,
        consistent_agents=[],
        divergent_agents=[],
    )


def _parse_inconsistent_attributes(raw: list) -> list[InconsistentAttribute]:
    """Parse inconsistent_attributes (EoICD-HLR 事实差异) with defensive coercion.

    LLM may return entries as dicts ({"attribute", "detail", "providers"}) or
    bare attribute-name strings. Malformed entries are dropped silently.
    """
    out: list[InconsistentAttribute] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            try:
                out.append(InconsistentAttribute(**item))
            except Exception:
                continue
        elif isinstance(item, str) and item.strip():
            out.append(InconsistentAttribute(attribute=item.strip()))
    return out


def _parse_field_disagreements(raw: list) -> list[FieldDisagreement]:
    """Parse field_disagreements from LLM JSON response with defensive coercion.

    LLM may return entries as:
    - dicts: {"field": "Direction", "category": "key", ...}  → FieldDisagreement
    - strings: "Direction"  → converted to vague FieldDisagreement (legacy/edge case)
    Invalid entries are dropped silently.
    """
    out: list[FieldDisagreement] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            try:
                out.append(FieldDisagreement(**item))
            except Exception:
                # Skip malformed entries; do not fail the whole response
                continue
        elif isinstance(item, str) and item.strip():
            # Legacy/edge: LLM returned bare field name string
            out.append(FieldDisagreement(field=item.strip(), category="vague"))
    return out


def _map_star_rating(agreement: str, field_disagreements: list) -> int:
    """Map (agreement_level, field_disagreements) to a 1-5 star rating (ADR-004 v2).

    Mapping table:
        5★: agreement=full,                  field_disagreements 为空
        4★: agreement=full,                  field_disagreements 非空（含 key/non_key/vague）
        3★: agreement=majority,              field_disagreements 无 key 字段
        2★: agreement=majority,              field_disagreements 含 ≥1 个 key 字段
        1★: agreement ∈ {split, single_source, no_consensus}

    The function never raises: an unrecognised agreement_level defaults to 1★
    so a malformed LLM response can never inflate the rating.
    """
    a = (agreement or "").lower()
    has_key = any(getattr(f, "category", "") == "key" for f in field_disagreements)
    has_any = bool(field_disagreements)
    if a in ("split", "single_source", "no_consensus"):
        return 1
    if a == "full":
        return 4 if has_any else 5
    if a == "majority":
        return 2 if has_key else 3
    return 1


def _majority_status(
    judgments: dict[str, dict],
    consistent_agents: list[str],
) -> str:
    """Return the coverage_status that the majority of agents agree on."""
    from collections import Counter
    if consistent_agents:
        for name in consistent_agents:
            if name in judgments:
                status = judgments[name].get("coverage_status", "")
                if status:
                    return status
    statuses = [j.get("coverage_status", "") for j in judgments.values()]
    counts = Counter([s for s in statuses if s])
    return counts.most_common(1)[0][0] if counts else "needs_review"


def _derive_consensus_details(
    data: dict,
    judgments: dict[str, dict],
) -> tuple[list[str], list[str]]:
    """Derive consistent/divergent agents from LLM response or majority vote.

    Prefers explicit fields from LLM; falls back to majority voting on coverage_status.
    """
    consistent = list(data.get("consistent_agents", []))
    divergent = list(data.get("divergent_agents", []))

    if not consistent and not divergent:
        # Fallback: majority vote by coverage_status
        from collections import Counter
        statuses = [
            j.get("coverage_status", "") for j in judgments.values()
        ]
        counts = Counter(statuses)
        majority_status = counts.most_common(1)[0][0] if counts else ""
        for provider, j in judgments.items():
            if j.get("coverage_status", "") == majority_status:
                consistent.append(provider)
            else:
                divergent.append(provider)

    return consistent, divergent


_STATUS_CN = {"covered": "已覆盖", "inconsistent": "不一致", "needs_review": "待确认", "待确认": "待确认"}


def _build_summary(results: list[ConsensusResult]) -> dict:
    """Aggregate review statistics (5-key star_distribution, ADR-004)."""
    star_dist = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    agreement_dist: dict[str, int] = {}
    status_dist: dict[str, int] = {}

    for r in results:
        key = str(r.star_rating) if r.star_rating in (1, 2, 3, 4, 5) else "1"
        star_dist[key] += 1
        agreement_dist[r.agreement_level] = agreement_dist.get(r.agreement_level, 0) + 1
        cn = _STATUS_CN.get(r.final_coverage_status, r.final_coverage_status)
        status_dist[cn] = status_dist.get(cn, 0) + 1

    avg_stars = (
        sum(r.star_rating for r in results) / len(results)
        if results else 0.0
    )

    return {
        "total": len(results),
        "star_distribution": star_dist,
        "agreement_distribution": agreement_dist,
        "status_distribution": status_dist,
        "average_star_rating": round(avg_stars, 2),
    }


_consensus_prompt: str | None = None


def _load_consensus_prompt() -> str:
    global _consensus_prompt
    if _consensus_prompt is None:
        _consensus_prompt = load_prompt("consensus")
    return _consensus_prompt
