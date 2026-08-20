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
