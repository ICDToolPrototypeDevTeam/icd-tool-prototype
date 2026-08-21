# -*- coding: utf-8 -*-
"""Pipeline orchestration: forward and reverse analysis workflows."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from app.v4.comparison.case_builder import build_cases
from app.v4.comparison.multi_judge import (
    _judge_with_provider,
    _load_reverse_prompt,
    judge_with_panel,
)
from app.v4.comparison.report_generator import generate_report, generate_consensus_reverse_report
from app.v4.comparison.re_review import re_review_judgments
from app.v4.comparison.review_agent import review_judgments, _build_summary
from app.v4.comparison.semantic_judge import judge_cases
from app.v4.config import DEEPSEEK_MODEL, JUDGE_PROVIDERS
from app.v4.degradation import DegradationConfig, DegradationContext
from app.v4.degradation.fallback import classify_exception, make_error_judgment
from app.v4.doc_generators.excel_generator import generate_eoicd_excel
from app.v4.doc_generators.word_generator import generate_consistency_report
from app.v4.doc_generators.consensus_word_generator import generate_consensus_report as gen_consensus_word
from app.job_manager import Job, JobStatus
from app.v4.matching.hlr_classifier import enrich_all_labels
from app.v4.matching.hlr_labeler import label_hlrs
from app.v4.matching.reverse_case_builder import build_reverse_cases
from app.v4.matching.reverse_matcher import match_reverse
from app.v4.matching.signal_profiler import build_profiles, build_blocks, ICDBlock
from app.v4.matching.entry_filter import should_keep
from app.v4.models import (
    ConsensusOutput,
    EoICDOutput,
    HLROutput,
    HLRLabelOutput,
    JudgmentOutput,
    MatchOutput,
    MultiJudgeOutput,
    PipelineResult,
    ReverseJudgmentOutput,
    ReverseMatchOutput,
    ConsensusOutput,
)
from app.v4.parsers.eoicd_excel_parser import EoICDExcelParser
from app.v4.parsers.hlr_word_parser import HLRWordParser
from app.v4.traceability import build_trace_index, name_to_block_key


def _parse_eoicd(
    publisher_path: Path | None,
    subscriber_path: Path | None,
    output_path: Path,
) -> EoICDOutput:
    """Parse Publisher and/or Subscriber Excel files into merged JSON output."""
    if not publisher_path and not subscriber_path:
        raise ValueError("at least one of publisher or subscriber is required")

    paths_desc = []
    if publisher_path:
        paths_desc.append(f"Publisher={publisher_path.name}")
    if subscriber_path:
        paths_desc.append(f"Subscriber={subscriber_path.name}")
    print(f"Parsing EoICD ({', '.join(paths_desc)})")

    parser = EoICDExcelParser(
        publisher_path=publisher_path,
        subscriber_path=subscriber_path,
    )
    result: EoICDOutput = parser.parse()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        result.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"  Output: {output_path}")
    print(f"  Generated (before any dedup): {result.total_generated}")
    print(f"  After per-sheet dedup: {result.total_raw}")
    print(f"  After global dedup: {result.total_after_dedup}")
    print(f"  Duplicates removed: {result.duplicates_removed}")
    return result


def _parse_hlr(input_path: Path, output_path: Path, system_config: dict) -> HLROutput:
    """Parse the HLR Word document."""
    print(f"Parsing HLR: {input_path} (system: {system_config['name']})")
    parser = HLRWordParser(input_path, system_config)
    result: HLROutput = parser.parse()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        result.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"  Output: {output_path}")
    print(f"  Requirements: {result.total_count}")
    print(f"  Glossary entries: {len(result.glossary)}")
    return result


def run_forward_pipeline(
    publisher: Path | None,
    subscriber: Path | None,
    hlr: Path,
    output_dir: Path,
    top_k: int,
    limit: int,
    job: Job,
) -> PipelineResult:
    """Run forward pipeline: parse → label → match → judge → report."""
    job.update(JobStatus.RUNNING, "Step 1/4: Parsing input files")

    # Step 1: Parse
    print("=" * 50)
    print("Step 1/4: Parsing input files")
    print("=" * 50)
    eoicd_out = None
    if publisher or subscriber:
        eoicd_out = _parse_eoicd(
            publisher, subscriber,
            output_dir / "eoicd_requirements.json",
        )
    hlr_out = _parse_hlr(hlr, output_dir / "hlr_requirements.json")

    if not eoicd_out or not hlr_out:
        raise RuntimeError("Need both EoICD and HLR data for analysis")

    # Step 1.5: HLR Labeling
    print()
    print("=" * 50)
    print("Step 1.5/4: HLR AI labeling")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 1.5/4: HLR AI labeling")
    labels_cache = output_dir / "hlr_labels.json"
    hlr_labels = label_hlrs(hlr_out.requirements, cache_path=labels_cache)
    print(f"  HLRs labeled: {len(hlr_labels)}")

    # Step 2: Match
    print()
    print("=" * 50)
    print(f"Step 2/4: Candidate matching (top_k={top_k}, limit={limit or 'all'})")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 2/4: Candidate matching")
    cases = build_cases(
        eoicd_out.requirements,
        hlr_out.requirements,
        top_k=top_k,
        limit=limit,
        hlr_labels=hlr_labels,
        enriched_output_path=output_dir / "enriched_queries.json",
        profiles_output_path=output_dir / "profiles.json",
    )
    match_out = MatchOutput(total_cases=len(cases), top_k=top_k, cases=cases)
    (output_dir / "matched_cases.json").write_text(
        match_out.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Matched cases: {len(cases)}")

    # Step 3: Judge
    print()
    print("=" * 50)
    print(f"Step 3/4: AI judging ({len(cases)} cases, model={DEEPSEEK_MODEL})")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 3/4: AI judging")
    results = judge_cases(cases)
    judge_out = JudgmentOutput(total_cases=len(results), results=results)
    (output_dir / "judgment_results.json").write_text(
        judge_out.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Step 4: Report
    print()
    print("=" * 50)
    print("Step 4/4: Generating report")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 4/4: Generating report")
    report = generate_report(results)
    report_path = output_dir / "difference_report.json"
    report_path.write_text(
        report.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Total: {report.total_cases}")
    print(f"  Stats: {report.statistics}")
    print(f"  Differences: {len(report.differences)}")
    print()
    print("Pipeline complete.")

    job.update(JobStatus.COMPLETED, "Forward pipeline complete")
    return PipelineResult(
        parsed_count=len(eoicd_out.requirements),
        match_count=len(cases),
        judged_count=len(results),
        report_path=str(report_path),
    )


def _count_match_types(results: list) -> dict[str, int]:
    """Count results by match_type for logging."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r.match_type] = counts.get(r.match_type, 0) + 1
    return counts


