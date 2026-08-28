# -*- coding: utf-8 -*-
"""Report generator: aggregates consensus results into a reverse coverage report."""

from __future__ import annotations

from app.v4.models import (
    ConsensusOutput,
    ReverseJudgmentResult,
    ReverseJudgmentOutput,
    ReverseMatchOutput,
)


def generate_reverse_report(
    judgments: list[ReverseJudgmentResult],
    match_output: ReverseMatchOutput | None = None,
) -> ReverseJudgmentOutput:
    """Aggregate reverse judgments into output with an overall summary.

    When match_output is provided, also reports "待确定" and "无匹配" HLRs
    that were filtered before AI judging.
    """
    total = len(judgments)

    # Per-status counts
    status_counts: dict[str, int] = {}
    for j in judgments:
        s = j.coverage_status or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    # Categorize by signal_category
    cat_counts: dict[str, int] = {}
    cat_status: dict[str, dict[str, int]] = {}
    for j in judgments:
        cat = j.signal_category or "其他"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        if cat not in cat_status:
            cat_status[cat] = {}
        s = j.coverage_status or "unknown"
        cat_status[cat][s] = cat_status[cat].get(s, 0) + 1

    # Key findings — four-category
    key_findings = []

    covered_count = status_counts.get("covered", 0)
    if covered_count:
        key_findings.append(f"{covered_count} 条HLR与EoICD一致（接口定义正确落实）")

    inconsistent_count = status_counts.get("inconsistent", 0)
    if inconsistent_count:
        key_findings.append(f"{inconsistent_count} 条HLR存在与ICD不一致（方向/数据类型/范围/bit条件等）")

    ai_review_count = status_counts.get("needs_review", 0)
    if ai_review_count:
        key_findings.append(f"{ai_review_count} 条HLR经AI判定为待确认（ICD Block与HLR不相关或无法判断，需人工确认）")

    # ── Match-layer 无匹配 (never sent to AI) ──
    no_match_judgments: list[ReverseJudgmentResult] = []
    if match_output:
        for r in match_output.results:
            if r.match_type == "无匹配":
                no_match_judgments.append(ReverseJudgmentResult(
                    case_id=f"NOMATCH-{len(no_match_judgments) + 1:04d}",
                    hlr_id=r.hlr_id,
                    hlr_content=r.hlr_content,
                    signal_category=r.signal_category,
                    matched_profiles_summary=[],
                    match_evidence={k: v for k, v in r.match_evidence.items() if k != "top_scores"},
                    coverage_status="无匹配",
                    analysis=r.summary or "匹配层未在EoICD中找到对应的ICD信号定义，建议人工确认。",
                    confidence=0.0,
                ))

    no_match_count = len(no_match_judgments)
    if no_match_count:
        key_findings.append(f"{no_match_count} 条HLR未匹配（匹配层未找到对应EoICD信号）")

    hlr_total = match_output.total_hlr if match_output else total
    summary = {
        "概述": (
            f"对{hlr_total}条软件高层需求(HLR)进行EoICD反向覆盖分析（DeepSeek单模型裁判）: "
            f"{total}条通过匹配层进入AI裁判, {no_match_count}条在匹配层未找到对应信号。"
        ),
        "判定分布": {
            "一致": covered_count,
            "不一致": inconsistent_count,
            "待确认": ai_review_count,
            "未匹配": no_match_count,
        },
        "按信号类别分布": {
            cat: {"小计": cat_counts[cat], "判定": cat_status[cat]}
            for cat in sorted(cat_counts.keys())
        },
        "关键发现": key_findings,
        "建议": [
            "一致的 HLR: EoICD定义的接口要求在HLR中正确落实，无需处理",
            "不一致的 HLR: 逐一核对与ICD矛盾的具体属性（方向/bit/数据类型/范围等），确定以哪方为准",
            "待确认的 HLR: AI判定ICD Block与HLR不相关或无法判断，需人工逐条确认",
            "未匹配的 HLR: 匹配层未在EoICD中找到对应信号，需检查HLR是否属于ICD接口范畴",
        ],
    }

    all_results = list(judgments) + no_match_judgments

    return ReverseJudgmentOutput(
        total_cases=hlr_total,
        summary=summary,
        results=all_results,
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
                    analysis=r.summary or "匹配层未在EoICD中找到对应的ICD信号定义，建议人工确认。",
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