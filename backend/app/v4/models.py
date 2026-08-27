# -*- coding: utf-8 -*-
"""Pydantic models for EoICD and HLR requirement outputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# EoICD Models
# ============================================================

class EoICDRequirement(BaseModel):
    """A single itemized requirement extracted from EoICD Excel."""

    ird_id: str                       # IRD-{bus}-{layer_abbr}-{seq:04d}
    side: str                         # "DP" | "RP"
    sheet_name: str
    bus_type: str
    layer_type: str                   # leaf layer type
    attribute_name: str               # English attribute name
    attribute_value: Any
    unit: str | None = None
    description: str                  # {signal}的{中文（English）}应为{value}{unit}
    source: str                       # "Publisher Table" or "Subscriber Table"
    is_dp_ref: bool = False           # True if this is a Subscriber dp_ref attribute
    dp_ref_name: str = ""             # DP sub-signal name from pub_names (P2.4)
    signal_name: str = ""  # hierarchical signal path from Software to current layer
    layer_path_types: list[str] = Field(default_factory=list)  # layer_type per level (Software→leaf); additive, forward-only


class EoICDOutput(BaseModel):
    """Top-level output from EoICD Excel parsing (merged Publisher + Subscriber)."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_generated: int
    total_raw: int
    total_after_dedup: int
    duplicates_removed: int
    sheet_statistics: dict[str, dict[str, int]]
    requirements: list[EoICDRequirement]


# ============================================================
# HLR Models
# ============================================================

class HLRRequirement(BaseModel):
    """A single requirement from the HLR Word document."""

    requirement_id: str
    content: str
    object_type: str
    is_derived: str
    rationale: str
    is_safety_related: str
    verification_method: str
    implementation_method: str
    source_file: str


class HLRGlossaryEntry(BaseModel):
    """A glossary term from the HLR document."""

    abbreviation: str
    english_name: str
    chinese_description: str


class HLRLabel(BaseModel):
    """AI-generated structured labels for one HLR requirement."""

    hlr_id: str
    bus_types: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)
    signal_keywords: list[str] = Field(default_factory=list)
    attr_categories: list[str] = Field(default_factory=list)
    direction_keywords: list[str] = Field(default_factory=list)
    enriched_text: str = ""
    # Script-extracted fields (hlr_classifier.py)
    signal_category: str = ""  # "A429显式"|"模拟量"|"离散量"|"A429隐式"|"逻辑/非通信"
    bit_fields: list[dict] = Field(default_factory=list)  # [{offset, size, text}]
    sdi_value: str = ""  # extracted SDI value
    extracted_direction: str = ""  # "发送"|"接收"|"" (regex-determined)

    @property
    def bus_types_set(self) -> set[str]:
        return {b.lower() for b in self.bus_types}

    @property
    def devices_set(self) -> set[str]:
        return {d.lower() for d in self.devices}

    @property
    def signal_keywords_set(self) -> set[str]:
        return {s.lower() for s in self.signal_keywords}

    @property
    def attr_categories_set(self) -> set[str]:
        return {a.lower() for a in self.attr_categories}

    @property
    def direction_keywords_set(self) -> set[str]:
        return {d.lower() for d in self.direction_keywords}


