# -*- coding: utf-8 -*-
"""One-star re-review: Step 7.5 of the V4 pipeline.

Reviews the cases that the consensus step (Step 8) flagged with low
confidence (``star_rating in {1, 2}`` in the 5-star system, ADR-004;
previously ``star_rating == 1`` in the 3-star system) by re-querying each
of the three judging providers with a peer-aware prompt: each provider
sees its own previous judgment labeled as "Judgment A" and the other two
as "Judgment B" / "Judgment C", then reconsiders the case and outputs a
fresh structured JSON.

The re-reviewed judgments replace the originals in the
``MultiJudgeResult.judgments`` dict; the original judgments are preserved
under ``re_review_results.json`` (one entry per re-reviewed case) for
auditability. The updated ``MultiJudgeOutput`` is also written back to
``multi_judge_results.json`` so downstream steps (report generation) see
the post-review state.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.v4.comparison.semantic_judge import _extract_json
from app.v4.config import JUDGE_PROVIDERS
from app.v4.llm import get_llm
from app.v4.models import (
    ConsensusOutput,
    MultiJudgeOutput,
    MultiJudgeResult,
    ReverseCase,
)
from app.v4.prompts import load_prompt


# ============================================================
# 1. Identify low-confidence cases (5-star system, ADR-004)
# ============================================================


def _resolve_low_confidence_case_ids(
    consensus_out: ConsensusOutput | None,
    output_dir: Path,
) -> set[str]:
    """Return the set of case_id values whose star_rating is 1 or 2.

    5 星体系（ADR-004）：触发条件从 1★ 扩展到 ≤2★。peer-aware 复查给
    1★（分歧/单源/无共识）一个升到 2★ 的机会，给 2★（多数一致但 evidence
    弱）一个升到 3★ 的机会。

    Uses ``consensus_out`` when provided; otherwise reads
    ``output_dir / "consensus_results.json"`` if it exists. Returns an
    empty set when neither source yields any low-confidence cases.
    """
    if consensus_out is not None:
        return {r.case_id for r in consensus_out.results if r.star_rating in (1, 2)}

    consensus_path = output_dir / "consensus_results.json"
    if not consensus_path.exists():
        return set()

    try:
        data = json.loads(consensus_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()

    return {
        r["case_id"]
        for r in data.get("results", [])
        if isinstance(r, dict) and r.get("star_rating") in (1, 2)
    }


# ============================================================
# 2. Build the per-provider user prompt
# ============================================================


def _build_re_review_user_prompt(
    case: ReverseCase,
    my_judgment: dict,
    other_judgments: list[dict],
) -> str:
    """Serialize a ReverseCase with three peer judgments for the re-review LLM.

    Structure: HLR (system intent) → ICD Block (benchmark evidence) → Judgment A
    (self) → Judgment B / C (peers) → reflection prompts.
    """
    parts: list[str] = []
    hlr = case.hlr_requirement

    # ── HLR (implementation to check) ──
    parts.append("## 软件高层需求 (HLR)")
    parts.append(f"- ID: {hlr.get('hlr_id', 'N/A')}")
    parts.append(f"- 内容: {hlr.get('content', 'N/A')}")
    if hlr.get("rationale"):
        parts.append(f"- 基本原理: {hlr.get('rationale', '')}")
    parts.append("")

    # ── ICD Block (benchmark) ──
    if case.matched_profiles:
        parts.append("## EoICD 信号块（ICD 基准定义）")
        for i, blk in enumerate(case.matched_profiles, 1):
            parts.append(f"### ICD Block {i}: {blk.get('signal_family', 'N/A')}")
            merged = blk.get("merged_attributes", {}) or {}
            parts.append(f"- Label号: {merged.get('Label', 'N/A')}")
            parts.append(
                f"- 方向: {merged.get('方向', merged.get('Direction', 'N/A'))}"
            )
            parts.append(f"- 数据类型: {merged.get('DataFormatType', 'N/A')}")
            parts.append(
                f"- Bit 偏移/宽度: {merged.get('BitOffsetWithinDS', 'N/A')}"
                f"/{merged.get('ParameterSize', 'N/A')}"
            )
            parts.append(
                f"- 取值范围: {merged.get('FullScaleRngMin', 'N/A')}"
                f"~{merged.get('FullScaleRngMax', 'N/A')}"
            )
            parts.append(f"- 单位: {merged.get('Units', 'N/A')}")
            parts.append("")
    else:
        parts.append("## EoICD 信号块")
        parts.append("（无匹配信号）")
        parts.append("")

    # ── Judgment A (self) and B / C (peers) ──
    parts.append("## 复查规则")
    parts.append("")
    parts.append(
        "**你是判断 A**：你之前的判断是 "
        f"{my_judgment.get('coverage_status', 'N/A')}，"
        f"分析：{my_judgment.get('analysis', 'N/A')}"
    )
    parts.append("")
    for i, oj in enumerate(other_judgments, 1):
        label = "B" if i == 1 else "C"
        parts.append(
            f"**判断 {label}**：{oj.get('coverage_status', 'N/A')}，"
            f"分析：{oj.get('analysis', 'N/A')}"
        )
        parts.append("")

    # ── Reflection guidance (generic, no agent-specific failure analysis) ──
    parts.append("## 反思引导")
    parts.append(
        "- 反思你之前的判断：是否存在对 HLR 属性的误解？是否忽略了 ICD 中的某些关键定义？"
    )
    parts.append("- 判断 B 和 C 的分析中，是否有你没有考虑到的关键证据？")
    parts.append("- 三个判断识别的差异点分别是什么？核心分歧在哪里？")
    parts.append("")
    parts.append("请重新评估并输出 JSON。")

    return "\n".join(parts)


# ============================================================
# 3. Per-provider re-review LLM call
# ============================================================


def _call_re_review_api(
    llm,
    system_prompt: str,
    user_prompt: str,
    agent_name: str,
) -> dict:
    """Call the re-review LLM and return a fresh judgment dict.

    Retries up to 3 times on parse / API errors (2 retries), with a 1-second
    backoff. On terminal failure the returned dict is marked with
    ``coverage_status == "error"`` and an empty analysis so the caller can
    still write it back without breaking the overall flow.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(3):
        try:
            response = llm.chat(messages=messages, temperature=0.1, max_tokens=4096)
            content = _extract_json(response["content"])
            data = json.loads(content)
            return {
                "agent_name": agent_name,
                "coverage_status": data.get("coverage_status", "needs_review"),
                "difference_type": data.get("difference_type", "需确认"),
                "missing_points": data.get("missing_points", []) or [],
                "inconsistent_points": data.get("inconsistent_points", []) or [],
                "analysis": data.get("analysis", ""),
                "suggested_action": data.get("suggested_action", ""),
                "confidence": float(data.get("confidence", 0.5)),
                "raw_response": "",
            }
        except Exception as exc:  # noqa: BLE001 — broad on purpose: re-review
            # is best-effort per provider; we never want one bad provider to
            # abort the whole re-review loop.
            if attempt < 2:
                time.sleep(1.0)
                continue
            return {
                "agent_name": agent_name,
                "coverage_status": "error",
                "difference_type": "",
                "missing_points": [],
                "inconsistent_points": [],
                "analysis": f"API error after retries: {exc}",
                "suggested_action": "",
                "confidence": 0.0,
                "raw_response": "",
            }

    # Unreachable in practice — the loop above returns on every path — but
    # kept for type-checker happiness.
    return {
        "agent_name": agent_name,
        "coverage_status": "error",
        "difference_type": "",
        "missing_points": [],
        "inconsistent_points": [],
        "analysis": "Max retries exceeded",
        "suggested_action": "",
        "confidence": 0.0,
        "raw_response": "",
    }


