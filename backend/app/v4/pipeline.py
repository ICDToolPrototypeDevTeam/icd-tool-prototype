# -*- coding: utf-8 -*-
"""Pipeline orchestration: reverse analysis workflow."""

# 取消检查点策略：每个 Step 开头 + Step 4 / Step 5.5 的每个 case 边界
# + Step 1 的子步骤边界（解析发布方 / 解析订阅方 / 解析 HLR / 生成条目化清单，
#   其中 Excel 解析另在 parsers/eoicd_excel_parser 里按行间隔埋点）。
# 取消是协作式的——已发出的 HTTP 请求无法中断，其结果会被丢弃。
# 模块级 raise_if_cancelled / report_progress 在未绑定 job（CLI 路径）时静默 no-op。

from __future__ import annotations

import concurrent.futures
import json
import sys
import textwrap
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from app.v4.comparison.multi_judge import (
    _judge_with_provider_sync,
    _build_judge_system_prompt,
)
from app.v4.comparison.report_generator import generate_consensus_reverse_report
from app.v4.comparison.re_review import re_review_judgments
from app.v4.comparison.review_agent import review_judgments, _build_summary
from app.v4.config import JUDGE_PROVIDERS
from app.v4.degradation import DegradationConfig, DegradationContext
from app.v4.degradation.concurrency import _get_drain_executor, _submit_with_gate
from app.v4.degradation.fallback import classify_exception, make_error_judgment
from app.v4.doc_generators.excel_generator import generate_eoicd_excel
from app.v4.doc_generators.word_generator import generate_consistency_report
from app.v4.doc_generators.consensus_word_generator import generate_consensus_report as gen_consensus_word
from app.job_manager import Job, JobStatus, raise_if_cancelled, report_progress
from app.v4.matching.hlr_classifier import enrich_all_labels
from app.v4.matching.hlr_labeler import label_hlrs
from app.v4.llm.mock_llm import forward_label_context
from app.v4.matching.reverse_case_builder import build_reverse_cases
from app.v4.matching.reverse_matcher import match_reverse
from app.v4.matching.signal_profiler import build_profiles, build_blocks, ICDBlock
from app.v4.matching.entry_filter import should_keep
from app.v4.llm_cache import KIND_FORWARD_HLR_LABEL, ReuseTracker, open_llm_cache
from app.v4.models import (
    ConsensusOutput,
    EoICDOutput,
    ForwardAIReviewOutput,
    HLROutput,
    HLRLabelOutput,
    MultiJudgeOutput,
    PipelineResult,
    ReverseJudgmentOutput,
    ReverseMatchOutput,
)
from app.v4.parsers.eoicd_excel_parser import EoICDExcelParser
from app.v4.parsers import create_hlr_parser
from app.v4.parsers.zip_entry_normalize import ensure_standard_zip
from app.v4.profiles.base import ControllerProfile, TraceabilityConfig
from app.v4.profiles import apply_hlr_preprocess_hook
from app.v4.traceability import build_trace_index, name_to_block_key


# ── Forward completeness (EoICD → HLR) imports ────────────────────────────
from app.v4.comparison.coverage_reviewer import (
    consolidate_forward_coverage,
    review_blocks_with_ai,
)
from app.v4.doc_generators.forward_excel_generator import generate_forward_excel
from app.v4.doc_generators.forward_word_generator import generate_forward_word
from app.v4.matching.forward_block_builder import build_forward_blocks
from app.v4.matching.forward_matcher import (
    build_deterministic_results,
    build_forward_candidates,
)
from app.v4.matching.hlr_identity_index import build_hlr_identity_index
from app.v4.traceability.forward_scope import build_forward_scope


def _resolve_profile(profile: ControllerProfile | None) -> ControllerProfile:
    """Return the provided profile, or fall back to the registry's AMS default.

    Centralizes registry lookup so that callers passing ``profile=None`` get
    byte-identical behaviour to pre-#63 code (AMS defaults). Raises a clear
    RuntimeError if neither is available.
    """
    if profile is not None:
        return profile
    from app.v4.profiles import init_registry, get_registry

    reg_dir = Path(__file__).resolve().parent / "profiles"
    try:
        init_registry(reg_dir)
        return get_registry().get_or_raise("ams")
    except Exception as e:
        raise RuntimeError(
            "No profile provided and default AMS profile not found. "
            "Pass profile=... explicitly or initialize the registry."
        ) from e