class HLRLabelOutput(BaseModel):
    """Persisted AI labeling results."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_labeled: int
    labels: dict[str, HLRLabel]  # hlr_id → HLRLabel


class HLROutput(BaseModel):
    """Top-level output from HLR Word parsing."""

    source_file: str
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_count: int
    requirements: list[HLRRequirement]
    glossary: list[HLRGlossaryEntry]


# ============================================================
# Reverse Matching Models (HLR → EoICD)
# ============================================================


class HLRCoverageResult(BaseModel):
    """Coverage result for one HLR requirement against EoICD signal profiles (matching only)."""

    hlr_id: str
    hlr_content: str
    hlr_rationale: str = ""               # HLR rationale for AI context
    signal_category: str = ""          # 4-path classification
    match_type: str = ""               # "精确匹配" | "语义匹配" | "无匹配"
    matched_profile_keys: list[str] = Field(default_factory=list)
    matched_profile_count: int = 0
    match_evidence: dict = Field(default_factory=dict)  # match details (labels, scores, etc.)
    overall: str = ""                  # "matched" | "unmatched"
    summary: str = ""                  # human-readable summary


class ReverseMatchOutput(BaseModel):
    """Top-level output from reverse matching (HLR → EoICD, matching only)."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_hlr: int
    total_eoicd_profiles: int
    stats: dict[str, int] = Field(default_factory=dict)
    results: list[HLRCoverageResult] = Field(default_factory=list)
    eoicd_unmatched_profile_keys: list[str] = Field(default_factory=list)


# ============================================================
# Reverse Judgment Models (Agent output)
# ============================================================


class ReverseJudgmentResult(BaseModel):
    """AI judgment output for one reverse case (HLR → EoICD profiles)."""

    case_id: str
    # ── Source data (from case) ──
    hlr_id: str = ""
    hlr_content: str = ""
    signal_category: str = ""
    matched_profiles_summary: list[str] = Field(default_factory=list)
    match_evidence: dict = Field(default_factory=dict)
    # ── Agent judgment ──
    coverage_status: str = ""          # "covered" | "inconsistent" | "needs_review" | "无匹配" (match-layer) | "error" (失败兜底)
    difference_type: str = ""          # 无差异 | 缺失 | 不一致 | 部分覆盖 | 需确认
    missing_points: list[str] = Field(default_factory=list)
    inconsistent_points: list[str] = Field(default_factory=list)
    analysis: str = ""
    suggested_action: str = ""
    confidence: float = 0.0


class ReverseJudgmentOutput(BaseModel):
    """Persisted reverse judgment results."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_cases: int
    summary: dict = Field(default_factory=dict)  # overall judgment summary
    results: list[ReverseJudgmentResult] = Field(default_factory=list)


class ReverseCase(BaseModel):
    """One HLR requirement paired with matched EoICD profiles for AI judgment."""

    case_id: str                       # "REV-0001"
    hlr_requirement: dict              # {hlr_id, content, signal_category, labels, ...}
    matched_profiles: list[dict]       # serialized SignalProfile summaries
    match_evidence: dict = Field(default_factory=dict)


# ============================================================
# Consensus & Pipeline Models (Phase 1 architecture scaffolding)
# ============================================================


class MultiJudgeResult(BaseModel):
    """One case judged by multiple providers."""

    case_id: str
    judgments: dict[str, dict] = Field(default_factory=dict)  # provider_name → judgment dict


class MultiJudgeOutput(BaseModel):
    """Top-level output from multi-agent judging panel."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_cases: int
    providers: list[str] = Field(default_factory=list)
    results: list[MultiJudgeResult] = Field(default_factory=list)


class ConsensusResult(BaseModel):
    """共识复核结果（Phase 2-3，Review Agent 输出）。"""

    case_id: str
    model_results: dict[str, dict] = Field(default_factory=dict)
    agreement_level: str = ""     # "full" | "majority" | "split" | "single_source" | "no_consensus" (降级覆写)
    star_rating: int = 0          # 1-3
    final_coverage_status: str = ""
    final_analysis: str = ""
    confidence: float = 0.0
    consistent_agents: list[str] = Field(default_factory=list)
    divergent_agents: list[str] = Field(default_factory=list)
    inconsistent_attributes: list[dict] = Field(default_factory=list)


