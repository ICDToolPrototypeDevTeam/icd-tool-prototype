# -*- coding: utf-8 -*-
"""Forward candidate recall (Stage C5) + deterministic judgment (Stage C6).

Recall (C5) maps each ForwardICDBlock to a ranked list of candidate HLR IDs:

  - trace mode: candidates come from the trace tables (block.trace.candidate_hlr_ids),
    already resolved by C2 — the traceability matrix IS the recall mechanism.
  - full mode:  candidates are recalled from the HLR identity index by scoring
    every HLR against the block's recall tokens (label + family + signal + port).
    Deterministic token overlap ranks ahead of llm_label-only overlap.

Deterministic judgment (C6) then assigns a coverage verdict using ONLY rule-based
evidence (no AI): A429 label match, exact signal token match, parent reference,
generic-word-only match, or no evidence.

Rule hierarchy (strongest first) and the resulting verdict:

  level             condition                                             coverage_status   needs_ai
  ──────────────────────────────────────────────────────────────────────────────────────────────────
  exact_fullname    full family / leaf verbatim (distinctive, non-generic) covered_direct     no
  exact_label       A429 label + specific signal evidence                 covered_direct     no
  exact_signal      >=2 specific token hits, or one distinctive token     covered_direct     no
  parent_referenced label-only or parent(port/message)-only reference     parent_referenced  yes
  generic_signal    only generic terms (STATUS/STATE/VOLTAGE/…) overlap   possible           yes
  weak_signal       a single short (non-distinctive) specific fragment    possible           yes
  trace_only        trace candidates exist but no text overlap            possible           yes
  no_evidence       no candidates at all                                  uncovered          no

Constraints enforced here:
  - a bare A429 label alone can NOT form covered (same Label ≠ all sub-signals);
  - generic terms (STATUS/VOLTAGE/FAULT/SPEED/TEMPERATURE…) can NOT form covered alone;
  - llm_label tokens never enter this judgment (they live in llm_token_index, recall-only).

matched_hlr_ids collects every HLR achieving the strongest level; weaker tiers
only inform the needs_ai routing.
"""

from __future__ import annotations

import re
from collections import Counter

from app.v4.config import FORWARD_AI_CANDIDATE_TOP_N, FORWARD_GENERIC_SIGNAL_TERMS
from app.v4.matching.hlr_identity_index import tokenize_identifier
from app.v4.models import (
    ForwardBlocksOutput,
    ForwardCandidatesOutput,
    ForwardDeterministicOutput,
    ForwardDeterministicResult,
    ForwardICDBlock,
    HLRIdentityEntry,
    HLRIdentityIndex,
    IdentityToken,
)


# ============================================================================
# Candidate recall (C5)
# ============================================================================


def block_recall_tokens(block: ForwardICDBlock) -> list[str]:
    """Recall tokens for one block (uppercase, deduped, noise-filtered).

    Chinese aliases are skipped: the HLR index is English-only (Chinese was
    folded to English via CN_SIGNAL_KEYWORD_MAP at index time), so only English
    tokens are queryable. Shared with deterministic judgment (C6).
    """
    toks: set[str] = set()
    ident = block.identity

    if ident.label:
        toks.add(f"L{ident.label}")
    for field in (ident.signal_family, ident.signal, ident.port, ident.message):
        toks.update(tokenize_identifier(field))
    for alias in block.aliases:
        toks.update(tokenize_identifier(alias))
    return sorted(toks)


def _recall_full_mode(
    block: ForwardICDBlock,
    token_index: dict[str, list[str]],
    llm_token_index: dict[str, list[str]] | None = None,
) -> list[str]:
    """Inverted-index recall over deterministic + llm_label tokens.

    Deterministic token overlap ranks ahead of llm_label-only overlap, so a
    deterministically-covered HLR always sits at the top of the candidate list
    (the AI top-N truncation then sees the strongest candidates first).
    llm_label-only matches still enter the candidate set (recall-only), but the
    deterministic judgment layer treats them as `trace_only` → `possible`.
    """
    llm_token_index = llm_token_index or {}
    det_scores: Counter = Counter()
    llm_scores: Counter = Counter()
    for tok in block_recall_tokens(block):
        for hlr_id in token_index.get(tok, []):
            det_scores[hlr_id] += 1
        for hlr_id in llm_token_index.get(tok, []):
            llm_scores[hlr_id] += 1

    scored: dict[str, tuple[int, int]] = {}
    for hid in set(det_scores) | set(llm_scores):
        scored[hid] = (det_scores.get(hid, 0), llm_scores.get(hid, 0))
    if not scored:
        return []
    # Rank: deterministic overlap desc, then llm overlap desc, then hlr_id asc.
    return [
        hid for hid, _ in sorted(
            scored.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0])
        )
    ]


