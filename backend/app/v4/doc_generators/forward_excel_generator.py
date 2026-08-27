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


def _coverage_detail_row(block, result: ForwardCoverageResult) -> dict:
    """Flatten a block + result into one Excel/Word row dict.

    正向缺陷修正 #8：补齐审计字段 —— 设备、追溯 ERD、原始/可分析/缺失候选 HLR、
    命中 token（来源）、候选截断、DP/RP 信号、通道变体、输入异常、AI 判定/理由/置信度。
    """
    ident = block.identity
    trace = block.trace

    raw_candidates = list(trace.candidate_hlr_ids) if trace else []
    missing = list(trace.missing_hlr_ids) if trace else []
    missing_set = set(missing)
    analyzable_candidates = [h for h in raw_candidates if h not in missing_set] if trace else []

    hit_tokens = ", ".join(
        f"{t.value}" + (f"[{t.source}]" if t.source else "") for t in result.evidence
    )

    # 输入异常（块级）：全部候选缺失 → input_error；部分缺失 → 记录 anomaly。
    if result.analysis_status == "input_error":
        input_anomaly = "输入异常：全部候选HLR缺失(missing_hlr_input)"
    elif missing:
        input_anomaly = f"部分候选HLR缺失: {', '.join(missing)}"
    else:
        input_anomaly = ""

    ai = result.ai_review
    ai_verdict = ""
    ai_confidence = ""
    ai_rationale = ""
    if ai is not None:
        ai_verdict = ai.review_verdict or (ai.error or "")
        ai_confidence = f"{ai.confidence:.2f}" if ai.confidence else ""
        ai_rationale = ai.rationale or ""

    return {
        "business_object_id": result.business_object_id,
        "protocol": ident.protocol,
        "label": f"L{ident.label}" if ident.label else "",
        "signal_family": ident.signal_family,
        "signal": ident.signal,
        "device": ident.device or (", ".join(block.devices) if block.devices else ""),
        "dp_signals": ", ".join(block.dp_signal_names),
        "rp_signals": ", ".join(block.rp_signal_names),
        "channel_variants": ", ".join(block.variants),
        "trace_erd": ", ".join(trace.erd_ids) if trace else "",
        "raw_candidates": ", ".join(raw_candidates),
        "analyzable_candidates": ", ".join(analyzable_candidates),
        "missing_candidates": ", ".join(missing),
        "analysis_status": result.analysis_status,
        "coverage_status": result.coverage_status,
        "coverage_label": _STATUS_LABELS.get(result.coverage_status, result.coverage_status),
        "matched_hlr_ids": ", ".join(result.matched_hlr_ids),
        "hit_tokens": hit_tokens,
        "candidate_truncated": "是" if result.candidate_truncated else "",
        "input_anomaly": input_anomaly,
        "ai_verdict": ai_verdict,
        "ai_confidence": ai_confidence,
        "ai_rationale": ai_rationale,
        "source": result.source,
        "rule_level": result.rule_level,
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
        "DP信号", "RP信号", "通道变体", "追溯ERD", "原始追溯候选HLR",
        "可分析候选HLR", "缺失候选HLR", "覆盖状态", "分析状态", "匹配HLR",
        "命中Token(来源)", "候选截断", "输入异常", "AI判定", "AI置信度",
        "AI理由", "来源", "规则等级",
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
            row["dp_signals"], row["rp_signals"], row["channel_variants"],
            row["trace_erd"], row["raw_candidates"], row["analyzable_candidates"],
            row["missing_candidates"], row["coverage_label"], row["analysis_status"],
            row["matched_hlr_ids"], row["hit_tokens"], row["candidate_truncated"],
            row["input_anomaly"], row["ai_verdict"], row["ai_confidence"],
            row["ai_rationale"], row["source"], row["rule_level"],
        ]
        for col, v in enumerate(values, 1):
            cell = ws.cell(row=i + 1, column=col, value=v)
            cell.font = cell_font
            cell.alignment = wrap_align

    widths = [6, 30, 9, 9, 22, 22, 14, 20, 20, 14, 14, 20, 20, 18, 14, 10, 16, 20, 8, 20, 12, 8, 24, 8, 14]
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
