# -*- coding: utf-8 -*-
"""Pipeline orchestration: forward and reverse analysis workflows."""

from __future__ import annotations

import sys
from pathlib import Path

from app.comparison.case_builder import build_cases
from app.comparison.multi_judge import judge_with_panel
from app.comparison.report_generator import generate_report, generate_consensus_reverse_report
from app.comparison.review_agent import review_judgments
from app.comparison.semantic_judge import judge_cases
from app.config import DEEPSEEK_MODEL, JUDGE_PROVIDERS
from app.doc_generators.excel_generator import generate_eoicd_excel
from app.doc_generators.word_generator import generate_consistency_report
from app.doc_generators.consensus_word_generator import generate_consensus_report as gen_consensus_word
from app.job_manager import Job, JobStatus
from app.matching.hlr_classifier import enrich_all_labels
from app.matching.hlr_labeler import label_hlrs
from app.matching.reverse_case_builder import build_reverse_cases
from app.matching.reverse_matcher import match_reverse
from app.matching.signal_profiler import build_profiles, build_blocks, ICDBlock
from app.matching.entry_filter import should_keep
from app.models import (
    EoICDOutput,
    HLROutput,
    HLRLabelOutput,
    JudgmentOutput,
    MatchOutput,
    PipelineResult,
    ReverseJudgmentOutput,
    ReverseMatchOutput,
)
from app.parsers.eoicd_excel_parser import EoICDExcelParser
from app.parsers.hlr_word_parser import HLRWordParser
from app.traceability import build_trace_index, name_to_block_key


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


def _parse_hlr(input_path: Path, output_path: Path) -> HLROutput:
    """Parse the HLR Word document."""
    print(f"Parsing HLR: {input_path}")
    parser = HLRWordParser(input_path)
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
) -> ReverseMatchOutput:
    """Run reverse matching with traceability-based pre-filtering.

    Splits HLRs into:
      - Group A (has trace data): match against filtered EoICD subset
      - Group B (no trace data): fallback to full EoICD matching
    """
    trace_index = build_trace_index(trace_dir)
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


def run_reverse_pipeline(
    hlr: Path,
    eoicd_json: Path | None,
    publisher: Path | None,
    subscriber: Path | None,
    output_dir: Path,
    job: Job,
    trace_dir: Path | None = None,
) -> PipelineResult:
    """Run reverse pipeline: parse → label → match → judge → report.

    If trace_dir is provided, enables traceability-based pre-filtering
    to narrow the EoICD search space before reverse matching.
    """
    if not (eoicd_json or publisher or subscriber):
        raise ValueError("need eoicd (parsed JSON) or publisher/subscriber (Excel)")

    # Step 1: Parse
    print("=" * 50)
    print("Step 1/3: Parsing input files")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 1/3: Parsing input files")

    if eoicd_json and eoicd_json.exists():
        import json
        print(f"  [skip] Using cached EoICD JSON: {eoicd_json}")
        eoicd_data = json.loads(eoicd_json.read_text(encoding="utf-8"))
        eoicd_out = EoICDOutput(**eoicd_data)
    else:
        eoicd_out = _parse_eoicd(
            publisher, subscriber,
            output_dir / "eoicd_requirements.json",
        )

    if hlr.suffix == ".json":
        import json
        print(f"  [skip] Using cached HLR JSON: {hlr}")
        hlr_data = json.loads(hlr.read_text(encoding="utf-8"))
        hlr_out = HLROutput(**hlr_data)
    else:
        hlr_out = _parse_hlr(hlr, output_dir / "hlr_requirements.json")

    # Step 1: EoICD itemization Excel
    eoicd_json_path = output_dir / "eoicd_requirements.json"
    generate_eoicd_excel(eoicd_json_path, output_dir / "EoICD条目化清单.xlsx")

    # Step 1.5: HLR labeling
    print()
    print("=" * 50)
    print("Step 1.5/3: HLR AI labeling")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 1.5/3: HLR AI labeling")
    labels_cache = output_dir / "hlr_labels.json"
    hlr_labels = label_hlrs(hlr_out.requirements, cache_path=labels_cache)
    hlr_labels = enrich_all_labels(hlr_out.requirements, hlr_labels)
    print(f"  HLRs labeled: {len(hlr_labels)}")

    # Step 2: Reverse match
    print()
    print("=" * 50)
    if trace_dir:
        print(f"Step 2/4: Traceability-filtered reverse matching ({len(hlr_out.requirements)} HLR → {len(eoicd_out.requirements)} EoICD)")
        print("=" * 50)
        job.update(JobStatus.RUNNING, "Step 2/4: Traceability-filtered reverse matching")
        match_result = _match_reverse_with_trace(
            hlr_out.requirements,
            hlr_labels,
            eoicd_out.requirements,
            trace_dir,
        )
    else:
        print(f"Step 2/3: Reverse matching ({len(hlr_out.requirements)} HLR → {len(eoicd_out.requirements)} EoICD)")
        print("=" * 50)
        job.update(JobStatus.RUNNING, "Step 2/3: Reverse matching")
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

    # Step 3: Multi-agent judging
    print()
    print("=" * 50)
    print(f"Step 3/5: Multi-agent judging ({len(cases)} cases, providers={JUDGE_PROVIDERS})")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 3/5: Multi-agent judging")
    multi_out = judge_with_panel(cases, providers=JUDGE_PROVIDERS)
    multi_path = output_dir / "multi_judge_results.json"
    multi_path.write_text(
        multi_out.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {multi_path}")

    # Step 4: Review agent consensus
    print()
    print("=" * 50)
    print(f"Step 4/5: Review agent consensus ({len(multi_out.results)} cases)")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 4/5: Review agent consensus")
    consensus_out = review_judgments(multi_out.results)
    consensus_path = output_dir / "consensus_results.json"
    consensus_path.write_text(
        consensus_out.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {consensus_path}")
    print(f"  Summary: {consensus_out.summary}")

    # Step 5: Report
    print()
    print("=" * 50)
    print("Step 5/5: Generating report")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 5/5: Generating report")
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
            output_dir / f"EoICD与HLR一致性分析报告_{display}.docx",
            model=model,
        )

    print("  → 多模型共识报告")
    gen_consensus_word(
        consensus_path,
        output_dir / "reverse_matches.json",
        output_dir / "EoICD与HLR多模型共识分析报告.docx",
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