def _write_text_atomic(path: Path, text: str) -> None:
    """先写同目录临时文件再原子改名，避免留下半截 JSON。

    续跑会把落盘的解析产物直接当成解析结果加载（见 runner._reuse_parse_inputs），
    因此半截文件不只是让本次运行失败 —— 该任务此后每次续跑都会解析失败，只能
    放弃重传。退出码非 0 或容器在写盘窗口内被重启都会造成这种残留。
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)  # POSIX/Windows 均为原子替换


_JSON_CHUNK_ROWS = 2000  # 每块约 1.4MB，12 万条 → 62 块


def _write_json_streaming(path: Path, model, big_field: str = "requirements") -> None:
    """分块写出「标量头部 + 一个超大列表」的模型，峰值内存与条目数无关。

    ``_write_text_atomic(path, model.model_dump_json(indent=2, ensure_ascii=False))``
    等价但更省内存：12 万条时 ``model_dump_json`` 先在堆上造 171MB 的 str
    （含中文时 CPython 按 UCS-2 存，2 字节/字符），``write_text`` 再编出 88MB 的
    bytes —— 实测 Step 1 的 630MB 峰值里有 ~254MB 来自这一步（BUG-20260923-008）。
    分块后每块只驻留约 1.4MB，峰值不再随 EoICD 条数线性放大。

    原子性同 ``_write_text_atomic``（先写 ``.tmp`` 再改名），续跑复用同样安全。
    输出与 ``indent=2`` **逐字节一致**（已用 7 组用例验证：空列表 / 单条 / 整块边界 /
    跨块余数 / 多块小尺寸 / 无其它字段 / 中文与嵌套结构），因此对下游
    ``json.loads`` 与 ``model_validate_json`` 完全透明。``big_field`` 在文件中排在最后。
    """
    head = model.model_dump(mode="json", exclude={big_field})
    items = getattr(model, big_field)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:  # TextIOWrapper 增量编码，不整份编 bytes
        head_text = json.dumps(head, indent=2, ensure_ascii=False)
        # {"a": 1\n} → 去掉尾部的 "\n}" 换成逗号，把大列表接在后面
        fh.write("{" if head_text == "{}" else head_text[:-2] + ",")
        if not items:
            fh.write(f'\n  "{big_field}": []\n')
            fh.write("}")
        else:
            fh.write(f'\n  "{big_field}": [\n')
            for start in range(0, len(items), _JSON_CHUNK_ROWS):
                chunk = [
                    r.model_dump(mode="json")
                    for r in items[start:start + _JSON_CHUNK_ROWS]
                ]
                # 缩进 +2：json.dumps 给整个列表加的 "[" / "]" 与首尾换行去掉后，
                # 元素在块内是 2 空格，落到文件里应是 4 空格（与 indent=2 对齐）
                body = json.dumps(chunk, indent=2, ensure_ascii=False)[1:-1].strip("\n")
                fh.write(",\n" if start else "")
                fh.write(textwrap.indent(body, "  "))
            fh.write("\n  ]\n}")
    tmp.replace(path)


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

    # WPS 等第三方 Office 保存的包可能用反斜杠作条目名分隔符，Linux 上读不出部件
    # （见 parsers/zip_entry_normalize）。
    zip_fixed = False
    if publisher_path:
        publisher_path, fixed = ensure_standard_zip(publisher_path)
        zip_fixed = zip_fixed or fixed
    if subscriber_path:
        subscriber_path, fixed = ensure_standard_zip(subscriber_path)
        zip_fixed = zip_fixed or fixed
    if zip_fixed:
        print("  [fix] Non-standard package entry names normalized")

    parser = EoICDExcelParser(
        publisher_path=publisher_path,
        subscriber_path=subscriber_path,
    )
    result: EoICDOutput = parser.parse()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 分块写：12 万条时整份序列化会额外占 ~254MB 堆（BUG-20260923-008）
    _write_json_streaming(output_path, result)

    print(f"  Output: {output_path}")
    print(f"  Generated (before any dedup): {result.total_generated}")
    print(f"  After per-sheet dedup: {result.total_raw}")
    print(f"  After global dedup: {result.total_after_dedup}")
    print(f"  Duplicates removed: {result.duplicates_removed}")
    return result


def _parse_hlr(
    input_path: Path,
    output_path: Path,
    profile: ControllerProfile | None = None,
) -> HLROutput:
    """Parse the HLR input file.

    The parser is selected by extension via ``create_hlr_parser``:

      - ``.docx`` -> ``HLRWordParser`` (AMS/FGMC/HSCU; profile-driven field map).
      - ``.xlsx`` -> ``HLRExcelParser`` (RPDU; fixed column mapping A/B/C from row 3).

    When ``profile`` is ``None`` the registry's AMS default is used (so that
    pre-#63 callers get byte-identical output).
    """
    print(f"Parsing HLR: {input_path}")
    resolved = _resolve_profile(profile)
    # WPS 等第三方 Office 保存的包可能用反斜杠作条目名分隔符，Linux 上读不出部件
    # （见 parsers/zip_entry_normalize）。非规范包改用规范化副本解析；hook 也用它。
    input_path, zip_fixed = ensure_standard_zip(input_path)
    if zip_fixed:
        print(f"  [fix] Non-standard package entry names normalized: {input_path}")
    parser = create_hlr_parser(input_path, profile=resolved)
    result: HLROutput = parser.parse()

    # Profile-specific HLR content rewrite (e.g. HSCU LBL_X → L<octal>_X
    # alias annotations). No-op for profiles that don't declare
    # hlr_preprocess.enabled. Mutates ``result.requirements[i].content``
    # in-place so the persisted JSON reflects the rewritten text.
    #
    # HSCU's hook needs to re-open the source Word to auto-parse the LBL
    # catalog table; ``HLRWordParser`` only stored the basename in
    # ``result.source_file``, which fails ``Path(...).exists()`` in the
    # backend cwd. Temporarily expose the full input path for the hook
    # call, then restore the basename so the persisted JSON keeps the
    # same display value (avoids changing AMS/FGMC behaviour).
    _saved_source_file = result.source_file
    result.source_file = str(input_path)
    rewritten = apply_hlr_preprocess_hook(resolved, result)
    result.source_file = _saved_source_file

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(
        output_path, result.model_dump_json(indent=2, ensure_ascii=False)
    )

    print(f"  Output: {output_path}")
    print(f"  Requirements: {result.total_count}")
    print(f"  Glossary entries: {len(result.glossary)}")
    if rewritten:
        print(f"  HLR preprocess: {rewritten} requirement(s) rewritten by profile hook")
    return result


def _count_match_types(results: list) -> dict[str, int]:
    """Count results by match_type for logging."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r.match_type] = counts.get(r.match_type, 0) + 1
    return counts