def _merge_reverse_match_outputs(
    result_a: ReverseMatchOutput,
    result_b: ReverseMatchOutput,
    trace_stats: dict,
) -> ReverseMatchOutput:
    """Merge traceability-filtered group (A) and fallback group (B) results."""
    merged_results = result_a.results + result_b.results

    all_block_keys_a = set(result_a.eoicd_unmatched_profile_keys or [])
    all_block_keys_b = set(result_b.eoicd_unmatched_profile_keys or [])
    unmatched_keys = sorted(all_block_keys_a | all_block_keys_b)

    total_blocks = len(all_block_keys_a | all_block_keys_b | {
        bk for r in merged_results for bk in r.matched_profile_keys
    })

    stats = {
        "hlr_total": len(merged_results),
        "hlr_已匹配": sum(1 for r in merged_results if r.match_type == "已匹配"),
        "hlr_待确定": sum(1 for r in merged_results if r.match_type == "待确定"),
        "hlr_无匹配": sum(1 for r in merged_results if r.match_type == "无匹配"),
        "eoicd_blocks_total": total_blocks if total_blocks > 0 else result_b.stats.get("eoicd_blocks_total", 0),
        "eoicd_blocks_matched": sum(1 for r in merged_results if r.matched_profile_keys),
        "eoicd_blocks_unmatched": len(unmatched_keys),
    }
    for tk, tv in trace_stats.items():
        if isinstance(tv, int):
            stats[f"trace_{tk}"] = tv

    return ReverseMatchOutput(
        total_hlr=len(merged_results),
        total_eoicd_profiles=total_blocks if total_blocks > 0 else result_b.total_eoicd_profiles,
        stats=stats,
        results=merged_results,
        eoicd_unmatched_profile_keys=unmatched_keys,
    )


