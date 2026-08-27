# -*- coding: utf-8 -*-
"""Forward completeness Word report (Stage C8).

Renders omission / uncertain / unsupported / input-error lists into
forward_report.docx. 正向缺陷修正 #8：新增「输入异常」章节、两类 AI 调用统计，
并在清单表补充设备 / 输入异常列。
"""

from __future__ import annotations

from pathlib import Path

from app.v4.doc_generators.forward_excel_generator import _coverage_detail_row
from app.v4.models import ForwardBlocksOutput, ForwardCoverageOutput


def generate_forward_word(
    coverage: ForwardCoverageOutput,
    blocks: ForwardBlocksOutput,
    output_path: Path,
) -> None:
    """Generate the forward completeness Word report (omission + uncertain lists)."""
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn

    block_map = {b.business_object_id: b for b in blocks.blocks}
    rows = [
        _coverage_detail_row(block_map[r.business_object_id], r)
        for r in coverage.results
        if r.business_object_id in block_map
    ]

    uncovered = [r for r in rows if r["coverage_status"] == "uncovered"]
    uncertain = [r for r in rows if r["coverage_status"] in ("possible", "parent_referenced")]
    unsupported = [r for r in rows if r["analysis_status"] == "unsupported"]
    input_errors = [r for r in rows if r["analysis_status"] == "input_error"]
    anomalies = [r for r in rows if r["input_anomaly"] and r["analysis_status"] != "input_error"]

    def set_font(cell, text, bold=False, size=9, color=None):
        cell.text = ""
        p = cell.paragraphs[0]
        run = p.add_run(str(text))
        run.font.size = Pt(size)
        run.font.name = "微软雅黑"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        run.bold = bold
        if color:
            run.font.color.rgb = color

    def style_header(table, headers):
        for i, h in enumerate(headers):
            set_font(table.rows[0].cells[i], h, bold=True, size=9)
            tc = table.rows[0].cells[i]._element.get_or_add_tcPr()
            shd = tc.makeelement(qn("w:shd"), {qn("w:fill"): "D9E2F3", qn("w:val"): "clear"})
            tc.insert(0, shd)

    def fill_table(table, headers, data, color=None):
        for i, h in enumerate(headers):
            table.rows[0].cells[i].text = h
        style_header(table, headers)
        for r in data:
            row = table.add_row()
            set_font(row.cells[0], r["business_object_id"])
            set_font(row.cells[1], r["protocol"])
            set_font(row.cells[2], f"{r['label']} {r['signal_family']}".strip() or r["signal"])
            set_font(row.cells[3], r["device"])
            set_font(row.cells[4], r["coverage_label"], bold=True, color=color)
            set_font(row.cells[5], r["matched_hlr_ids"] or "—")
            set_font(row.cells[6], r["input_anomaly"] or "—")
            set_font(row.cells[7], r["rule_level"])

    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(29.7)
    section.page_height = Cm(21.0)
    section.left_margin = Cm(1.0)
    section.right_margin = Cm(1.0)

    title = doc.add_heading("EoICD 正向完整性分析报告", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(
        f"分析模式: {coverage.analysis_mode or 'N/A'}    "
        f"生成时间: {coverage.generated_at[:19]}"
    )
    doc.add_paragraph(
        f"AI 调用统计: HLR 标签调用 {coverage.hlr_label_calls} 次 / "
        f"正向三态复核调用 {coverage.ai_review_calls} 次"
    )

    doc.add_heading("一、覆盖状态概览", level=2)
    doc.add_paragraph("本报告从 EoICD 出发，检查每个业务对象在软件高层需求（HLR）正文中是否存在对应描述（漏写检测）。")
    for label, desc in [
        ("已覆盖", "HLR 中存在该 EoICD 业务对象的对应描述。"),
        ("仅父级引用", "HLR 仅引用父级接口（端口/消息/Label），未描述具体信号。"),
        ("待确认", "存在候选但无法确定是否描述了该对象，需人工审查。"),
        ("未覆盖（疑似漏写）", "HLR 正文中未找到该对象的对应描述，疑似漏写。"),
        ("不支持", "该对象的协议类型（如原生 A664）暂不支持分析。"),
        ("输入异常", "追溯表引用的候选 HLR 全部缺失于上传的 HLR 文档，无法分析。"),
    ]:
        p = doc.add_paragraph()
        p.add_run(f"{label}：").bold = True
        p.add_run(desc)

    total = len(rows)
    covered = total - len(uncovered) - len(uncertain) - len(unsupported) - len(input_errors)
    ot = doc.add_table(rows=1, cols=3)
    ot.style = "Table Grid"
    style_header(ot, ["覆盖状态", "数量", "占比"])
    for label, count in [
        ("已覆盖", covered),
        ("仅父级引用/待确认", len(uncertain)),
        ("未覆盖（疑似漏写）", len(uncovered)),
        ("不支持", len(unsupported)),
        ("输入异常", len(input_errors)),
    ]:
        row = ot.add_row()
        pct = f"{count / total * 100:.1f}%" if total else "0%"
        set_font(row.cells[0], label, bold=True)
        set_font(row.cells[1], str(count))
        set_font(row.cells[2], pct)
    row = ot.add_row()
    set_font(row.cells[0], "合计", bold=True)
    set_font(row.cells[1], str(total), bold=True)
    set_font(row.cells[2], "100%")

    doc.add_page_break()

    headers = ["对象ID", "协议", "Label/信号族", "设备", "覆盖状态", "匹配HLR", "输入异常", "规则等级"]
    red = RGBColor(0xCC, 0x33, 0x00)
    orange = RGBColor(0xCC, 0x55, 0x00)

    doc.add_heading("二、漏写清单（未覆盖）", level=2)
    doc.add_paragraph(f"共 {len(uncovered)} 个 EoICD 业务对象未在 HLR 中找到对应描述。")
    if uncovered:
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        fill_table(t, headers, uncovered, color=red)
    else:
        doc.add_paragraph("（无）")

    doc.add_heading("三、待确认清单", level=2)
    doc.add_paragraph(f"共 {len(uncertain)} 个对象存在候选但无法确定，需人工审查。")
    if uncertain:
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        fill_table(t, headers, uncertain, color=orange)
    else:
        doc.add_paragraph("（无）")

    doc.add_heading("四、输入异常清单", level=2)
    doc.add_paragraph(
        f"共 {len(input_errors)} 个对象因追溯表引用的候选 HLR 全部缺失于上传的 HLR 文档而无法分析；"
        f"另有 {len(anomalies)} 个对象存在部分候选缺失。"
    )
    if input_errors or anomalies:
        t = doc.add_table(rows=1, cols=3)
        t.style = "Table Grid"
        style_header(t, ["对象ID", "协议", "输入异常详情"])
        for r in input_errors:
            row = t.add_row()
            set_font(row.cells[0], r["business_object_id"])
            set_font(row.cells[1], r["protocol"])
            set_font(row.cells[2], r["input_anomaly"])
        for r in anomalies:
            row = t.add_row()
            set_font(row.cells[0], r["business_object_id"])
            set_font(row.cells[1], r["protocol"])
            set_font(row.cells[2], r["input_anomaly"])
    else:
        doc.add_paragraph("（无）")

    if unsupported:
        doc.add_heading("五、不支持清单", level=2)
        doc.add_paragraph(f"共 {len(unsupported)} 个对象因协议类型暂不支持分析。")
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        fill_table(t, headers, unsupported)
    else:
        doc.add_heading("五、不支持清单", level=2)
        doc.add_paragraph("（无）")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    print(f"  Forward Word: {output_path}")
