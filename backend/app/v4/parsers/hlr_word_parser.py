# -*- coding: utf-8 -*-
"""HLR Word parser — extracts software high-level requirements from .docx tables.

Profile-driven: field names, table positions, and non-requirement filtering
are all controlled by ControllerProfile.hlr_parser. AMS and FGMC are both
supported via profiles/{ams,fgmc}/config.yaml.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document  # type: ignore

from app.v4.models import HLRGlossaryEntry, HLROutput, HLRRequirement
from app.v4.profiles.base import ControllerProfile


def _cell_text(table, row: int, col: int) -> str:
    """Extract stripped text from a table cell."""
    try:
        return table.cell(row, col).text.strip()
    except (IndexError, AttributeError):
        return ""


def _build_field_map_index(
    field_map: dict[str, tuple[str, ...]],
) -> dict[str, str]:
    """Reverse index: header text (lowercased) -> standard field name."""
    index: dict[str, str] = {}
    for std_field, headers in field_map.items():
        for h in headers:
            index[h.strip().lower()] = std_field
    return index


def _extract_requirement(
    table,
    field_index: dict[str, str],
    source_name: str,
    filter_non_requirement: bool,
    non_requirement_value: str,
    skip_when_empty: bool,
    non_requirement_field_aliases: tuple[str, ...] = ("is_requirement",),
) -> HLRRequirement | None:
    """Extract one HLRRequirement from a table by matching row headers.

    Returns None if the row should be skipped (empty id, or
    filter_non_requirement=True with the indicator column marked '否').
    """
    # First pass: build a dict of {std_field: row_value} by reading row 0 (header)
    # then row 1 (value).  For multi-row tables the same pattern is repeated.
    rows_data: dict[str, str] = {}
    is_requirement_value: str | None = None

    for r in range(len(table.rows)):
        header = _cell_text(table, r, 0)
        if not header:
            continue
        header_lower = header.lower()
        std_field = field_index.get(header_lower)
        if std_field is None:
            continue
        value = _cell_text(table, r, 1)
        rows_data[std_field] = value

        # Track the is-requirement indicator.  The std_field name must be
        # one of the configured aliases (default: "is_requirement").  No
        # hardcoded Chinese-literal fallback — controllers that use a
        # different column name must declare it in field_map and add the
        # std_field to non_requirement_field_aliases in their profile.
        if std_field in non_requirement_field_aliases:
            is_requirement_value = value

    # Apply filter_non_requirement
    if filter_non_requirement:
        # If the table doesn't have an is_requirement column, keep it
        # (don't drop tables that lack the field)
        if is_requirement_value is not None:
            if is_requirement_value.strip() == non_requirement_value:
                return None

    # Skip rows with empty id (e.g., template rows)
    req_id = rows_data.get("id", "")
    if skip_when_empty and not req_id:
        return None

    return HLRRequirement(
        requirement_id=req_id,
        content=rows_data.get("content", ""),
        object_type=rows_data.get("object_type", ""),
        is_derived=rows_data.get("is_derived", ""),
        rationale=rows_data.get("rationale", ""),
        is_safety_related=rows_data.get("is_safety_related", ""),
        verification_method=rows_data.get("verification_method", ""),
        implementation_method=rows_data.get("implementation", ""),
        source_file=source_name,
        code=rows_data.get("code", ""),
        source=rows_data.get("source", ""),
        covered_ids=rows_data.get("covered_ids", ""),
        notes=rows_data.get("notes", ""),
        input_data=rows_data.get("input_data", ""),
        output_data=rows_data.get("output_data", ""),
    )


class HLRWordParser:
    """Parse a Software HLR Word document into structured requirements.

    Field mapping, glossary position, and non-requirement filtering
    are all driven by the injected ControllerProfile.
    """

    def __init__(self, source_path: Path, profile: ControllerProfile):
        self.source_path = source_path
        self.source_name = source_path.name
        self.profile = profile
        self.cfg = profile.hlr_parser

    def parse(self) -> HLROutput:
        """Main entry point. Parse the Word document and return HLROutput."""
        doc = Document(str(self.source_path))
        tables = doc.tables

        glossary: list[HLRGlossaryEntry] = []
        requirements: list[HLRRequirement] = []

        field_index = _build_field_map_index(self.cfg.field_map)

        # Glossary: tables[cfg.glossary_table_index]
        glossary_idx = self.cfg.glossary_table_index
        if (
            len(tables) > glossary_idx
            and len(tables[glossary_idx].columns) >= 3
        ):
            glossary = self._parse_glossary(tables[glossary_idx])

        # Requirement tables: everything after the glossary table
        for table in tables[glossary_idx + 1:]:
            if len(table.rows) < self.cfg.requirement_table_min_rows:
                continue
            if len(table.columns) < 2:
                continue
            req = _extract_requirement(
                table,
                field_index,
                self.source_name,
                filter_non_requirement=self.cfg.filter_non_requirement,
                non_requirement_value=self.cfg.non_requirement_value,
                skip_when_empty=self.cfg.skip_requirement_when_empty,
                non_requirement_field_aliases=self.cfg.non_requirement_field_aliases,
            )
            if req is not None:
                requirements.append(req)

        return HLROutput(
            source_file=self.source_name,
            total_count=len(requirements),
            requirements=requirements,
            glossary=glossary,
        )

    @staticmethod
    def _parse_glossary(table) -> list[HLRGlossaryEntry]:
        """Parse the glossary table (3 columns).

        First row is the header: 缩写, 英文全名, 中文说明
        """
        entries: list[HLRGlossaryEntry] = []
        for r in range(1, len(table.rows)):
            abbr = _cell_text(table, r, 0)
            eng = _cell_text(table, r, 1)
            chn = _cell_text(table, r, 2)
            if abbr:
                entries.append(
                    HLRGlossaryEntry(
                        abbreviation=abbr,
                        english_name=eng,
                        chinese_description=chn,
                    )
                )
        return entries