def _match_reverse_with_trace(
    hlr_requirements: list,
    hlr_labels: dict,
    eoicd_requirements: list,
    trace_dir: Path,
    trace_config: dict | None = None,
) -> ReverseMatchOutput:
    """Run reverse matching with traceability-based pre-filtering.

    Splits HLRs into:
      - Group A (has trace data): match against filtered EoICD subset
      - Group B (no trace data): fallback to full EoICD matching
    """
    trace_index = build_trace_index(trace_dir, trace_config)
    print(f"  Traced HLRs: {trace_index.total_hlrs_traced}")
    print(f"  ERDs: {trace_index.total_erds}")
    print(f"  ICD FullNames: {trace_index.total_icd_fullnames}")
    print(f"  Mapped to blocks: {trace_index.icd_mapped_to_blocks}")
    print(f"  Unmapped: {len(trace_index.icd_unmapped)}")

    group_a_hlrs: list = []
    group_b_hlrs: list = []
    all_traced_block_keys: set[str] = set()

    for hlr in hlr_requirements:
        traced_blocks = trace_index.hlr_to_blocks.get(hlr.requirement_id)
        if traced_blocks:
            group_a_hlrs.append(hlr)
            all_traced_block_keys.update(traced_blocks)
        else:
            group_b_hlrs.append(hlr)

    print(f"  Group A (traceable): {len(group_a_hlrs)} HLRs")
    print(f"  Group B (fallback):  {len(group_b_hlrs)} HLRs")
    print(f"  Union traced block_keys: {len(all_traced_block_keys)}")

    group_a_ids = {h.requirement_id for h in group_a_hlrs}
    group_b_ids = {h.requirement_id for h in group_b_hlrs}

    # Group A: filtered EoICD
    if group_a_hlrs:
        filtered_eoicd = []
        for req in eoicd_requirements:
            bk = name_to_block_key(req.signal_name)
            if bk in all_traced_block_keys:
                filtered_eoicd.append(req)
        print(f"  Filtered EoICD: {len(filtered_eoicd)} / {len(eoicd_requirements)} entries")
        result_a = match_reverse(
            group_a_hlrs,
            {k: v for k, v in hlr_labels.items() if k in group_a_ids},
            filtered_eoicd,
        )
        print(f"  Group A stats: {result_a.stats}")

        # Per-HLR fallback: if prefilter matching produced "无匹配" for
        # any HLR, retry against the full EoICD set.  The traceability
        # table may be incomplete or its block_keys may be incompatible
        # with the HLR's matching path (e.g. label mismatch).
        fallback_ids = {
            r.hlr_id for r in result_a.results if r.match_type == "无匹配"
        }
        if fallback_ids:
            fallback_hlrs = [h for h in group_a_hlrs if h.requirement_id in fallback_ids]
            print(f"  Prefilter fallback: {len(fallback_hlrs)} HLR(s) retrying on full EoICD")
            result_fb = match_reverse(
                fallback_hlrs,
                {k: v for k, v in hlr_labels.items() if k in fallback_ids},
                eoicd_requirements,
            )
            # Replace the failed results with fallback results
            fb_map = {r.hlr_id: r for r in result_fb.results}
            result_a.results = [
                fb_map.get(r.hlr_id, r) for r in result_a.results
            ]
            print(f"  After fallback — Group A stats: {_count_match_types(result_a.results)}")
    else:
        result_a = ReverseMatchOutput(
            total_hlr=0, total_eoicd_profiles=0,
            stats={}, results=[], eoicd_unmatched_profile_keys=[],
        )

    # Group B: full EoICD (fallback)
    if group_b_hlrs:
        result_b = match_reverse(
            group_b_hlrs,
            {k: v for k, v in hlr_labels.items() if k in group_b_ids},
            eoicd_requirements,
        )
        print(f"  Group B stats: {result_b.stats}")
    else:
        result_b = ReverseMatchOutput(
            total_hlr=0, total_eoicd_profiles=0,
            stats={}, results=[], eoicd_unmatched_profile_keys=[],
        )

    trace_stats = {
        "hlrs_with_trace": len(group_a_hlrs),
        "hlrs_without_trace": len(group_b_hlrs),
        "traced_icd_fullnames_total": trace_index.total_icd_fullnames,
        "traced_icd_fullnames_mapped": trace_index.icd_mapped_to_blocks,
        "traced_icd_fullnames_unmapped": len(trace_index.icd_unmapped),
        "avg_blocks_per_hlr_after_trace": (
            len(all_traced_block_keys) // max(len(group_a_hlrs), 1)
        ),
    }

    return _merge_reverse_match_outputs(result_a, result_b, trace_stats)


