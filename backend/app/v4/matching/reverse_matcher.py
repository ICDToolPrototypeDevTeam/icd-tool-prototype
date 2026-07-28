# -*- coding: utf-8 -*-
"""Reverse matcher: HLR → EoICD ICD blocks via 4-path classification.

Two-stage matching per Path 1:
  Stage 1: Label-prefix coarse filter (high recall) — works on block keys
  Stage 2: 6-dimension block-level scoring → Top-K (high precision)

Matching unit: ICDBlock (signal family), not individual SignalProfile.
An ICDBlock groups all channel/bus variants of the same signal, so the
AI judge sees complete signal definitions rather than fragmented profiles.

Script responsibility: MATCHING only — find which EoICD blocks correspond
to each HLR.  Consistency judgment is the Agent's job (semantic_judge.py).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.v4.matching.entry_filter import should_keep
from app.v4.matching.eoicd_enricher import _resolve_aliases, _get_synonym_lookup, _tokenize_name
from app.v4.config import CN_SIGNAL_KEYWORD_MAP
from app.v4.matching.hlr_classifier import (
    classify_hlr,
    extract_labels,
    extract_bit_fields,
    extract_sdi,
    extract_direction,
)
from app.v4.matching.signal_profiler import build_profiles, build_blocks, SignalProfile, ICDBlock
from app.v4.models import (
    HLRCoverageResult,
    ReverseMatchOutput,
    HLRLabel,
    HLRRequirement,
    EoICDRequirement,
)

# ── Scoring constants ────────────────────────────────────────────────

_TOP_K = 20  # max blocks per HLR passed to Agent

# Score tiering: blocks are classified into 3 tiers based on total score
# and the number of active (non-zero) dimensions.
#   "已匹配"   → goes to AI for diff judgment
#   "待确定"   → flagged for human review (partial dimension match)
#   "未匹配"   → filtered out (too weak)
_HIGH_SCORE_THRESHOLD = 25   # total >= this → "已匹配" candidate
_MIN_ACTIVE_DIMS = 2        # need at least this many non-zero dimensions
_MIN_SCORE_THRESHOLD = 12   # below this → "未匹配"


def _has_chinese(text: str) -> bool:
    """Check if text contains any Chinese characters (CJK Unified Ideographs)."""
    return any('一' <= c <= '鿿' for c in text)


@dataclass
class HLRMatchProfile:
    """Enriched HLR requirement for reverse matching."""

    hlr_id: str
    content: str

    signal_category: str = ""
    labels: list[str] = field(default_factory=list)
    bit_fields: list[dict] = field(default_factory=list)
    sdi_value: str = ""
    extracted_direction: str = ""

    bus_types: set[str] = field(default_factory=set)
    devices: set[str] = field(default_factory=set)
    signal_keywords: set[str] = field(default_factory=set)
    attr_categories: set[str] = field(default_factory=set)
    direction_keywords: set[str] = field(default_factory=set)
    cn_english_tokens: set[str] = field(default_factory=set)


def build_hlr_profile(hlr: HLRRequirement, lbl: HLRLabel) -> HLRMatchProfile:
    """Build an enriched HLR profile from requirement + AI label + classifier extraction."""
    # Pre-compute Chinese→English token injection
    cn_english_tokens: set[str] = set()
    content_text = hlr.content or ""
    for kw in lbl.signal_keywords_set:
        if _has_chinese(kw):
            for cn_word, en_tokens in CN_SIGNAL_KEYWORD_MAP.items():
                if cn_word in kw:
                    cn_english_tokens.update(t.lower() for t in en_tokens)
    if _has_chinese(content_text):
        for cn_word, en_tokens in CN_SIGNAL_KEYWORD_MAP.items():
            if cn_word in content_text:
                cn_english_tokens.update(t.lower() for t in en_tokens)

    return HLRMatchProfile(
        hlr_id=hlr.requirement_id,
        content=hlr.content,

        signal_category=classify_hlr(hlr.content),
        labels=extract_labels(hlr.content),
        bit_fields=extract_bit_fields(hlr.content),
        sdi_value=extract_sdi(hlr.content),
        extracted_direction=extract_direction(hlr.content),

        bus_types=lbl.bus_types_set,
        devices=lbl.devices_set,
        signal_keywords=lbl.signal_keywords_set,
        attr_categories=lbl.attr_categories_set,
        direction_keywords=lbl.direction_keywords_set,
        cn_english_tokens=cn_english_tokens,
    )


# ── Keyword specificity classification ──────────────────────────────

_SPECIFICITY_DIGIT_SUFFIX_RE = re.compile(r'^(?P<alpha>[A-Za-z_]+?)(?P<digit>\d+)$')


def _classify_keyword_specificity(kw: str) -> str:
    """Classify an HLR signal keyword by specificity to control matching.

    Returns 'precise' (long alpha + digit, like AFTEFAN1),
            'moderate' (shorter alpha + digit, like BBSOV1),
            'generic' (no digit suffix or very short).
    """
    kw_clean = kw.strip()
    m = _SPECIFICITY_DIGIT_SUFFIX_RE.match(kw_clean)
    if not m:
        return 'generic'
    alpha_len = len(m.group('alpha'))
    total_len = len(kw_clean)
    if alpha_len >= 5 and total_len >= 7:
        return 'precise'
    elif alpha_len >= 3 or total_len >= 5:
        return 'moderate'
    return 'generic'


def _boundary_match(kw: str, text: str) -> bool:
    """Check if keyword matches at token boundaries in text.

    Prevents cross-contamination: 'AFTEFAN1' matches 'AFTEFAN1_HW_FAULT'
    but NOT 'AFTEFAN2_HW_FAULT' (different numeric suffix).
    """
    kw_lower = kw.lower()
    text_lower = text.lower()
    if text_lower == kw_lower:
        return True
    if text_lower.startswith(kw_lower + '_'):
        return True
    if text_lower.endswith('_' + kw_lower):
        return True
    idx = text_lower.find(kw_lower)
    if idx == -1:
        return False
    # Character before kw must be _ or lowercase→uppercase boundary (CamelCase)
    if idx > 0:
        before = text_lower[idx - 1]
        if before != '_' and not (before.islower() and text_lower[idx].isupper()):
            return False
    # Character after kw must be _, digit (not same-numeric-suffix), or uppercase (boundary)
    after_idx = idx + len(kw_lower)
    if after_idx < len(text_lower):
        after = text_lower[after_idx]
        if after != '_' and not after.isdigit() and not after.isupper():
            return False
    return True


# Regex to find alphanumeric components that may be precise signal references
_PRECISE_COMPONENT_RE = re.compile(r'[A-Za-z]+\d+')


def _has_named_component(text: str) -> bool:
    """Check if text contains any identifiable signal/device reference (alpha + digit).

    Used to prevent token-decomposition of names like '通道AFTEFAN1' or '通道BBSOV1'
    whose decomposed tokens ('aftefan', 'bbsov') would substring-match unrelated blocks.
    Returns True for both 'precise' and 'moderate' specificity components.
    """
    for match in _PRECISE_COMPONENT_RE.finditer(text):
        if _classify_keyword_specificity(match.group()) in ('precise', 'moderate'):
            return True
    return False


# ── Block-level scoring ──────────────────────────────────────────


def _score_block(
    hlr_prof: HLRMatchProfile,
    block: ICDBlock,
) -> tuple[int, dict[str, int]]:
    """Score a single ICDBlock against an HLR across 6 dimensions.

    Returns (total_score, dimension_scores).
    """
    syn = _get_synonym_lookup()
    dims: dict[str, int] = {}

    # ── 1. Signal name match (30pts) ──
    # Use signal_family (cleaner than leaf name — no channel prefixes)
    family_lower = block.signal_family.lower()
    family_tokens: set[str] = {family_lower}
    family_tokens.update(_tokenize_name(family_lower))
    for tok in list(family_tokens):
        family_tokens.update(_resolve_aliases(tok, syn))

    hlr_tokens: set[str] = set()
    for kw in hlr_prof.signal_keywords:
        kw_lower = kw.lower()
        hlr_tokens.add(kw_lower)
        if not _has_named_component(kw_lower):
            hlr_tokens.update(_tokenize_name(kw_lower))
        hlr_tokens.update(_resolve_aliases(kw_lower, syn))

    for dev in hlr_prof.devices:
        dev_lower = dev.lower()
        hlr_tokens.add(dev_lower)
        if not _has_named_component(dev_lower):
            hlr_tokens.update(_tokenize_name(dev_lower))
        hlr_tokens.update(_resolve_aliases(dev_lower, syn))

    # Inject Chinese→English mapped tokens
    for token in hlr_prof.cn_english_tokens:
        hlr_tokens.add(token)
        hlr_tokens.update(_resolve_aliases(token, syn))

    # Token overlap (set intersection)
    signal_overlap = family_tokens & hlr_tokens

    # Substring / boundary matching with specificity control per keyword
    for kw_raw in hlr_prof.signal_keywords:
        kw = kw_raw.lower()
        if len(kw) < 3:
            continue
        specificity = _classify_keyword_specificity(kw)
        if specificity == 'precise':
            if _boundary_match(kw, family_lower):
                signal_overlap.add(kw)
        else:
            if kw in family_lower or family_lower in kw:
                signal_overlap.add(kw)

    # Device substring match with specificity control
    for dev in hlr_prof.devices:
        dev_lower = dev.lower()
        if len(dev_lower) < 3:
            continue
        if _has_named_component(dev_lower):
            # Extract precise components and boundary-match each individually
            for match in _PRECISE_COMPONENT_RE.finditer(dev_lower):
                component = match.group().lower()
                specificity = _classify_keyword_specificity(component)
                if specificity == 'precise':
                    if _boundary_match(component, family_lower):
                        signal_overlap.add(component)
                else:
                    # Moderate/generic: substring match (e.g. 'bbsov1' in 'fwdbbbsov1_fc')
                    if component in family_lower or family_lower in component:
                        signal_overlap.add(component)
        else:
            if dev_lower in family_lower or family_lower in dev_lower:
                signal_overlap.add(dev_lower)

    dims["signal_name"] = min(30, len(signal_overlap) * 8)

    # ── 2. Direction match (15pts) ──
    hlr_dir = hlr_prof.extracted_direction
    block_dir = block.direction
    if hlr_dir:
        if hlr_dir in block_dir:
            dims["direction"] = 15
        elif block_dir == "发送/接收":
            dims["direction"] = 8
        else:
            dims["direction"] = 0
    else:
        dims["direction"] = 0

    # ── 3. Bit field match (20pts) ──
    # Check bit fields against profile-level attributes first,
    # then fall back to block.sub_signals for multi-bit labels
    # where profile-level "first value wins" loses sub-signal offsets.
    bit_score = 0
    for bf in hlr_prof.bit_fields:
        bf_offset = str(bf.get("offset", ""))
        bf_size = str(bf.get("size", ""))
        if not bf_offset:
            continue

        # Pass 1: profile-level attributes
        for prof in block.profiles:
            prof_offset = str(prof.attributes.get("BitOffsetWithinDS", {}).get("value", ""))
            prof_size_attr = str(prof.attributes.get("ParameterSize", {}).get("value", ""))
            if not prof_offset:
                continue
            if prof_offset == bf_offset and prof_size_attr == bf_size:
                bit_score = 20
                break
            elif prof_offset == bf_offset:
                bit_score = max(bit_score, 12)
            elif prof_size_attr == bf_size:
                bit_score = max(bit_score, 8)
        if bit_score == 20:
            break

        # Pass 2: block.sub_signals (covers bits lost by "first value wins")
        for ss in block.sub_signals:
            ss_offset = str(ss.get("bit_offset", ""))
            ss_size = str(ss.get("size", ""))
            if not ss_offset:
                continue
            if ss_offset == bf_offset and ss_size == bf_size:
                bit_score = 20
                break
            elif ss_offset == bf_offset:
                bit_score = max(bit_score, 12)
            elif ss_size == bf_size:
                bit_score = max(bit_score, 8)
        if bit_score == 20:
            break
    dims["bit_field"] = bit_score

    # ── 4. SDI match (15pts) ──
    hlr_sdi = hlr_prof.sdi_value.strip()
    dims["sdi"] = 0
    if hlr_sdi:
        for prof in block.profiles:
            prof_sdi = str(prof.attributes.get("SDIExpected", {}).get("value", ""))
            if prof_sdi and hlr_sdi == prof_sdi:
                dims["sdi"] = 15
                break

    # ── 5. Data type match (10pts) ──
    dims["data_type"] = 0
    cat = hlr_prof.signal_category
    content_lower = hlr_prof.content.lower()
    for prof in block.profiles:
        prof_dtype = str(prof.attributes.get("DataFormatType", {}).get("value", "")).strip().upper()
        if cat == "离散量" and prof_dtype == "DIS":
            dims["data_type"] = 10
            break
        elif cat == "模拟量" and prof_dtype.startswith("BNR"):
            dims["data_type"] = 10
            break
        elif cat in ("A429显式", "A429隐式"):
            dis_hints = ("离散", "discrete", "状态", "故障", "开关", "failed", "fault", "status", "cmd")
            bnr_hints = ("模拟", "analog", "数值", "温度", "压力", "速度", "转速", "temp", "speed", "current")
            if prof_dtype == "DIS" and any(w in content_lower for w in dis_hints):
                dims["data_type"] = 10
                break
            elif prof_dtype.startswith("BNR") and any(w in content_lower for w in bnr_hints):
                dims["data_type"] = 10
                break
            elif prof_dtype in ("DIS",) or prof_dtype.startswith("BNR"):
                dims["data_type"] = 5

    # ── 6. Device / Bus match (10pts) ──
    hlr_devices_expanded: set[str] = set()
    for dev in hlr_prof.devices:
        dev_lower = dev.lower()
        hlr_devices_expanded.add(dev_lower)
        hlr_devices_expanded.update(_resolve_aliases(dev_lower, syn))
        for token in _tokenize_name(dev_lower):
            hlr_devices_expanded.add(token)

    dev_overlap = block.device_tokens_set & hlr_devices_expanded
    bus_match = bool(
        block.bus_aliases_set & hlr_prof.bus_types
        or block.bus_types & hlr_prof.bus_types
    )
    dims["device_bus"] = (5 if bus_match else 0) + min(5, len(dev_overlap) * 2)

    total = sum(dims.values())
    return total, dims


def _apply_hard_gates(
    hlr_prof: HLRMatchProfile,
    scored: list[tuple[int, dict[str, int], ICDBlock]],
) -> list[tuple[int, dict[str, int], ICDBlock]]:
    """Remove blocks that fail definitive-contradiction gates.

    These gates are conservative — they only fire when the evidence is
    unambiguous.  Removing a correct block is worse than keeping noise,
    so each gate has a high bar for activation.
    """
    hlr_dir = hlr_prof.extracted_direction.strip()
    hlr_sdi = hlr_prof.sdi_value.strip()
    cat = hlr_prof.signal_category

    # Only apply direction gate when direction extraction is unambiguous
    # AND not bidirectional (发送/接收 means both are plausible).
    dir_gate_active = hlr_dir in ("发送", "接收")

    filtered: list[tuple[int, dict[str, int], ICDBlock]] = []
    for total, dims, block in scored:
        # —— Gate 1: Direction contradiction ——
        if dir_gate_active:
            block_dir = block.direction
            if hlr_dir == "发送" and block_dir == "接收":
                continue
            if hlr_dir == "接收" and block_dir == "发送":
                continue

        # —— Gate 2: SDI contradiction ——
        # Only gate when block has an explicit SDI but none match HLR's SDI.
        if hlr_sdi:
            block_has_sdi = False
            block_sdi_matches = False
            for prof in block.profiles:
                prof_sdi = str(prof.attributes.get("SDIExpected", {}).get("value", ""))
                if prof_sdi:
                    block_has_sdi = True
                    if hlr_sdi == prof_sdi:
                        block_sdi_matches = True
                        break
            if block_has_sdi and not block_sdi_matches:
                continue

        # —— Gate 3: Bus contradiction (non-A429 paths only) ——
        # For semantic paths without Label constraint, bus type must overlap.
        if cat in ("模拟量", "离散量"):
            bus_overlap = (
                block.bus_aliases_set & hlr_prof.bus_types
                or block.bus_types & hlr_prof.bus_types
            )
            if not bus_overlap:
                continue

        filtered.append((total, dims, block))

    return filtered


def _filter_sn_zero_within_label(
    scored: list[tuple[int, dict[str, int], ICDBlock]],
) -> list[tuple[int, dict[str, int], ICDBlock]]:
    """Remove sn=0 blocks from labels that already have sn>0 blocks.

    When a label has blocks with positive signal_name scores, the sn=0
    blocks under the same label are almost certainly noise (e.g. FWDBBSOV2
    mixed in with FWDBBSOV1 matches).  Labels where ALL blocks have sn=0
    are kept as-is — no better alternative exists.
    """
    from collections import defaultdict
    by_label: dict[str, list[tuple[int, dict[str, int], ICDBlock]]] = defaultdict(list)
    for total, dims, block in scored:
        by_label[block.label or block.block_key].append((total, dims, block))

    result: list[tuple[int, dict[str, int], ICDBlock]] = []
    for entries in by_label.values():
        has_sn_positive = any(d.get("signal_name", 0) > 0 for _, d, _ in entries)
        if has_sn_positive:
            result.extend((t, d, b) for t, d, b in entries if d.get("signal_name", 0) > 0)
        else:
            result.extend(entries)

    result.sort(key=lambda x: x[0], reverse=True)
    return result


def _score_and_rank_blocks(
    hlr_prof: HLRMatchProfile,
    candidates: list[ICDBlock],
) -> list[tuple[int, dict[str, int], ICDBlock]]:
    """Score blocks, apply hard gates, sort desc, return top results."""
    scored: list[tuple[int, dict[str, int], ICDBlock]] = []
    for block in candidates:
        total, dims = _score_block(hlr_prof, block)
        if total >= _MIN_SCORE_THRESHOLD:
            scored.append((total, dims, block))
    scored.sort(key=lambda x: x[0], reverse=True)
    scored = _apply_hard_gates(hlr_prof, scored)
    return scored


# ── Path-routing functions ──────────────────────────────────────────


def _match_path1_label(
    hlr_prof: HLRMatchProfile,
    block_index: dict[str, ICDBlock],
) -> list[tuple[int, dict[str, int], ICDBlock]]:
    """Path 1: A429显式 — Stage 1 label-prefix filter → Stage 2 block scoring."""
    candidates: list[ICDBlock] = []

    for lbl in hlr_prof.labels:
        clean_label = lbl.strip().upper()
        if not clean_label.startswith("L"):
            clean_label = f"L{clean_label}"
        prefix = clean_label + "/"
        for key, block in block_index.items():
            if key.startswith(prefix):
                candidates.append(block)

    return _score_and_rank_blocks(hlr_prof, candidates)


def _match_path_semantic(
    hlr_prof: HLRMatchProfile,
    block_index: dict[str, ICDBlock],
) -> list[tuple[int, dict[str, int], ICDBlock]]:
    """Paths 2/3/4: score all non-label blocks with optional bus filter."""
    candidates: list[ICDBlock] = []
    for key, block in block_index.items():
        # Skip label-based blocks (those go through Path 1)
        if block.label:
            continue
        # Bus filter
        if not (
            block.bus_aliases_set & hlr_prof.bus_types
            or block.bus_types & hlr_prof.bus_types
        ):
            if hlr_prof.signal_category == "A429隐式":
                continue  # Path 4 requires bus match
        candidates.append(block)

    return _score_and_rank_blocks(hlr_prof, candidates)


# ── Match evidence ──────────────────────────────────────────────────


def _build_match_evidence(
    hlr_prof: HLRMatchProfile,
    scored: list[tuple[int, dict[str, int], ICDBlock]],
    match_type: str,
) -> dict:
    """Build match_evidence dict with dimension-level score breakdown + block details."""
    evidence: dict = {
        "match_type": match_type,
        "signal_category": hlr_prof.signal_category,
        "hlr_labels": hlr_prof.labels,
        "matched_block_count": len(scored),
        "matched_block_keys": [block.block_key for _, _, block in scored],
    }

    if match_type == "精确匹配":
        evidence["label_matched"] = hlr_prof.labels
        evidence["direction"] = hlr_prof.extracted_direction

    # All matched blocks with dimension breakdown (sorted desc)
    evidence["top_scores"] = []
    for total, dims, block in scored:
        evidence["top_scores"].append({
            "block_key": block.block_key,
            "signal_family": block.signal_family,
            "total": total,
            "dimensions": dims,
            "channel_count": block.channel_count,
        })

    return evidence


# ── Indexing ────────────────────────────────────────────────────────


def _index_blocks(blocks: list[ICDBlock]) -> dict[str, ICDBlock]:
    """Build block_key → ICDBlock index."""
    return {b.block_key: b for b in blocks}


# ── Main entry point ────────────────────────────────────────────────


def match_reverse(
    hlr_reqs: list[HLRRequirement],
    hlr_labels: dict[str, HLRLabel],
    eoicd_reqs: list[EoICDRequirement],
) -> ReverseMatchOutput:
    """Reverse matching: HLR → EoICD ICD blocks (matching only, no judgment).

    1. Build HLR profiles (classifier + labeler data)
    2. Build EoICD signal profiles, group into ICDBlocks
    3. For each HLR: 4-path routing → Stage 1 coarse filter → Stage 2 6-dim scoring
    4. Output match results with dimension-level evidence
    """
    eoicd_kept = [req for req in eoicd_reqs if should_keep(req)]
    eoicd_profiles = build_profiles(eoicd_kept)
    blocks = build_blocks(eoicd_profiles)
    block_index = _index_blocks(blocks)

    results: list[HLRCoverageResult] = []
    matched_block_keys: set[str] = set()

    for hlr in hlr_reqs:
        lbl = hlr_labels.get(hlr.requirement_id)
        if lbl is None:
            results.append(HLRCoverageResult(
                hlr_id=hlr.requirement_id,
                hlr_content=hlr.content,
                hlr_rationale=hlr.rationale,
                match_type="无匹配",
                overall="unmatched",
                summary="HLR未标注",
            ))
            continue

        hlr_prof = build_hlr_profile(hlr, lbl)
        cat = hlr_prof.signal_category
        scored: list[tuple[int, dict[str, int], ICDBlock]] = []
        match_type = ""

        # ── Route to matching path ──
        path = ""
        if cat == "A429显式" and hlr_prof.labels:
            scored = _match_path1_label(hlr_prof, block_index)
            path = "精确匹配"
        elif cat in ("模拟量", "离散量"):
            scored = _match_path_semantic(hlr_prof, block_index)
            path = "语义匹配"
        elif cat == "A429隐式":
            scored = _match_path_semantic(hlr_prof, block_index)
            path = "语义匹配"
        else:
            path = ""

        # ── Score tiering with signal-name quality ──
        # "已匹配" requires: high total + enough active dims + signal_name > 0
        # Signal_name=0 handling varies by path:
        #   Path ① (A429显式): Label constrains candidates → downgrade to 待确定
        #   Path ②③ (模拟量/离散量): No Label constraint → 无匹配 (garbage)
        #   Path ④ (A429隐式): Bus filter constrains → downgrade to 待确定
        if scored:
            top_total, top_dims, _ = scored[0]
            top_signal_name = top_dims.get("signal_name", 0)
            active_dims = sum(1 for v in top_dims.values() if v > 0)

            if top_total >= _HIGH_SCORE_THRESHOLD and active_dims >= _MIN_ACTIVE_DIMS and top_signal_name > 0:
                match_type = "已匹配"
            elif cat in ("模拟量", "离散量") and top_signal_name == 0:
                match_type = "无匹配"
            else:
                match_type = "待确定"
        elif cat == "A429显式" and hlr_prof.labels:
            has_label_in_eoicd = False
            for lbl in hlr_prof.labels:
                clean_label = lbl.strip().upper()
                if not clean_label.startswith("L"):
                    clean_label = f"L{clean_label}"
                prefix = clean_label + "/"
                label_blocks = [block_index[k] for k in block_index if k.startswith(prefix)]
                if label_blocks:
                    has_label_in_eoicd = True
                    # Score all label-matched blocks without threshold filter,
                    # sort by score desc, so AI has signal context even for weak matches
                    raw_scored: list[tuple[int, dict[str, int], ICDBlock]] = []
                    for block in label_blocks:
                        total, dims = _score_block(hlr_prof, block)
                        raw_scored.append((total, dims, block))
                    raw_scored.sort(key=lambda x: x[0], reverse=True)
                    raw_scored = _apply_hard_gates(hlr_prof, raw_scored)
                    scored = raw_scored
                    break
            match_type = "待确定" if has_label_in_eoicd else "无匹配"
        else:
            match_type = "无匹配"

        # Top-K limit + post-filter
        top_scored = scored[:_TOP_K]
        top_scored = _filter_sn_zero_within_label(top_scored)

        # Clear blocks when match_type is 无匹配
        if match_type == "无匹配":
            top_scored = []

        matched_blocks = [block for _, _, block in top_scored]

        # ── Build match evidence ──
        match_evidence = _build_match_evidence(hlr_prof, top_scored, match_type)
        # Store the original path for context
        if path:
            match_evidence["match_path"] = path

        for block in matched_blocks:
            matched_block_keys.add(block.block_key)

        overall = "matched" if match_type == "已匹配" else ("uncertain" if match_type == "待确定" else "unmatched")

        # Build summary
        if matched_blocks:
            top_keys = [b.block_key for b in matched_blocks]
            summary = (
                f"[{cat}] {match_type} → "
                f"{', '.join(top_keys)}"
            )
        else:
            labels_str = ", ".join(hlr_prof.labels[:3]) if hlr_prof.labels else ""
            summary = f"[{cat}] {match_type}" + (f" (labels: {labels_str})" if labels_str else "")

        results.append(HLRCoverageResult(
            hlr_id=hlr.requirement_id,
            hlr_content=hlr.content,
            hlr_rationale=hlr.rationale,
            signal_category=cat,
            match_type=match_type,
            matched_profile_keys=[b.block_key for b in matched_blocks],
            matched_profile_count=len(matched_blocks),
            match_evidence=match_evidence,
            overall=overall,
            summary=summary,
        ))

    # Unmatched EoICD blocks
    all_block_keys = {b.block_key for b in blocks}
    unmatched_keys = sorted(all_block_keys - matched_block_keys)

    stats = {
        "hlr_total": len(hlr_reqs),
        "hlr_已匹配": sum(1 for r in results if r.match_type == "已匹配"),
        "hlr_待确定": sum(1 for r in results if r.match_type == "待确定"),
        "hlr_无匹配": sum(1 for r in results if r.match_type == "无匹配"),
        "eoicd_blocks_total": len(all_block_keys),
        "eoicd_blocks_matched": len(matched_block_keys),
        "eoicd_blocks_unmatched": len(unmatched_keys),
    }

    return ReverseMatchOutput(
        total_hlr=len(hlr_reqs),
        total_eoicd_profiles=len(all_block_keys),
        stats=stats,
        results=results,
        eoicd_unmatched_profile_keys=unmatched_keys,
    )
