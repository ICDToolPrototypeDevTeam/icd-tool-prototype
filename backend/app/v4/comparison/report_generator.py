# -*- coding: utf-8 -*-
"""Report generator: aggregates consensus results into a reverse coverage report."""

from __future__ import annotations

from app.v4.models import (
    ConsensusOutput,
    ReverseJudgmentResult,
    ReverseJudgmentOutput,
    ReverseMatchOutput,
)


def generate_consensus_reverse_report(
    consensus_out: ConsensusOutput,
    match_output: ReverseMatchOutput | None = None,
) -> ReverseJudgmentOutput:
    """Generate reverse report from consensus review results.

    Maps ConsensusResult fields to ReverseJudgmentResult for backward-compatible
    output format, augmented with consensus metadata (star ratings, agreement).
    """
    # ── Per-status counts ──
    status_counts: dict[str, int] = {}
    for c in consensus_out.results:
        s = c.final_coverage_status or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    # ── Agreement and star stats ──
    agreement_counts: dict[str, int] = {}
    star_counts: dict[str, int] = {}
    for c in consensus_out.results:
        agreement_counts[c.agreement_level] = agreement_counts.get(c.agreement_level, 0) + 1
        star_counts[str(c.star_rating)] = star_counts.get(str(c.star_rating), 0) + 1

    # ── Convert to ReverseJudgmentResult for output compatibility ──
    converted: list[ReverseJudgmentResult] = []
    for c in consensus_out.results:
        converted.append(ReverseJudgmentResult(
            case_id=c.case_id,
            coverage_status=c.final_coverage_status,
            difference_type="",
            analysis=c.final_analysis,
            confidence=c.confidence,
            # Preserve consensus metadata in match_evidence
            match_evidence={
                "agreement_level": c.agreement_level,
                "star_rating": c.star_rating,
                "model_results": c.model_results,
            },
        ))

    # ── No-match HLRs from match output ──
    pending_review: list[dict] = []
    no_match_judgments: list[ReverseJudgmentResult] = []
    if match_output:
        for r in match_output.results:
            if r.match_type in ("待确定", "无匹配"):
                pending_review.append({
                    "hlr_id": r.hlr_id,
                    "signal_category": r.signal_category,
                    "match_type": r.match_type,
                    "summary": r.summary,
                })
            if r.match_type == "无匹配":
                no_match_judgments.append(ReverseJudgmentResult(
                    case_id=f"NOMATCH-{len(no_match_judgments) + 1:04d}",
                    hlr_id=r.hlr_id,
                    hlr_content=r.hlr_content,
                    signal_category=r.signal_category,
                    matched_profiles_summary=[],
                    coverage_status="无匹配",
                    analysis="匹配层未在EoICD中找到对应的ICD信号定义，建议人工确认。",
                    confidence=0.0,
                ))

    hlr_total = match_output.total_hlr if match_output else len(consensus_out.results)
    covered = status_counts.get("covered", 0)
    partial = status_counts.get("partial", 0)
    missing = status_counts.get("missing", 0)
    inconsistent = status_counts.get("inconsistent", 0)
    needs_review = status_counts.get("needs_review", 0)

    key_findings = []
    if pending_review:
        uncertain = sum(1 for p in pending_review if p["match_type"] == "待确定")
        no_match = sum(1 for p in pending_review if p["match_type"] == "无匹配")
        key_findings.append(
            f"{len(pending_review)} 条HLR未进入AI裁判: "
            f"{uncertain}条待确定, {no_match}条无匹配"
        )
    if covered:
        key_findings.append(f"{covered} 条完全覆盖")
    if partial:
        key_findings.append(f"{partial} 条部分覆盖")
    if missing:
        key_findings.append(f"{missing} 条缺失")
    if inconsistent:
        key_findings.append(f"{inconsistent} 条不一致")
    if needs_review:
        key_findings.append(f"{needs_review} 条需人工确认")

    # Review agent quality metrics
    full_agree = agreement_counts.get("full", 0)
    majority_agree = agreement_counts.get("majority", 0)
    split_agree = agreement_counts.get("split", 0)
    avg_stars = (
        sum(c.star_rating for c in consensus_out.results) / len(consensus_out.results)
        if consensus_out.results else 0
    )

    summary = {
        "概述": (
            f"对{hlr_total}条HLR进行多模型共识分析 (3 Agent裁判 + Review Agent复核): "
            f"full={full_agree}, majority={majority_agree}, split={split_agree}, "
            f"平均星级={avg_stars:.1f}"
        ),
        "判定分布": {
            "AI已裁判": status_counts,
            "待人工审核": {
                "待确定": sum(1 for p in pending_review if p["match_type"] == "待确定"),
                "无匹配": sum(1 for p in pending_review if p["match_type"] == "无匹配"),
            },
        },
        "共识质量": {
            "agreement_distribution": agreement_counts,
            "star_distribution": star_counts,
            "average_star_rating": round(avg_stars, 1),
        },
        "关键发现": key_findings,
        "建议": [
            "3星full agreement的条目可直接采纳",
            "2星majority的条目需关注少数意见",
            "1星split的条目建议人工逐条复核",
        ],
    }

    all_results = converted + no_match_judgments

    return ReverseJudgmentOutput(
        total_cases=hlr_total,
        summary=summary,
        results=all_results,
    )