def build_forward_candidates(
    blocks: ForwardBlocksOutput,
    index: HLRIdentityIndex,
) -> ForwardCandidatesOutput:
    """Build the block → candidate HLR id mapping (C5)."""
    candidates: dict[str, list[str]] = {}
    for block in blocks.blocks:
        if block.unsupported:
            candidates[block.business_object_id] = []
            continue
        if blocks.analysis_mode == "trace":
            trace = block.trace
            if trace:
                # 正向缺陷修正 #4：只让上传 HLR 文档中存在的（analyzable）候选参与
                # 匹配/AI；raw candidate_hlr_ids（含缺失）保留在 block.trace 供审计。
                missing = set(trace.missing_hlr_ids)
                candidates[block.business_object_id] = [
                    h for h in trace.candidate_hlr_ids if h not in missing
                ]
            else:
                candidates[block.business_object_id] = []
        else:
            candidates[block.business_object_id] = _recall_full_mode(
                block, index.token_index, index.llm_token_index
            )
    return ForwardCandidatesOutput(
        total_blocks=len(candidates),
        candidates=candidates,
    )


# ============================================================================
# Deterministic judgment (C6)
# ============================================================================

# Strongest → weakest (higher wins). Only used for ranking; verdict mapping is
# handled separately below.
_RANK = {
    "exact_label": 6,
    "exact_fullname": 5,
    "exact_signal": 4,
    "parent_referenced": 3,
    "generic_signal": 2,
    "weak_signal": 1,
    "none": 0,
}

_COVERED_LEVELS = {"exact_label", "exact_fullname", "exact_signal"}


def _signal_tokens(block: ForwardICDBlock) -> set[str]:
    """Signal-side tokens: family + leaf + aliases (NOT port/message)."""
    ident = block.identity
    toks: set[str] = set()
    for field in (ident.signal_family, ident.signal):
        toks.update(tokenize_identifier(field))
    for alias in block.aliases:
        toks.update(tokenize_identifier(alias))
    return toks


def _parent_tokens(block: ForwardICDBlock) -> set[str]:
    """Parent-side tokens: port + message (interface identity)."""
    ident = block.identity
    toks: set[str] = set()
    for field in (ident.port, ident.message):
        toks.update(tokenize_identifier(field))
    return toks


def _is_distinctive(tok: str) -> bool:
    """A token is 'distinctive' (strong enough to support `covered` alone) if it
    is a compound identifier (contains _ or /) or a long whole word. A short
    plain word (e.g. FLOW, RPM, CPA, PACK) is too common to prove the HLR
    describes the object, so it only supports `possible` (weak_signal).
    """
    return ("_" in tok) or ("/" in tok) or len(tok) >= 6


def _device_tokens(block: ForwardICDBlock) -> set[str]:
    """Device-side tokens: identity.device + all block.devices."""
    toks: set[str] = set()
    devices = list(block.devices) if block.devices else []
    if block.identity.device and block.identity.device not in devices:
        devices.append(block.identity.device)
    for d in devices:
        toks.update(tokenize_identifier(d))
    return toks


def _is_device_like(tok: str) -> bool:
    """A token looks like a device identifier if it carries digits (AFTEFAN1,
    FWDBFAN2, DS17, pi825) — digits distinguish one physical/logical device
    instance from another."""
    return len(tok) >= 4 and bool(re.search(r"\d", tok))


_ANALOG_DISCRETE_CATEGORIES = {"模拟量", "离散量"}


def _protocol_conflict(block_protocol: str, entry_category: str) -> bool:
    """True when the block protocol hard-conflicts with the HLR signal_category.

    正向缺陷修正 #1：协议冲突必须阻断 covered。规则（只判明确矛盾，不判模糊）：
      - A429 block vs 模拟量/离散量 HLR → 冲突（HLR 描述的是非 A429 信号）；
      - A825 block vs A429显式/模拟量/离散量 HLR → 冲突；
      - Analog block vs A429显式/离散量 HLR → 冲突；
      - Discrete block vs A429显式/模拟量 HLR → 冲突。
    "A429隐式"（仅提及总线/通信）不参与冲突判定，保持中性。
    """
    p = (block_protocol or "").upper()
    c = entry_category or ""
    if p == "A429":
        return c in _ANALOG_DISCRETE_CATEGORIES
    if p == "A825":
        return c in ("A429显式", "模拟量", "离散量")
    if p == "ANALOG":
        return c in ("A429显式", "离散量")
    if p == "DISCRETE":
        return c in ("A429显式", "模拟量")
    return False


