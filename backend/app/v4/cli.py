# -*- coding: utf-8 -*-
"""CLI entry point for EoICD pre-processing and consistency analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.v4.config import (
    DEFAULT_LIMIT,
    DEFAULT_TOP_K,
)
from app.v4.comparison.case_builder import build_cases
from app.v4.comparison.report_generator import generate_report, generate_reverse_report
from app.v4.comparison.semantic_judge import judge_cases, judge_reverse_cases
from app.v4.matching.hlr_classifier import enrich_all_labels
from app.v4.matching.hlr_labeler import label_hlrs
from app.v4.matching.reverse_case_builder import build_reverse_cases
from app.v4.matching.reverse_matcher import match_reverse
from app.v4.matching.signal_profiler import build_profiles, build_blocks, ICDBlock
from app.v4.matching.entry_filter import should_keep
from app.v4.models import (
    EoICDOutput,
    HLROutput,
    HLRLabelOutput,
    JudgmentOutput,
    MatchOutput,
    ReverseCase,
    ReverseJudgmentOutput,
    ReverseMatchOutput,
)
from app.v4.parsers.eoicd_excel_parser import EoICDExcelParser
from app.v4.parsers.hlr_word_parser import HLRWordParser
from app.v4.doc_generators.excel_generator import generate_eoicd_excel
from app.v4.doc_generators.word_generator import generate_consistency_report
from app.v4.doc_generators.consensus_word_generator import generate_consensus_report as generate_consensus_word


def _parse_eoicd(
    publisher_path: Path | None,
    subscriber_path: Path | None,
    output_path: Path,
) -> EoICDOutput:
    """Parse Publisher and/or Subscriber Excel files into merged JSON output."""
    if not publisher_path and not subscriber_path:
        print("Error: at least one of --publisher or --subscriber is required")
        sys.exit(1)

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


# ——— New pipeline commands ———


def _cmd_match(args: argparse.Namespace) -> None:
    """match: read parsed JSON, run 3-way matching, output matched cases."""
    print("Loading parsed data...")
    eoicd_data = json.loads(
        Path(args.eoicd).read_text(encoding="utf-8")
    )
    hlr_data = json.loads(
        Path(args.hlr).read_text(encoding="utf-8")
    )
    eoicd_out = EoICDOutput(**eoicd_data)
    hlr_out = HLROutput(**hlr_data)

    top_k = args.top_k
    limit = args.limit
    print(
        f"Matching: {len(eoicd_out.requirements)} EoICD items "
        f"vs {len(hlr_out.requirements)} HLR items "
        f"(top_k={top_k}, limit={limit or 'all'})"
    )

    out_path = Path(args.output)
    enriched_path = out_path.parent / "enriched_queries.json"
    profiles_path = out_path.parent / "profiles.json"
    labels_cache = out_path.parent / "hlr_labels.json"

    cases = build_cases(
        eoicd_out.requirements,
        hlr_out.requirements,
        top_k=top_k,
        limit=limit,
        labels_cache_path=labels_cache if labels_cache.exists() else None,
        enriched_output_path=enriched_path,
        profiles_output_path=profiles_path,
    )

    output = MatchOutput(
        total_cases=len(cases),
        top_k=top_k,
        cases=cases,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        output.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {out_path}")
    print(f"  Cases: {len(cases)}")


def _cmd_judge(args: argparse.Namespace) -> None:
    """judge: read matched cases, call AI, output judgment results."""
    print("Loading matched cases...")
    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    match_out = MatchOutput(**data)

    print(f"Judging: {len(match_out.cases)} cases")
    results = judge_cases(match_out.cases)

    output = JudgmentOutput(
        total_cases=len(results),
        results=results,
    )
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        output.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {out_path}")


def _cmd_report(args: argparse.Namespace) -> None:
    """report: read judgment results, generate difference report."""
    print("Loading judgment results...")
    data = json.loads(Path(args.judgments).read_text(encoding="utf-8"))
    judge_out = JudgmentOutput(**data)

    report = generate_report(judge_out.results)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        report.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {out_path}")
    print(f"  Total: {report.total_cases}")
    print(f"  Stats: {report.statistics}")
    print(f"  Differences: {len(report.differences)}")


def _cmd_analyze(args: argparse.Namespace) -> None:
    """analyze: full pipeline — parse → match → judge → report."""
    from app.v4.pipeline import run_forward_pipeline
    from app.job_manager import job_manager

    publisher = Path(args.publisher) if args.publisher else None
    subscriber = Path(args.subscriber) if args.subscriber else None
    hlr_path = Path(args.hlr) if args.hlr else None
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not hlr_path:
        print("Error: --hlr is required for analyze")
        sys.exit(1)
    if not publisher and not subscriber:
        print("Error: at least one of --publisher or --subscriber is required")
        sys.exit(1)

    job = job_manager.create_job()
    result = run_forward_pipeline(
        publisher=publisher,
        subscriber=subscriber,
        hlr=hlr_path,
        output_dir=output_dir,
        top_k=args.top_k,
        limit=args.limit,
        job=job,
    )
    if result.errors:
        for e in result.errors:
            print(f"  Error: {e}", file=sys.stderr)


def _cmd_all(args: argparse.Namespace) -> None:
    """Run all parsers in batch mode."""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.publisher or args.subscriber:
        pub = Path(args.publisher) if args.publisher else None
        sub = Path(args.subscriber) if args.subscriber else None
        _parse_eoicd(pub, sub, output_dir / "eoicd_requirements.json")
        print()
    if args.hlr:
        _parse_hlr(Path(args.hlr), output_dir / "hlr_requirements.json")


def _cmd_label_hlr(args: argparse.Namespace) -> None:
    """label-hlr: AI pre-label HLR requirements and save to JSON."""
    from pathlib import Path as _P
    from app.v4.profiles import ProfileRegistry

    reg = ProfileRegistry()
    reg.load_all(_P(__file__).resolve().parent / "profiles")
    profile = reg.get_or_raise(args.controller_profile)

    print("Loading HLR data...")
    hlr_data = json.loads(Path(args.hlr).read_text(encoding="utf-8"))
    hlr_out = HLROutput(**hlr_data)

    print(f"Labeling {len(hlr_out.requirements)} HLRs...")
    labels = label_hlrs(
        hlr_out.requirements,
        cache_path=None,  # force re-label when explicitly invoked
        profile=profile,
    )

    output = HLRLabelOutput(
        total_labeled=len(labels),
        labels=labels,
    )
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        output.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {out_path}")
    print(f"  Labeled: {len(labels)} HLRs")


def _cmd_reverse_match(args: argparse.Namespace) -> None:
    """reverse-match: HLR → EoICD reverse matching with 4-path classification."""
    from pathlib import Path as _P
    from app.v4.profiles import ProfileRegistry

    reg = ProfileRegistry()
    reg.load_all(_P(__file__).resolve().parent / "profiles")
    profile = reg.get_or_raise(args.controller_profile)

    print("Loading parsed data...")
    hlr_data = json.loads(Path(args.hlr).read_text(encoding="utf-8"))
    eoicd_data = json.loads(Path(args.eoicd).read_text(encoding="utf-8"))

    hlr_out = HLROutput(**hlr_data)
    eoicd_out = EoICDOutput(**eoicd_data)

    # Load or compute HLR labels
    labels_cache = Path(args.labels) if args.labels else None
    if labels_cache and labels_cache.exists():
        print(f"  [label] Loading cached labels from {labels_cache}")
        labels_data = json.loads(labels_cache.read_text(encoding="utf-8"))
        labels_out = HLRLabelOutput(**labels_data)
        hlr_labels = labels_out.labels
    else:
        print("  [label] No cache found, running AI labeling...")
        hlr_labels = label_hlrs(hlr_out.requirements, profile=profile)

    # Enrich with script-based classifier
    hlr_labels = enrich_all_labels(
        hlr_out.requirements,
        hlr_labels,
        keywords=profile.classifier_keywords,
    )

    print(
        f"Reverse matching: {len(hlr_out.requirements)} HLR items "
        f"→ {len(eoicd_out.requirements)} EoICD items"
    )

    trace_dir = Path(args.traceability_dir) if args.traceability_dir else None
    if trace_dir:
        from app.v4.pipeline import _match_reverse_with_trace
        if not trace_dir.exists():
            print(f"Error: --traceability-dir does not exist: {trace_dir}")
            sys.exit(1)
        result = _match_reverse_with_trace(
            hlr_out.requirements,
            hlr_labels,
            eoicd_out.requirements,
            trace_dir,
        )
    else:
        result = match_reverse(
            hlr_out.requirements,
            hlr_labels,
            eoicd_out.requirements,
        )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        result.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"  Output: {out_path}")
    print(f"  Stats: {result.stats}")


def _cmd_reverse_judge(args: argparse.Namespace) -> None:
    """reverse-judge: read reverse match results, call AI, output judgment results."""
    print("Loading reverse match results...")
    match_data = json.loads(Path(args.matches).read_text(encoding="utf-8"))
    match_out = ReverseMatchOutput(**match_data)

    # Rebuild EoICD profiles for matched profile details
    print("Loading EoICD data for profile context...")
    eoicd_data = json.loads(Path(args.eoicd).read_text(encoding="utf-8"))
    eoicd_out = EoICDOutput(**eoicd_data)
    eoicd_kept = [req for req in eoicd_out.requirements if should_keep(req)]
    eoicd_profiles = build_profiles(eoicd_kept)
    blocks = build_blocks(eoicd_profiles)
    block_index: dict[str, ICDBlock] = {b.block_key: b for b in blocks}

    # Build cases from match results
    cases = build_reverse_cases(match_out, block_index)
    print(f"  Reverse cases: {len(cases)} (matched HLRs with blocks)")

    if not cases:
        print("  No matched HLRs to judge — writing empty output.")
        output = ReverseJudgmentOutput(total_cases=0, results=[])
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            output.model_dump_json(indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return

    print(f"Judging: {len(cases)} cases")
    results = judge_reverse_cases(cases)

    output = ReverseJudgmentOutput(
        total_cases=len(results),
        results=results,
    )
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        output.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {out_path}")


def _cmd_reverse_report(args: argparse.Namespace) -> None:
    """reverse-report: read reverse judgment results, generate report."""
    print("Loading reverse judgment results...")
    data = json.loads(Path(args.judgments).read_text(encoding="utf-8"))
    judge_out = ReverseJudgmentOutput(**data)

    match_out = None
    if args.matches:
        match_data = json.loads(Path(args.matches).read_text(encoding="utf-8"))
        match_out = ReverseMatchOutput(**match_data)

    report = generate_reverse_report(judge_out.results, match_output=match_out)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        report.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {out_path}")
    print(f"  Total: {report.total_cases}")

    stats: dict[str, int] = {}
    for r in judge_out.results:
        s = r.coverage_status or "unknown"
        stats[s] = stats.get(s, 0) + 1
    print(f"  Stats: {stats}")


def _cmd_reverse_analyze(args: argparse.Namespace) -> None:
    """reverse-analyze: full reverse pipeline — match → judge → report."""
    from app.v4.pipeline import run_reverse_pipeline
    from app.job_manager import job_manager
    from pathlib import Path as _P
    from app.v4.profiles import ProfileRegistry

    reg = ProfileRegistry()
    reg.load_all(_P(__file__).resolve().parent / "profiles")
    profile = reg.get_or_raise(args.controller_profile)

    hlr_path = Path(args.hlr) if args.hlr else None
    eoicd_json = Path(args.eoicd) if args.eoicd else None
    publisher = Path(args.publisher) if args.publisher else None
    subscriber = Path(args.subscriber) if args.subscriber else None
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not hlr_path:
        print("Error: --hlr is required")
        sys.exit(1)
    if not eoicd_json and not publisher and not subscriber:
        print("Error: need either --eoicd (parsed JSON) or --publisher/--subscriber (Excel)")
        sys.exit(1)

    trace_dir = Path(args.traceability_dir) if args.traceability_dir else None
    if trace_dir and not trace_dir.exists():
        print(f"Error: --traceability-dir does not exist: {trace_dir}")
        sys.exit(1)

    job = job_manager.create_job()
    result = run_reverse_pipeline(
        hlr=hlr_path,
        eoicd_json=eoicd_json,
        publisher=publisher,
        subscriber=subscriber,
        output_dir=output_dir,
        job=job,
        trace_dir=trace_dir,
        profile=profile,
    )
    if result.errors:
        for e in result.errors:
            print(f"  Error: {e}", file=sys.stderr)


def _cmd_generate_word(args: argparse.Namespace) -> None:
    """generate-word: read JSON outputs, generate Word documents."""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.eoicd:
        print("Generating EoICD itemization Excel...")
        generate_eoicd_excel(
            Path(args.eoicd),
            output_dir / "EoICD条目化清单.xlsx",
        )

    if args.reverse_report:
        model_display_map = {"deepseek": "DeepSeek", "minimax": "MiniMax", "qwen": "Qwen"}
        for model in args.model:
            model_display = model_display_map.get(model, model)
            print(f"Generating {model_display} consistency analysis report...")
            generate_consistency_report(
                Path(args.reverse_report),
                output_dir / f"EoICD与SWHLR单模型差异分析报告_{model_display}.docx",
                model=model,
            )

    if not args.eoicd and not args.reverse_report:
        print("Error: at least one of --eoicd or --reverse-report is required")
        sys.exit(1)


def _cmd_generate_consensus(args: argparse.Namespace) -> None:
    """generate-consensus-report: generate multi-model consensus Word report."""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.consensus:
        print("Error: --consensus is required (path to consensus_results.json)")
        sys.exit(1)

    print("Generating multi-model consensus report...")
    match_path = args.match or (
        Path(args.consensus).parent / "reverse_matches.json"
    )
    generate_consensus_word(
        consensus_path=Path(args.consensus),
        match_path=Path(match_path),
        output_path=output_dir / "EoICD与SWHLR多模型差异分析报告.docx",
    )


def main() -> None:
    p = argparse.ArgumentParser(
        description="EoICD Pre-processing & Consistency Analysis"
    )
    sub = p.add_subparsers(dest="command")

    # parse-eoicd
    p_eoicd = sub.add_parser(
        "parse-eoicd", help="Parse EoICD Excel to merged JSON"
    )
    p_eoicd.add_argument("--publisher", default=None)
    p_eoicd.add_argument("--subscriber", default=None)
    p_eoicd.add_argument("--output", required=True)

    # parse-hlr
    p_hlr = sub.add_parser("parse-hlr", help="Parse HLR Word to JSON")
    p_hlr.add_argument("--input", required=True)
    p_hlr.add_argument("--output", required=True)

    # all (parsers only)
    p_all = sub.add_parser("all", help="Run all parsers in batch")
    p_all.add_argument("--publisher", default=None)
    p_all.add_argument("--subscriber", default=None)
    p_all.add_argument("--hlr", default=None)
    p_all.add_argument("--output-dir", default="output")

    # match
    p_match = sub.add_parser(
        "match", help="Run candidate matching on parsed JSON"
    )
    p_match.add_argument("--eoicd", required=True)
    p_match.add_argument("--hlr", required=True)
    p_match.add_argument("--output", required=True)
    p_match.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    p_match.add_argument("--limit", type=int, default=DEFAULT_LIMIT)

    # judge
    p_judge = sub.add_parser(
        "judge", help="Run AI judging on matched cases"
    )
    p_judge.add_argument("--cases", required=True)
    p_judge.add_argument("--output", required=True)

    # report
    p_report = sub.add_parser(
        "report", help="Generate difference report from judgments"
    )
    p_report.add_argument("--judgments", required=True)
    p_report.add_argument("--output", required=True)

    # analyze (full pipeline)
    p_analyze = sub.add_parser(
        "analyze", help="Run full pipeline (parse → match → judge → report)"
    )
    p_analyze.add_argument("--publisher", default=None)
    p_analyze.add_argument("--subscriber", default=None)
    p_analyze.add_argument("--hlr", default=None)
    p_analyze.add_argument("--output-dir", default="output")
    p_analyze.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    p_analyze.add_argument("--limit", type=int, default=DEFAULT_LIMIT)

    # label-hlr
    p_label = sub.add_parser(
        "label-hlr", help="AI pre-label HLR requirements"
    )
    p_label.add_argument("--hlr", required=True)
    p_label.add_argument("--output", required=True)
    p_label.add_argument(
        "--controller-profile",
        default="ams",
        choices=["ams", "fgmc"],
        help="Controller profile id (default: ams)",
    )

    # reverse-match
    p_rev = sub.add_parser(
        "reverse-match", help="Reverse matching: HLR → EoICD (4-path classification)"
    )
    p_rev.add_argument("--hlr", required=True)
    p_rev.add_argument("--eoicd", required=True)
    p_rev.add_argument("--labels", default=None)
    p_rev.add_argument("--traceability-dir", default=None, help="Optional: directory with traceability Excel files for pre-filtering")
    p_rev.add_argument("--output", required=True)
    p_rev.add_argument(
        "--controller-profile",
        default="ams",
        choices=["ams", "fgmc"],
        help="Controller profile id (default: ams)",
    )

    # reverse-judge
    p_rj = sub.add_parser(
        "reverse-judge", help="AI judge reverse match results"
    )
    p_rj.add_argument("--matches", required=True)
    p_rj.add_argument("--eoicd", required=True)
    p_rj.add_argument("--output", required=True)

    # reverse-report
    p_rr = sub.add_parser(
        "reverse-report", help="Generate reverse coverage report from judgments"
    )
    p_rr.add_argument("--judgments", required=True)
    p_rr.add_argument("--matches", default=None, help="Optional: reverse match JSON for pending-review HLRs")
    p_rr.add_argument("--output", required=True)

    # reverse-analyze (full reverse pipeline)
    p_ra = sub.add_parser(
        "reverse-analyze", help="Run full reverse pipeline (parse → match → judge → report)"
    )
    p_ra.add_argument("--hlr", required=True, help="HLR Word document or parsed JSON")
    p_ra.add_argument("--publisher", default=None, help="Publisher Excel file")
    p_ra.add_argument("--subscriber", default=None, help="Subscriber Excel file")
    p_ra.add_argument("--eoicd", default=None, help="Optional: skip parsing, use cached EoICD JSON")
    p_ra.add_argument("--traceability-dir", default=None, help="Optional: directory with traceability Excel files for pre-filtering")
    p_ra.add_argument("--output-dir", default="output")
    p_ra.add_argument(
        "--controller-profile",
        default="ams",
        choices=["ams", "fgmc"],
        help="Controller profile id (default: ams)",
    )

    # generate-word
    p_gw = sub.add_parser(
        "generate-word", help="Generate Word documents from JSON outputs"
    )
    p_gw.add_argument("--eoicd", default=None, help="Path to eoicd_requirements.json")
    p_gw.add_argument("--reverse-report", default=None, help="Path to reverse_report.json")
    p_gw.add_argument("--model", nargs="+", default=["deepseek"],
                      help="Which model(s) to extract (default: deepseek). "
                           "Example: --model deepseek minimax qwen")
    p_gw.add_argument("--output-dir", default="output")

    # generate-consensus-report
    p_gc = sub.add_parser(
        "generate-consensus-report",
        help="Generate multi-model consensus Word report with star ratings",
    )
    p_gc.add_argument("--consensus", required=True, help="Path to consensus_results.json")
    p_gc.add_argument("--match", default=None, help="Path to reverse_matches.json (auto-detected if omitted)")
    p_gc.add_argument("--output-dir", default="output")

    args = p.parse_args()

    if args.command == "parse-eoicd":
        pub = Path(args.publisher) if args.publisher else None
        sub_p = Path(args.subscriber) if args.subscriber else None
        _parse_eoicd(pub, sub_p, Path(args.output))
    elif args.command == "parse-hlr":
        _parse_hlr(Path(args.input), Path(args.output))
    elif args.command == "all":
        _cmd_all(args)
    elif args.command == "match":
        _cmd_match(args)
    elif args.command == "judge":
        _cmd_judge(args)
    elif args.command == "report":
        _cmd_report(args)
    elif args.command == "analyze":
        _cmd_analyze(args)
    elif args.command == "label-hlr":
        _cmd_label_hlr(args)
    elif args.command == "reverse-match":
        _cmd_reverse_match(args)
    elif args.command == "reverse-judge":
        _cmd_reverse_judge(args)
    elif args.command == "reverse-report":
        _cmd_reverse_report(args)
    elif args.command == "reverse-analyze":
        _cmd_reverse_analyze(args)
    elif args.command == "generate-word":
        _cmd_generate_word(args)
    elif args.command == "generate-consensus-report":
        _cmd_generate_consensus(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
