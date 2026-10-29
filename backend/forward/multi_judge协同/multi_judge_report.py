# -*- coding: utf-8 -*-
"""多 judge 正向检查 Word 共识报告（对齐反向 consensus_word_generator 章节结构）。

保留粒度：一行 = 一个 EoICD 信号（与 aggregate 产物一致）。

报告章节
--------
一、一致性分析总结
    判定分布 / 星级分布（★☆☆☆☆ + 平均星级）/ 处置建议
二、分析明细
    展示模式由 word_detail_mode 控制（默认 exceptions_only）：
      - exceptions_only：仅列异常信号（不一致/未落实/覆盖缺口/部分落实中的低星或需复核项）；
      - all：全量逐行；top_n:N：全量但封顶 N 行。
    各判定分组的数量见『一、一致性分析总结』的判定分布，本章不再重复计数表。

注：AI 复核覆盖（复核路由命中、第二裁决生效/未生效、路由规则）不渲染进本报告，
作为中间产物保留在 runs/integration_workspace/ 下的 multi_judge_summary.json /
multi_judge_review_stats.json（aggregate.json 亦含 review_trace）；报告只呈现
落实结论与共识度（判定 + 星级 + 属性比对）。

列头：序号 | EoICD 信号 | 判定 | 匹配 HLR | 属性比对 | 共识 | 星级 | 分析摘要

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

DETAIL_HEADERS = ["序号", "EoICD 信号", "判定", "子分类", "匹配 HLR", "属性比对", "共识", "星级", "分析摘要"]
# A4 纵向（21.0cm，左右边距各 1.0cm → 可用 19.0cm），合计 19.0
DETAIL_COL_WIDTHS = [1.0, 3.0, 1.6, 3.0, 2.5, 2.3, 2.1, 1.5, 7.0]

# 未落实二级子类 → 展示描述（对齐单 judge `summary.py:906-909` 的 _SUB_DISPLAY）
SUB_CLASS_DISPLAY = {
    "已识别未承接": "需求中有信号信息但未提及任何属性",
    "未识别": "需求中没有任何信号信息",
}

SIGNAL_MAX = 200
NOTE_MAX = 1000

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


def _format_attrs(inconsistent_attributes: list, final_verdict: str | None = None,
                  n_judges: int | None = None) -> str:
    """渲染「属性比对」列。

    来源标注（2026-10-15）：当信号级判定不是「不一致」、却仍列出不一致属性时（典型：多数 judge
    判「未落实」，仅个别 judge 报出属性不一致），逐条附来源强度标注，避免读者误读为
    「这信号明明是不一致、为什么判未落实」。信号级已是「不一致」时不标注（结论方向一致）。
    格式：单源 → `类名（judge_A 单源）`；多方 → `类名（2/3 judge 报出）`。
    """
    if not inconsistent_attributes:
        return "—"
    annotate = (final_verdict is not None and final_verdict != "不一致")
    items = []
    for a in inconsistent_attributes:
        if isinstance(a, dict):
            name = a.get("attribute") or a.get("attribute_class") or ""
            js = a.get("judges") or []
        else:
            name = str(a)
            js = []
        if not name:
            continue
        if annotate and js:
            if len(js) == 1:
                name = f"{name}（{js[0]} 单源）"
            else:
                tail = f"/{n_judges}" if n_judges else ""
                name = f"{name}（{len(js)}{tail} judge 报出）"
        if name not in items:
            items.append(name)
    if not items:
        return "—"
    return " | ".join(items)


def _collect_compared_attributes_by_direction(agg: dict) -> dict:
    """按信号方向（发送/接收）分组汇总属性比对涉及的属性类。

    来源：每条信号 aggregation.attr_consensus_detail（= attr_by_class）的键，按其
    aggregation.signal_direction 归入「发送 / 接收」两个桶。同一属性类可能同时出现在
    两类信号中（桶之间可重叠）。signal_direction 为空的信号（未匹配 HLR、无方向可比）不计入。
    """
    buckets: dict[str, set] = {"发送": set(), "接收": set()}
    for item in agg.values():
        a = (item or {}).get("aggregation") or {}
        d = a.get("signal_direction")
        if d not in buckets:
            continue
        detail = a.get("attr_consensus_detail") or {}
        buckets[d].update(detail.keys())
    return {k: sorted(v) for k, v in buckets.items()}


def _attr_scope_text(classes: list, cap: int = 40) -> str:
    """把属性类列表渲染成逗号 连接的文本，超过 cap 截断并标注总数。"""
    if not classes:
        return ""
    text = "、".join(classes)
    if len(classes) > cap:
        text = "、".join(classes[:cap]) + f" 等共 {len(classes)} 类属性"
    return text


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
    ]


def generate_report(agg: dict, summary: dict, output_path: Path,
                     word_detail_mode: str = "exceptions_only",
                     excel_filename: str = "") -> bool:
    """生成 Word 报告；返回是否成功。依赖缺失或异常时优雅返回 False。

    word_detail_mode 控制『二、分析明细』的展示范围：
      - "exceptions_only"（默认）：仅列需人工关注的异常信号——不一致 / 未落实 /
        覆盖缺口 全列；部分落实仅列其中低星(≤2★)或 need_review 的子集；已落实不列。
        各判定分组的数量已在『一、判定分布』给出，本章不重复计数表。
      - "all"：全量逐行（保留旧行为）。
      - "top_n:N"：全量但封顶 N 行，超出注明详见《实际 Excel 文件名》（由 runner 传入）。
    """
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

    # 配套 Excel 报告的实际文件名（由 runner 传入 excel_path.name，如
    # 《EoICD至HLR正向一致性多模型共识分析报告_20261012_1025.xlsx》），用于在正文中
    # 精确引用，避免泛称「配套 Excel 报告」。未传入时降级为「配套 Excel 报告」，
    # 保证函数仍可独立调用（如自测）。
    excel_ref = f"《{excel_filename}》" if excel_filename else "配套 Excel 报告"

    def set_cell(cell, text, bold=False, size=9, color=None):
        cell.text = ""
        # 表格单元格显式左对齐：Normal 样式已设为两端对齐（供正文使用），
        # 窄列内的长文本若两端对齐会被拉伸不均，故此处覆盖回左对齐。
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT
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

    # 字体统一：正文(Normal)与各级标题统一为微软雅黑，与表格单元格(set_cell)一致；
    # 仅统一字族，字号仍由各处自行设定（表格小、正文大）
    for _style in ("Normal", "Heading 1", "Heading 2", "Heading 3"):
        _st = doc.styles[_style]
        _st.font.name = "微软雅黑"
        _rpr = _st.element.get_or_add_rPr()
        _rpr.get_or_add_rFonts().set(qn("w:eastAsia"), "微软雅黑")

    # 正文两端对齐（中文段落常规排版）：设在 Normal 上，对所有正文段落生效；
    # 表格单元格在 set_cell 内显式设回左对齐，避免窄列长文本被拉伸不均。
    doc.styles["Normal"].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    title = doc.add_heading("EoICD 至 HLR 正向一致性多模型共识分析报告", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    # 任务累计 token（含续跑前的 base）；旧任务 / CLI 未提供时不写该行
    token_usage = summary.get("token_usage")
    if isinstance(token_usage, dict):
        token_usage = token_usage.get("total_tokens")
    if isinstance(token_usage, (int, float)) and not isinstance(token_usage, bool):
        doc.add_paragraph(f"总 Token 用量: {max(0, int(token_usage)):,}")

    # ── 一、分析总结 ──
    doc.add_heading("一、一致性分析总结", level=2)
    doc.add_paragraph(
        f"对 {summary.get('total', 0)} 条 EoICD 信号进行多 judge 并行独立判定，"
        f"按共识度给出星级评价；判定粒度为「信号」（一个信号可命中多条 HLR 需求）。"
    )
    # 该块是**多行文本**（format_summary_text 以 \n 分隔，Word 里是同一段落内的软换行）。
    # 两端对齐会把「除最后一行外」的每一行都拉满到右边界 → 字距被撑开、观感差
    # （如「信号总数 372 条」被拉开）。故此类统计块显式左对齐；其余单行段落仍继承 Normal 的两端对齐。
    _sum_para = doc.add_paragraph(
        "  " + format_summary_text(summary, include_review_coverage=False)
    )
    _sum_para.alignment = WD_ALIGN_PARAGRAPH.LEFT

    # ── 运行有效性（2026-10-29）──
    # 「多模型共识」报告必须让读者看见本轮实际有几个模型真正参与了判定。
    # 2026-10-29 实测：judge_C 崩溃（returncode=1）后，372/372 条信号的共识都只由
    # 2 个 judge 支撑，而正文对此只字未提（仅在明细 note 里写「覆盖缺口：judge_C(...)」），
    # 读者会把 2 模型结论误读为 3 模型共识。
    _je = summary.get("judge_effectiveness") or {}
    _judges = _je.get("judges") or {}
    if _judges:
        doc.add_heading("运行有效性", level=3)
        _valid = int(_je.get("valid", 0) or 0)
        _cfg = int(_je.get("configured", 0) or 0)
        if _valid < _cfg:
            _p = doc.add_paragraph()
            _r = _p.add_run("本轮实际参与判定的 judge 为 {0}/{1} 个".format(_valid, _cfg))
            _r.bold = True
            _p.add_run("，缺失的 judge 及其原因见下表；本报告的结论由存活的 {0} 个 judge "
                       "共同支撑，并非全部 {1} 个模型的共识。".format(_valid, _cfg))
        else:
            doc.add_paragraph("本轮 {0} 个 judge 全部正常参与判定。".format(_cfg))
        _jt = doc.add_table(rows=1, cols=5)
        _jt.style = "Table Grid"
        style_header(_jt, ["judge", "模型", "状态", "原因", "产出信号数"])
        for _name, _m in _judges.items():
            _row = _jt.add_row()
            _ok = bool(_m.get("valid"))
            set_cell(_row.cells[0], str(_name), bold=True)
            set_cell(_row.cells[1], str(_m.get("model") or "—"))
            set_cell(_row.cells[2], "正常" if _ok else "失效", bold=True,
                     color=_GREEN if _ok else _RED)
            set_cell(_row.cells[3], str(_m.get("error") or "—"),
                     color=None if _ok else _RED)
            set_cell(_row.cells[4], str(_m.get("n_signals", 0)))
        for r in _jt.rows:
            r.cells[0].width = Cm(2.6)
            r.cells[1].width = Cm(4.2)
            r.cells[2].width = Cm(1.8)
            r.cells[3].width = Cm(5.4)
            r.cells[4].width = Cm(2.0)
        layout_fixed(_jt)

    # 注：「AI 复核覆盖」**不进用户向交付物**——复核过程是中间产物，交付物只呈现
    # 落实结论（约定见 multi_judge_summary.format_summary_text 的 include_review_coverage
    # 说明，报告侧固定传 False）。复核执行与失败情况落在 runs 工作区的
    # multi_judge_summary.json / multi_judge_aggregate.json 与 runner 控制台，需要时从那里查。

    doc.add_heading("判定分布", level=3)
    st = doc.add_table(rows=1, cols=2)
    st.style = "Table Grid"
    style_header(st, ["判定结果", "数量"])
    for label, count in summary.get("status_distribution", {}).items():
        row = st.add_row()
        set_cell(row.cells[0], label, bold=True, color=_verdict_color(label))
        set_cell(row.cells[1], f"{count} 条")
    # 合计行：两格填在**同一行**。
    # 2026-10-29 修复：原写成两个 add_row()（一个放「合计」、另一个放总数），
    # 于是合计被拆成两行、数量列还留空 —— 与 Excel 侧同表写法（gen_excel_report.py
    # 的 ws.cell(r,1,"合计") + ws.cell(r,2,total)）不一致，看着像漏了数据。
    _sum_row = st.add_row()
    set_cell(_sum_row.cells[0], "合计", bold=True)
    set_cell(_sum_row.cells[1], f"{summary.get('total', 0)} 条", bold=True)
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

    doc.add_heading("属性比对范围", level=3)
    _buckets = _collect_compared_attributes_by_direction(agg)
    _send, _recv = _buckets["发送"], _buckets["接收"]
    _union = sorted(set(_send) | set(_recv))
    if _union:
        doc.add_paragraph(
            f"属性比对阶段（EoICD 信号属性 ↔ 匹配 HLR 需求属性）共覆盖 {len(_union)} 类属性，"
            f"按信号方向拆分为：发送信号 {len(_send)} 类、接收信号 {len(_recv)} 类"
            f"（同一属性可能同时出现在两类信号中，故拆分计数之和可大于总数）。"
        )
        if _send:
            doc.add_paragraph(f"发送信号（{len(_send)} 类）：{_attr_scope_text(_send)}。",
                              style="List Bullet")
        else:
            doc.add_paragraph("发送信号：无属性比对（本次匹配到的信号均为接收信号）。",
                              style="List Bullet")
        if _recv:
            doc.add_paragraph(f"接收信号（{len(_recv)} 类）：{_attr_scope_text(_recv)}。",
                              style="List Bullet")
        else:
            doc.add_paragraph("接收信号：无属性比对（本次匹配到的信号均为发送信号）。",
                              style="List Bullet")
    else:
        doc.add_paragraph("本次未触发属性比对（所有信号均未匹配到 HLR 需求，无 HLR 侧属性可比对）。")

    doc.add_page_break()

    # ── 二、分析明细 ──
    doc.add_heading("二、分析明细", level=2)

    PARTIAL_CAP = 200  # 部分落实信号在 Word 中的展示上限，超出部分注明见 Excel

    # 全部信号按判定分组（不变；后续按 word_detail_mode 决定展示哪些）
    all_groups: dict[str, list[tuple[str, dict]]] = {g: [] for g in VERDICT_GROUPS}
    for full, item in agg.items():
        a = (item or {}).get("aggregation") or {}
        verdict = a.get("final_verdict") or "覆盖缺口"
        all_groups.setdefault(verdict, []).append((full, a))

    seq = 0
    _cap = 0  # top_n 封顶上限（仅 top_n 模式使用）

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
            set_cell(row.cells[5], _format_attrs(a.get("inconsistent_attributes"),
                                                 final_verdict=verdict,
                                                 n_judges=a.get("n_judges_participating")), size=7)
            set_cell(row.cells[6], a.get("consensus_label") or _consensus_label(star, ""),
                     size=8, color=_consensus_color(star))
            set_cell(row.cells[7], _star_str(star), bold=True, size=10, color=_YELLOW)
            set_cell(row.cells[8], _truncate((a.get("analysis_summary") or "").strip() or "—", NOTE_MAX), size=8)
        for r in table.rows:
            for i, w in enumerate(DETAIL_COL_WIDTHS):
                r.cells[i].width = Cm(w)
        layout_fixed(table)

    # 明细展示模式（默认 exceptions_only：只列异常信号，降低大数据量生成耗时）
    if word_detail_mode == "all":
        groups = all_groups
        doc.add_paragraph(
            f"共 {summary.get('total', 0)} 条信号，按判定结果分组列出（全量明细）。"
        )
    elif word_detail_mode == "exceptions_only":
        def _is_exc(a: dict) -> bool:
            v = a.get("final_verdict") or "覆盖缺口"
            # 「待确认」= 无 judge 给出有效结论，属必须人工关注项，务必列出
            if v in ("不一致", "未落实", "覆盖缺口", "待确认"):
                return True
            if v == "部分落实":
                # 仅关注低星或需复核的部分落实；其余进 Excel
                return (a.get("star_rating") or 0) <= 2 or bool(a.get("need_review"))
            return False
        groups = {g: [(f, a) for (f, a) in all_groups.get(g, []) if _is_exc(a)]
                  for g in VERDICT_GROUPS}
        doc.add_paragraph(
            f"共 {summary.get('total', 0)} 条信号，各判定分组数量见『一、一致性分析总结』判定分布。"
            f"本章仅列出需人工关注的异常信号（不一致 / 未落实 / 覆盖缺口 / 部分落实中的"
            f"低星或需复核项）；已落实与多数部分落实信号的完整明细详见{excel_ref}。"
            f"若『部分落实』信号数量过多，本表仅列前 {PARTIAL_CAP} 条，其余在表中注明条数并详见{excel_ref}。"
        )
    else:  # top_n:N
        try:
            _cap = int(str(word_detail_mode).split(":", 1)[1])
        except (ValueError, IndexError):
            _cap = 200
        groups = all_groups
        doc.add_paragraph(
            f"共 {summary.get('total', 0)} 条信号，按判定结果分组列出（全量明细，封顶 {_cap} 行；"
            f"超出部分详见{excel_ref}）。"
        )

    _total_signals = sum(len(v) for v in all_groups.values())
    for group_name in VERDICT_GROUPS:
        rows = groups.get(group_name) or []
        if not rows:
            continue
        # top_n 封顶：已达上限则停止后续所有组
        if word_detail_mode.startswith("top_n:") and seq >= _cap:
            break
        # top_n 封顶：本组截断到剩余额度
        if word_detail_mode.startswith("top_n:") and seq + len(rows) > _cap:
            rows = rows[:_cap - seq]
        # 部分落实信号过多时，Word 仅列前 PARTIAL_CAP 条，其余注明见 Excel
        partial_omitted = 0
        if group_name == "部分落实" and len(rows) > PARTIAL_CAP:
            partial_omitted = len(rows) - PARTIAL_CAP
            rows = rows[:PARTIAL_CAP]
        if group_name == "未落实":
            # 按子类拆两个三级标题（对齐单 judge 把『未落实』拆为两个子小节）
            _any_sub = False
            for _sub_key in ("已识别未承接", "未识别"):
                _sub_rows = [(f, a) for (f, a) in rows if a.get("sub_classification") == _sub_key]
                if not _sub_rows:
                    continue
                _any_sub = True
                doc.add_heading("未落实·%s（%d 条）" % (SUB_CLASS_DISPLAY[_sub_key], len(_sub_rows)), level=3)
                emit_detail_table(_sub_rows)
            if not _any_sub:
                doc.add_heading("未落实（%d 条）" % len(rows), level=3)
                emit_detail_table(rows)
        else:
            _title = "%s（%d 条" % (group_name, len(rows))
            if group_name == "部分落实" and partial_omitted:
                _title += "，本表列前 %d 条" % PARTIAL_CAP
            _title += "）"
            doc.add_heading(_title, level=3)
            emit_detail_table(rows)
            if group_name == "部分落实" and partial_omitted:
                doc.add_paragraph(
                    f"其余 {partial_omitted} 条「部分落实」信号的完整匹配与冲突信息详见{excel_ref}。"
                )

    # top_n 封顶提示
    if word_detail_mode.startswith("top_n:") and seq < _total_signals:
        doc.add_paragraph(
            f"……（Word 明细已封顶 {_cap} 行，其余 {_total_signals - seq} 条信号详见{excel_ref}）"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    print(f"  [报告] 已写出 Word: {output_path}（{summary.get('total', 0)} 个信号）")
    return True


def _self_test() -> None:
    """最小自测：3 种明细模式（exceptions_only / all / top_n）生成 + 高频回归点。

    原 inline __main__ 测试已抽到本函数（保留所有断言 + 新增列宽/格式护栏），
    便于从外部脚本 / 集成测试入口调用。
    改这块前/后跑一次 _self_test() 即可肉眼确认。
    """
    # 高频回归点 1：表头与列宽数量必须严格一致（防「加了表头忘加列宽」类回归）
    assert len(DETAIL_HEADERS) == len(DETAIL_COL_WIDTHS), \
        f"DETAIL_HEADERS({len(DETAIL_HEADERS)}) 与 DETAIL_COL_WIDTHS({len(DETAIL_COL_WIDTHS)}) 数量不一致"
    # 列宽必须全为正数
    assert all(isinstance(w, (int, float)) and w > 0 for w in DETAIL_COL_WIDTHS), DETAIL_COL_WIDTHS
    # 高频回归点 2：SUB_CLASS_DISPLAY 必须覆盖「已识别未承接 / 未识别」两个子类
    # （防「sub_classification 派生新增了第三类但 SUB_CLASS_DISPLAY 忘加」类回归）
    assert "已识别未承接" in SUB_CLASS_DISPLAY and "未识别" in SUB_CLASS_DISPLAY, SUB_CLASS_DISPLAY
    # 高频回归点 3：_star_str 在 0/1/2/3/4/5/越界 各档都必须有合法输出
    for n in (0, 1, 2, 3, 4, 5, 99):
        s = _star_str(n)
        assert isinstance(s, str) and s, f"_star_str({n})={s!r}"

    import tempfile
    from multi_judge_summary import build_summary
    _agg = {
        "SIG_姿态角速率": {"aggregation": {
            "star_rating": 1, "agreement": "split", "final_verdict": "部分落实",
            "consensus_label": _consensus_label(1, "split"),
            "matched_hlr_consensus": "R12",  # 多 HLR 修复·显示层：只含 req_id，不带 [iface]
            "inconsistent_attributes": [{"attribute": "周期"}, {"attribute": "精度"}],
            "note": "1/2：分歧；judge 间分歧 2 项"}},
        "SIG_开关量": {"aggregation": {
            "star_rating": 5, "agreement": "full", "final_verdict": "已落实",
            "consensus_label": _consensus_label(5, "full"),
            "matched_hlr_consensus": "R7",  # 多 HLR 修复·显示层：只含 req_id
            "inconsistent_attributes": [], "note": "3/3 一致"}},
        "SIG_未定义温度": {"aggregation": {
            "star_rating": 0, "agreement": "no_consensus", "final_verdict": "未落实",
            "consensus_label": _consensus_label(0, "no_consensus"),
            "matched_hlr_consensus": "", "inconsistent_attributes": [],
            "note": "所有 judge 均无有效结论"}},
    }
    _out = Path(tempfile.mkdtemp()) / "multi_judge_report.docx"
    _ok = generate_report(_agg, build_summary(_agg), _out,  # 默认 exceptions_only
                           excel_filename=_out.with_suffix(".xlsx").name)
    assert _ok and _out.exists(), _out
    # 验证 all 模式也能生成（全量逐行），且 exceptions_only 与 all 行数不同
    _out_all = Path(tempfile.mkdtemp()) / "multi_judge_report_all.docx"
    _ok_all = generate_report(_agg, build_summary(_agg), _out_all, word_detail_mode="all",
                              excel_filename=_out_all.with_suffix(".xlsx").name)
    assert _ok_all and _out_all.exists(), _out_all
    # top_n 模式：封顶 1 行也能生成
    _out_cap = Path(tempfile.mkdtemp()) / "multi_judge_report_topn.docx"
    _ok_cap = generate_report(_agg, build_summary(_agg), _out_cap, word_detail_mode="top_n:1",
                              excel_filename=_out_cap.with_suffix(".xlsx").name)
    assert _ok_cap and _out_cap.exists(), _out_cap
    print("multi_judge_report 自测通过（3 种明细模式生成/表头列宽一致/SUB_CLASS_DISPLAY 覆盖/"
          "_star_str 越界 均符合预期）")


if __name__ == "__main__":  # pragma: no cover
    _self_test()