# ============================================================
# 4. Top-level entry point
# ============================================================


def re_review_judgments(
    multi_out: MultiJudgeOutput,
    consensus_out: ConsensusOutput | None,
    cases: list[ReverseCase],
    output_dir: Path,
    providers: list[str] | None = None,
) -> tuple[MultiJudgeOutput, set[str]]:
    """Re-review every low-confidence (1★/2★) case with each judging provider.

    5 星体系（ADR-004）：触发条件从 star_rating == 1 扩展到
    star_rating ∈ {1, 2}。peer-aware 复查给 1★ 一个升 2★ 的机会，
    给 2★（多数一致但 evidence 弱）一个升 3★ 的机会。

    For each case in ``consensus_out`` (or ``output_dir /
    consensus_results.json``) with ``star_rating in {1, 2}``, query every
    provider in ``providers`` (default :data:`JUDGE_PROVIDERS`) with a
    peer-aware user prompt and replace that provider's entry in the
    case's ``judgments`` dict. Original judgments are preserved under
    ``output_dir / re_review_results.json``; the updated
    ``MultiJudgeOutput`` is also written to
    ``output_dir / multi_judge_results.json``.

    Returns the updated ``multi_out`` (the same object passed in, mutated
    in place) so callers can chain it with downstream steps.

    A no-op when no low-confidence cases are present.
    """
    if providers is None:
        providers = JUDGE_PROVIDERS

    low_confidence_ids = _resolve_low_confidence_case_ids(consensus_out, output_dir)
    if not low_confidence_ids:
        return multi_out, set()

    # 当 multi_out=None 时从 output_dir 加载
    if multi_out is None:
        multi_path = output_dir / "multi_judge_results.json"
        if multi_path.exists():
            multi_data = json.loads(multi_path.read_text(encoding="utf-8"))
            multi_out = MultiJudgeOutput(**multi_data)

    case_map: dict[str, ReverseCase] = {c.case_id: c for c in cases}
    mjr_map: dict[str, MultiJudgeResult] = {r.case_id: r for r in multi_out.results}

    system_prompt = load_prompt("re_review")

    re_review_meta: list[dict] = []
    re_reviewed_count = 0

    for case_id in sorted(low_confidence_ids):
        mjr = mjr_map.get(case_id)
        case = case_map.get(case_id)
        if mjr is None or case is None:
            # Nothing to re-review — likely a stale consensus entry. Skip
            # silently so a partial pipeline state does not break the run.
            continue

        original_judgments: dict[str, dict] = dict(mjr.judgments or {})

        # Re-query every provider (or the subset that has a recorded
        # judgment in this case). A provider missing from
        # ``original_judgments`` is skipped — it was never part of this
        # case's panel.
        new_judgments: dict[str, dict] = dict(original_judgments)
        for provider in providers:
            if provider not in original_judgments:
                continue
            my_judgment = original_judgments.get(provider, {}) or {}
            # Skip providers that errored in the original judgment — their
            # error status is meaningful and should not be overwritten.
            if my_judgment.get("coverage_status") == "error":
                continue
            other_judgments = [
                j for p, j in original_judgments.items() if p != provider
            ]

            user_prompt = _build_re_review_user_prompt(
                case=case,
                my_judgment=my_judgment,
                other_judgments=other_judgments,
            )

            llm = get_llm(provider)
            new_judgment = _call_re_review_api(
                llm=llm,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                agent_name=provider,
            )
            # Always stamp the provider name explicitly — guards against an
            # LLM echoing a different ``agent_name`` in its JSON.
            new_judgment["agent_name"] = provider
            new_judgments[provider] = new_judgment

        mjr.judgments = new_judgments

        re_review_meta.append(
            {
                "case_id": case_id,
                "original_judgments": original_judgments,
                "re_review_judgments": new_judgments,
            }
        )
        re_reviewed_count += 1

        print(
            f"  [re-review] {case_id}: re-reviewed "
            f"{len([p for p in providers if p in original_judgments])} providers",
            file=sys.stderr,
        )

    if re_reviewed_count == 0:
        # No actionable cases — write nothing and leave files untouched.
        return multi_out, set()

    # Persist audit trail.
    re_review_path = output_dir / "re_review_results.json"
    re_review_path.write_text(
        json.dumps(
            {
                "total_low_confidence_cases": len(low_confidence_ids),
                "re_reviewed_cases": re_reviewed_count,
                "results": re_review_meta,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Persist updated multi-judge output so downstream steps see the
    # post-review state.
    multi_path = output_dir / "multi_judge_results.json"
    multi_path.write_text(
        multi_out.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    re_reviewed_ids = {entry["case_id"] for entry in re_review_meta}
    return multi_out, re_reviewed_ids