class ConsensusOutput(BaseModel):
    """Top-level output from review agent."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_cases: int
    summary: dict = Field(default_factory=dict)  # {star_distribution, agreement_stats, etc.}
    results: list[ConsensusResult] = Field(default_factory=list)


class PipelineResult(BaseModel):
    """管线执行结果摘要。"""

    parsed_count: int = 0
    match_count: int = 0
    judged_count: int = 0
    report_path: str = ""
    errors: list[str] = Field(default_factory=list)


# ============================================================
# Forward Completeness Models (EoICD → HLR)
# ============================================================


class ForwardIdentity(BaseModel):
    """Standardized EoICD business identity for one ForwardICDBlock.

    identity_key is the stable business_object_id, derived from protocol +
    protocol-stable fields (NOT from traceability FullName, and NOT using hash()).
    """

    identity_key: str
    protocol: str                     # A429 | A825 | Analog | Discrete | A664 | unknown
    label: str = ""                   # A429 label value (e.g. "121")
    signal_family: str = ""
    device: str = ""                  # Software segment
    port: str = ""                    # port / message
    message: str = ""
    channel: str = ""
    signal: str = ""                  # leaf name


class ForwardScopeItem(BaseModel):
    """One traceability FullName resolved against parsed EoICD, with candidate HLRs.

    candidate_hlr_ids keeps the RAW trace-referenced HLR ids (for audit).
    missing_hlr_ids are the raw candidates NOT present in the uploaded HLR doc;
    only the remaining (analyzable) ids participate in matching / AI review
    (正向缺陷修正 #4).
    """

    icd_fullname: str
    protocol: str = ""
    erd_ids: list[str] = Field(default_factory=list)
    candidate_hlr_ids: list[str] = Field(default_factory=list)
    missing_hlr_ids: list[str] = Field(default_factory=list)
    located_eoicd_signal_names: list[str] = Field(default_factory=list)


class ForwardScopeOutput(BaseModel):
    """C2 stage output: analysis scope + mode + candidate HLR set."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    analysis_mode: str = ""           # "trace" | "full"
    scope_source: str = ""
    total_scope_fullnames: int = 0
    total_candidate_hlrs: int = 0
    scope_items: list[ForwardScopeItem] = Field(default_factory=list)
    input_errors: list[dict] = Field(default_factory=list)  # {kind, detail}


class IdentityToken(BaseModel):
    """A single identity evidence token extracted from HLR text/index."""

    value: str
    normalized_value: str = ""
    source: str = ""                  # literal | regex | glossary | synonym | mapped | llm_cache | llm_label
    confidence: float = 1.0


class ForwardICDBlock(BaseModel):
    """Business-signal-level detection unit (not per-attribute)."""

    business_object_id: str
    identity: ForwardIdentity
    dp_signal_names: list[str] = Field(default_factory=list)
    rp_signal_names: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    dp_entry_ids: list[str] = Field(default_factory=list)
    rp_entry_ids: list[str] = Field(default_factory=list)
    variants: list[str] = Field(default_factory=list)   # A429 channels A1/A2/B1/B2
    devices: list[str] = Field(default_factory=list)
    trace: ForwardScopeItem | None = None
    unsupported: bool = False         # native A664 etc.


class ForwardBlocksOutput(BaseModel):
    """C3 stage output."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    analysis_mode: str = ""
    total_blocks: int = 0
    blocks: list[ForwardICDBlock] = Field(default_factory=list)


class ForwardCandidatesOutput(BaseModel):
    """C5 stage output: block_id → recalled candidate HLR ids."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_blocks: int = 0
    candidates: dict[str, list[str]] = Field(default_factory=dict)


class ForwardDeterministicResult(BaseModel):
    """C6 stage per-block deterministic rule result."""

    business_object_id: str
    rule_level: str = ""              # exact_fullname | exact_signal | ... | no_evidence
    coverage_status: str = ""         # covered_direct | covered_aggregate | parent_referenced | possible | uncovered
    matched_hlr_ids: list[str] = Field(default_factory=list)
    candidate_hlr_ids: list[str] = Field(default_factory=list)
    candidate_truncated: bool = False
    needs_ai: bool = False
    identity_tokens: list[IdentityToken] = Field(default_factory=list)


class ForwardDeterministicOutput(BaseModel):
    """C6 stage output."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_blocks: int = 0
    stats: dict = Field(default_factory=dict)
    results: list[ForwardDeterministicResult] = Field(default_factory=list)


class ForwardAIReviewResult(BaseModel):
    """C7 stage per-block AI three-state review."""

    business_object_id: str
    review_verdict: str = ""          # covered | not_same_object | unconfirmed
    matched_hlr_ids: list[str] = Field(default_factory=list)
    rejected_hlr_ids: list[str] = Field(default_factory=list)
    identity_evidence: list[dict] = Field(default_factory=list)
    confidence: float = 0.0
    rationale: str = ""
    error: str | None = None


class ForwardAIReviewOutput(BaseModel):
    """C7 stage raw AI review results."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_reviewed: int = 0
    stats: dict = Field(default_factory=dict)  # ai_review_total/ai_success/ai_timeout/ai_error
    results: list[ForwardAIReviewResult] = Field(default_factory=list)


class ForwardCoverageResult(BaseModel):
    """Final coverage result for one ForwardICDBlock."""

    business_object_id: str
    analysis_status: str = "supported"  # supported | unsupported | input_error
    coverage_status: str = ""           # final: covered_* | parent_referenced | possible | uncovered
    matched_hlr_ids: list[str] = Field(default_factory=list)
    evidence: list[IdentityToken] = Field(default_factory=list)
    source: str = "rule"                # rule | ai
    rule_level: str = ""
    candidate_truncated: bool = False
    referenced_variants: list[str] = Field(default_factory=list)
    unconfirmed_variants: list[str] = Field(default_factory=list)
    ai_review: ForwardAIReviewResult | None = None
    error: str | None = None


class ForwardCoverageOutput(BaseModel):
    """C7/C8 final coverage result set."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    analysis_mode: str = ""
    scope_source: str = ""
    stats: dict = Field(default_factory=dict)
    unsupported: list[dict] = Field(default_factory=list)
    input_errors: list[dict] = Field(default_factory=list)
    # Two independent AI call counters (正向缺陷修正 #8 audit)：HLR 标签调用 +
    # 正向三态复核调用。由 pipeline 在 consolidate 之后回填。
    hlr_label_calls: int = 0
    ai_review_calls: int = 0
    results: list[ForwardCoverageResult] = Field(default_factory=list)


class HLRIdentityEntry(BaseModel):
    """Identity extraction for one HLR requirement.

    signal_tokens are deterministic (regex / CN→EN map); llm_label_tokens are
    AI-derived (label_hlrs + enrich_all_labels) and can ONLY enhance candidate
    recall — they never support a covered conclusion.
    """

    hlr_id: str
    labels: list[str] = Field(default_factory=list)          # ["L275", ...] (regex)
    signal_tokens: list[str] = Field(default_factory=list)   # English + Chinese-mapped tokens
    llm_label_tokens: list[str] = Field(default_factory=list)  # AI labels (source="llm_label")
    direction: str = ""                                       # 发送 | 接收 | ""
    signal_category: str = ""                                 # A429显式 | 模拟量 | ...


class HLRIdentityIndex(BaseModel):
    """C4 stage output: deterministic HLR identity + inverted token index.

    token_index maps deterministic tokens; llm_token_index maps AI-derived
    tokens (source="llm_label") that only broaden recall, never coverage.
    """

    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_hlrs: int = 0
    entries: dict[str, HLRIdentityEntry] = Field(default_factory=dict)
    token_index: dict[str, list[str]] = Field(default_factory=dict)  # token -> [hlr_id] (deterministic)
    llm_token_index: dict[str, list[str]] = Field(default_factory=dict)  # token -> [hlr_id] (llm_label)
