# -*- coding: utf-8 -*-
"""Forward completeness Excel detail sheet (Stage C8).

Renders the consolidated ForwardCoverageOutput into a flat per-block detail
sheet (forward_coverage.xlsx). The `_STATUS_LABELS` / `_coverage_detail_row`
helpers are shared with the Word generator.
"""

from __future__ import annotations

from pathlib import Path

from app.v4.models import ForwardBlocksOutput, ForwardCoverageOutput, ForwardCoverageResult

_STATUS_LABELS = {
    "covered_direct": "已覆盖",
    "covered_aggregate": "已覆盖（聚合）",
    "parent_referenced": "仅父级引用",
    "possible": "待确认",
    "uncovered": "未覆盖（疑似漏写）",
    "unsupported": "不支持",
    "input_error": "输入异常",
    "": "—",
}


def _final_status_label(result: ForwardCoverageResult) -> str:
    """Unified display label: unsupported / input_error use analysis_status, the
    rest use coverage_status. 这样 Word/Excel 都能用一个「覆盖状态」列统一展示，
    且 unsupported / input_error 不再显示为「—」。
    """
    if result.analysis_status == "unsupported":
        return "不支持"
    if result.analysis_status == "input_error":
        return "输入异常"
    return _STATUS_LABELS.get(result.coverage_status, result.coverage_status or "—")


def _natural_reason(result: ForwardCoverageResult, missing_count: int) -> str:
    """用户可读的自然语言原因，不出现 weak_signal / trace_only / not_same_object
    等内部术语，也不展开完整缺失 HLR ID（仅数量）。
    """
    as_status = result.analysis_status
    cs = result.coverage_status

    if as_status == "unsupported":
        return "该对象的协议类型（原生 A664）暂不支持分析。"
    if as_status == "input_error":
        return f"追溯表引用的候选 HLR 全部缺失于上传的 HLR 文档，无法分析（缺失 {missing_count} 条）。"

    if cs == "uncovered":
        if missing_count:
            return f"HLR 正文中未找到该对象的对应描述，疑似漏写；另有 {missing_count} 条追溯候选缺失。"
        return "HLR 正文中未找到该对象的对应描述，疑似漏写。"
    if cs == "parent_referenced":
        return "HLR 仅引用父级接口（端口/消息/Label），未描述具体信号，需人工确认。"
    if cs == "possible":
        if missing_count:
            return f"追溯候选 HLR 缺失 {missing_count} 条，无法确认是否已覆盖。"
        rule_level = result.rule_level
        if rule_level == "generic_signal":
            return "仅匹配到通用词（如 STATUS/STATE/VOLTAGE 等），无法确认是否描述该对象。"
        if rule_level == "weak_signal":
            return "仅匹配到单一短小片段，证据不足，无法确认是否描述该对象。"
        if rule_level == "trace_only":
            return "存在候选 HLR 但无文本重叠，无法确认是否描述该对象。"
        if result.source == "ai":
            return "AI 复核无法确认是否描述该对象，需人工审查。"
        return "无法确认是否描述该对象，需人工审查。"
    if cs in ("covered_direct", "covered_aggregate"):
        return "已在 HLR 中找到该对象的对应描述。"
    return ""


def _coverage_detail_row(block, result: ForwardCoverageResult) -> dict:
    """Flatten a block + result into one Excel/Word row dict.

    只保留报告所需字段：对象身份（对象ID / 协议 / Label / 信号族 / 信号 / 设备）、
    最终状态（覆盖状态 / 原因）、审计（匹配 HLR / 缺失 HLR 数量 / 完整缺失 HLR ID）。
    Word 只用其中的「EoICD ID / 协议 / 信号族 / 设备 / 覆盖状态 / 原因」六列，
    完整缺失 HLR ID 仅保留在 Excel / JSON 供审计（Word 只显示数量）。
    """
    ident = block.identity
    trace = block.trace

    missing = list(trace.missing_hlr_ids) if trace else []

    return {
        "business_object_id": result.business_object_id,
        "protocol": ident.protocol,
        "label": f"L{ident.label}" if ident.label else "",
        "signal_family": ident.signal_family,
        "signal": ident.signal,
        "device": ident.device or (", ".join(block.devices) if block.devices else ""),
        "missing_candidates": ", ".join(missing),
        "missing_count": len(missing),
        "analysis_status": result.analysis_status,
        "coverage_status": result.coverage_status,
        "coverage_label": _final_status_label(result),
        "reason": _natural_reason(result, len(missing)),
        "matched_hlr_ids": ", ".join(result.matched_hlr_ids),
    }


def generate_forward_excel(
    coverage: ForwardCoverageOutput,
    blocks: ForwardBlocksOutput,
    output_path: Path,
) -> None:
    """Generate the forward coverage detail Excel sheet."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    block_map = {b.business_object_id: b for b in blocks.blocks}
    rows = [
        _coverage_detail_row(block_map[r.business_object_id], r)
        for r in coverage.results
        if r.business_object_id in block_map
    ]

    header_font = Font(name="微软雅黑", size=10, bold=True)
    header_fill = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center")
    cell_font = Font(name="微软雅黑", size=9)
    wrap_align = Alignment(vertical="top", wrap_text=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "正向完整性分析"

    headers = [
        "序号", "对象ID", "协议", "Label", "信号族", "信号", "设备",
        "覆盖状态", "原因", "匹配HLR", "缺失HLR数量", "缺失候选HLR",
    ]
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = header_align

    for i, row in enumerate(rows, 1):
        values = [
            i, row["business_object_id"], row["protocol"], row["label"],
            row["signal_family"], row["signal"], row["device"],
            row["coverage_label"], row["reason"], row["matched_hlr_ids"],
            row["missing_count"], row["missing_candidates"],
        ]
        for col, v in enumerate(values, 1):
            cell = ws.cell(row=i + 1, column=col, value=v)
            cell.font = cell_font
            cell.alignment = wrap_align

    widths = [6, 30, 9, 9, 24, 24, 14, 14, 46, 22, 10, 22]
    for idx, w in enumerate(widths, 1):
        col_letter = _column_letter(idx)
        ws.column_dimensions[col_letter].width = w

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{_column_letter(len(headers))}{len(rows) + 1}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output_path))
    print(f"  Forward Excel: {output_path}")


def _column_letter(idx: int) -> str:
    """Convert a 1-based column index to an Excel column letter (supports >26)."""
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
