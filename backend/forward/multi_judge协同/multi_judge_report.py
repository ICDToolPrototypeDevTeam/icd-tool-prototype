# -*- coding: utf-8 -*-
"""多 judge 正向检查 Word 共识报告（对齐反向 consensus_word_generator 章节结构）。

保留粒度：一行 = 一个 EoICD 信号（与 aggregate 产物一致）。

报告章节
--------
一、一致性分析总结
    判定分布 / 星级分布（★☆☆☆☆ + 平均星级）/ 处置建议
二、分析明细
    按判定分组出表：不一致 / 未落实 / 部分落实 / 已落实 / 覆盖缺口

注：AI 复核覆盖（复核路由命中、第二裁决生效/未生效、路由规则）不渲染进本报告，
作为中间产物保留在 runs/integration_workspace/ 下的 multi_judge_summary.json /
multi_judge_review_stats.json（aggregate.json 亦含 review_trace）；报告只呈现
落实结论与共识度（判定 + 星级 + 不一致属性）。

列头：序号 | EoICD 信号 | 判定 | 匹配 HLR | 不一致属性 | 共识 | 星级 | 分析摘要

依赖缺失（未装 python-docx）时优雅跳过并返回 False，不影响主流程。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from multi_judge_aggregate import _consensus_label  # noqa: E402  同包复用中文标签
from multi_judge_summary import (  # noqa: E402
    AGREEMENT_CN,
    VERDICT_COLORS,
    VERDICT_GROUPS,
    format_summary_text,
)

DETAIL_HEADERS = ["序号", "EoICD 信号", "判定", "子分类", "匹配 HLR", "不一致属性", "共识", "星级", "分析摘要"]
# A4 纵向（21.0cm，左右边距各 1.0cm → 可用 19.0cm），合计 19.0
DETAIL_COL_WIDTHS = [1.0, 3.0, 1.6, 3.0, 3.0, 2.8, 2.1, 1.5, 3.0]

# 未落实二级子类 → 展示描述（对齐单 judge `summary.py:906-909` 的 _SUB_DISPLAY）
SUB_CLASS_DISPLAY = {
    "已识别未承接": "需求中有信号信息但未提及任何属性",
    "未识别": "需求中没有任何信号信息",
}

SIGNAL_MAX = 40
NOTE_MAX = 60
ATTR_MAX = 3

_GREEN = (0x00, 0x80, 0x00)
_YELLOW = (0xCC, 0x88, 0x00)
_RED = (0xCC, 0x00, 0x00)
_GRAY = (0x80, 0x80, 0x80)

_STAR_DESC = {
    "5": "全部 judge 判断完全一致，无字段级分歧",
    "4": "judge 判断一致，但存在字段异议",
    "3": "多数 judge 判断一致，少数意见仅涉及辅助字段",
    "2": "多数 judge 判断一致，少数意见涉及关键字段",
    "1": "judge 之间分歧或全部失效，需人工复核",
}


def _star_str(n: int) -> str:
    """渲染星级字符串（与反向 _star_str 同口径）。"""
    n = max(0, min(5, int(n)))
    return "★" * n + "☆" * (5 - n)


def _verdict_color(verdict: str):
    from docx.shared import RGBColor
    hexv = VERDICT_COLORS.get(verdict, "808080")
    return RGBColor(int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16))


def _consensus_color(star: int):
    """三色口径：绿=可直接采纳，黄=可采纳但有提醒，红=需关注或人工复核。"""
    if star == 5:
        return _GREEN
    if star in (3, 4):
        return _YELLOW
    if star in (1, 2):
        return _RED
    return _GRAY


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _format_attrs(inconsistent_attributes: list) -> str:
    if not inconsistent_attributes:
        return "—"
    names = []
    for a in inconsistent_attributes:
        if isinstance(a, dict):
            name = a.get("attribute") or a.get("attribute_class") or ""
        else:
            name = str(a)
        if name and name not in names:
            names.append(name)
    if not names:
        return "—"
    if len(names) > ATTR_MAX:
        return " | ".join(names[:ATTR_MAX]) + f" …(+{len(names) - ATTR_MAX})"
    return " | ".join(names)


def _build_paragraphs() -> list[tuple[str, str]]:
    """构造处置建议条目（对齐反向「处置建议」分档口径）。"""
    return [
        (f"{_star_str(5)} 完全共识：全部 judge 一致，可直接采纳结论。",
         "可直接采纳"),
        (f"{_star_str(4)} 完全共识·字段异议：judge 判断一致，建议核对字段细节。",
         "建议核对字段"),
        (f"{_star_str(3)} 多数共识：可采纳多数结论。", "可采纳多数"),
        (f"{_star_str(2)} 多数共识·关键异议：涉及关键字段，建议人工复核少数意见。",
         "人工复核"),
        (f"{_star_str(1)} 三方分歧：各 judge 持不同意见，建议人工逐条复核后定论。",
         "必须人工"),
        (f"{_star_str(1)} 仅单源：有效 judge 不足，结论仅供参考，建议人工确认。",
         "必须人工"),
        (f"{_star_str(1)} 全部失效：所有 judge 均无效，AI 结论不可用，必须人工复核。",
         "必须人工"),
        ("覆盖缺口：无 judge 给出有效结论，需确认是被判为未落实还是遗漏。",
         "必须人工"),
    ]


def generate_report(agg: dict, summary: dict, output_path: Path) -> bool:
    """生成 Word 报告；返回是否成功。依赖缺失或异常时优雅返回 False。"""
    try:
        from docx import Document
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Cm, Pt, RGBColor
    except ImportError:
        print("  [报告] 跳过：未安装 python-docx（pip install python-docx）")
        return False
    if not agg:
        print("  [报告] 跳过：无聚合结果（0 个信号）")
        return False

    def set_cell(cell, text, bold=False, size=9, color=None):
        cell.text = ""
        run = cell.paragraphs[0].add_run(str(text))
        run.font.size = Pt(size)
        run.font.name = "微软雅黑"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        run.bold = bold
        if color:
            # 允许传入 (r,g,b) 元组或 RGBColor（三色口径常量为元组，判定色为 RGBColor）
            run.font.color.rgb = RGBColor(*color) if isinstance(color, tuple) else color

    def style_header(table, headers):
        cells = table.rows[0].cells
        for i, h in enumerate(headers):
            set_cell(cells[i], h, bold=True, size=9)
            shading = cells[i]._element.get_or_add_tcPr()
            shd = shading.makeelement(qn("w:shd"), {qn("w:fill"): "D9E2F3", qn("w:val"): "clear"})
            shading.insert(0, shd)

    def layout_fixed(table):
        tbl = table._tbl
        tblPr = tbl.tblPr
        if tblPr is None:
            tblPr = OxmlElement("w:tblPr")
            tbl.insert(0, tblPr)
        tblW = tblPr.find(qn("w:tblW"))
        if tblW is None:
            tblW = OxmlElement("w:tblW")
            tblPr.append(tblW)
        tblW.set(qn("w:w"), "5000")
        tblW.set(qn("w:type"), "pct")
        layout = tblPr.find(qn("w:tblLayout"))
        if layout is None:
            layout = OxmlElement("w:tblLayout")
            tblPr.append(layout)
        layout.set(qn("w:type"), "fixed")

    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(1.0)
    section.right_margin = Cm(1.0)

    title = doc.add_heading("EoICD 至 HLR 正向一致性多模型共识分析报告", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── 一、分析总结 ──
    doc.add_heading("一、一致性分析总结", level=2)
    doc.add_paragraph(
        f"对 {summary.get('total', 0)} 条 EoICD 信号进行多 judge 并行独立判定，"
        f"按共识度给出星级评价；判定粒度为「信号」（一个信号可命中多条 HLR 需求）。"
    )
    doc.add_paragraph("  " + format_summary_text(summary, include_review_coverage=False))

    doc.add_heading("判定分布", level=3)
    st = doc.add_table(rows=1, cols=2)
    st.style = "Table Grid"
    style_header(st, ["判定结果", "数量"])
    for label, count in summary.get("status_distribution", {}).items():
        row = st.add_row()
        set_cell(row.cells[0], label, bold=True, color=_verdict_color(label))
        set_cell(row.cells[1], f"{count} 条")
    set_cell(st.add_row().cells[0], "合计", bold=True)
    set_cell(st.add_row().cells[0], str(summary.get("total", 0)), bold=True)
    for r in st.rows:
        r.cells[0].width = Cm(6.0)
        r.cells[1].width = Cm(3.0)
    layout_fixed(st)

    doc.add_heading("星级分布", level=3)
    qt = doc.add_table(rows=1, cols=4)
    qt.style = "Table Grid"
    style_header(qt, ["星级", "共识等级", "数量", "说明"])
    for star_key in ("5", "4", "3", "2"):
        row = qt.add_row()
        set_cell(row.cells[0], _star_str(int(star_key)), bold=True, size=10, color=_YELLOW)
        set_cell(row.cells[1], _consensus_label(int(star_key), ""), bold=True)
        set_cell(row.cells[2], f"{summary['star_distribution'].get(star_key, 0)} 条")
        set_cell(row.cells[3], _STAR_DESC[star_key])
    # 1★ 三个降级子类型（split / single_source / no_consensus），计数取 agreement 分布
    for agr_key, label in (("split", "三方分歧"), ("single_source", "仅单源"),
                           ("no_consensus", "全部失效")):
        row = qt.add_row()
        set_cell(row.cells[0], _star_str(1), bold=True, size=10, color=_YELLOW)
        set_cell(row.cells[1], label, bold=True)
        set_cell(row.cells[2], f"{summary['agreement_distribution'].get(agr_key, 0)} 条")
        set_cell(row.cells[3], AGREEMENT_CN.get(agr_key, label))
    row = qt.add_row()
    set_cell(row.cells[1], "平均星级", bold=True)
    set_cell(row.cells[2], f"{summary.get('average_star_rating', 0):.2f}", bold=True)
    for r in qt.rows:
        r.cells[0].width = Cm(2.5)
        r.cells[1].width = Cm(3.1)
        r.cells[2].width = Cm(1.9)
        r.cells[3].width = Cm(8.4)
    layout_fixed(qt)

    doc.add_heading("处置建议", level=3)
    for text, _tag in _build_paragraphs():
        doc.add_paragraph(text, style="List Bullet")

    doc.add_page_break()

    # ── 二、分析明细 ──
    doc.add_heading("二、分析明细", level=2)
    doc.add_paragraph(f"共 {summary.get('total', 0)} 条信号，按判定结果分组如下。")

    groups: dict[str, list[tuple[str, dict]]] = {g: [] for g in VERDICT_GROUPS}
    for full, item in agg.items():
        a = (item or {}).get("aggregation") or {}
        verdict = a.get("final_verdict") or "覆盖缺口"
        groups.setdefault(verdict, []).append((full, a))

    seq = 0

    def emit_detail_table(rows):
        """按 DETAIL_HEADERS 渲染一张明细表（含新增的『子分类』列）。"""
        nonlocal seq
        table = doc.add_table(rows=1, cols=len(DETAIL_HEADERS))
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        style_header(table, DETAIL_HEADERS)
        for full, a in rows:
            seq += 1
            row = table.add_row()
            star = a.get("star_rating") or 0
            verdict = a.get("final_verdict") or "覆盖缺口"
            sub = SUB_CLASS_DISPLAY.get(a.get("sub_classification")) or "—"
            set_cell(row.cells[0], str(seq))
            set_cell(row.cells[1], _truncate(full, SIGNAL_MAX), size=8)
            set_cell(row.cells[2], verdict, bold=True, size=8, color=_verdict_color(verdict))
            set_cell(row.cells[3], _truncate(sub, NOTE_MAX), size=7)
            set_cell(row.cells[4], _truncate(a.get("matched_hlr_consensus") or "—", SIGNAL_MAX), size=7)
            set_cell(row.cells[5], _format_attrs(a.get("inconsistent_attributes")), size=7)
            set_cell(row.cells[6], a.get("consensus_label") or _consensus_label(star, ""),
                     size=8, color=_consensus_color(star))
            set_cell(row.cells[7], _star_str(star), bold=True, size=10, color=_YELLOW)
            set_cell(row.cells[8], _truncate((a.get("analysis_summary") or "").strip() or "—", NOTE_MAX), size=7)
        for r in table.rows:
            for i, w in enumerate(DETAIL_COL_WIDTHS):
                r.cells[i].width = Cm(w)
        layout_fixed(table)

    for group_name in VERDICT_GROUPS:
        rows = groups.get(group_name) or []
        if not rows:
            continue
        if group_name == "未落实":
            # 按子类拆两个三级标题（对齐单 judge 把『未落实』拆为两个子小节）
            for _sub_key in ("已识别未承接", "未识别"):
                _sub_rows = [(f, a) for (f, a) in rows if a.get("sub_classification") == _sub_key]
                if not _sub_rows:
                    continue
                doc.add_heading("未落实·%s（%d 条）" % (SUB_CLASS_DISPLAY[_sub_key], len(_sub_rows)), level=3)
                emit_detail_table(_sub_rows)
        else:
            doc.add_heading("%s（%d 条）" % (group_name, len(rows)), level=3)
            emit_detail_table(rows)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    print(f"  [报告] 已写出 Word: {output_path}（{summary.get('total', 0)} 个信号）")
    return True


if __name__ == "__main__":  # pragma: no cover
    import tempfile

    from multi_judge_summary import build_summary

    _agg = {
        "SIG_姿态角速率": {"aggregation": {
            "star_rating": 1, "agreement": "split", "final_verdict": "部分落实",
            "consensus_label": _consensus_label(1, "split"),
            "matched_hlr_consensus": "R12[bus=1|label=0x10|bit=8|dir=pub] ⟷ R12[bus=1|label=0x11|bit=2|dir=sub]",
            "inconsistent_attributes": [{"attribute": "周期"}, {"attribute": "精度"}],
            "note": "1/2：分歧；judge 间分歧 2 项"}},
        "SIG_开关量": {"aggregation": {
            "star_rating": 5, "agreement": "full", "final_verdict": "已落实",
            "consensus_label": _consensus_label(5, "full"),
            "matched_hlr_consensus": "R7[bus=2|label=0x20|bit=0|dir=pub]",
            "inconsistent_attributes": [], "note": "3/3 一致"}},
        "SIG_未定义温度": {"aggregation": {
            "star_rating": 0, "agreement": "no_consensus", "final_verdict": "未落实",
            "consensus_label": _consensus_label(0, "no_consensus"),
            "matched_hlr_consensus": "", "inconsistent_attributes": [],
            "note": "所有 judge 均无有效结论"}},
    }
    _out = Path(tempfile.mkdtemp()) / "multi_judge_report.docx"
    _ok = generate_report(_agg, build_summary(_agg), _out)
    assert _ok and _out.exists(), _out
    print(f"✅ multi_judge_report 自测通过：{_out}")