# ── Degradation helpers ────────────────────────────────────


async def _judge_case_with_timeout(
    case,
    providers: list[str],
    system_prompt: str,
    ceiling: float,
    extra_wait: float,
) -> tuple[dict[str, dict], bool]:
    """Run all providers in parallel with fixed extra-wait timeout.

    Waits for providers one-by-one (FIRST_COMPLETED). Once 2 valid (non-error)
    completions are collected, sets a fixed deadline for the remaining:

        deadline = start + t2 + extra_wait

    Before 2 valid samples, uses *ceiling* as the fallback timeout.
    Fast errors (connection refused, etc.) are excluded from valid_times
    so they do not pollute the formula.
    """
    start = time.monotonic()
    tasks = {
        asyncio.create_task(_judge_with_provider(case, p, system_prompt)): p
        for p in providers
    }

    pending = set(tasks.keys())
    results: dict[str, dict] = {}
    valid_times: list[float] = []   # elapsed times of non-error completions
    had_timeout = False

    while pending:
        if len(valid_times) >= 2:
            extra = extra_wait
            deadline = start + valid_times[-1] + extra
        else:
            deadline = start + ceiling

        remaining = deadline - time.monotonic()

        if remaining <= 0:
            for t in pending:
                t.cancel()
                results[tasks[t]] = make_error_judgment(
                    tasks[t], "adaptive timeout", "TIMEOUT"
                )
            had_timeout = True
            break

        done, pending = await asyncio.wait(
            pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED,
        )

        for t in done:
            elapsed = time.monotonic() - start
            p = tasks[t]
            if t.exception():
                exc = t.exception()
                results[p] = make_error_judgment(
                    p, str(exc), classify_exception(exc) if exc else "UNKNOWN"
                )
            else:
                result = t.result()
                results[p] = result
                if result.get("coverage_status") != "error":
                    valid_times.append(elapsed)

    return results, had_timeout


def _is_failure(judgment: dict) -> bool:
    """Check if a judgment dict represents a failure (error or very low confidence)."""
    return judgment.get("coverage_status") == "error"


def _judge_with_degradation(
    cases: list,
    providers: list[str],
    ctx: DegradationContext,
) -> MultiJudgeOutput:
    """Judge cases with provider health tracking, timeout, and circuit breaking.

    Replaces direct judge_with_panel() call. Handles:
    - Skipping providers marked unhealthy
    - Case-level timeout via asyncio.wait
    - Recording per-provider failures for circuit breaker
    """
    from app.v4.models import MultiJudgeOutput, MultiJudgeResult

    system_prompt = _load_reverse_prompt()
    total = len(cases)
    results: list[MultiJudgeResult] = []

    for idx, case in enumerate(cases):
        healthy = ctx.filter_healthy(providers)

        # Pre-fill skipped providers
        skipped = {
            p: make_error_judgment(p, "provider unhealthy", "SKIPPED")
            for p in providers if p not in healthy
        }

        # Parallel judge with adaptive timeout
        gathered, had_timeout = asyncio.run(
            _judge_case_with_timeout(
                case, healthy, system_prompt,
                ceiling=ctx.config.case_total_timeout,
                extra_wait=ctx.config.extra_wait,
            )
        )
        if had_timeout:
            ctx.record_case_timeout()

        # Update health per provider from actual results
        for provider, judgment in gathered.items():
            if _is_failure(judgment):
                ctx.record_failure(provider)
            else:
                ctx.record_success(provider)

        case_judgments = {**skipped, **gathered}
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