def _device_conflict(block: ForwardICDBlock, entry: HLRIdentityEntry) -> bool:
    """True when block and HLR both mention explicit, disjoint device identifiers.

    Only applies to non-A429 (device is part of the identity there). A429 blocks
    are gated by Label, not device, so device noise must not override a Label hit.
    """
    block_devices = {t for t in _device_tokens(block) if _is_device_like(t)}
    hlr_devices = {t for t in entry.signal_tokens if _is_device_like(t)}
    if not block_devices or not hlr_devices:
        return False
    return not (block_devices & hlr_devices)


def _match_level(
    block: ForwardICDBlock,
    entry: HLRIdentityEntry,
    generic_terms: set[str],
) -> str:
    """Return the strongest match level of this HLR entry against the block.

    Rules (strongest first) are deliberately conservative so that a `covered`
    conclusion requires real signal-level evidence, never just:
      - a bare A429 Label number (same Label ≠ all sub-signals covered), or
      - a single short common fragment (FLOW/PACK/CPA…), or
      - a generic term alone (STATUS/VOLTAGE/FAULT/SPEED/TEMPERATURE/ALTITUDE/
        OVERHEAT…), or
      - an AI (llm_label) token alone (those never enter entry.signal_tokens).

    正向缺陷修正 #1：进入等级判定前先做"硬冲突"门控 —— Label 冲突 / 协议冲突 /
    设备冲突（仅非 A429）直接降级为 not_same_object，阻止 covered 误判；并且
    exact_signal（单 token）必须结合 label/device/port/message 之一才能支撑 covered。

    Only entry.signal_tokens (deterministic), entry.labels (regex) and
    entry.signal_category are read; llm_label_tokens are intentionally ignored.
    """
    ident = block.identity
    label_tok = f"L{ident.label}" if ident.label else ""
    family = ident.signal_family.upper()
    leaf = ident.signal.upper()

    hlr_tokens = set(entry.signal_tokens)
    hlr_labels = {l.upper() for l in entry.labels}

    # ── Hard conflict gating (demote to not_same_object) ──
    # 1. Label conflict: block has explicit Label; HLR has explicit Labels that
    #    do NOT contain it → HLR describes a different A429 word.
    if label_tok and hlr_labels and label_tok not in hlr_labels:
        return "not_same_object"
    # 2. Protocol conflict: A429 vs non-A429 signal categories.
    if _protocol_conflict(ident.protocol, entry.signal_category):
        return "not_same_object"
    # 3. Device conflict (non-A429 only — device is primary identity there).
    if ident.protocol.upper() != "A429" and _device_conflict(block, entry):
        return "not_same_object"

    label_hit = bool(label_tok and label_tok in hlr_labels)

    # 1. Full family / leaf verbatim, and distinctive (non-generic + compound/long).
    family_hit = bool(family and family in hlr_tokens and family not in generic_terms and _is_distinctive(family))
    leaf_hit = bool(leaf and leaf in hlr_tokens and leaf not in generic_terms and _is_distinctive(leaf))
    if family_hit or leaf_hit:
        return "exact_fullname"

    # 2. Signal token overlap (specific vs generic) + corroborating context.
    signal_toks = _signal_tokens(block)
    specific = {t for t in signal_toks if t not in generic_terms}
    generic = {t for t in signal_toks if t in generic_terms}
    s_overlap = specific & hlr_tokens
    g_overlap = generic & hlr_tokens
    parent = {t for t in _parent_tokens(block) if t not in generic_terms}
    p_overlap = parent & hlr_tokens
    d_overlap = _device_tokens(block) & hlr_tokens

    # 3. Label + specific signal evidence → covered (label alone is NOT enough).
    if label_hit and s_overlap:
        return "exact_label"

    # 4. Specific signal overlap → covered only when unambiguous:
    #    - >=2 distinct specific tokens, OR
    #    - a single distinctive token corroborated by label/device/port/message.
    #    A lone distinctive token with no context (e.g. ALTITUDE/CHANGE) → weak.
    if s_overlap:
        if len(s_overlap) >= 2:
            return "exact_signal"
        only = next(iter(s_overlap))
        if _is_distinctive(only) and (label_hit or p_overlap or d_overlap):
            return "exact_signal"
        return "weak_signal"

    # 5. Label-only or parent-only reference → parent_referenced (needs AI).
    if label_hit or p_overlap:
        return "parent_referenced"

    # 6. Generic-only overlap → possible (generic terms can't form covered alone).
    if g_overlap:
        return "generic_signal"

    return "none"


