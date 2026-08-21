# -*- coding: utf-8 -*-
"""HLR Word parser — extracts software high-level requirements from .docx tables.

This parser is config-driven: it accepts a per-system configuration dict from
``app.v4.config.get_hlr_system_config`` so the same code can parse multiple
HLR layouts (e.g. ``hvac`` and ``fuel``) without hard-coded row/column
positions.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document  # type: ignore

from app.v4.models import HLRGlossaryEntry, HLROutput, HLRRequirement


def _cell_text(table, row: int, col: int) -> str:
    """Extract stripped text from a table cell. Return empty string on error."""
    try:
        return table.cell(row, col).text.strip()
    except (IndexError, AttributeError):
        return ""


class HLRWordParser:
    """Parse a Software HLR Word document into structured requirements.

    The first table is treated as the glossary (3 columns:
    abbreviation, English full name, Chinese description).
    Subsequent tables are expected to be requirement tables
    (configurable row count, 2 columns).
    """

    def __init__(self, source_path: Path, system_config: dict):
        self.source_path = source_path
        self.source_name = source_path.name
        self.system_config = system_config
        self.doc = Document(str(source_path))

    def parse(self) -> HLROutput:
        """Main entry point."""
        # 使用配置解析术语表
        glossary = self._parse_glossary(
            table_index=self.system_config["glossary_table_index"],
            cols=self.system_config["glossary_cols"],
        )

        # 解析需求表
        requirements: list[HLRRequirement] = []
        req_rows = self.system_config["requirement_rows"]

        for table in self.doc.tables[1:]:  # 跳过术语表
            if len(table.rows) == req_rows and len(table.columns) == 2:
                req = self._extract_requirement(table)
                # 过滤掉 is_requirement=False 的非需求条目
                if req is not None and req.requirement_id:
                    requirements.append(req)

        return HLROutput(
            source_file=self.source_name,
            total_count=len(requirements),
            requirements=requirements,
            glossary=glossary,
        )

    def _parse_glossary(self, table_index: int, cols: int) -> list[HLRGlossaryEntry]:
        """根据配置解析术语表"""
        if table_index >= len(self.doc.tables):
            return []
        table = self.doc.tables[table_index]
        if len(table.columns) < cols:
            return []
        entries: list[HLRGlossaryEntry] = []
        for r in range(1, min(len(table.rows), 50)):
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

    def _extract_requirement(self, table) -> HLRRequirement | None:
        """根据配置动态提取需求字段

        需求表为2列结构(列0=字段名, 列1=值)

        如果 is_requirement=False（非需求条目），返回 None 由调用方过滤
        """
        field_rows = self.system_config["field_rows"]

        # 提取各字段(值在列1)
        requirement_id = _cell_text(table, field_rows["requirement_id"], 1)
        content = _cell_text(table, field_rows["content"], 1)

        # is_requirement 特殊处理
        is_req_row = field_rows["is_requirement"]
        is_req_cell = _cell_text(table, is_req_row, 1)
        if self.system_config.get("is_requirement_is_boolean"):
            is_requirement = is_req_cell.lower() in ("是", "true", "yes")
        else:
            is_requirement = is_req_cell == self.system_config.get("is_requirement_value")

        # 如果不是需求条目，返回 None 由调用方过滤
        if not is_requirement:
            return None

        # object_type: 优先使用 object_type_value 配置
        if "object_type_value" in self.system_config:
            object_type = self.system_config["object_type_value"]
        else:
            object_type = _cell_text(table, field_rows["content"], 0)

        # is_derived
        is_derived_cell = _cell_text(table, field_rows["is_derived"], 1)
        if self.system_config.get("is_derived_is_boolean"):
            is_derived = "是" if is_derived_cell.lower() in ("是", "true", "yes") else "否"
        else:
            is_derived = is_derived_cell

        # rationale
        rationale = _cell_text(table, field_rows["rationale"], 1)

        # is_safety_related
        is_safety_cell = _cell_text(table, field_rows["is_safety_related"], 1)
        is_safety_related = (
            is_safety_cell.lower() in ("是", "true", "yes") if is_safety_cell else False
        )

        # verification_method
        verification_method = _cell_text(table, field_rows["verification_method"], 1)

        # implementation_method(可能为空)
        impl_row = field_rows.get("implementation_method")
        implementation_method = (
            _cell_text(table, impl_row, 1) if impl_row is not None else ""
        )

        return HLRRequirement(
            requirement_id=requirement_id,
            content=content,
            object_type=object_type,
            is_derived=is_derived,
            rationale=rationale,
            is_safety_related=str(is_safety_related),
            verification_method=verification_method,
            implementation_method=implementation_method,
            source_file=self.source_name,
        )
