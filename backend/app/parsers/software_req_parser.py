"""
软件高层需求解析模块。

按 SRS 文档固定模板：每个需求对应一张 8 行 × 2 列的表格，
键值对为 (字段名, 内容)。Table[0] 是缩略语表（3×3），跳过。

字段映射约定：
- 需求ID / 需求中文：保留原文
- 对象类型：需求 → requirement，注释 → comment
- 是否衍生：是 → True，其他一律 False（默认 False）
- 基本原理 / 验证方法：保留原文（NA / N/A 视为空）
- 实现方法：手工编码 → manual_coding，基于模型 → model_based

边界处理：
- 表格形状非 8×2 → 跳过 + debug log
- 需求ID 或 需求中文 为空 → 跳过该条 + warn log
- 对象类型 / 实现方法 未知值 → 原样保留 + warn log
- 不抛异常，保证任务不会因单条需求失败而整体崩溃
"""

from __future__ import annotations

import logging
from pathlib import Path

from docx import Document

from app.models import ParsedSoftwareRequirement, ParsedSoftwareRequirements

logger = logging.getLogger(__name__)

# 视为空的单元格内容
_EMPTY_CELL = {"", "NA", "N/A", "na", "n/a"}

# 字段标签映射
_OBJECT_TYPE_MAP = {
    "需求": "requirement",
    "注释": "comment",
}

_IS_DERIVED_TRUE = {"是", "yes", "Yes", "YES", "true", "True", "TRUE"}

_IMPLEMENTATION_METHOD_MAP = {
    "手工编码": "manual_coding",
    "基于模型": "model_based",
}


def _empty_or_val(v: str) -> str:
    """NA/N/A/空白 视为空字符串，否则返回原值（strip 后）。"""
    return "" if v.strip() in _EMPTY_CELL else v.strip()


def _map_object_type(v: str) -> str:
    """需求 → requirement，注释 → comment。未知值原样保留 + warn。"""
    s = v.strip()
    if s in _OBJECT_TYPE_MAP:
        return _OBJECT_TYPE_MAP[s]
    if not s or s in _EMPTY_CELL:
        return ""
    logger.warning("unknown object_type %r, keep as-is", s)
    return s


def _map_is_derived(v: str) -> bool:
    """是 → True，其他（含 否 / 空 / 未知）一律 False。"""
    return v.strip() in _IS_DERIVED_TRUE


def _map_implementation_method(v: str) -> str:
    """手工编码 → manual_coding，基于模型 → model_based。未知原样保留 + warn。"""
    s = v.strip()
    if s in _IMPLEMENTATION_METHOD_MAP:
        return _IMPLEMENTATION_METHOD_MAP[s]
    if not s or s in _EMPTY_CELL:
        return ""
    logger.warning("unknown implementation_method %r, keep as-is", s)
    return s


def _is_requirement_table(table) -> bool:
    """需求表固定 8 行 × 2 列。"""
    return len(table.rows) == 8 and len(table.columns) == 2


def _parse_one_table(table) -> dict[str, str]:
    """把一张 8×2 表格解析为 key→value 字典。"""
    kv: dict[str, str] = {}
    for row in table.rows:
        if len(row.cells) >= 2:
            key = row.cells[0].text.strip()
            val = row.cells[1].text.strip()
            kv[key] = val
    return kv


def parse_software_requirement(req_path: Path) -> ParsedSoftwareRequirements:
    """解析软件高层需求文档。

    Args:
        req_path: 软件高层需求 Word 文件路径

    Returns:
        ParsedSoftwareRequirements 结构化解析结果
    """
    doc = Document(req_path)
    requirements: list[ParsedSoftwareRequirement] = []

    for ti, table in enumerate(doc.tables):
        if not _is_requirement_table(table):
            logger.debug(
                "skip non-requirement table[%d] shape=%dx%d",
                ti, len(table.rows), len(table.columns),
            )
            continue

        kv = _parse_one_table(table)
        req_id = kv.get("需求ID", "")
        req_text = kv.get("需求中文", "")
        if not req_id or not req_text:
            logger.warning(
                "skip table[%d]: missing 需求ID or 需求中文 (id=%r text=%r)",
                ti, req_id, req_text[:30],
            )
            continue

        requirements.append(ParsedSoftwareRequirement(
            requirement_id=req_id,
            requirement_text=req_text,
            object_type=_map_object_type(kv.get("对象类型", "")),
            is_derived=_map_is_derived(kv.get("是否衍生", "")),
            rationale=_empty_or_val(kv.get("基本原理", "")),
            verification_method=_empty_or_val(kv.get("验证方法", "")),
            implementation_method=_map_implementation_method(kv.get("实现方法", "")),
            source_file=str(req_path),
        ))

    return ParsedSoftwareRequirements(requirements=requirements)