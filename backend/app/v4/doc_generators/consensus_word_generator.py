# -*- coding: utf-8 -*-
"""Word document generator: multi-model consensus review report with star ratings."""

from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn


def _set_cell_font(cell, text: str, bold: bool = False, size: int = 9, color=None):
    """Set cell text with formatting."""
    cell.text = ""
    p = cell.paragraphs[0]
    run = p.add_run(str(text))
    run.font.size = Pt(size)
    run.font.name = "微软雅黑"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    run.bold = bold
    if color:
        run.font.color.rgb = color


def _style_header_row(table, headers: list[str]):
    """Style the header row of a table."""
    header_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        _set_cell_font(header_cells[i], h, bold=True, size=9)
        shading = header_cells[i]._element.get_or_add_tcPr()
        shd = shading.makeelement(qn("w:shd"), {
            qn("w:fill"): "D9E2F3",
            qn("w:val"): "clear",
        })
        shading.insert(0, shd)


def _star_str(n: int) -> str:
    """Render star rating as visual string: ★★★ (3), ★★☆ (2), ★☆☆ (1)."""
    return "★" * n + "☆" * (3 - n)


def generate_consensus_report(
    consensus_path: Path,
    match_path: Path,
    output_path: Path,
) -> None:
    """Generate a multi-model consensus analysis Word document.

    Reads consensus_results.json + reverse_matches.json and produces
    a report with star ratings per HLR, agreement metrics, and per-model
    judgment details.

    Columns: 序号 | HLR ID | 信号类别 | 判定 | 匹配类型
             ICD Block | 分析摘要 | 共识 | 星级
    """
    consensus_data = json.loads(consensus_path.read_text(encoding="utf-8"))
    match_data = json.loads(match_path.read_text(encoding="utf-8"))

    consensus_results = consensus_data.get("results", [])
    consensus_summary = consensus_data.get("summary", {})
    match_results = match_data.get("results", [])

    # ── Build case_id → match info lookup ──
    # Match results in order: 已匹配 → 待确定 → 无匹配
    # Consensus results map to the first two groups (non-"无匹配")
    case_match_map: dict[str, dict] = {}
    match_idx = 0
    for mr in match_results:
        if mr.get("match_type") in ("已匹配", "待确定"):
            if match_idx < len(consensus_results):
                cid = consensus_results[match_idx]["case_id"]
                case_match_map[cid] = mr
                match_idx += 1

    # ── Merge consensus + match (judged entries) ──
    merged: list[dict] = []
    for cr in consensus_results:
        cid = cr["case_id"]
        mr = case_match_map.get(cid, {})
        merged.append({
            "case_id": cid,
            "hlr_id": mr.get("hlr_id", ""),
            "hlr_content": mr.get("hlr_content", ""),
            "matched_profile_keys": mr.get("matched_profile_keys", []),
            "agreement_level": cr.get("agreement_level", ""),
            "star_rating": cr.get("star_rating", 0),
            "final_coverage_status": cr.get("final_coverage_status", ""),
            "final_analysis": cr.get("final_analysis", ""),
            "confidence": cr.get("confidence", 0),
            "model_results": cr.get("model_results", {}),
            "is_no_match": False,
        })

    # ── Append "无匹配" entries (not judged, for full coverage) ──
    no_match_entries: list[dict] = []
    seq_after_judged = len(merged)
    for mr in match_results:
        if mr.get("match_type") == "无匹配":
            seq_after_judged += 1
            no_match_entries.append({
                "case_id": f"NOMATCH-{len(no_match_entries) + 1:04d}",
                "hlr_id": mr.get("hlr_id", ""),
                "hlr_content": mr.get("hlr_content", ""),
                "matched_profile_keys": [],
                "agreement_level": "",
                "star_rating": 0,
                "final_coverage_status": "无匹配",
                "final_analysis": "匹配层未找到对应EoICD信号",
                "confidence": 0,
                "model_results": {},
                "is_no_match": True,
            })
    merged_all = merged + no_match_entries

    # ── Build document ──
    doc = Document()

    section = doc.sections[0]
    section.page_width = Cm(29.7)
    section.page_height = Cm(21.0)
    section.left_margin = Cm(1.0)
    section.right_margin = Cm(1.0)

    # Title
    title = doc.add_heading("EoICD 与 HLR 多模型共识分析报告", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(
        f"生成时间: {consensus_data.get('generated_at', 'N/A')[:19]}"
    )

    # ── Summary section ──
    doc.add_heading("共识分析总结", level=2)

    total_hlr = match_data.get("total_hlr", 0)
    match_stats = match_data.get("stats", {})
    cs = consensus_summary

    overview_parts = [
        f"对 {total_hlr} 条软件高层需求(HLR)进行多模型共识分析：",
        f"3 个裁判模型（DeepSeek / MiniMax / Qwen）并行独立裁判，",
        f"1 个 Review Agent 综合复核并给出星级评价。",
    ]
    doc.add_paragraph("".join(overview_parts))

    # ── Summary tables ──
    doc.add_heading("判定分布", level=3)

    status_dist = cs.get("status_distribution", {})
    st = doc.add_table(rows=1, cols=2)
    st.style = "Table Grid"
    _style_header_row(st, ["判定结果", "数量"])

    # AI 裁判结果（仅共识判定）
    judged_categories = [
        ("已覆盖", RGBColor(0x00, 0x80, 0x00)),
        ("不一致", RGBColor(0xCC, 0x33, 0x00)),
        ("待确认", RGBColor(0xCC, 0x55, 0x00)),
    ]
    judged_total = 0
    for label, color in judged_categories:
        count = status_dist.get(label, 0)
        if count > 0:
            row = st.add_row()
            _set_cell_font(row.cells[0], label, bold=True, color=color)
            _set_cell_font(row.cells[1], str(count))
            judged_total += count

    row = st.add_row()
    _set_cell_font(row.cells[0], "合计", bold=True)
    _set_cell_font(row.cells[1], str(judged_total), bold=True)

    for row_obj in st.rows:
        row_obj.cells[0].width = Cm(6.0)
        row_obj.cells[1].width = Cm(3.0)

    # ── Consensus quality section ──
    doc.add_heading("共识质量", level=3)

    agreement_dist = cs.get("agreement_distribution", {})
    avg_stars = cs.get("average_star_rating", 0)

    # Map agreement → star: full=3★, majority=2★, split=1★
    star_levels = [
        ("3", _star_str(3), "完全一致", agreement_dist.get("full", 0),
         "3 个模型判断完全一致，可直接采纳"),
        ("2", _star_str(2), "多数一致", agreement_dist.get("majority", 0),
         "2/3 模型一致，需关注少数意见中是否有被忽略的证据"),
        ("1", _star_str(1), "分歧", agreement_dist.get("split", 0),
         "三方各持不同意见，建议人工逐条复核"),
    ]

    qt = doc.add_table(rows=1, cols=4)
    qt.style = "Table Grid"
    _style_header_row(qt, ["星级", "共识等级", "数量", "说明"])

    for _, star_icon, label, count, desc in star_levels:
        row = qt.add_row()
        _set_cell_font(row.cells[0], star_icon, bold=True, size=10,
                       color=RGBColor(0xCC, 0x88, 0x00))
        _set_cell_font(row.cells[1], label, bold=True)
        _set_cell_font(row.cells[2], f"{count} 条")
        _set_cell_font(row.cells[3], desc)

    row = qt.add_row()
    _set_cell_font(row.cells[0], "", bold=True)
    _set_cell_font(row.cells[1], "平均星级", bold=True)
    _set_cell_font(row.cells[2], f"{avg_stars:.1f}", bold=True)
    _set_cell_font(row.cells[3], "")

    for row_obj in qt.rows:
        row_obj.cells[0].width = Cm(2.5)
        row_obj.cells[1].width = Cm(3.0)
        row_obj.cells[2].width = Cm(2.0)
        row_obj.cells[3].width = Cm(8.0)

    # ── Suggestions ──
    doc.add_heading("处置建议", level=3)
    suggestions = [
        f"{_star_str(3)} 星条目：模型完全一致，可直接采纳结论。",
        f"{_star_str(2)} 星条目：多数模型一致，需关注少数意见中是否有被忽略的证据。",
        f"{_star_str(1)} 星条目：三方分歧，建议人工逐条复核并做出最终判断。",
        "无匹配 条目：匹配层未找到对应EoICD信号，需确认HLR是否属于ICD接口范畴。",
    ]
    for s in suggestions:
        doc.add_paragraph(s, style="List Bullet")

    doc.add_page_break()

    # ── Detail table ──
    doc.add_heading("覆盖分析明细（共识结果）", level=2)
    judged_count = len(merged)
    no_match_count = len(no_match_entries)
    doc.add_paragraph(f"共 {len(merged_all)} 条记录"
                      f"（{judged_count} 条已裁判 + {no_match_count} 条无匹配）")

    headers = ["序号", "HLR ID", "判定", "ICD Block",
               "分析摘要", "共识", "星级"]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _style_header_row(table, headers)

    status_labels = {
        "covered": "已覆盖",
        "inconsistent": "不一致",
        "needs_review": "待确认",
        "无匹配": "无匹配",
        "待确认": "待确认",
    }
    status_colors = {
        "已覆盖": RGBColor(0x00, 0x80, 0x00),
        "不一致": RGBColor(0xCC, 0x33, 0x00),
        "待确认": RGBColor(0xCC, 0x55, 0x00),
        "无匹配": RGBColor(0x99, 0x33, 0xCC),
    }
    agreement_colors = {
        "full": RGBColor(0x00, 0x80, 0x00),
        "majority": RGBColor(0xCC, 0x88, 0x00),
        "split": RGBColor(0xCC, 0x00, 0x00),
    }

    for i, m in enumerate(merged_all, 1):
        row = table.add_row()
        status = m["final_coverage_status"]
        agreement = m["agreement_level"]
        stars = m["star_rating"]
        is_nm = m["is_no_match"]

        status_cn = status_labels.get(status, status)

        blocks_str = ", ".join(m["matched_profile_keys"][:5]) if m["matched_profile_keys"] else "—"
        if len(m["matched_profile_keys"]) > 5:
            blocks_str += f" ... (+{len(m['matched_profile_keys']) - 5})"

        agreement_label = {"full": "完全一致", "majority": "多数一致", "split": "分歧"}.get(agreement, agreement)

        _set_cell_font(row.cells[0], str(i))
        _set_cell_font(row.cells[1], m["hlr_id"])
        _set_cell_font(row.cells[2], status_cn, bold=True, color=status_colors.get(status_cn))
        _set_cell_font(row.cells[3], blocks_str, size=7)
        _set_cell_font(row.cells[4], m["final_analysis"], size=8)
        if is_nm:
            _set_cell_font(row.cells[5], "—")
            _set_cell_font(row.cells[6], "—")
        else:
            _set_cell_font(row.cells[5], agreement_label, color=agreement_colors.get(agreement))
            _set_cell_font(row.cells[6], _star_str(stars), bold=True, size=10,
                           color=RGBColor(0xCC, 0x88, 0x00))

    # Column widths
    col_widths = [0.8, 2.5, 1.5, 5.5, 9.0, 1.5, 1.5]
    for row_obj in table.rows:
        for i, w in enumerate(col_widths):
            row_obj.cells[i].width = Cm(w)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    print(f"  Consensus report: {output_path}")
