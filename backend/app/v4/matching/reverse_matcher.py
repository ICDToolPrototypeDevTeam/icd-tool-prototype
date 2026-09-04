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
from app.v4.config import CN_SIGNAL_KEYWORD_MAP, FRAME_SIGNAL_KEYWORDS
from app.v4.matching.hlr_classifier import (
    classify_hlr,
    extract_labels,
    extract_bit_fields,
    extract_sdi,
    extract_direction,
    _SDI_RE,
)
from app.v4.matching.signal_profiler import build_profiles, build_blocks, SignalProfile, ICDBlock
from app.v4.models import (
    HLRCoverageResult,
    ReverseMatchOutput,
    HLRLabel,
    HLRRequirement,
    EoICDRequirement,
)
from app.v4.profiles.base import ControllerProfile, MatcherEnhancementConfig

# ── Scoring constants ────────────────────────────────────────────────

_TOP_K_DEFAULT = 20  # default max blocks per HLR passed to Agent
_TOP_K = _TOP_K_DEFAULT  # backward-compat alias for any external importer

# Dimensions whose values contribute to the numeric total. Diagnostic
# tags (``direction_conflict`` / ``direction_softened_for_exact_signal``)
# are clamped to 1 and intentionally NOT summed.
_NUMERIC_DIMS = ("signal_name", "direction", "bit_field", "sdi", "data_type", "device_bus", "channel_suffix")

# Common Chinese suffixes that occasionally append to otherwise-Latin
# signal tokens in HLR text (e.g. ``EDP_TCB_STATUS_034状态``). Only
# consulted when ``MatcherEnhancementConfig.enable_cn_suffix_strip`` is
# True (RPDU).
_CN_SUFFIX_RE = re.compile(
    r'(状态|命令|值|位|信号|参数|数据|量|参数值|状态位)$'
)

# Matches 3+ digit sequences in HLR text, optionally preceded by a
# signal-context prefix (e.g. ``EDP_TCB_STATUS_034`` → ``034``). Used
# for signal-number exact-match bonus (RPDU only).
_SIGNAL_NUM_RE = re.compile(
    r'(?:状态|信号|参数|变量|Data|Port|编号|名称|cmd|fault|bit|通道|状态位|参数值)?'
    r'[_]?'
    r'(\d{3,})'
    r'(?![A-Za-z])',
    re.IGNORECASE,
)

# Numbers too common in bus/rate context to count as signal numbers.
_SIGNAL_NUM_BLACKLIST = frozenset({
    "200", "664", "429", "100", "500", "1000", "000", "255", "128",
})

# Channel suffix pattern: _R<digit>[_<letter>] at end of signal name.
# Matches _R1, _R1A, _R2B, etc. Used for redundancy-channel matching
# (HSCU/RPDU: R1A/R1B = channel 1 lane A/B, R2A/R2B = channel 2).
_CHANNEL_SUFFIX_RE = re.compile(r'_([Rr])(\d+)([A-Za-z]?)$')
# Bare variant for token-level filtering (no leading underscore).
_CHANNEL_SUFFIX_TOKEN_RE = re.compile(r'[Rr]\d+[A-Za-z]?$')

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


# —— A429 协议层规则检测 ——
_A429_PROTOCOL_HINT = (
    "\U0001f4a1 HLR 描述的是 A429 协议级规则（如 SSM/SDI/PARITY 等字段位号定义），"
    "不对应单个 EoICD Block，建议人工审查 ARINC 429 协议合规性。"
)

# 匹配"标签第 N 位" / "标签 第 N 和 M 位" / "标签 第 9-10 位" / "标签的第 9 和 10 位" 等位号位置表达
_LABEL_BIT_POS_RE = re.compile(
    r"(?:标签|label)\s*(?:第|的\s*第|之\s*第|中\s*第)?\s*\d+\s*(?:[和与至、，/\-]\s*\d+\s*)*位",
    re.IGNORECASE,
)


def _is_a429_protocol_rule(hlr_content: str, signal_keywords: set[str] | None) -> bool:
    """检测 HLR 是否描述 A429 协议层规则（非应用层数据）。

    三重判定（任一命中即返回 True）：
      1. signal_keywords 与 FRAME_SIGNAL_KEYWORDS 交集非空
      2. hlr_content 中出现"标签第 N 位"位号位置表达
      3. hlr_content 中直接出现 ssm/sdi/parity/奇偶校验 字面

    参数:
        hlr_content: HLR 全文
        signal_keywords: hlr_labeler 标注出的关键词集合（小写，可为空）

    返回:
        True 表示该 HLR 是协议级规则。
    """
    if signal_keywords and (signal_keywords & FRAME_SIGNAL_KEYWORDS):
        return True
    text = (hlr_content or "").lower()
    if _LABEL_BIT_POS_RE.search(text):
        return True
    # 兜底：content 里出现协议字段字面（应对 labeler 漏标的中英文混写）
    if any(k in text for k in ("ssm", "sdi", "parity", "奇偶校验")):
        return True
    return False


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