def _count_surviving_providers(judgments: dict[str, dict]) -> int:
    """Count how many providers returned non-error judgments for a case."""
    return sum(
        1 for j in judgments.values()
        if j.get("coverage_status") != "error"
    )


def _apply_degradation_review(
    consensus_out: ConsensusOutput,
    ctx: DegradationContext,
) -> ConsensusOutput:
    """Apply star cap and agreement override based on surviving provider count.

    Does NOT modify review_judgments(). Operates as post-processing on
    the already-computed ConsensusOutput.
    """
    for result in consensus_out.results:
        surviving = _count_surviving_providers(result.model_results)
        if surviving == 0:
            # 所有 provider 均失败：共识输入无有效裁判，共识 LLM 在纯 error 输入上
            # 可能幻觉出高星级，强制最低置信度并转入人工审核
            if result.star_rating > ctx.config.zero_provider_star_cap:
                result.star_rating = ctx.config.zero_provider_star_cap
                ctx.record_review_star_capped()
            result.agreement_level = ctx.config.zero_provider_agreement
            result.final_coverage_status = "待确认"
            print(
                f"  [degradation] review downgraded: {result.case_id} "
                f"0 surviving → star_cap={ctx.config.zero_provider_star_cap}, "
                f"agreement={ctx.config.zero_provider_agreement}, status=待确认",
                file=sys.stderr,
            )
        elif surviving == 1:
            if result.star_rating > ctx.config.single_provider_star_cap:
                result.star_rating = ctx.config.single_provider_star_cap
                ctx.record_review_star_capped()
            result.agreement_level = ctx.config.single_provider_agreement
            print(
                f"  [degradation] review downgraded: {result.case_id} "
                f"1 surviving → star_cap={ctx.config.single_provider_star_cap}, "
                f"agreement={ctx.config.single_provider_agreement}",
                file=sys.stderr,
            )
        elif surviving == 2:
            if result.star_rating > ctx.config.two_provider_star_cap:
                result.star_rating = ctx.config.two_provider_star_cap
                ctx.record_review_star_capped()

        # 共识多数投票可能含 error 票（如 2 error + 1 真实），此时
        # final_coverage_status="error" 不是可展示状态，统一转待确认
        if result.final_coverage_status == "error":
            result.final_coverage_status = "待确认"
            print(
                f"  [degradation] review downgraded: {result.case_id} "
                f"status=error → 待确认（多数票含 error 票）",
                file=sys.stderr,
            )

    return consensus_out


def _extract_frozen_providers(multi_out: MultiJudgeOutput, all_providers: list[str]) -> list[str]:
    """从 multi_out 中提取本轮有效的 provider 集合。

    一个 provider 被纳入集合的条件：在 multi_out 的任意 case 中，
    其 judgment 的 coverage_status 不是 "error"。

    即：该 provider 在本轮的至少一个 case 上成功返回过有效结果（OR 逻辑）。
    """
    successful_providers = set()
    for mr in multi_out.results:
        for p, j in (mr.judgments or {}).items():
            if j.get("coverage_status") != "error":
                successful_providers.add(p)
    return [p for p in all_providers if p in successful_providers]


