# -*- coding: utf-8 -*-
"""Forward AI three-state coverage review (Stage C7).

For blocks the deterministic layer could not settle (needs_ai=True), call a
SINGLE LLM (FORWARD_REVIEW_PROVIDER, default deepseek) to classify whether the
block's candidate HLRs describe the same EoICD business object.

Three states:
  covered         — at least one candidate HLR describes this object
  not_same_object — none of the candidates describe it (=> likely 漏写)
  unconfirmed     — cannot determine (=> keep "possible" for human review)

No reverse-style multi-judge / consensus: forward uses a single model. The raw
results feed the final coverage consolidation (C8). Any call/parse failure
falls back to "unconfirmed" + error (never a false covered/uncovered).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.v4.config import (
    FORWARD_AI_CANDIDATE_TOP_N,
    FORWARD_AI_MAX_INFLIGHT,
    FORWARD_REVIEW_PROVIDER,
)
from app.v4.llm import get_llm
from app.v4.models import (
    ForwardAIReviewOutput,
    ForwardAIReviewResult,
    ForwardBlocksOutput,
    ForwardCandidatesOutput,
    ForwardCoverageOutput,
    ForwardCoverageResult,
    ForwardDeterministicOutput,
    ForwardDeterministicResult,
    ForwardICDBlock,
    ForwardScopeOutput,
    HLRIdentityIndex,
)
from app.v4.prompts import load_prompt


def _extract_json(text: str) -> str:
    """Extract a JSON object from LLM output, with light truncation repair."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if text.startswith("{"):
        # Close unterminated string + missing braces.
        in_string = False
        escape = False
        for ch in text:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
        if in_string:
            text += '"'
        text += "}" * (text.count("{") - text.count("}"))
    return text


def _build_user_prompt(
    block: ForwardICDBlock,
    candidate_ids: list[str],
    hlr_content: dict[str, str],
    index: HLRIdentityIndex,
) -> str:
    """Serialize one block + its candidate HLRs as the review user prompt."""
    ident = block.identity
    parts: list[str] = []

    parts.append("## EoICD 业务对象")
    parts.append(f"- 对象 ID: {block.business_object_id}")
    parts.append(f"- 协议类型: {ident.protocol}")
    if ident.label:
        parts.append(f"- Label 号: L{ident.label}")
    if ident.signal_family:
        parts.append(f"- 信号族: {ident.signal_family}")
    if ident.signal:
        parts.append(f"- 信号名: {ident.signal}")
    if ident.port:
        parts.append(f"- 端口: {ident.port}")
    if ident.message:
        parts.append(f"- 消息: {ident.message}")
    if block.devices:
        parts.append(f"- 设备: {', '.join(block.devices)}")
    if block.variants:
        parts.append(f"- 通道变体: {', '.join(block.variants)}")
    if block.aliases:
        parts.append(f"- 别名: {', '.join(block.aliases)}")
    parts.append("")

    parts.append("## 候选 HLR（请逐条判断是否描述了上述对象）")
    for i, hid in enumerate(candidate_ids, 1):
        content = hlr_content.get(hid, "")
        entry = index.entries.get(hid)
        direction = entry.direction if entry else ""
        parts.append(f"### 候选 {i}: {hid}" + (f"（方向 {direction}）" if direction else ""))
        parts.append(content if content else "（无内容）")
        parts.append("")
    parts.append("请输出 JSON 判定。")
    return "\n".join(parts)


def _review_one(
    llm,
    system_prompt: str,
    block: ForwardICDBlock,
    candidate_ids: list[str],
    hlr_content: dict[str, str],
    index: HLRIdentityIndex,
    max_retries: int = 2,
) -> ForwardAIReviewResult:
    """Review one block against its candidate HLRs (never raises)."""
    boid = block.business_object_id
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _build_user_prompt(block, candidate_ids, hlr_content, index)},
    ]

    last_error: str | None = None
    for attempt in range(max_retries + 1):
        try:
            response = llm.chat(messages=messages, temperature=0.1, max_tokens=2048)
            data = json.loads(_extract_json(response["content"]))
            verdict = data.get("review_verdict", "unconfirmed")
            if verdict not in ("covered", "not_same_object", "unconfirmed"):
                verdict = "unconfirmed"
            return ForwardAIReviewResult(
                business_object_id=boid,
                review_verdict=verdict,
                matched_hlr_ids=[h for h in data.get("matched_hlr_ids", []) if h],
                rejected_hlr_ids=[h for h in data.get("rejected_hlr_ids", []) if h],
                confidence=float(data.get("confidence", 0.0)),
                rationale=data.get("rationale", ""),
                error=None,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            last_error = f"parse error: {exc}"
        except Exception as exc:  # noqa: BLE001 — network/API failure
            last_error = f"api error: {type(exc).__name__}: {exc}"

    return ForwardAIReviewResult(
        business_object_id=boid,
        review_verdict="unconfirmed",
        confidence=0.0,
        rationale="",
        error=last_error or "max retries exceeded",
    )


def review_blocks_with_ai(
    blocks: ForwardBlocksOutput,
    deterministic: ForwardDeterministicOutput,
    index: HLRIdentityIndex,
    hlr_content: dict[str, str],
    provider: str = FORWARD_REVIEW_PROVIDER,
    llm=None,
) -> ForwardAIReviewOutput:
    """Run three-state AI review over all needs_ai blocks (C7)."""
    det_map: dict[str, ForwardDeterministicResult] = {
        r.business_object_id: r for r in deterministic.results
    }
    todo = [b for b in blocks.blocks if det_map.get(b.business_object_id) and det_map[b.business_object_id].needs_ai]

    if not todo:
        return ForwardAIReviewOutput(total_reviewed=0, stats={}, results=[])

    llm = llm or get_llm(provider)
    system_prompt = load_prompt("forward_review")

    stats: dict[str, int] = {}
    results: list[ForwardAIReviewResult] = []

    def run(block: ForwardICDBlock) -> ForwardAIReviewResult:
        cand_ids = det_map[block.business_object_id].candidate_hlr_ids
        top_n = cand_ids[:FORWARD_AI_CANDIDATE_TOP_N]
        return _review_one(llm, system_prompt, block, top_n, hlr_content, index)

    with ThreadPoolExecutor(max_workers=FORWARD_AI_MAX_INFLIGHT) as pool:
        futures = {pool.submit(run, b): b for b in todo}
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: r.business_object_id)
    for r in results:
        key = r.error or r.review_verdict
        stats[key] = stats.get(key, 0) + 1

    return ForwardAIReviewOutput(
        total_reviewed=len(results),
        stats=stats,
        results=results,
    )