def match_reverse_per_hlr(
    hlr_requirements: list,
    hlr_labels: dict,
    per_hlr_eoicd: dict[str, list],
    profile: ControllerProfile | None = None,
) -> ReverseMatchOutput:
    """Run reverse matching per-HLR with a per-HLR EoICD subset.

    Each HLR is matched against its own EoICD subset, not a global union.
    This is the correct semantics for traceability-based prefiltering:
    HLR_A should not see HLR_B's traced blocks (Issue #74).

    ``profile`` is forwarded to each per-HLR ``match_reverse`` call so RPDU's
    four matcher enhancements (Chinese-suffix stripping, direction-soft
    matching, signal-number bonus, top_k=50) still apply.
    """
    from app.v4.matching.reverse_matcher import match_reverse as _match_reverse

    all_results: list = []
    for hlr in hlr_requirements:
        eoicd_subset = per_hlr_eoicd.get(hlr.requirement_id, [])
        single_hlr_labels = (
            {hlr.requirement_id: hlr_labels[hlr.requirement_id]}
            if hlr.requirement_id in hlr_labels
            else {}
        )
        out = _match_reverse(
            [hlr],
            single_hlr_labels,
            eoicd_subset,
            profile=profile,
        )
        all_results.extend(out.results)

    total_hlr = len(hlr_requirements)
    total_eoicd = sum(len(v) for v in per_hlr_eoicd.values())
    stats = _count_match_types(all_results)
    return ReverseMatchOutput(
        total_hlr=total_hlr,
        total_eoicd_profiles=total_eoicd,
        stats=stats,
        results=all_results,
        eoicd_unmatched_profile_keys=[],
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
    trace_cfg: TraceabilityConfig,
    profile: ControllerProfile | None = None,
) -> ReverseMatchOutput:
    """Run reverse matching with traceability-based pre-filtering.

    Splits HLRs into:
      - Group A (has trace data): match against filtered EoICD subset
      - Group B (no trace data): fallback to full EoICD matching

    ``trace_cfg`` is required (Task 7 made the second arg to
    ``build_trace_index`` non-optional). Callers resolve it from a profile
    (via ``_resolve_profile``) so AMS defaults keep working.

    ``profile`` (Issue #74) is forwarded to ``build_trace_index`` so the
    ``header_adaptive`` trace strategy activates for RPDU, and to each
    ``match_reverse`` call so RPDU's enhancements (Chinese-suffix stripping,
    direction-soft matching, signal-number bonus, top-k=50) are applied.
    ``profile=None`` keeps the AMS default behaviour (byte-identical to
    pre-#63 / pre-#74 code).
    """
    trace_index = build_trace_index(trace_dir, trace_cfg, profile=profile)
    print(f"  Traced HLRs: {trace_index.total_hlrs_traced}")
    print(f"  ERDs: {trace_index.total_erds}")
    print(f"  ICD FullNames: {trace_index.total_icd_fullnames}")
    print(f"  Mapped to blocks: {trace_index.icd_mapped_to_blocks}")
    print(f"  Unmapped: {len(trace_index.icd_unmapped)}")

    group_a_hlrs: list = []
    group_b_hlrs: list = []
    all_traced_block_keys: set[str] = set()
    # Issue #74 (RPDU): per-HLR traced block sets so each HLR only sees its
    # own traced blocks, not the global union of all HLRs' blocks.
    hlr_traced_blocks: dict[str, set[str]] = {}

    for hlr in hlr_requirements:
        traced_blocks = trace_index.hlr_to_blocks.get(hlr.requirement_id)
        if traced_blocks:
            group_a_hlrs.append(hlr)
            all_traced_block_keys.update(traced_blocks)
            hlr_traced_blocks[hlr.requirement_id] = traced_blocks
        else:
            group_b_hlrs.append(hlr)

    print(f"  Group A (traceable): {len(group_a_hlrs)} HLRs")
    print(f"  Group B (fallback):  {len(group_b_hlrs)} HLRs")
    print(f"  Union traced block_keys: {len(all_traced_block_keys)}")

    group_a_ids = {h.requirement_id for h in group_a_hlrs}
    group_b_ids = {h.requirement_id for h in group_b_hlrs}

    # Issue #74 (RPDU): opt-in per-HLR prefilter pool. AMS/FGMC/HSCU keep
    # legacy union-pool semantics (``profile.prefilter_per_hlr`` defaults
    # to False).
    use_per_hlr = bool(profile and getattr(profile, "prefilter_per_hlr", False))

    # Group A: filtered EoICD
    if group_a_hlrs:
        if use_per_hlr:
            # Per-HLR filtered EoICD: each HLR sees only its own traced
            # blocks. Prevents unrelated blocks (from other HLRs sharing
            # the same EoICD) from polluting top_k=50 with status/noise.
            per_hlr_filtered: dict[str, list] = {}
            for hlr_id, traced_bks in hlr_traced_blocks.items():
                per_hlr_filtered[hlr_id] = [
                    req for req in eoicd_requirements
                    if (bk := name_to_block_key(req.signal_name)) in traced_bks
                ]
            total_filtered = sum(len(v) for v in per_hlr_filtered.values())
            print(
                f"  Per-HLR filtered EoICD total: {total_filtered} / "
                f"{len(eoicd_requirements)} entries"
            )
            result_a = match_reverse_per_hlr(
                group_a_hlrs,
                {k: v for k, v in hlr_labels.items() if k in group_a_ids},
                per_hlr_filtered,
                profile=profile,
            )
        else:
            # Legacy union-pool (AMS/FGMC/HSCU): all Group A HLRs share one
            # filtered EoICD subset built from the union of traced blocks.
            filtered_eoicd = []
            for req in eoicd_requirements:
                bk = name_to_block_key(req.signal_name)
                if bk in all_traced_block_keys:
                    filtered_eoicd.append(req)
            print(
                f"  Filtered EoICD: {len(filtered_eoicd)} / "
                f"{len(eoicd_requirements)} entries"
            )
            result_a = match_reverse(
                group_a_hlrs,
                {k: v for k, v in hlr_labels.items() if k in group_a_ids},
                filtered_eoicd,
                profile=profile,
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
                profile=profile,
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
            profile=profile,
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


def _judge_case_with_timeout(
    case,
    providers: list[str],
    system_prompt: str,
    ceiling: float,
    extra_wait: float,
    executor: ThreadPoolExecutor,
) -> tuple[dict[str, dict], bool, list[tuple[str, Future]]]:
    """Run all providers in parallel with fixed extra-wait timeout (thread version).

    Waits for providers one-by-one (FIRST_COMPLETED). Once 2 valid (non-error)
    completions are collected, sets a fixed deadline for the remaining:

        deadline = start + t2 + extra_wait

    Before 2 valid samples, uses *ceiling* as the fallback timeout.
    Fast errors (connection refused, etc.) are excluded from valid_times
    so they do not pollute the formula.

    Timed-out tasks are NOT cancelled: they keep running in the shared drain
    executor, and the caller joins them after Step 4 via _drain_and_rereview()
    so late-but-valid outputs are not wasted.
    """
    futures = {
        _submit_with_gate(executor, _judge_with_provider_sync, case, p, system_prompt): p
        for p in providers
    }
    # start 计时放在 submit 之后，避免信号量阻塞时间吃掉超时预算
    start = time.monotonic()

    pending = set(futures.keys())
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
            for f in pending:
                results[futures[f]] = make_error_judgment(
                    futures[f], "adaptive timeout", "TIMEOUT"
                )
            had_timeout = True
            break

        done, pending = concurrent.futures.wait(
            pending, timeout=remaining, return_when=concurrent.futures.FIRST_COMPLETED,
        )

        for f in done:
            elapsed = time.monotonic() - start
            p = futures[f]
            try:
                result = f.result()
            except Exception as exc:
                results[p] = make_error_judgment(
                    p, str(exc), classify_exception(exc) if exc else "UNKNOWN"
                )
            else:
                results[p] = result
                if result.get("coverage_status") != "error":
                    valid_times.append(elapsed)

    # Timed-out futures are handed back for draining; results already hold
    # their TIMEOUT placeholders.
    timed_out = [(futures[f], f) for f in pending]
    return results, had_timeout, timed_out


class _StepClock:
    """步骤耗时计时器：``mark(label)`` 报告 ``label`` 这一步用了多久。

    每行命名并计时的是**刚结束**的那一步（第 1 次 mark 即第 1 步自己的耗时，不特殊），
    mark 插在该步收尾处、与该步自己的 ``job.update(...)`` 相邻，因此它相对**下一步
    横幅**的落点随插桩点而变：16 处中 2 处在其之前、12 处在其之后、末步 2 处落在输出末尾。

    目的是让「慢在哪一步」在日志面板里直接可见——MOCK 模式耗时 65s 而真实模式
    Step 4 要 11 分钟，此前日志里只有步骤横幅、没有耗时，无法判断。

    用「插入一行 mark」而不是 ``with`` 包裹，是为了避免在 1300 行的既有函数里
    做大规模重排缩进。
    """

    def __init__(self) -> None:
        self._t = time.monotonic()

    def mark(self, label: str) -> None:
        print(f'  [timing] {label}: {time.monotonic() - self._t:.1f}s', flush=True)
        self._t = time.monotonic()


def _is_failure(judgment: dict) -> bool:
    """Check if a judgment dict represents a failure (error or very low confidence)."""
    return judgment.get("coverage_status") == "error"


def _judge_with_degradation(
    cases: list,
    providers: list[str],
    ctx: DegradationContext,
    profile=None,
    cache=None,
    tracker=None,
) -> MultiJudgeOutput:
    """Judge cases with provider health tracking, timeout, and circuit breaking.

    Handles:
    - Skipping providers marked unhealthy
    - Case-level timeout via concurrent.futures.wait
    - Recording per-provider failures for circuit breaker
    - Handing timed-out judgments to ctx.drain for background finishing
    - Reusing already-finished judgments from ``cache`` (同一 job 目录内)

    ``cache`` 命中与否不改变语义：未命中的 provider 走原有调用与降级路径，
    命中的直接落位且不占 executor / gate 名额。case_judgments 一律**按
    providers 顺序**组装 —— 它决定下游 prompt 里「裁判 N」的编号，必须跨
    进程可复现（原先取自 wait() 返回的 set 迭代序，恢复运行时会整体错位）。
    """
    from app.v4.comparison.semantic_judge import (
        REVERSE_JUDGE_PARAMS,
        _build_reverse_user_prompt,
    )
    from app.v4.llm_cache import KIND_REVERSE_JUDGE, compute_key, resolve_model
    from app.v4.models import MultiJudgeOutput, MultiJudgeResult

    system_prompt = _build_judge_system_prompt(profile)
    total = len(cases)
    results: list[MultiJudgeResult] = []
    executor = _get_drain_executor()
    models = {p: resolve_model(p) for p in providers} if cache is not None else {}

    for idx, case in enumerate(cases):
        raise_if_cancelled()
        report_progress(case_index=idx + 1, case_total=total)
        hit: dict[str, dict] = {}
        keys: dict[str, str] = {}
        if cache is not None:
            user_prompt = _build_reverse_user_prompt(case)
            for p in providers:
                keys[p] = compute_key(
                    kind=KIND_REVERSE_JUDGE,
                    provider=p,
                    model=models[p],
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    params=REVERSE_JUDGE_PARAMS,
                )
                payload = cache.get(keys[p])
                if payload is not None:
                    hit[p] = payload
        miss = [p for p in providers if p not in hit]

        # filter_healthy([]) 会抛 AllProvidersUnhealthyError，全部命中时短路；
        # 非空时仍传全量 providers，保持与现状一致的抛错语义。
        healthy_all = ctx.filter_healthy(providers) if miss else []
        healthy_miss = [p for p in miss if p in healthy_all]

        # 恢复运行计数：命中即复用；rerun 只计实际重新发起调用的判定
        # （被熔断 SKIPPED 的 provider 不算「重新分析」）
        if tracker is not None:
            tracker.add(reused=len(hit), rerun=len(healthy_miss))

        # Pre-fill skipped providers
        skipped = {
            p: make_error_judgment(p, "provider unhealthy", "SKIPPED")
            for p in miss if p not in healthy_all
        }

        # Parallel judge with adaptive timeout
        gathered, had_timeout, timed_out = _judge_case_with_timeout(
            case, healthy_miss, system_prompt,
            ceiling=ctx.config.case_total_timeout,
            extra_wait=ctx.config.extra_wait,
            executor=executor,
        )
        if had_timeout:
            ctx.record_case_timeout()
            for p, f in timed_out:
                if len(ctx.drain) < ctx.config.drain_max_tasks:
                    ctx.drain.append((case.case_id, p, f))
                else:
                    cancelled = f.cancel()
                    print(
                        f"  [degradation] drain limit ({ctx.config.drain_max_tasks}) reached, "
                        f"discarding {case.case_id} {p}"
                        f"{' (cancelled)' if cancelled else ' (already running)'}",
                        file=sys.stderr,
                    )

        # Update health per provider from actual results
        for provider, judgment in gathered.items():
            if _is_failure(judgment):
                ctx.record_failure(provider)
            else:
                ctx.record_success(provider)

        # 只固化成功判定：TIMEOUT 占位、SKIPPED、异常都不是判定结果
        if cache is not None:
            for provider, judgment in gathered.items():
                if judgment.get("coverage_status") != "error":
                    cache.put(
                        keys[provider],
                        kind=KIND_REVERSE_JUDGE,
                        provider=provider,
                        model=models[provider],
                        case_id=case.case_id,
                        payload=judgment,
                        params=REVERSE_JUDGE_PARAMS,
                    )

        case_judgments = {
            p: hit[p] if p in hit else (skipped[p] if p in skipped else gathered[p])
            for p in providers
        }
        results.append(MultiJudgeResult(
            case_id=case.case_id,
            judgments=case_judgments,
        ))

        statuses = {p: j.get("coverage_status", "?") for p, j in case_judgments.items()}
        print(
            f"  [multi] {case.case_id} ({idx + 1}/{total}) {statuses}",
            file=sys.stderr,
        )
        # 仅命中时打缓存行（miss 不打印）：首跑全未命中时不再出现 hit=0/3 噪声
        if cache is not None and hit:
            print(
                f"  [cache] {case.case_id} hit={len(hit)}/{len(providers)}",
                file=sys.stderr,
            )

        if idx < total - 1:
            time.sleep(0.3)

    return MultiJudgeOutput(
        total_cases=total,
        providers=providers,
        results=results,
    )


def _drain_and_rereview(
    multi_out: MultiJudgeOutput,
    ctx: DegradationContext,
    budget: float,
) -> tuple[MultiJudgeOutput, set[str]]:
    """Join timed-out judgments (Step 4.5) and apply late results.

    Waits up to *budget* seconds for every future in ctx.drain. Late results
    replace the TIMEOUT placeholder in the case's judgments so Step 5
    consensus sees final judgments. Late valid results reset the provider's
    failure counter; late errors keep the counter as-is (the TIMEOUT
    placeholder already counted one failure).
    """
    if not ctx.drain:
        return multi_out, set()

    futures = [f for _, _, f in ctx.drain]
    done, _ = concurrent.futures.wait(futures, timeout=budget)

    case_map = {r.case_id: r for r in multi_out.results}
    updated: set[str] = set()

    for case_id, provider, future in ctx.drain:
        if future not in done:
            continue
        mjr = case_map.get(case_id)
        if mjr is None:
            continue
        old = mjr.judgments.get(provider, {})
        if old.get("coverage_status") != "error":
            # 不是 TIMEOUT 占位（已有真实结果），不覆盖
            continue
        try:
            result = future.result()
        except Exception as exc:
            result = make_error_judgment(
                provider, str(exc), classify_exception(exc) if exc else "UNKNOWN"
            )
        mjr.judgments[provider] = result
        if result.get("coverage_status") != "error":
            updated.add(case_id)
            ctx.record_success(provider)
            ctx.record_drained_late()
            print(
                f"  [degradation] drain: {case_id} {provider} late result "
                f"applied ({result.get('coverage_status', '?')})",
                file=sys.stderr,
            )
        else:
            print(
                f"  [degradation] drain: {case_id} {provider} late result "
                f"is also error ({result.get('analysis', '')[:60]})",
                file=sys.stderr,
            )

    ctx.drain.clear()
    return multi_out, updated


def _backfill_judge_cache(
    multi_out: MultiJudgeOutput,
    cases: list,
    providers: list[str],
    profile,
    cache,
    case_ids: set[str],
) -> int:
    """把 Step 4.5 drain 回填的晚期判定补写进缓存（幂等，已存在的 key 跳过）。

    首跑写盘时这些 case 上还是 TIMEOUT 占位，判定是 drain 之后才拿到的；
    不补写的话恢复运行会重新排队等待这些慢 provider，等于白跑一次。
    """
    from app.v4.comparison.semantic_judge import (
        REVERSE_JUDGE_PARAMS,
        _build_reverse_user_prompt,
    )
    from app.v4.llm_cache import KIND_REVERSE_JUDGE, compute_key, resolve_model

    system_prompt = _build_judge_system_prompt(profile)
    models = {p: resolve_model(p) for p in providers}
    case_map = {c.case_id: c for c in cases}
    added = 0

    for mr in multi_out.results:
        if mr.case_id not in case_ids:
            continue
        case = case_map.get(mr.case_id)
        if case is None:
            continue
        user_prompt = _build_reverse_user_prompt(case)
        for p in providers:
            judgment = (mr.judgments or {}).get(p)
            if not judgment or judgment.get("coverage_status") == "error":
                continue
            key = compute_key(
                kind=KIND_REVERSE_JUDGE,
                provider=p,
                model=models[p],
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                params=REVERSE_JUDGE_PARAMS,
            )
            # put 幂等：Step 4 已写入的 (case, provider) 返回 False，不重复追加
            if cache.put(
                key,
                kind=KIND_REVERSE_JUDGE,
                provider=p,
                model=models[p],
                case_id=mr.case_id,
                payload=judgment,
                params=REVERSE_JUDGE_PARAMS,
            ):
                added += 1

    return added


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
            # 所有 provider 均失败：共识输入全部失效，共识 LLM 在纯 error 输入上
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
    profile: ControllerProfile | None = None,
    refine: bool = False,
) -> PipelineResult:
    """Run reverse pipeline: parse → label → match → judge → report.

    If trace_dir is provided, enables traceability-based pre-filtering
    to narrow the EoICD search space before reverse matching.

    ``profile`` (Task 10, Issue #63) is threaded into every profile-aware
    consumer: ``HLRWordParser``, ``label_hlrs``, ``enrich_all_labels``, and
    ``build_trace_index``. ``None`` falls back to the registry's AMS default
    so pre-#63 callers keep byte-identical output.

    When ``refine=True`` (RPDU-only via CLI), the pipeline runs an extra
    pre-processing stage after Step 3 (reverse match): filter irrelevant
    ICD blocks, recover signals missed by top-N candidates (including
    Chinese→English synonym recovery such as 空速→airspeed), then re-build
    cases for the standard Step 4-6. ``refine=False`` keeps byte-identical
    original behaviour.
    """
    clock = _StepClock()
    if not (eoicd_json or publisher or subscriber):
        raise ValueError("need eoicd (parsed JSON) or publisher/subscriber (Excel)")

    # Resolve profile once (loads AMS from registry if profile=None). Done
    # eagerly so any registry/load error surfaces before the long-running
    # parse/label steps.
    resolved_profile = _resolve_profile(profile)

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

    # Step 1 的子步骤边界检查点：解析十万行级的表要几十秒，而取消是协作式的，
    # 没有检查点就只能等整步跑完（见 job_manager.raise_if_cancelled）
    raise_if_cancelled()

    if hlr.suffix == ".json":
        print(f"  [skip] Using cached HLR JSON: {hlr}")
        hlr_data = json.loads(hlr.read_text(encoding="utf-8"))
        hlr_out = HLROutput(**hlr_data)
    else:
        hlr_out = _parse_hlr(
            hlr,
            output_dir / "hlr_requirements.json",
            profile=resolved_profile,
        )

    raise_if_cancelled()

    # Step 1: EoICD itemization Excel
    # 传 eoicd_out 复用刚解析好的对象：12 万条时重读 JSON 峰值 +410MB（BUG-20260923-007）
    eoicd_json_path = output_dir / "eoicd_requirements.json"
    generate_eoicd_excel(
        eoicd_json_path,
        output_dir / "EoICD条目化清单.xlsx",
        eoicd_out=eoicd_out,
    )

    # 同一 job 目录内的 LLM 判定缓存：内容寻址，命中即复用（见 app/v4/llm_cache.py）
    cache = open_llm_cache(output_dir)
    # 恢复运行才累计复用计数（首跑与 CLI 全程 None，行为与之前一致）
    tracker = ReuseTracker(job.set_reuse_stats) if job.resumed else None

    # Step 2: HLR labeling
    print()
    print("=" * 50)
    print("Step 2/6: HLR AI labeling")
    print("=" * 50)
    clock.mark("Step 1/6: Parsing input files")
    raise_if_cancelled()
    job.update(JobStatus.RUNNING, "Step 2/6: HLR AI labeling")
    labels_cache = output_dir / "hlr_labels.json"
    hlr_labels = label_hlrs(
        hlr_out.requirements,
        cache_path=labels_cache,
        profile=resolved_profile,
        cache=cache,
        tracker=tracker,
    )
    hlr_labels = enrich_all_labels(
        hlr_out.requirements,
        hlr_labels,
        keywords=resolved_profile.classifier_keywords,
    )
    print(f"  HLRs labeled: {len(hlr_labels)}")

    clock.mark("Step 2/6: HLR AI labeling")
    raise_if_cancelled()
    # Step 3: Reverse match
    print()
    print("=" * 50)
    if trace_dir:
        print(f"Step 3/6: Traceability-filtered reverse matching ({len(hlr_out.requirements)} HLR → {len(eoicd_out.requirements)} EoICD)")
        print("=" * 50)
        job.update(JobStatus.RUNNING, "Step 3/6: Traceability-filtered reverse matching")
        match_result = _match_reverse_with_trace(
            hlr_out.requirements,
            hlr_labels,
            eoicd_out.requirements,
            trace_dir,
            resolved_profile.traceability,
            profile=resolved_profile,
        )
    else:
        print(f"Step 3/6: Reverse matching ({len(hlr_out.requirements)} HLR → {len(eoicd_out.requirements)} EoICD)")
        print("=" * 50)
        job.update(JobStatus.RUNNING, "Step 3/6: Reverse matching")
        match_result = match_reverse(
            hlr_out.requirements,
            hlr_labels,
            eoicd_out.requirements,
            profile=resolved_profile,
        )
    match_path = output_dir / "reverse_matches.json"
    match_path.write_text(
        match_result.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Output: {match_path}")
    print(f"  Stats: {match_result.stats}")

    # 精化分支：仅 RPDU profile 走过滤+补采（refine runner 内部自构 block_index），
    # 后续继续走原 Step 4-6（多模型并发 + drain + degradation + 5星共识 + re_review）。
    if refine:
        print()
        print("=" * 50)
        print("Refine: 过滤无关 ICD Block + 精确补采 + 同义词补采")
        print("=" * 50)
        job.update(JobStatus.RUNNING, "Refine: 过滤无关 ICD Block + 补采")
        from app.v4.refine.runner import run_pipeline_refined_stage
        match_result, cases = run_pipeline_refined_stage(
            eoicd_out=eoicd_out,
            hlr_labels=hlr_labels,
            match_result=match_result,
            output_dir=output_dir,
        )
        match_path.write_text(  # 覆盖写过滤后的 reverse_matches.json
            match_result.model_dump_json(indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  Output: {match_path}")
        print(f"  Stats: {match_result.stats}")
    else:
        # Build block index for case building
        eoicd_kept = [req for req in eoicd_out.requirements if should_keep(req)]
        eoicd_profiles = build_profiles(eoicd_kept)
        blocks = build_blocks(eoicd_profiles)
        block_index: dict[str, ICDBlock] = {b.block_key: b for b in blocks}

        cases = build_reverse_cases(match_result, block_index)

    clock.mark("Step 3/6: Reverse matching")
    raise_if_cancelled()
    report_progress(message="Step 4/6: Multi-agent judging", stage="multi_judge", stage_index=4, stage_total=6, force_flush=True)
    # Step 4: Multi-agent judging (with degradation)
    print()
    print("=" * 50)
    print(f"Step 4/6: Multi-agent judging ({len(cases)} cases, providers={JUDGE_PROVIDERS})")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 4/6: Multi-agent judging")
    ctx = DegradationContext(config=DegradationConfig.from_env())
    multi_out = _judge_with_degradation(
        cases, JUDGE_PROVIDERS, ctx, profile=resolved_profile, cache=cache,
        tracker=tracker,
    )

    # Step 4.5: Drain timed-out judgments — late-but-valid outputs are kept
    # and replace their TIMEOUT placeholders before consensus runs.
    if ctx.drain:
        print()
        print("=" * 50)
        print(f"Step 4.5/6: Draining {len(ctx.drain)} timed-out judgments "
              f"(budget {ctx.config.drain_budget:.0f}s)")
        print("=" * 50)
        multi_out, drained_ids = _drain_and_rereview(
            multi_out, ctx, ctx.config.drain_budget
        )
        print(f"  Late results applied to {len(drained_ids)} case(s): "
              f"{sorted(drained_ids) if drained_ids else 'none'}")
    else:
        drained_ids = set()

    if drained_ids:
        backfilled = _backfill_judge_cache(
            multi_out, cases, JUDGE_PROVIDERS, resolved_profile, cache, drained_ids
        )
        if backfilled:
            print(f"  [cache] drain backfill: {backfilled} late judgment(s) written")

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
    clock.mark("Step 4/6: Multi-agent judging + drain")
    raise_if_cancelled()
    job.update(JobStatus.RUNNING, "Step 5/6: Review agent consensus")
    consensus_out = review_judgments(multi_out.results, cache=cache, tracker=tracker)
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

    # Step 5.5: Re-review low-confidence cases (1★/2★, ADR-004)
    print()
    print("=" * 50)
    print("Step 5.5/6: Re-review low-confidence cases (1★/2★)")
    print("=" * 50)
    clock.mark("Step 5/6: Review agent consensus")
    raise_if_cancelled()
    job.update(JobStatus.RUNNING, "Step 5.5/6: Re-review low-confidence cases")
    multi_out, re_reviewed_ids = re_review_judgments(
        multi_out=multi_out,
        consensus_out=consensus_out,
        cases=cases,
        output_dir=output_dir,
        providers=frozen_providers,
        cache=cache,
        tracker=tracker,
    )

    # Step 5.6: Re-run consensus only for re-reviewed cases
    print()
    print("=" * 50)
    print("Step 5.6/6: Re-run consensus after re-review")
    print("=" * 50)
    clock.mark("Step 5.5/6: Re-review")
    raise_if_cancelled()
    job.update(JobStatus.RUNNING, "Step 5.6/6: Re-run consensus after re-review")

    if re_reviewed_ids:
        # Partial update: re-run consensus only for re-reviewed cases
        consensus_map = {r.case_id: r for r in consensus_out.results}
        for case_id in sorted(re_reviewed_ids):
            mr = next((m for m in multi_out.results if m.case_id == case_id), None)
            if mr is None:
                continue
            new_consensus = review_judgments([mr], cache=cache, tag="consensus(5.6)", tracker=tracker)
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
    clock.mark("Step 5.6/6: Re-run consensus")
    raise_if_cancelled()
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

    clock.mark("Step 6/6: Generating report")
    report_progress(force_flush=True)
    job.update(JobStatus.COMPLETED, "Reverse pipeline complete")
    return PipelineResult(
        parsed_count=len(hlr_out.requirements),
        match_count=len(match_result.results),
        judged_count=len(consensus_out.results),
        report_path=str(report_path),
        # 计数在手上直接回传：runner 收尾反读同一份 JSON 只为取这两个整数，
        # 12 万条时峰值 +163~247MB（BUG-20260923-008）
        eoicd_count=eoicd_out.total_after_dedup,
        hlr_count=len(hlr_out.requirements),
    )


# ============================================================================
# Forward completeness pipeline (EoICD → HLR)
# ============================================================================


def _save_forward(model, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Output: {path}")


def run_forward_pipeline(
    hlr: Path,
    eoicd_json: Path | None,
    publisher: Path | None,
    subscriber: Path | None,
    output_dir: Path,
    job: Job,
    analysis_mode: str = "full",
    device_icd_trace_file: Path | None = None,
    system_device_trace_file: Path | None = None,
) -> PipelineResult:
    """Run forward completeness pipeline: parse → scope → blocks → index → recall → judge → AI → report."""
    clock = _StepClock()
    if not (eoicd_json or publisher or subscriber):
        raise ValueError("need eoicd (parsed JSON) or publisher/subscriber (Excel)")

    output_dir.mkdir(parents=True, exist_ok=True)

    # C1: parse
    print("=" * 50)
    print("Step 1/8: Parsing input files")
    print("=" * 50)
    job.update(JobStatus.RUNNING, "Step 1/8: Parsing input files")
    if eoicd_json and eoicd_json.exists():
        eoicd_out = EoICDOutput(**json.loads(eoicd_json.read_text(encoding="utf-8")))
        print(f"  [skip] Using cached EoICD JSON: {eoicd_json}")
    else:
        eoicd_out = _parse_eoicd(publisher, subscriber, output_dir / "eoicd_requirements.json")

    # Step 1 的子步骤边界检查点：解析十万行级的表要几十秒，而取消是协作式的，
    # 没有检查点就只能等整步跑完（见 job_manager.raise_if_cancelled）
    raise_if_cancelled()
    if hlr.suffix == ".json":
        hlr_out = HLROutput(**json.loads(hlr.read_text(encoding="utf-8")))
        print(f"  [skip] Using cached HLR JSON: {hlr}")
    else:
        hlr_out = _parse_hlr(hlr, output_dir / "hlr_requirements.json")

    raise_if_cancelled()

    # C2: scope
    print()
    print("=" * 50)
    print(f"Step 2/8: Forward scope ({analysis_mode} mode)")
    print("=" * 50)
    clock.mark("Step 1/8: Parsing input files")
    raise_if_cancelled()
    job.update(JobStatus.RUNNING, f"Step 2/8: Forward scope ({analysis_mode} mode)")
    scope = build_forward_scope(
        eoicd_out, hlr_out, analysis_mode,
        device_icd_trace_file=device_icd_trace_file,
        system_device_trace_file=system_device_trace_file,
    )
    _save_forward(scope, output_dir / "forward_scope.json")
    if scope.input_errors:
        print(f"  Input errors: {len(scope.input_errors)} (non-fatal)")
    print(f"  Mode: {scope.analysis_mode} | scope items: {scope.total_scope_fullnames} | candidate HLRs: {scope.total_candidate_hlrs}")

    # C3: blocks
    print()
    print("=" * 50)
    print("Step 3/8: Building forward ICD blocks")
    print("=" * 50)
    clock.mark("Step 2/8: Forward scope")
    raise_if_cancelled()
    job.update(JobStatus.RUNNING, "Step 3/8: Building forward ICD blocks")
    blocks = build_forward_blocks(eoicd_out, scope)
    _save_forward(blocks, output_dir / "forward_blocks.json")
    print(f"  Total blocks: {blocks.total_blocks}")

    # 同一 job 目录内的 LLM 调用缓存：内容寻址，命中即复用（见 app/v4/llm_cache.py）
    cache = open_llm_cache(output_dir)
    # 恢复运行才累计复用计数（首跑 tracker 为 None，行为与之前一致）
    tracker = ReuseTracker(job.set_reuse_stats) if job.resumed else None

    # C4: HLR identity index (deterministic) + optional AI label recall enhancement.
    # label_hlrs() is recall-only and degrades gracefully to the deterministic
    # index on any failure — it never fails the forward task.
    print()
    print("=" * 50)
    print("Step 4/8: Building HLR identity index")
    print("=" * 50)
    clock.mark("Step 3/8: Building forward ICD blocks")
    raise_if_cancelled()
    job.update(JobStatus.RUNNING, "Step 4/8: Building HLR identity index")
    hlr_labels: dict = {}
    try:
        # 正向缺陷修正 #3：正向管线需要非空 llm_label tokens（召回增强），
        # 但 label_hlrs 与反向管线共享、prompt 完全一致。进入 forward_label_context
        # 让 mock 仅为正向返回非空标签，反向保持空标签以维持基线不变。
        with forward_label_context():
            hlr_labels = label_hlrs(
                hlr_out.requirements,
                cache_path=output_dir / "hlr_labels.json",
                cache=cache,
                tracker=tracker,
                cache_kind=KIND_FORWARD_HLR_LABEL,
            )
        hlr_labels = enrich_all_labels(hlr_out.requirements, hlr_labels)
    except Exception as exc:  # noqa: BLE001 — label failure must not fail the task
        hlr_labels = {}
        print(f"  [warn] HLR labeling skipped ({type(exc).__name__}: {exc}); "
              f"falling back to deterministic index only")
    index = build_hlr_identity_index(hlr_out, hlr_labels or None)
    _save_forward(index, output_dir / "hlr_identity_index.json")
    print(f"  HLRs indexed: {index.total_hlrs} | deterministic tokens: {len(index.token_index)} "
          f"| llm_label tokens: {len(index.llm_token_index)}")
    print(f"  HLR label calls: {len(hlr_labels)} (separate from forward review calls)")

    # C5: candidate recall
    print()
    print("=" * 50)
    print("Step 5/8: Candidate recall")
    print("=" * 50)
    clock.mark("Step 4/8: Building HLR identity index")
    raise_if_cancelled()
    job.update(JobStatus.RUNNING, "Step 5/8: Candidate recall")
    candidates = build_forward_candidates(blocks, index)
    _save_forward(candidates, output_dir / "forward_candidates.json")

    # C6: deterministic judgment
    print()
    print("=" * 50)
    print("Step 6/8: Deterministic coverage judgment")
    print("=" * 50)
    clock.mark("Step 5/8: Candidate recall")
    raise_if_cancelled()
    job.update(JobStatus.RUNNING, "Step 6/8: Deterministic coverage judgment")
    deterministic = build_deterministic_results(blocks, candidates, index)
    _save_forward(deterministic, output_dir / "forward_deterministic.json")
    print(f"  Deterministic stats: {deterministic.stats}")

    # C7: AI three-state review (degrade gracefully if model unavailable)
    print()
    print("=" * 50)
    print("Step 7/8: AI three-state review")
    print("=" * 50)
    clock.mark("Step 6/8: Deterministic coverage judgment")
    raise_if_cancelled()
    report_progress(message="Step 7/8: AI three-state review", stage="ai_review", stage_index=7, stage_total=8, force_flush=True)
    job.update(JobStatus.RUNNING, "Step 7/8: AI three-state review")
    hlr_content = {r.requirement_id: r.content for r in hlr_out.requirements}
    try:
        ai_review = review_blocks_with_ai(
            blocks, deterministic, index, hlr_content,
            cache=cache, tracker=tracker,
        )
    except Exception as exc:  # noqa: BLE001 — model unavailable / no API key
        ai_review = ForwardAIReviewOutput(total_reviewed=0, stats={"skipped": 1}, results=[])
        print(f"  [warn] AI review skipped ({type(exc).__name__}: {exc}); possible-tier blocks stay 'possible'")
    _save_forward(ai_review, output_dir / "forward_ai_review.json")
    print(f"  Forward review calls: {ai_review.total_reviewed} (separate from HLR label calls) | stats: {ai_review.stats}")

    # C8: consolidate + report
    print()
    print("=" * 50)
    print("Step 8/8: Consolidating coverage + generating reports")
    print("=" * 50)
    clock.mark("Step 7/8: AI three-state review")
    raise_if_cancelled()
    job.update(JobStatus.RUNNING, "Step 8/8: Consolidating coverage + generating reports")
    coverage = consolidate_forward_coverage(blocks, scope, deterministic, ai_review)
    # 正向缺陷修正 #8：两类独立 AI 调用计数（标签 + 正向复核）写入最终 coverage，
    # 供 API / Excel / Word 审计展示。
    coverage.hlr_label_calls = len(hlr_labels)
    coverage.ai_review_calls = ai_review.total_reviewed
    _save_forward(coverage, output_dir / "forward_coverage.json")
    generate_forward_excel(coverage, blocks, output_dir / "EoICD至HLR正向完整性分析明细.xlsx")
    generate_forward_word(coverage, blocks, output_dir / "EoICD至HLR正向完整性分析报告.docx")
    print(f"  Final stats: {coverage.stats}")

    clock.mark("Step 8/8: Consolidating coverage + generating reports")
    report_progress(force_flush=True)
    job.update(JobStatus.COMPLETED, "Forward pipeline complete")
    covered = coverage.stats.get("covered_direct", 0) + coverage.stats.get("covered_aggregate", 0)
    return PipelineResult(
        parsed_count=blocks.total_blocks,
        match_count=covered,
        judged_count=ai_review.total_reviewed,
        report_path=str(output_dir / "forward_coverage.json"),
        # 同反向管线：避免 runner 收尾为两个整数反读整份大 JSON（BUG-20260923-008）
        eoicd_count=eoicd_out.total_after_dedup,
        hlr_count=len(hlr_out.requirements),
    )