def run_reverse_pipeline(
    hlr: Path,
    eoicd_json: Path | None,
    publisher: Path | None,
    subscriber: Path | None,
    output_dir: Path,
    job: Job,
    trace_dir: Path | None = None,
    system_type: str = "hvac",
) -> PipelineResult:
    """Run reverse pipeline: parse → label → match → judge → report.

    If trace_dir is provided, enables traceability-based pre-filtering
    to narrow the EoICD search space before reverse matching.
    system_type selects the HLR system config (default "hvac").
    """
    if not (eoicd_json or publisher or subscriber):
        raise ValueError("need eoicd (parsed JSON) or publisher/subscriber (Excel)")

    from app.v4.config import get_hlr_system_config
    system_config = get_hlr_system_config(system_type)

    # Step 1: Parse
    print("=" * 50)
    print("Step 1/6: Parsing input files")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 1/6: Parsing input files")

    if eoicd_json and eoicd_json.exists():
        print(f"  [skip] Using cached EoICD JSON: {eoicd_json}")
        eoicd_data = json.loads(eoicd_json.read_text(encoding="utf-8"))
        eoicd_out = EoICDOutput(**eoicd_data)
    else:
        eoicd_out = _parse_eoicd(
            publisher, subscriber,
            output_dir / "eoicd_requirements.json",
        )

    if hlr.suffix == ".json":
        print(f"  [skip] Using cached HLR JSON: {hlr}")
        hlr_data = json.loads(hlr.read_text(encoding="utf-8"))
        hlr_out = HLROutput(**hlr_data)
    else:
        hlr_out = _parse_hlr(
            hlr,
            output_dir / "hlr_requirements.json",
            system_config,
        )

    # Step 1: EoICD itemization Excel
    eoicd_json_path = output_dir / "eoicd_requirements.json"
    generate_eoicd_excel(eoicd_json_path, output_dir / "EoICD条目化清单.xlsx")

    # Step 2: HLR labeling
    print()
    print("=" * 50)
    print("Step 2/6: HLR AI labeling")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 2/6: HLR AI labeling")
    labels_cache = output_dir / "hlr_labels.json"
    hlr_labels = label_hlrs(hlr_out.requirements, cache_path=labels_cache)
    hlr_labels = enrich_all_labels(hlr_out.requirements, hlr_labels)
    print(f"  HLRs labeled: {len(hlr_labels)}")

    # Step 3: Reverse match
    print()
    print("=" * 50)
    if trace_dir:
        print(f"Step 3/6: Traceability-filtered reverse matching ({len(hlr_out.requirements)} HLR → {len(eoicd_out.requirements)} EoICD)")
        print("=" * 50)
        job.update(JobStatus.RUNNING, "Step 3/6: Traceability-filtered reverse matching")
        from app.v4.config import get_traceability_config
        trace_config = get_traceability_config(system_type)
        match_result = _match_reverse_with_trace(
            hlr_out.requirements,
            hlr_labels,
            eoicd_out.requirements,
            trace_dir,
            trace_config,
        )
    else:
        print(f"Step 3/6: Reverse matching ({len(hlr_out.requirements)} HLR → {len(eoicd_out.requirements)} EoICD)")
        print("=" * 50)
        job.update(JobStatus.RUNNING, "Step 3/6: Reverse matching")
        match_result = match_reverse(
            hlr_out.requirements,
            hlr_labels,
            eoicd_out.requirements,
        )
    match_path = output_dir / "reverse_matches.json"
    match_path.write_text(
        match_result.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {match_path}")
    print(f"  Stats: {match_result.stats}")

    # Build block index for case building
    eoicd_kept = [req for req in eoicd_out.requirements if should_keep(req)]
    eoicd_profiles = build_profiles(eoicd_kept)
    blocks = build_blocks(eoicd_profiles)
    block_index: dict[str, ICDBlock] = {b.block_key: b for b in blocks}

    cases = build_reverse_cases(match_result, block_index)

    # Step 4: Multi-agent judging (with degradation)
    print()
    print("=" * 50)
    print(f"Step 4/6: Multi-agent judging ({len(cases)} cases, providers={JUDGE_PROVIDERS})")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 4/6: Multi-agent judging")
    ctx = DegradationContext(config=DegradationConfig.from_env())
    multi_out = _judge_with_degradation(cases, JUDGE_PROVIDERS, ctx)
    multi_path = output_dir / "multi_judge_results.json"
    multi_path.write_text(
        multi_out.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {multi_path}")

    # Freeze provider set after Step 4 — used for all subsequent steps
    frozen_providers = _extract_frozen_providers(multi_out, JUDGE_PROVIDERS)
    print(f"  Frozen providers: {frozen_providers}")

    # Step 5: Review agent consensus (first pass — identifies one-star cases for re-review)
    print()
    print("=" * 50)
    print(f"Step 5/6: Review agent consensus ({len(multi_out.results)} cases)")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 5/6: Review agent consensus")
    consensus_out = review_judgments(multi_out.results)
    consensus_out = _apply_degradation_review(consensus_out, ctx)
    consensus_path = output_dir / "consensus_results.json"
    consensus_data = json.loads(consensus_out.model_dump_json(indent=2, ensure_ascii=False))
    consensus_data["degradation"] = ctx.to_summary()
    consensus_path.write_text(
        json.dumps(consensus_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {consensus_path}")
    print(f"  Summary: {consensus_out.summary}")
    print(f"  Degradation: {consensus_data['degradation']}")

    # Step 5.5: Re-review one-star cases (AFTER first consensus to know which are one-star)
    print()
    print("=" * 50)
    print("Step 5.5/6: Re-review one-star cases")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 5.5/6: Re-review one-star cases")
    multi_out, re_reviewed_ids = re_review_judgments(
        multi_out=multi_out,
        consensus_out=consensus_out,  # pass in-memory consensus_out for one-star detection
        cases=cases,
        output_dir=output_dir,
        providers=frozen_providers,
    )

    # Step 5.6: Re-run consensus only for re-reviewed cases
    print()
    print("=" * 50)
    print("Step 5.6/6: Re-run consensus after re-review")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 5.6/6: Re-run consensus after re-review")

    if re_reviewed_ids:
        # Partial update: re-run consensus only for re-reviewed cases
        consensus_map = {r.case_id: r for r in consensus_out.results}
        for case_id in sorted(re_reviewed_ids):
            mr = next((m for m in multi_out.results if m.case_id == case_id), None)
            if mr is None:
                continue
            new_consensus = review_judgments([mr])
            if new_consensus.results:
                consensus_map[case_id] = new_consensus.results[0]
        all_results = list(consensus_map.values())
        new_summary = _build_summary(all_results)
        consensus_out = ConsensusOutput(
            total_cases=len(consensus_out.results),
            summary=new_summary,
            results=all_results,
        )
        # 复查改变了部分 case 的 judgments，需重新应用降级星封顶
        _apply_degradation_review(consensus_out, ctx)
        consensus_out.summary = _build_summary(consensus_out.results)
        consensus_path = output_dir / "consensus_results.json"
        consensus_data = json.loads(consensus_out.model_dump_json(indent=2, ensure_ascii=False))
        consensus_data["degradation"] = ctx.to_summary()
        consensus_path.write_text(
            json.dumps(consensus_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  Updated {len(re_reviewed_ids)} case(s): {sorted(re_reviewed_ids)}")
        print(f"  Summary: {consensus_out.summary}")
    else:
        print("  No cases re-reviewed, skipping consensus update")
        print(f"  Summary: {consensus_out.summary}")

    # Step 6: Report (uses re-reviewed multi_judge + re-computed consensus)
    print()
    print("=" * 50)
    print("Step 6/6: Generating report")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 6/6: Generating report")
    report = generate_consensus_reverse_report(
        consensus_out,
        match_output=match_result,
    )
    report_path = output_dir / "reverse_report.json"
    report_path.write_text(
        report.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"  Report: {report_path}")
    print(f"  Total: {report.total_cases}")
    print(f"  Summary: {report.summary}")

    # ── Word reports ──
    model_display = {"deepseek": "DeepSeek", "minimax": "MiniMax", "qwen": "Qwen"}
    for model in ("deepseek", "minimax", "qwen"):
        display = model_display[model]
        print(f"  → {display} 单模型报告")
        generate_consistency_report(
            report_path,
            output_dir / f"EoICD与SWHLR单模型差异分析报告_{display}.docx",
            model=model,
        )

    print("  → 多模型报告")
    gen_consensus_word(
        consensus_path,
        output_dir / "reverse_matches.json",
        output_dir / "EoICD与SWHLR多模型差异分析报告.docx",
    )

    print()
    print("Reverse pipeline complete.")

    job.update(JobStatus.COMPLETED, "Reverse pipeline complete")
    return PipelineResult(
        parsed_count=len(hlr_out.requirements),
        match_count=len(match_result.results),
        judged_count=len(consensus_out.results),
        report_path=str(report_path),
    )
