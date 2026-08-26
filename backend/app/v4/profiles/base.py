"""Controller profile data structures and loaders."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ProfileLoadError(Exception):
    """Raised when a profile cannot be loaded or found."""


@dataclass(frozen=True)
class HLRParserConfig:
    glossary_table_index: int
    requirement_table_min_rows: int
    field_map: dict[str, tuple[str, ...]]
    filter_non_requirement: bool = False
    non_requirement_value: str = "否"
    skip_requirement_when_empty: bool = True
    # std_field names whose value is the "is this a requirement?" indicator.
    # The parser picks the first alias that resolves to a std_field present
    # in the table headers.  Replaces the previous hardcoded Chinese-literal
    # fallback so new controllers can declare any column name they use.
    non_requirement_field_aliases: tuple[str, ...] = ("is_requirement",)


@dataclass(frozen=True)
class ClassifierKeywords:
    analog: tuple[str, ...] = ()
    discrete: tuple[str, ...] = ()
    bus: tuple[str, ...] = ()
    direction_send: tuple[str, ...] = ()
    direction_receive: tuple[str, ...] = ()


@dataclass(frozen=True)
class SheetMatchConfig:
    by_name_keywords: tuple[str, ...] = ()
    fallback_index: int = 0


@dataclass(frozen=True)
class TraceabilityTableConfig:
    filename_patterns: tuple[str, ...]
    sheet_match: SheetMatchConfig
    columns: dict[str, int]
    skip_module: tuple[str, ...] = ()
    data_start_row: int = 1


@dataclass(frozen=True)
class TraceabilityConfig:
    table1: TraceabilityTableConfig  # ERD ↔ ICD
    table2: TraceabilityTableConfig  # ERD ↔ HLR


@dataclass(frozen=True)
class AILabelingConfig:
    device_examples: tuple[str, ...] = ()
    signal_examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class HLRParserDriverConfig:
    """Selects the HLR parser implementation.

    Defaults to ``docx`` (legacy behaviour, ``HLRWordParser``). RPDU-style
    controllers whose HLR lives in an Excel sheet declare ``driver: xlsx``
    so the factory dispatches to ``HLRExcelParser``.

    Adding a new driver is opt-in: profiles that omit this section keep
    the existing Word-only path.
    """

    driver: str = "docx"  # "docx" | "xlsx"


@dataclass(frozen=True)
class MatcherEnhancementConfig:
    """Profile-specific knobs for the reverse-matcher scorer.

    Every flag defaults to False / 20 so AMS/FGMC/HSCU — which omit this
    section entirely — keep byte-identical scoring. Only RPDU (and any
    future profile that declares them) opt in.

    Attributes:
        enable_cn_suffix_strip: Strip common Chinese suffixes
            ("状态", "命令", "值", ...) from HLR signal keywords and family
            strings before boundary matching. Lets an HLR that writes
            ``EDP_TCB_STATUS_034状态`` correctly match a family named
            ``EDP_TCB_STATUS_034``.
        enable_direction_soft_on_exact_signal: When HLR direction
            contradicts the block's direction but the signal name already
            matches exactly (>= 30 pts), DON'T drop the candidate. Tag it
            with ``direction_conflict`` so the AI judge can review the
            inconsistency (used for "注入故障" / 文档笔误 scenarios).
        enable_signal_number_bonus: Award extra points when the HLR
            content contains a 3+ digit signal number that also appears in
            the block's signal_family. Skips protocol constants like
            "200", "429", "664" to avoid noise.
        top_k: Override the hardcoded ``_TOP_K = 20`` for candidates
            passed downstream. RPDU raises this to 50 because its HLR
            catalogue expects a wider candidate window.
    """

    enable_cn_suffix_strip: bool = False
    enable_direction_soft_on_exact_signal: bool = False
    enable_signal_number_bonus: bool = False
    top_k: int = 20  # default matches legacy hardcoded constant


@dataclass(frozen=True)
class HLRPreprocessConfig:
    """Optional HLR pre-processing configuration for profile-specific transformations.

    Used by `apply_hlr_preprocess_hook()` in profiles/__init__.py. The pipeline
    invokes the hook after HLR Word parsing and before AI labeling, so any
    rewriting of `hlr.content` flows downstream into classifier/labeler/matcher.

    Attributes:
        enabled: Whether this profile declares an HLR preprocess step.
        extra_mappings: dict mapping symbolic HLR terms (e.g. ``LBL_DIS_00_SYS1``)
            to their canonical EoICD-encoded form (e.g. ``L145_DIS_00_SYS1``).
            The hook substitutes occurrences in ``hlr.content`` (append_aliases
            strategy: original text + appended aliased form, so AI labeler still
            sees both forms).
        auto_parse_hlr_table_0: If True, parse HLR Word table[0] as an LABEL
            catalog (cols: name | octal | ...), using ``table0_name_column``
            and ``table0_octal_column`` as field positions. Combined with
            ``extra_mappings`` (Table 0 entries act as the base, extra_mappings
            extend/override).
        table0_name_column: 0-based column index for symbolic label name.
        table0_octal_column: 0-based column index for octal label number.
        apply_to_fields: which HLR fields to rewrite; default ``("content",)``.
            Other fields (``signal_keywords``, ``labels``) are filled in by the
            AI labeler / classifier after the hook runs.
    """

    enabled: bool = False
    extra_mappings: tuple[tuple[str, str], ...] = ()
    auto_parse_hlr_table_0: bool = False
    table0_name_column: int = 1
    table0_octal_column: int = 2
    apply_to_fields: tuple[str, ...] = ("content",)


@dataclass(frozen=True)
class ControllerProfile:
    profile_id: str
    display_name: str
    version: str
    hlr_parser: HLRParserConfig
    classifier_keywords: ClassifierKeywords
    traceability: TraceabilityConfig
    ai_labeling: AILabelingConfig
    hlr_preprocess: HLRPreprocessConfig = field(
        default_factory=HLRPreprocessConfig
    )
    hlr_parser_driver: HLRParserDriverConfig = field(
        default_factory=HLRParserDriverConfig
    )
    matcher: MatcherEnhancementConfig = field(
        default_factory=MatcherEnhancementConfig
    )
    # Trace strategy: "profile_columns" (legacy, controlled by columns: cfg)
    # or "header_adaptive" (RPDU-style: scan header row for ERD/ICD/HLR
    # keywords to locate columns automatically).
    trace_strategy: str = "profile_columns"
    # Issue #74 (RPDU): run Group A matching per-HLR instead of union-pool.
    # Each HLR sees only its own traced EoICD blocks, preventing unrelated
    # blocks (from other HLRs sharing the same EoICD) from polluting the
    # candidate pool and overflowing top_k=50 with status/noise signals.
    # Defaults to False so AMS/FGMC/HSCU keep legacy union-pool semantics.
    prefilter_per_hlr: bool = False


def _to_tuple(value: Any) -> tuple:
    """Convert list to tuple, pass through other types."""
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(value)
    return value


def _parse_hlr_parser(data: dict[str, Any]) -> HLRParserConfig:
    field_map_raw = data.get("field_map", {})
    field_map = {k: tuple(v) for k, v in field_map_raw.items()}
    return HLRParserConfig(
        glossary_table_index=int(data.get("glossary_table_index", 0)),
        requirement_table_min_rows=int(data.get("requirement_table_min_rows", 8)),
        field_map=field_map,
        filter_non_requirement=bool(data.get("filter_non_requirement", False)),
        non_requirement_value=str(data.get("non_requirement_value", "否")),
        skip_requirement_when_empty=bool(data.get("skip_requirement_when_empty", True)),
        non_requirement_field_aliases=_to_tuple(
            data.get("non_requirement_field_aliases", ["is_requirement"])
        ),
    )


def _parse_classifier_keywords(data: dict[str, Any]) -> ClassifierKeywords:
    return ClassifierKeywords(
        analog=_to_tuple(data.get("analog", [])),
        discrete=_to_tuple(data.get("discrete", [])),
        bus=_to_tuple(data.get("bus", [])),
        direction_send=_to_tuple(data.get("direction_send", [])),
        direction_receive=_to_tuple(data.get("direction_receive", [])),
    )


def _parse_sheet_match(data: dict[str, Any]) -> SheetMatchConfig:
    return SheetMatchConfig(
        by_name_keywords=_to_tuple(data.get("by_name_keywords", [])),
        fallback_index=int(data.get("fallback_index", 0)),
    )


def _parse_traceability_table(data: dict[str, Any]) -> TraceabilityTableConfig:
    sheet_match_data = data.get("sheet_match", {})
    columns = {k: int(v) for k, v in (data.get("columns", {}) or {}).items()}
    return TraceabilityTableConfig(
        filename_patterns=_to_tuple(data.get("filename_patterns", [])),
        sheet_match=_parse_sheet_match(sheet_match_data),
        columns=columns,
        skip_module=_to_tuple(data.get("skip_module", [])),
        data_start_row=int(data.get("data_start_row", 1)),
    )


def _parse_traceability(data: dict[str, Any]) -> TraceabilityConfig:
    return TraceabilityConfig(
        table1=_parse_traceability_table(data.get("table1", {})),
        table2=_parse_traceability_table(data.get("table2", {})),
    )


def _parse_ai_labeling(data: dict[str, Any]) -> AILabelingConfig:
    return AILabelingConfig(
        device_examples=_to_tuple(data.get("device_examples", [])),
        signal_examples=_to_tuple(data.get("signal_examples", [])),
    )


def _parse_hlr_parser_driver(data: dict[str, Any] | None) -> HLRParserDriverConfig:
    if not data:
        return HLRParserDriverConfig()
    driver = str(data.get("driver", "docx")).lower().strip()
    if driver not in ("docx", "xlsx"):
        raise ProfileLoadError(
            f"hlr_parser_driver.driver must be 'docx' or 'xlsx', got {driver!r}"
        )
    return HLRParserDriverConfig(driver=driver)


def _parse_matcher_enhancement(data: dict[str, Any] | None) -> MatcherEnhancementConfig:
    if not data:
        return MatcherEnhancementConfig()
    top_k_raw = data.get("top_k", 20)
    try:
        top_k = int(top_k_raw)
    except (TypeError, ValueError) as e:
        raise ProfileLoadError(
            f"matcher.top_k must be an integer, got {top_k_raw!r}"
        ) from e
    return MatcherEnhancementConfig(
        enable_cn_suffix_strip=bool(data.get("enable_cn_suffix_strip", False)),
        enable_direction_soft_on_exact_signal=bool(
            data.get("enable_direction_soft_on_exact_signal", False)
        ),
        enable_signal_number_bonus=bool(data.get("enable_signal_number_bonus", False)),
        top_k=top_k,
    )


def _parse_hlr_preprocess(data: dict[str, Any] | None) -> HLRPreprocessConfig:
    if not data:
        return HLRPreprocessConfig()
    extra_raw = data.get("extra_mappings", {}) or {}
    if not isinstance(extra_raw, dict):
        raise ProfileLoadError(
            f"hlr_preprocess.extra_mappings must be a dict, got {type(extra_raw).__name__}"
        )
    extra_mappings = tuple((str(k), str(v)) for k, v in extra_raw.items())
    return HLRPreprocessConfig(
        enabled=bool(data.get("enabled", True)),
        extra_mappings=extra_mappings,
        auto_parse_hlr_table_0=bool(data.get("auto_parse_hlr_table_0", False)),
        table0_name_column=int(data.get("table0_name_column", 1)),
        table0_octal_column=int(data.get("table0_octal_column", 2)),
        apply_to_fields=_to_tuple(data.get("apply_to_fields", ["content"])),
    )


def load_profile_from_yaml(yaml_path: Path) -> ControllerProfile:
    """Parse a single profile config.yaml into a ControllerProfile.

    Raises ProfileLoadError if the file is missing or invalid.
    """
    if not yaml_path.exists():
        raise ProfileLoadError(f"Profile config not found: {yaml_path}")
    try:
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ProfileLoadError(f"YAML parse error in {yaml_path}: {e}") from e

    profile_meta = data.get("profile", {})
    if "id" not in profile_meta:
        raise ProfileLoadError(f"Profile config missing 'profile.id': {yaml_path}")

    return ControllerProfile(
        profile_id=str(profile_meta["id"]),
        display_name=str(profile_meta.get("display_name", profile_meta["id"])),
        version=str(profile_meta.get("version", "1.0")),
        hlr_parser=_parse_hlr_parser(data.get("hlr_parser", {})),
        classifier_keywords=_parse_classifier_keywords(data.get("classifier_keywords", {})),
        traceability=_parse_traceability(data.get("traceability", {})),
        ai_labeling=_parse_ai_labeling(data.get("ai_labeling", {})),
        hlr_preprocess=_parse_hlr_preprocess(data.get("hlr_preprocess")),
        hlr_parser_driver=_parse_hlr_parser_driver(data.get("hlr_parser_driver")),
        matcher=_parse_matcher_enhancement(data.get("matcher")),
        trace_strategy=str(data.get("trace_strategy", "profile_columns")),
        prefilter_per_hlr=bool(data.get("prefilter_per_hlr", False)),
    )