def _coverage_status_for(level: str) -> str:
    if level in _COVERED_LEVELS:
        return "covered_direct"
    if level == "parent_referenced":
        return "parent_referenced"
    if level in ("generic_signal", "weak_signal", "trace_only", "not_same_object"):
        return "possible"
    return "uncovered"  # no_evidence


def _needs_ai_for(level: str) -> bool:
    return level in ("parent_referenced", "generic_signal", "weak_signal", "trace_only", "not_same_object")


def _evidence_tokens(block: ForwardICDBlock, matched_entries: list[HLRIdentityEntry]) -> list[IdentityToken]:
    """Collect the label + specific tokens that actually matched."""
    ident = block.identity
    label_tok = f"L{ident.label}" if ident.label else ""
    hlr_labels = {l.upper() for e in matched_entries for l in e.labels}
    hlr_tokens = {t for e in matched_entries for t in e.signal_tokens}

    evidence: list[IdentityToken] = []
    if label_tok and label_tok in hlr_labels:
        evidence.append(IdentityToken(value=label_tok, source="literal"))
    for tok in block_recall_tokens(block):
        if tok in hlr_tokens and tok not in FORWARD_GENERIC_SIGNAL_TERMS:
            evidence.append(IdentityToken(value=tok, source="regex"))
    return evidence


def build_deterministic_results(
    blocks: ForwardBlocksOutput,
    candidates: ForwardCandidatesOutput,
    index: HLRIdentityIndex,
) -> ForwardDeterministicOutput:
    """Run deterministic coverage judgment over all blocks (C6)."""
    generic_terms = {t.upper() for t in FORWARD_GENERIC_SIGNAL_TERMS}
    results: list[ForwardDeterministicResult] = []

    for block in blocks.blocks:
        cand_ids = candidates.candidates.get(block.business_object_id, [])

        if block.unsupported:
            results.append(ForwardDeterministicResult(
                business_object_id=block.business_object_id,
                rule_level="no_evidence",
                coverage_status="",
                candidate_hlr_ids=cand_ids,
                needs_ai=False,
            ))
            continue

        # 正向缺陷修正 #4：trace 模式下 raw 候选非空但全缺失于上传 HLR → input_error。
        # 不参与匹配/AI，不计 uncovered/possible（最终 analysis_status=input_error）。
        if block.trace and block.trace.candidate_hlr_ids and not cand_ids:
            results.append(ForwardDeterministicResult(
                business_object_id=block.business_object_id,
                rule_level="input_error",
                coverage_status="",
                candidate_hlr_ids=list(block.trace.candidate_hlr_ids),
                candidate_truncated=False,
                needs_ai=False,
            ))
            continue

        if not cand_ids:
            results.append(ForwardDeterministicResult(
                business_object_id=block.business_object_id,
                rule_level="no_evidence",
                coverage_status="uncovered",
                needs_ai=False,
            ))
            continue

        # Rank every candidate HLR, keep the strongest level + its HLRs.
        # Hard-conflict candidates (not_same_object) are excluded from
        # matched_hlr_ids; if every candidate conflicts, the block demotes to
        # not_same_object → possible (needs AI).
        best_level = "none"
        best_entries: list[HLRIdentityEntry] = []
        conflict_ids: list[str] = []
        for hid in cand_ids:
            entry = index.entries.get(hid)
            if entry is None:
                continue
            level = _match_level(block, entry, generic_terms)
            if level == "not_same_object":
                conflict_ids.append(hid)
                continue
            if _RANK[level] > _RANK[best_level]:
                best_level = level
                best_entries = [entry]
            elif _RANK[level] == _RANK[best_level] and level != "none":
                best_entries.append(entry)

        # trace_only: candidates exist but none produced any text overlap.
        if best_level == "none":
            if conflict_ids:
                best_level = "not_same_object"
            else:
                best_level = "trace_only"

        matched_ids = [e.hlr_id for e in best_entries]
        status = _coverage_status_for(best_level)

        results.append(ForwardDeterministicResult(
            business_object_id=block.business_object_id,
            rule_level=best_level,
            coverage_status=status,
            matched_hlr_ids=matched_ids,
            candidate_hlr_ids=cand_ids,
            candidate_truncated=len(cand_ids) > FORWARD_AI_CANDIDATE_TOP_N,
            needs_ai=_needs_ai_for(best_level),
            identity_tokens=_evidence_tokens(block, best_entries),
        ))

    stats: dict[str, int] = {}
    for r in results:
        stats[r.coverage_status or "unsupported"] = (
            stats.get(r.coverage_status or "unsupported", 0) + 1
        )

    return ForwardDeterministicOutput(
        total_blocks=len(results),
        stats=stats,
        results=results,
    )
