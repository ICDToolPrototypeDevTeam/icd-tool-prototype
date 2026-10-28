# -*- coding: utf-8 -*-
"""HLR Word parser — extracts software high-level requirements from .docx tables.

Profile-driven: field names, table positions, and non-requirement filtering
are all controlled by ControllerProfile.hlr_parser. AMS and FGMC are both
supported via profiles/{ams,fgmc}/config.yaml.
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document  # type: ignore
from docx.text.paragraph import Paragraph  # type: ignore

from app.v4.models import (
    HLRGlossaryEntry,
    HLROutput,
    HLRRequirement,
    HLRTermEntry,
)
from app.v4.parsers.glossary_tables import (
    detect_glossary_tables,
    parse_abbreviation_table,
    parse_term_table,
)
from app.v4.profiles.base import ControllerProfile


def _cell_text(table, row: int, col: int) -> str:
    """Extract stripped text from a table cell.

    ``cell.text`` only joins the cell's direct paragraphs, so content
    described by a nested table (e.g. the signal/bit/value rows following
    "按如下内容发送…") would be silently dropped.  When nested tables are
    present they are flattened (cells joined with " | ", rows with newline)
    in document order; cells without nested tables keep the previous
    ``cell.text`` result exactly.
    """
    try:
        cell = table.cell(row, col)
    except (IndexError, AttributeError):
        return ""
    if not cell.tables:
        return cell.text.strip()
    parts: list[str] = []
    for item in cell.iter_inner_content():
        if isinstance(item, Paragraph):
            parts.append(item.text)
        else:
            for r in item.rows:
                cells = (c.text.replace("\n", " ").strip() for c in r.cells)
                parts.append(" | ".join(cells))
    return "\n".join(parts).strip()


def _build_field_map_index(
    field_map: dict[str, tuple[str, ...]],
) -> dict[str, str]:
    """Reverse index: header text (lowercased) -> standard field name."""
    index: dict[str, str] = {}
    for std_field, headers in field_map.items():
        for h in headers:
            index[h.strip().lower()] = std_field
    return index


def _id_row_info(table, field_index: dict[str, str]) -> tuple[bool, str]:
    """Return ``(has_id_header_row, id_value)`` for one table.

    ``has_id_header_row`` is True when some row's col-0 header resolves via
    the field map to std_field ``"id"`` (first such row wins).
    ``id_value`` is that row's col-1 text ("" when absent or empty).

    Used by the requirement-scan layer (D2) to apply ``id_pattern`` and to
    distinguish "looks like a requirement table but id value is odd"
    (strong signal) from "no id row header at all" (weak signal).
    """
    for r in range(len(table.rows)):
        header = _cell_text(table, r, 0)
        if not header:
            continue
        if field_index.get(header.lower()) == "id":
            return True, _cell_text(table, r, 1)
    return False, ""


def _first_nonempty_cell(table) -> str:
    """First non-empty cell text of a table (row-major). For warning samples."""
    for row in table.rows:
        for cell in row.cells:
            t = cell.text.strip()
            if t:
                return t
    return ""


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
        terms: list[HLRTermEntry] = []
        requirements: list[HLRRequirement] = []
        parse_warnings: list[dict] = []

        field_index = _build_field_map_index(self.cfg.field_map)
        scan_cfg = self.cfg.requirement_scan
        id_re = re.compile(scan_cfg.id_pattern) if scan_cfg.id_pattern else None

        # Identify requirement tables before glossary detection so a
        # requirement table can never be mistaken for a glossary merely
        # because one of its field labels contains a glossary-like word.
        requirement_indices: set[int] = set()
        id_info: dict[int, tuple[bool, str]] = {}
        for idx in range(len(tables)):
            table = tables[idx]
            if len(table.columns) < 2:
                continue
            info = _id_row_info(table, field_index)
            id_info[idx] = info
            if info[0]:
                requirement_indices.add(idx)
        first_requirement_index = (
            min(requirement_indices) if requirement_indices else None
        )

        # Glossary (D1) + terminology tables: header-semantic detection, not
        # a fixed table index.  A profile-configured index remains as a
        # fallback so documents supported before this change keep parsing.
        specs, detect_warnings = detect_glossary_tables(
            tables,
            _cell_text,
            first_requirement_index=first_requirement_index,
            requirement_indices=requirement_indices,
        )
        detected_indices = {spec.table_index for spec in specs}

        glossary_idx = self._locate_glossary_table(tables)
        if (
            glossary_idx is not None
            and glossary_idx not in requirement_indices
            and glossary_idx not in detected_indices
        ):
            # Keep the pre-change parse path for the configured table.
            glossary = self._parse_glossary(tables[glossary_idx])
            detected_indices.add(glossary_idx)

        if scan_cfg.register_skipped:
            parse_warnings.extend(detect_warnings)

        seen_abbr = {entry.abbreviation.lower() for entry in glossary}
        seen_terms: set[str] = set()
        for spec in specs:
            table = tables[spec.table_index]
            if spec.kind == "abbreviation":
                for entry in parse_abbreviation_table(table, spec, _cell_text):
                    key = entry.abbreviation.lower()
                    if key not in seen_abbr:
                        seen_abbr.add(key)
                        glossary.append(entry)
            else:
                for entry in parse_term_table(table, spec, _cell_text):
                    key = entry.term.lower()
                    if key not in seen_terms:
                        seen_terms.add(key)
                        terms.append(entry)

        # Requirement tables (D2): scan range depends on mode.
        #  - all_tables: whole document (positions fully decoupled);
        #  - after_glossary: after the located glossary table; when the
        #    glossary was NOT located, the legacy start point
        #    (``glossary_table_index + 1``) is kept so default profiles are
        #    byte-identical to the pre-change behaviour.
        if scan_cfg.mode == "all_tables":
            start = 0
        elif glossary_idx is not None:
            start = glossary_idx + 1
        else:
            start = self.cfg.glossary_table_index + 1

        for idx in range(start, len(tables)):
            table = tables[idx]
            if idx in detected_indices:
                continue
            # No row-count gate: a requirement table may be missing field rows
            # (e.g. no "实现方法" row).  _extract_requirement decides by the
            # semantic criterion instead (field_map "id" row, value non-empty).
            if len(table.columns) < 2:
                continue
            has_id_row, id_value = id_info[idx]
            if id_re is not None and id_value and not id_re.match(id_value):
                # 强信号：有 id 行头行、值非空但不匹配 → 拒收（D2 登记）。
                if scan_cfg.register_skipped:
                    parse_warnings.append(
                        {
                            "table_index": idx,
                            "kind": "id_pattern_rejected",
                            "reason": (
                                f"id 值不匹配 id_pattern（{scan_cfg.id_pattern}）："
                                f"{id_value!r}"
                            ),
                            "sample": id_value[:120],
                        }
                    )
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
                continue
            # 弱信号：恰 2 列、≥ warn_min_rows 行、无 id 行头行——形状近似
            # 需求表却不是（如修订记录表）。宽表（目录表）与空表/小表不告警。
            if (
                scan_cfg.register_skipped
                and not has_id_row
                and len(table.columns) == 2
                and len(table.rows) >= scan_cfg.warn_min_rows
            ):
                parse_warnings.append(
                    {
                        "table_index": idx,
                        "kind": "no_requirement_fields",
                        "reason": "2 列表无「需求ID」行头行，未识别为需求表",
                        "sample": _first_nonempty_cell(table)[:120],
                    }
                )

        return HLROutput(
            source_file=self.source_name,
            total_count=len(requirements),
            requirements=requirements,
            glossary=glossary,
            terms=terms,
            parse_warnings=parse_warnings,
        )

    def _locate_glossary_table(self, tables) -> int | None:
        """Locate the glossary table index, or None when absent.

        mode="index" (legacy): the table at ``glossary_table_index``, parsed
        only when it has >=3 columns and its first row hits
        ``glossary_header_keywords`` (empty keywords = legacy always-parse).

        mode="keyword_scan" (D1): first table in document order whose first
        row hits the keywords (same >=3-column gate).  Missing → None →
        empty glossary.  Locating the glossary no longer implies anything
        about the requirement-scan range.
        """
        if self.cfg.glossary_locate.mode == "keyword_scan":
            for idx in range(len(tables)):
                tbl = tables[idx]
                if len(tbl.columns) >= 3 and self._looks_like_glossary(tbl):
                    return idx
            return None
        idx = self.cfg.glossary_table_index
        if (
            len(tables) > idx
            and len(tables[idx].columns) >= 3
            and self._looks_like_glossary(tables[idx])
        ):
            return idx
        return None

    def _looks_like_glossary(self, table) -> bool:
        """Gate glossary parsing on a header keyword.

        With ``glossary_header_keywords`` set (AMS/FGMC), the table at
        ``glossary_table_index`` is parsed only when one of its first-row
        cells contains a keyword; otherwise it is left unparsed and the
        glossary stays empty instead of picking up garbage entries from a
        non-glossary table (revision log, change record).  Empty keyword
        tuple keeps the legacy behaviour.
        """
        keywords = self.cfg.glossary_header_keywords
        if not keywords:
            return True
        for c in range(len(table.columns)):
            cell = _cell_text(table, 0, c)
            if any(kw in cell for kw in keywords):
                return True
        return False

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