def _strip_cn_suffix(text: str) -> str:
    """Strip a single common Chinese suffix token from the tail.

    Used by RPDU only (when ``enable_cn_suffix_strip=True``) so that
    ``EDP_TCB_STATUS_034状态`` becomes ``edp_tcb_status_034`` for
    boundary matching. Strips at most one suffix so an over-eager match
    doesn't eat unrelated text.
    """
    return _CN_SUFFIX_RE.sub('', text)


def _classify_keyword_specificity(kw: str, strip_cn_suffix: bool = False) -> str:
    """Classify an HLR signal keyword by specificity to control matching.

    Returns 'precise' (long alpha + digit, like AFTEFAN1),
            'moderate' (shorter alpha + digit, like BBSOV1),
            'generic' (no digit suffix or very short).

    When ``strip_cn_suffix`` is True (RPDU), Chinese suffixes such as
    "状态" are stripped first so that ``EDP_TCB_STATUS_034状态`` is
    correctly classified as 'precise'.
    """
    kw_clean = kw.strip()
    if strip_cn_suffix:
        kw_clean = _strip_cn_suffix(kw_clean)
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


def _boundary_match(
    kw: str,
    text: str,
    strip_cn_suffix: bool = False,
) -> bool:
    """Check if keyword matches at token boundaries in text.

    Prevents cross-contamination: 'AFTEFAN1' matches 'AFTEFAN1_HW_FAULT'
    but NOT 'AFTEFAN2_HW_FAULT' (different numeric suffix).

    When ``strip_cn_suffix`` is True (RPDU), common Chinese suffixes are
    stripped from both keyword and text before matching so that
    ``EDP_TCB_STATUS_034`` can match ``EDP_TCB_STATUS_034状态``.
    """
    if strip_cn_suffix:
        kw = _strip_cn_suffix(kw)
        text = _strip_cn_suffix(text)
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

    Also treats short alpha+digit tokens (e.g. R1, R2A, ABV1) as named
    components so that ``_tokenize_name`` doesn't strip the digit part
    and leave an overly-generic single letter ('r') that matches every
    block with a channel suffix.
    """
    for match in _PRECISE_COMPONENT_RE.finditer(text):
        component = match.group()
        # Short alpha+digit tokens like R1, R2A are channel/device
        # identifiers — always treat as named to prevent digit stripping.
        if len(component) <= 3 and component[-1].isdigit():
            return True
        if _classify_keyword_specificity(component) in ('precise', 'moderate'):
            return True
    return False


# ── Block-level scoring ──────────────────────────────────────────


def _score_block(
    hlr_prof: HLRMatchProfile,
    block: ICDBlock,
    enhancements: MatcherEnhancementConfig | None = None,
) -> tuple[int, dict[str, int]]:
    """Score a single ICDBlock against an HLR across 7 dimensions.

    Returns (total_score, dimension_scores).

    Dimensions: signal_name(30), direction(15), bit_field(20), sdi(15),
    data_type(10), device_bus(10), channel_suffix(10).  channel_suffix
    fires only when HLR keywords carry a redundancy-channel suffix
    (_R1, _R2A, etc.); AMS/FGMC signals lack this pattern → always 0.

    ``enhancements`` (RPDU profile only) toggles three optional scoring
    augmentations; when ``None`` or all flags False the result is
    byte-identical to the legacy scorer used by AMS/FGMC/HSCU.
    """
    if enhancements is None:
        enhancements = MatcherEnhancementConfig()
    strip_cn = enhancements.enable_cn_suffix_strip
    direction_soft = enhancements.enable_direction_soft_on_exact_signal
    sig_num_bonus = enhancements.enable_signal_number_bonus
    syn = _get_synonym_lookup()
    dims: dict[str, int] = {}

    # ── 1. Signal name match (30pts) ──
    # Use signal_family (cleaner than leaf name — no channel prefixes)
    family_lower = block.signal_family.lower()
    family_tokens: set[str] = {family_lower}
    family_tokens.update(_tokenize_name(family_lower))
    for tok in list(family_tokens):
        family_tokens.update(_resolve_aliases(tok, syn))
    # Remove channel-suffix tokens from family_tokens (same rationale as above)
    family_tokens = {
        t for t in family_tokens
        if not _CHANNEL_SUFFIX_TOKEN_RE.fullmatch(t)
    }

    hlr_tokens: set[str] = set()
    for kw in hlr_prof.signal_keywords:
        kw_lower = kw.lower()
        hlr_tokens.add(kw_lower)
        if not _has_named_component(kw_lower):
            hlr_tokens.update(_tokenize_name(kw_lower))
        else:
            # Segment-based tokenization: split on '_', tokenize each segment
            # individually. Named components (e.g. AFTEFAN1, ABV1) are kept
            # as-is to prevent sub-token noise; generic segments (e.g. HW,
            # FAULT, LOAD, VOLT) are tokenized normally. This prevents a
            # single moderate/precise component from blocking tokenization of
            # the entire compound keyword.
            for segment in kw_lower.split("_"):
                if not segment:
                    continue
                if not _has_named_component(segment):
                    hlr_tokens.update(_tokenize_name(segment))
                else:
                    hlr_tokens.add(segment)
        hlr_tokens.update(_resolve_aliases(kw_lower, syn))

    # Remove channel-suffix tokens (R1, R1A, R2B, etc.) from hlr_tokens.
    # These are handled by the dedicated channel_suffix dimension (1a);
    # leaving them in signal_name would cause every RPDU block with any
    # channel suffix to match equally on the generic "r" token that
    # _tokenize_name("R1") produces.
    hlr_tokens = {
        t for t in hlr_tokens
        if not _CHANNEL_SUFFIX_TOKEN_RE.fullmatch(t)
    }

    for dev in hlr_prof.devices:
        dev_lower = dev.lower()
        hlr_tokens.add(dev_lower)
        if not _has_named_component(dev_lower):
            hlr_tokens.update(_tokenize_name(dev_lower))
        else:
            for segment in dev_lower.split("_"):
                if not segment:
                    continue
                if not _has_named_component(segment):
                    hlr_tokens.update(_tokenize_name(segment))
                else:
                    hlr_tokens.add(segment)
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
        specificity = _classify_keyword_specificity(kw, strip_cn_suffix=strip_cn)
        if specificity == 'precise':
            if _boundary_match(kw, family_lower, strip_cn_suffix=strip_cn):
                signal_overlap.add(kw if not strip_cn else _strip_cn_suffix(kw))
        else:
            kw_eff = _strip_cn_suffix(kw) if strip_cn else kw
            if kw_eff in family_lower or family_lower in kw_eff:
                signal_overlap.add(kw_eff)

    # Device substring match with specificity control
    for dev in hlr_prof.devices:
        dev_lower = dev.lower()
        if len(dev_lower) < 3:
            continue
        if _has_named_component(dev_lower):
            # Extract precise components and boundary-match each individually
            for match in _PRECISE_COMPONENT_RE.finditer(dev_lower):
                component = match.group().lower()
                specificity = _classify_keyword_specificity(
                    component, strip_cn_suffix=strip_cn
                )
                if specificity == 'precise':
                    if _boundary_match(component, family_lower, strip_cn_suffix=strip_cn):
                        signal_overlap.add(component)
                else:
                    # Moderate/generic: substring match (e.g. 'bbsov1' in 'fwdbbbsov1_fc')
                    if component in family_lower or family_lower in component:
                        signal_overlap.add(component)
        else:
            if dev_lower in family_lower or family_lower in dev_lower:
                signal_overlap.add(dev_lower)

    dims["signal_name"] = min(30, len(signal_overlap) * 8)

    # ── 1a. Channel suffix match (10pts) ──
    # HLR keywords like ABV1_LOAD_VOLT_AVAIL_RPDU_R1 carry a redundancy-
    # channel suffix (_R1).  EoICD block families carry the full suffix
    # (_R1A, _R1B, _R2A, _R2B).  Match by channel number: _R1 → _R1A/​_R1B
    # but NOT _R2A/​_R2B.  When the HLR suffix also carries a sub-channel
    # letter (e.g. _R1A), require exact letter match.
    hlr_channel_nums: set[str] = set()
    hlr_channel_letters: set[str] = set()
    for kw in hlr_prof.signal_keywords:
        ch = _CHANNEL_SUFFIX_RE.search(kw.lower())
        if ch:
            hlr_channel_nums.add(ch.group(2))
            if ch.group(3):
                hlr_channel_letters.add(ch.group(3).upper())
    for dev in hlr_prof.devices:
        ch = _CHANNEL_SUFFIX_RE.search(dev.lower())
        if ch:
            hlr_channel_nums.add(ch.group(2))
            if ch.group(3):
                hlr_channel_letters.add(ch.group(3).upper())

    dims["channel_suffix"] = 0
    if hlr_channel_nums:
        blk_ch = _CHANNEL_SUFFIX_RE.search(family_lower)
        if blk_ch:
            blk_num = blk_ch.group(2)
            blk_letter = blk_ch.group(3).upper() if blk_ch.group(3) else ""
            if blk_num in hlr_channel_nums:
                if hlr_channel_letters:
                    # HLR specifies sub-channel letter → exact match required
                    if blk_letter in hlr_channel_letters:
                        dims["channel_suffix"] = 10
                else:
                    # HLR has _R1 (no letter) → matches any sub-channel
                    dims["channel_suffix"] = 10

    # ── 1b. Signal-number exact-match bonus (RPDU only) ──
    # HLR content like "EDP_TCB_STATUS_034状态" hides the trailing 3-digit
    # number behind a Chinese suffix; the regular boundary matcher
    # misses it. Extract 3+ digit sequences from the HLR body, filter
    # protocol-constant noise, and award +10/number that also appears
    # in the block's signal_family. Saturation: signal_name already
    # capped at 30; bonus raises the cap proportionally.
    if sig_num_bonus:
        hlr_signal_numbers = {
            m.group(1) for m in _SIGNAL_NUM_RE.finditer(hlr_prof.content)
        } - _SIGNAL_NUM_BLACKLIST
        if hlr_signal_numbers:
            family_nums = set(re.findall(r'\d{3,}', family_lower))
            matched_nums = hlr_signal_numbers & family_nums
            if matched_nums:
                dims["signal_name"] = min(
                    30, dims["signal_name"] + len(matched_nums) * 10
                )

    # ── 2. SDI match (15pts) ──
    # 从 HLR 正文提取全部 SDI 值（目录表别名可注入多个 SDI=，如一条需求
    # 引用多个 LBL），任一与 block 的 SDIExpected 一致即得分。
    # 先于 direction 计算：SDI 值级命中（如 SDI=1 vs SDIExpected=1）是
    # 身份级命中证据，方向矛盾时用于 direction-soft 救援判定。
    hlr_sdis = {m for m in _SDI_RE.findall(hlr_prof.content)}
    dims["sdi"] = 0
    if hlr_sdis:
        for prof in block.profiles:
            prof_sdi = str(prof.attributes.get("SDIExpected", {}).get("value", ""))
            if prof_sdi and prof_sdi in hlr_sdis:
                dims["sdi"] = 15
                break

    # ── 3. Direction match (15pts) ──
    hlr_dir = hlr_prof.extracted_direction
    block_dir = block.direction
    if hlr_dir:
        if hlr_dir in block_dir:
            dims["direction"] = 15
        elif block_dir == "发送/接收":
            dims["direction"] = 8
        else:
            # Direction contradiction.  When the HLR's signal name already
            # matched the block exactly (signal_name saturated at 30) or the
            # block's SDI value matches the HLR's SDI assertion, this is
            # often an injected-fault or doc typo, not a "wrong target".
            # Don't let the 15pt gap bury it below direction-coincident noise
            # before the AI judge reviews the inconsistency. Apportion a
            # mid value (8) instead, and tag the candidate so downstream
            # stages can surface the conflict.
            if direction_soft and (
                dims.get("signal_name", 0) >= 30 or dims.get("sdi", 0) > 0
            ):
                dims["direction"] = 8
                dims["direction_softened_for_exact_signal"] = 1
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

    # ── 4. Data type match (10pts) ──
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

    # ── 6b. Protocol-family explicit-mention bonus ──
    # SDI 等协议族 block：HLR 文本明确提到该字段词元时，是对协议位的显式
    # 断言（如"设置Label和SDI"），按精确信号名命中给予 signal_name 基准
    # 分，避免其被同分的 SPAR 等低质块挤出 top-K。
    fam_upper = (block.signal_family or "").upper()
    gate_re = _PROTOCOL_FAMILY_TOKEN_GATE.get(fam_upper)
    if gate_re is not None and gate_re.search(hlr_prof.content):
        dims["signal_name"] = max(int(dims.get("signal_name", 0)), 30)

    total = sum(dims.get(k, 0) for k in _NUMERIC_DIMS)
    return total, dims


def _apply_hard_gates(
    hlr_prof: HLRMatchProfile,
    scored: list[tuple[int, dict[str, int], ICDBlock]],
    enhancements: MatcherEnhancementConfig | None = None,
) -> list[tuple[int, dict[str, int], ICDBlock]]:
    """Remove blocks that fail definitive-contradiction gates.

    These gates are conservative — they only fire when the evidence is
    unambiguous.  Removing a correct block is worse than keeping noise,
    so each gate has a high bar for activation.

    When ``enhancements.enable_direction_soft_on_exact_signal`` is True
    (RPDU), Gate 1 retains direction-contradiction candidates whose
    ``signal_name`` dimension already matched exactly — those are tagged
    ``direction_conflict`` so the AI judge can review the inconsistency
    instead of the block being silently dropped.
    """
    if enhancements is None:
        enhancements = MatcherEnhancementConfig()
    direction_soft = enhancements.enable_direction_soft_on_exact_signal
    hlr_dir = hlr_prof.extracted_direction.strip()
    cat = hlr_prof.signal_category

    # Only apply direction gate when direction extraction is unambiguous
    # AND not bidirectional (发送/接收 means both are plausible).
    dir_gate_active = hlr_dir in ("发送", "接收")
    hlr_sdis = {m for m in _SDI_RE.findall(hlr_prof.content)}

    filtered: list[tuple[int, dict[str, int], ICDBlock]] = []
    for total, dims, block in scored:
        # —— Gate 1: Direction contradiction ——
        if dir_gate_active and hlr_dir in ("发送", "接收"):
            block_dir = block.direction
            dir_conflict = hlr_dir == "发送" and block_dir == "接收"
            dir_conflict = dir_conflict or (
                hlr_dir == "接收" and block_dir == "发送"
            )
            if dir_conflict:
                # 信号名已精确命中（sn>=30）或 SDI 值级命中（目录表 SDI=1
                # vs ICD SDIExpected=1）的 block，方向矛盾不剔除——这是
                # 注入故障/文档笔误，应保留并标记不一致让上层甄别。
                sn_score = dims.get("signal_name", 0)
                if direction_soft and (
                    sn_score >= 30
                    or (sn_score > 0 and block_dir == "发送/接收")
                    or dims.get("sdi", 0) > 0
                ):
                    # direction 已在 _score_block 为精确命中矛盾信号给 8 分，
                    # 保留该分值；仅叠加不一致标记供语义层甄别。重算 total。
                    if dims.get("direction_softened_for_exact_signal"):
                        total = sum(dims.get(k, 0) for k in _NUMERIC_DIMS)
                    dims["direction_conflict"] = 1
                    dims["hlr_direction"] = hlr_dir
                    dims["icd_direction"] = block_dir
                    filtered.append((total, dims, block))
                    continue
                else:
                    continue  # 方向矛盾且信号名未命中，仍视为噪声剔除

        # —— Gate 2: SDI contradiction ——
        # Only gate when block has an explicit SDI but none match the HLR's
        # SDI values (正文/目录表别名可携带多个 SDI=，任一匹配即保留)。
        if hlr_sdis:
            block_has_sdi = False
            block_sdi_matches = False
            for prof in block.profiles:
                prof_sdi = str(prof.attributes.get("SDIExpected", {}).get("value", ""))
                if prof_sdi:
                    block_has_sdi = True
                    if prof_sdi in hlr_sdis:
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
    enhancements: MatcherEnhancementConfig | None = None,
) -> list[tuple[int, dict[str, int], ICDBlock]]:
    """Remove sn=0 blocks from labels that already have sn>0 blocks.

    When a label has blocks with positive signal_name scores, the sn=0
    blocks under the same label are almost certainly noise (e.g. FWDBBSOV2
    mixed in with FWDBBSOV1 matches).  Labels where ALL blocks have sn=0
    are kept as-is — no better alternative exists.

    When RPDU's ``enable_direction_soft_on_exact_signal`` is enabled,
    direction-conflict candidates are sorted to the front so the AI
    judge sees them above direction-coincident noise.
    """
    from collections import defaultdict
    if enhancements is None:
        enhancements = MatcherEnhancementConfig()
    direction_soft = enhancements.enable_direction_soft_on_exact_signal
    by_label: dict[str, list[tuple[int, dict[str, int], ICDBlock]]] = defaultdict(list)
    for total, dims, block in scored:
        by_label[block.label or block.block_key].append((total, dims, block))

    result: list[tuple[int, dict[str, int], ICDBlock]] = []
    for entries in by_label.values():
        # 协议族 block（如 SDI）的 signal_name 来自词元门控加分，不能作为
        # "该 label 已有真实信号名命中"的证据——否则 HLR 提到 SDI 但数据块
        # 均 sn=0 时，SDI 会触发过滤把全部真实上下文删光。
        has_sn_positive = any(
            d.get("signal_name", 0) > 0
            and (b.signal_family or "").upper() not in _PROTOCOL_FAMILY_TOKEN_GATE
            for _, d, b in entries
        )
        if has_sn_positive:
            for t, d, b in entries:
                if d.get("signal_name", 0) > 0:
                    result.append((t, d, b))
                elif (b.signal_family or "").upper() in _PROTOCOL_FAMILY_TOKEN_GATE:
                    # 协议族块（SDI）即使 sn=0 也保留：其入围要么靠 HLR 显式
                    # 词元（6b 已给 sn=30），要么靠 ICD 位范围重叠门控——两者
                    # 都是证据级准入，不应被"同 label 已有 sn>0 数据块"误删。
                    result.append((t, d, b))
        else:
            result.extend(entries)

    if direction_soft:
        # direction_conflict 优先保留在前面，便于 AI judge 复核。
        result.sort(
            key=lambda x: (
                0 if x[1].get("direction_conflict") else 1,
                -x[0],
            )
        )
    else:
        result.sort(key=lambda x: x[0], reverse=True)
    return result


def _filter_spar_when_data_matched(
    scored: list[tuple[int, dict[str, int], ICDBlock]],
) -> list[tuple[int, dict[str, int], ICDBlock]]:
    """SPAR（备用位）块降级：仅当同 label 没有其他 signal_name>0 的块时才保留。

    SPAR 叶子名携带词名（如 SPAR_22_DIS_00_SYS1_T1A），是"HLR 只提词名"场景
    下仅有的词名线索；但 HLR 不会对备用位做断言，当同 label 的真实数据位已
    命中（signal_name>0）时，SPAR 块是纯噪音，应从裁判上下文中剔除。
    """
    by_label: dict[str, list[tuple[int, dict[str, int], ICDBlock]]] = defaultdict(list)
    for total, dims, block in scored:
        by_label[block.label or block.block_key].append((total, dims, block))

    result: list[tuple[int, dict[str, int], ICDBlock]] = []
    for entries in by_label.values():
        # "数据命中"只认真实数据块：协议族 block（如 SDI，其 sn 来自词元
        # 门控加分）与 SPAR 本身都不算，否则"只提词名"场景下 SDI 块会误
        # 触发过滤、把唯一的词名线索（SPAR）删光。
        has_data_hit = any(
            d.get("signal_name", 0) > 0
            and (b.signal_family or "").upper() not in _PROTOCOL_FAMILY_TOKEN_GATE
            and not (b.signal_family or "").upper().startswith("SPAR")
            for _, d, b in entries
        )
        for total, dims, block in entries:
            fam = (block.signal_family or "").upper()
            if fam.startswith("SPAR") and has_data_hit:
                continue
            result.append((total, dims, block))
    return result


# 协议族 block 匹配资格门控：SDI 等泛名协议位 block 仅在 HLR 文本明确
# 提到该字段词元时才作为候选参与匹配。否则每个 A429 word 的 SDI block
# 都会以低分混入所有 label 命中的 case，形成噪音；且与"只比对 HLR 明确
# 写出的声明"的判定语义一致。
_PROTOCOL_FAMILY_TOKEN_GATE = {
    # 注意：不能用 \b——Python3 re 的 \b 把中文也视为单词字符，
    # "设置Label和SDI" 中 "和SDI" 之间无边界，\bSDI\b 匹配不上。
    "SDI": re.compile(r"(?<![A-Za-z0-9_])SDI(?![A-Za-z0-9_])", re.IGNORECASE),
}


def _hlr_bit_fields_overlap(hlr_prof: HLRMatchProfile, icd_offset: int, icd_size: int) -> bool:
    """HLR 位号（已换算 0 基）与 ICD 位范围 [icd_offset, icd_offset+icd_size) 是否有交集."""
    icd_lo, icd_hi = icd_offset, icd_offset + icd_size
    for bf in hlr_prof.bit_fields:
        try:
            lo = int(bf.get("offset"))
            sz = int(bf.get("size") or 1)
        except (TypeError, ValueError):
            continue
        if lo + max(sz, 1) > icd_lo and lo < icd_hi:
            return True
    return False


def _block_icd_bit_range(block: ICDBlock) -> tuple[int, int] | None:
    """Block 的 ICD 位范围 (offset, size)，取自 profile 级 BitOffsetWithinDS/ParameterSize."""
    for prof in block.profiles:
        off = prof.attributes.get("BitOffsetWithinDS", {}).get("value", "")
        size = prof.attributes.get("ParameterSize", {}).get("value", "")
        try:
            o = int(off)
        except (TypeError, ValueError):
            continue
        try:
            s = int(size)
        except (TypeError, ValueError):
            s = 1
        return (o, max(s, 1))
    return None


def _protocol_family_eligible(hlr_prof: HLRMatchProfile, block: ICDBlock) -> bool:
    """True when a protocol-family block may be a matching candidate.

    两条放行路径（任一命中即 eligible）：
      1. HLR 文本显式提到该字段词元（如 "设置Label和SDI"）；
      2. HLR 中文位号（"第9和10位"等，已换算 0 基）与该协议字段在 ICD 中的
         位范围重叠——例如对所有 A429 标签的 SDI 位做全局断言的需求并不写
         "SDI" 词元，但其位号指向 SDI 字段，ICD 数据本身就是证据。
    """
    fam = (block.signal_family or "").upper()
    gate = _PROTOCOL_FAMILY_TOKEN_GATE.get(fam)
    if gate is None:
        return True
    if gate.search(hlr_prof.content):
        return True
    br = _block_icd_bit_range(block)
    if br is not None and _hlr_bit_fields_overlap(hlr_prof, br[0], br[1]):
        return True
    return False


# HLR 正文中显式列出的全大写信号名 token（如"根据匹配的数据设置各数据位：
# HYD_QTY_XDCR..."清单），用于 top-K 豁免。排除协议/通用噪声词。
_LISTED_SIGNAL_RE = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")
_LISTED_SIGNAL_NOISE = {
    "HLR", "EOICD", "TRUE", "FALSE", "SSM", "SDI", "LBL", "A429", "ARINC",
    "LABEL", "PARITY", "HSCU",
}


def _extract_listed_signal_tokens(content: str) -> list[str]:
    """Extract explicitly-listed signal-name tokens from HLR body text."""
    out: list[str] = []
    seen: set[str] = set()
    for t in _LISTED_SIGNAL_RE.findall(content):
        if t in _LISTED_SIGNAL_NOISE:
            continue
        lt = t.lower()
        if lt not in seen:
            seen.add(lt)
            out.append(lt)
    return out


def _score_and_rank_blocks(
    hlr_prof: HLRMatchProfile,
    candidates: list[ICDBlock],
    enhancements: MatcherEnhancementConfig | None = None,
) -> list[tuple[int, dict[str, int], ICDBlock]]:
    """Score blocks, apply hard gates, sort desc, return top results."""
    scored: list[tuple[int, dict[str, int], ICDBlock]] = []
    for block in candidates:
        if not _protocol_family_eligible(hlr_prof, block):
            continue
        total, dims = _score_block(hlr_prof, block, enhancements=enhancements)
        if total >= _MIN_SCORE_THRESHOLD:
            scored.append((total, dims, block))
    scored.sort(key=lambda x: x[0], reverse=True)
    scored = _apply_hard_gates(hlr_prof, scored, enhancements=enhancements)
    if enhancements and enhancements.enable_direction_soft_on_exact_signal:
        # 排序保底：方向矛盾但信号名精确命中的 block 代表「注入故障/文档笔误」
        # 的可疑点，必须置于顶层候选，避免被方向一致(+15)的无关噪声挤出 Top-K，
        # 从而让上层语义甄别能捕获需求方向与 ICD 方向的不一致。
        scored.sort(
            key=lambda x: (
                0 if x[1].get("direction_conflict") else 1,
                -x[0],
            )
        )
    return scored


# ── Path-routing functions ──────────────────────────────────────────


def _match_path1_label(
    hlr_prof: HLRMatchProfile,
    block_index: dict[str, ICDBlock],
    enhancements: MatcherEnhancementConfig | None = None,
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

    return _score_and_rank_blocks(hlr_prof, candidates, enhancements=enhancements)


def _match_path_semantic(
    hlr_prof: HLRMatchProfile,
    block_index: dict[str, ICDBlock],
    enhancements: MatcherEnhancementConfig | None = None,
) -> list[tuple[int, dict[str, int], ICDBlock]]:
    """Paths 2/3/4: score all non-label blocks with optional bus filter."""
    candidates: list[ICDBlock] = []
    for key, block in block_index.items():
        # Skip label-based blocks UNLESS this is A429隐式, which may
        # semantically map to a block that has a Label (RPDU only).
        if block.label and hlr_prof.signal_category != "A429隐式":
            continue
        # Bus filter (required for A429隐式; soft for 模拟量/离散量)
        bus_overlap = (
            block.bus_aliases_set & hlr_prof.bus_types
            or block.bus_types & hlr_prof.bus_types
        )
        if hlr_prof.signal_category == "A429隐式":
            if not bus_overlap:
                continue  # A429隐式 requires bus match
        candidates.append(block)

    return _score_and_rank_blocks(hlr_prof, candidates, enhancements=enhancements)


# ── Match evidence ──────────────────────────────────────────────────


def _build_match_evidence(
    hlr_prof: HLRMatchProfile,
    scored: list[tuple[int, dict[str, int], ICDBlock]],
    match_type: str,
    enhancements: MatcherEnhancementConfig | None = None,
) -> dict:
    """Build match_evidence dict with dimension-level score breakdown + block details."""
    if enhancements is None:
        enhancements = MatcherEnhancementConfig()
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

    # Direction-contradiction aggregation (RPDU only). Bubble these up
    # to the surface so the UI / summary can show them without digging
    # into each top_score's dimensions dict.
    if enhancements.enable_direction_soft_on_exact_signal:
        direction_conflicts: list[dict] = []
        for total, dims, block in scored:
            if dims.get("direction_conflict"):
                direction_conflicts.append({
                    "block_key": block.block_key,
                    "signal_family": block.signal_family,
                    "total": total,
                    "hlr_direction": dims.get("hlr_direction"),
                    "icd_direction": dims.get("icd_direction"),
                })
        if direction_conflicts:
            evidence["direction_conflicts"] = direction_conflicts

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
    profile: ControllerProfile | None = None,
) -> ReverseMatchOutput:
    """Reverse matching: HLR → EoICD ICD blocks (matching only, no judgment).

    1. Build HLR profiles (classifier + labeler data)
    2. Build EoICD signal profiles, group into ICDBlocks
    3. For each HLR: 4-path routing → Stage 1 coarse filter → Stage 2 6-dim scoring
    4. Output match results with dimension-level evidence

    ``profile`` (Issue #74) is the active controller profile. Its
    ``profile.matcher`` carries four opt-in scoring flags (RPDU enables
    them all; AMS/FGMC/HSCU keep all False by default → byte-identical to
    pre-#74 behaviour). ``profile=None`` falls back to ``MatcherEnhancementConfig()``
    with all flags off.
    """
    enhancements = profile.matcher if profile is not None else MatcherEnhancementConfig()

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
            scored = _match_path1_label(hlr_prof, block_index, enhancements=enhancements)
            path = "精确匹配"
        elif cat in ("模拟量", "离散量"):
            scored = _match_path_semantic(hlr_prof, block_index, enhancements=enhancements)
            path = "语义匹配"
        elif cat == "A429隐式":
            scored = _match_path_semantic(hlr_prof, block_index, enhancements=enhancements)
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
            active_dims = sum(
                1 for v in top_dims.values()
                # Issue #74: ``direction_conflict`` is a string tag (RPDU only).
                # Skip non-numeric values so the count is strictly the 6 scoring
                # dimensions with non-zero contribution.
                if isinstance(v, (int, float)) and v > 0
            )

            if top_total >= _HIGH_SCORE_THRESHOLD and active_dims >= _MIN_ACTIVE_DIMS and top_signal_name > 0:
                match_type = "已匹配"
            elif cat in ("模拟量", "离散量") and top_signal_name == 0:
                match_type = "无匹配"
            else:
                match_type = "待确定"
        elif cat == "A429显式" and hlr_prof.labels:
            has_label_in_eoicd = False
            for label_str in hlr_prof.labels:
                clean_label = label_str.strip().upper()
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
                        if not _protocol_family_eligible(hlr_prof, block):
                            continue
                        total, dims = _score_block(hlr_prof, block, enhancements=enhancements)
                        raw_scored.append((total, dims, block))
                    raw_scored.sort(key=lambda x: x[0], reverse=True)
                    raw_scored = _apply_hard_gates(hlr_prof, raw_scored, enhancements=enhancements)
                    scored = raw_scored
                    break
            match_type = "待确定" if has_label_in_eoicd else "无匹配"
        else:
            match_type = "无匹配"

        # Top-K limit + post-filter
        # Issue #74: ``enhancements.top_k`` is the per-profile candidate
        # window (RPDU=50; AMS/FGMC/HSCU default=20). ``_TOP_K`` is kept
        # as a backward-compat constant for external importers but no
        # longer used at runtime here.
        top_scored = scored[:enhancements.top_k]
        # 显式写出词元的协议族 block（如 "设置Label和SDI"）不受 top-K 截断：
        # HLR 明确提到该字段时其定义必须出现在裁判上下文里，即使总分低于
        # 其他块。位号门控放行的 SDI 块（"所有标签第9和10位"）不在此列——
        # 其 ICD 布局/值语义跨标签同质（同为 offset/size + 同一 CodedSet），
        # top-K 内的样例已足以支撑判断；曾尝试按 bit_field>0 全量保送，
        # 实测把 case 从 20 块撑到 131 块（127 个同质 SDI），prompt 膨胀拖垮
        # 长输出 provider（deepseek 截断→error→熔断连锁），已撤销（2026-09-03）。
        for extra in scored[enhancements.top_k:]:
            fam = (extra[2].signal_family or "").upper()
            gate_re = _PROTOCOL_FAMILY_TOKEN_GATE.get(fam)
            if gate_re is not None and gate_re.search(hlr_prof.content):
                top_scored.append(extra)
        # HLR 正文显式列名的 block 同样不受 top-K 截断：数据位清单
        # （"根据匹配的数据设置各数据位：HYD_QTY_XDCR..."）中列出的信号，
        # 即使 labeler 未将其完整捕获为关键词（只给碎片词元如 "xdcr"）、
        # 导致总分偏低，也必须进入裁判上下文。
        listed = _extract_listed_signal_tokens(hlr_prof.content)
        if listed:
            for extra in scored[enhancements.top_k:]:
                fam = (extra[2].signal_family or "").lower()
                if any(fam == t or fam.startswith(t + "_") for t in listed):
                    top_scored.append(extra)
        top_scored = _filter_sn_zero_within_label(top_scored, enhancements=enhancements)
        top_scored = _filter_spar_when_data_matched(top_scored)

        # Clear blocks when match_type is 无匹配
        if match_type == "无匹配":
            top_scored = []

        matched_blocks = [block for _, _, block in top_scored]

        # ── Build match evidence ──
        match_evidence = _build_match_evidence(hlr_prof, top_scored, match_type, enhancements=enhancements)
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
            base = f"[{cat}] {match_type}" + (f" (labels: {labels_str})" if labels_str else "")
            # 无匹配时，若 HLR 描述的是 A429 协议层规则，附加人工审查提示
            if match_type == "无匹配" and _is_a429_protocol_rule(
                hlr.content, lbl.signal_keywords_set
            ):
                summary = f"{base}\n{_A429_PROTOCOL_HINT}"
            else:
                summary = base

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