def consolidate_forward_coverage(
    blocks: ForwardBlocksOutput,
    scope: ForwardScopeOutput | None,
    deterministic: ForwardDeterministicOutput,
    ai_review: ForwardAIReviewOutput | None = None,
) -> ForwardCoverageOutput:
    """Merge deterministic + AI into final per-block coverage results (C8).

    The `not_same_object` AI verdict only concludes `uncovered` when the AI saw
    the FULL candidate set (not truncated) AND explicitly rejected every
    candidate; otherwise it stays `possible`.
    """
    det_map = {r.business_object_id: r for r in deterministic.results}
    ai_map = {r.business_object_id: r for r in (ai_review.results if ai_review else [])}

    results: list[ForwardCoverageResult] = []
    for block in blocks.blocks:
        det = det_map.get(block.business_object_id)

        if block.unsupported:
            results.append(ForwardCoverageResult(
                business_object_id=block.business_object_id,
                analysis_status="unsupported",
                coverage_status="",
            ))
            continue

        if det is None:
            results.append(ForwardCoverageResult(
                business_object_id=block.business_object_id,
                analysis_status="input_error",
                coverage_status="",
            ))
            continue

        # 正向缺陷修正 #4：trace 全候选缺失 → 块级 input_error（无 AI，无 possible/uncovered）。
        if det.rule_level == "input_error":
            results.append(ForwardCoverageResult(
                business_object_id=block.business_object_id,
                analysis_status="input_error",
                coverage_status="",
                candidate_truncated=det.candidate_truncated,
            ))
            continue

        status = det.coverage_status or ""
        source = "rule"
        matched = list(det.matched_hlr_ids)
        ai = ai_map.get(block.business_object_id)

        if det.needs_ai:
            source = "ai"
            if ai is not None and not ai.error:
                if ai.review_verdict == "covered":
                    status = "covered_direct"
                    matched = list(ai.matched_hlr_ids)
                elif ai.review_verdict == "not_same_object":
                    # Only conclude `uncovered` when the AI saw the FULL candidate set
                    # AND explicitly rejected every candidate. Otherwise keep possible.
                    if det.candidate_truncated:
                        status = "possible"
                        matched = []
                    elif set(ai.rejected_hlr_ids) >= set(det.candidate_hlr_ids):
                        status = "uncovered"
                        matched = []
                    else:
                        status = "possible"
                        matched = []
                else:  # unconfirmed -> keep the deterministic "possible"-tier status
                    status = status if status in ("parent_referenced", "possible") else "possible"
            else:
                # AI failed/missing -> stay on the deterministic "possible"-tier status.
                status = status if status in ("parent_referenced", "possible") else "possible"

        results.append(ForwardCoverageResult(
            business_object_id=block.business_object_id,
            analysis_status="supported",
            coverage_status=status,
            matched_hlr_ids=matched,
            evidence=list(det.identity_tokens),
            source=source,
            rule_level=det.rule_level,
            candidate_truncated=det.candidate_truncated,
            referenced_variants=list(block.variants) if status.startswith("covered") else [],
            ai_review=ai,
        ))

    stats: dict[str, int] = {}
    for r in results:
        key = r.analysis_status if r.analysis_status != "supported" else (r.coverage_status or "supported_blank")
        stats[key] = stats.get(key, 0) + 1

    return ForwardCoverageOutput(
        analysis_mode=scope.analysis_mode if scope else blocks.analysis_mode,
        scope_source=scope.scope_source if scope else "",
        stats=stats,
        unsupported=[{"business_object_id": r.business_object_id} for r in results if r.analysis_status == "unsupported"],
        input_errors=list(scope.input_errors) if scope else [],
        results=results,
    )
