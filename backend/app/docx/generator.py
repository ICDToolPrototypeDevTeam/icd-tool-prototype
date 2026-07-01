"""
docx/generator.py —— 生成 4 份 Word 输出文档。

1. MiniMax条目化需求.docx
2. DeepSeek条目化需求.docx
3. 最优条目化需求.docx
4. EoICD条目化需求.docx（旧 requirements 下载接口复用，语义=最优）
5. EoICD与软件高层需求差异报告.docx
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.models import (
    DifferenceItem,
    MergedRequirementResult,
    ModelRequirementResult,
)


# ---------------------------------------------------------------------------
# 通用渲染工具
# ---------------------------------------------------------------------------


def _add_entries_table(doc: Document, entries: list[dict], title: str | None = None) -> None:
    """在 doc 中添加一张"条目化需求"表格。"""
    if title:
        doc.add_heading(title, level=2)

    if not entries:
        doc.add_paragraph("（无条目）")
        return

    table = doc.add_table(rows=1, cols=5)
    table.style = "Light Grid Accent 1"

    headers = ["条目编号", "需求描述", "接口名称", "信号名称", "来源"]
    for i, header in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = header
        run = cell.paragraphs[0].runs[0]
        run.bold = True
        run.font.size = Pt(10)

    for entry in entries:
        row = table.add_row().cells
        row[0].text = str(entry.get("entry_id", ""))
        row[1].text = str(entry.get("description", ""))
        row[2].text = str(entry.get("interface_name", ""))
        row[3].text = str(entry.get("signal_name", ""))
        row[4].text = str(entry.get("source", ""))
        for c in row:
            for p in c.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)


def _add_doc_header(doc: Document, title: str, info_lines: list[str]) -> None:
    h = doc.add_heading(title, level=1)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    for line in info_lines:
        doc.add_paragraph(line)
    doc.add_paragraph("")


# ---------------------------------------------------------------------------
# 1. MiniMax 条目化需求
# ---------------------------------------------------------------------------


def generate_minimax_docx(
    minimax_merged: ModelRequirementResult,
    job_dir: Path,
) -> Path:
    """生成 MiniMax 条目化需求 Word 文档。"""
    doc = Document()
    _add_doc_header(
        doc,
        "MiniMax 条目化需求",
        [
            f"模型: MiniMax",
            f"条目总数: {len(minimax_merged.entries)}",
        ],
    )
    doc.add_heading("候选摘要", level=2)
    doc.add_paragraph(minimax_merged.summary)
    doc.add_paragraph("")
    _add_entries_table(doc, minimax_merged.entries, title="条目化需求列表")
    doc.add_paragraph("")
    doc.add_heading("备注", level=2)
    doc.add_paragraph("本文档由 ICD工具原型Ver2.0 基于 MiniMax generation agent 自动生成，需求内容需人工复核。")
    doc.add_paragraph("条目化需求用于辅助需求整理和差异分析，不替代人工工程判断。")

    out = job_dir / "MiniMax条目化需求.docx"
    doc.save(str(out))
    return out


# ---------------------------------------------------------------------------
# 2. DeepSeek 条目化需求
# ---------------------------------------------------------------------------


def generate_deepseek_docx(
    deepseek_merged: ModelRequirementResult,
    job_dir: Path,
) -> Path:
    """生成 DeepSeek 条目化需求 Word 文档。"""
    doc = Document()
    _add_doc_header(
        doc,
        "DeepSeek 条目化需求",
        [
            f"模型: DeepSeek",
            f"条目总数: {len(deepseek_merged.entries)}",
        ],
    )
    doc.add_heading("候选摘要", level=2)
    doc.add_paragraph(deepseek_merged.summary)
    doc.add_paragraph("")
    _add_entries_table(doc, deepseek_merged.entries, title="条目化需求列表")
    doc.add_paragraph("")
    doc.add_heading("备注", level=2)
    doc.add_paragraph("本文档由 ICD工具原型Ver2.0 基于 DeepSeek generation agent 自动生成，需求内容需人工复核。")
    doc.add_paragraph("条目化需求用于辅助需求整理和差异分析，不替代人工工程判断。")

    out = job_dir / "DeepSeek条目化需求.docx"
    doc.save(str(out))
    return out


# ---------------------------------------------------------------------------
# 3. 最优条目化需求（额外落盘，便于人工查看；同时也是 requirements 接口返回文件）
# ---------------------------------------------------------------------------


def generate_best_docx(
    merged: MergedRequirementResult,
    job_dir: Path,
) -> Path:
    """生成 最优条目化需求.docx 文档。

    同时落盘 EoICD条目化需求.docx（与旧 requirements 接口文件名一致，
    旧接口语义重映射为"最优条目化需求"，前端文案无需改动）。
    """
    doc = Document()
    _add_doc_header(
        doc,
        "最优 EoICD 条目化需求",
        [
            f"chunk 数量: {merged.chunk_count}",
            f"条目总数: {len(merged.entries)}",
            "本结果为各 chunk 最佳候选合并后的全局最优条目化需求。",
        ],
    )
    doc.add_heading("合并摘要", level=2)
    doc.add_paragraph(merged.summary)
    doc.add_paragraph("")
    _add_entries_table(doc, merged.entries, title="条目化需求列表（按 chunk 顺序、REQ-xxx 重新编号）")
    doc.add_paragraph("")
    doc.add_heading("备注", level=2)
    doc.add_paragraph("本文档由 ICD工具原型Ver2.0 在 chunk-level 多智能体评分择优后自动生成。")
    doc.add_paragraph("最优候选 = 每个 chunk 中 crew 多智能体评分 × 0.6 + Python 硬规则评分 × 0.4 后的最高分。")
    doc.add_paragraph("条目化需求用于辅助需求整理和差异分析，不替代人工工程判断。")

    # 落两份：一份"最优"（人工直接查看），一份"EoICD条目化需求"（旧接口）
    best_path = job_dir / "最优条目化需求.docx"
    doc.save(str(best_path))

    requirements_path = job_dir / "EoICD条目化需求.docx"
    doc.save(str(requirements_path))
    return best_path


# ---------------------------------------------------------------------------
# 4. 差异报告
# ---------------------------------------------------------------------------


def _format_diff_location(diff: DifferenceItem) -> str:
    """把 SRS ID 和 EoICD ID 格式化为"关联定位"展示文本。

    缺失/冗余场景下只有一侧，空字符串会被过滤掉。
    """
    parts = []
    if diff.difference_requirement_id:
        parts.append(f"SRS: {diff.difference_requirement_id}")
    if diff.difference_eoicd_entry_id:
        parts.append(f"EoICD: {diff.difference_eoicd_entry_id}")
    return "\n".join(parts) if parts else "（无关联定位）"


def _render_description(doc: Document, description: str) -> None:
    """渲染 description 字段：按 \\n 拆行，"属性 XX:" / "整体XX:" 前缀加粗。

    容错：
    - description 为空 → 直接返回
    - 无 \\n → 整段作为一行渲染（兼容老格式）
    - 不识别的前缀 → 不加粗，整段作为普通段落
    """
    if not description:
        return
    for line in description.split("\n"):
        line = line.strip()
        if not line:
            continue
        p = doc.add_paragraph()
        colon_idx = line.find(":")
        prefix = line[: colon_idx + 1] if colon_idx > 0 else None
        rest = line[colon_idx + 1 :] if colon_idx > 0 else line
        if prefix and (prefix.startswith("属性 ") or prefix.startswith("整体")):
            run = p.add_run(prefix)
            run.bold = True
            if rest:
                p.add_run(rest)
        else:
            p.add_run(line)


def generate_difference_report_docx(
    differences: list[DifferenceItem],
    job_dir: Path,
) -> Path:
    """生成 EoICD 与软件高层需求差异报告 .docx。"""
    doc = Document()
    _add_doc_header(
        doc,
        "EoICD 与软件高层需求差异报告",
        [f"差异项总数: {len(differences)}"],
    )

    # 汇总表（5 列：差异编号 / 关联定位 / 差异类型 / 差异描述 / 建议处理方式）
    doc.add_heading("差异汇总", level=2)
    if differences:
        summary_table = doc.add_table(rows=1, cols=5)
        summary_table.style = "Light Grid Accent 1"
        headers = ["差异编号", "关联定位", "差异类型", "差异描述", "建议处理方式"]
        for i, h in enumerate(headers):
            cell = summary_table.rows[0].cells[i]
            cell.text = h
            run = cell.paragraphs[0].runs[0]
            run.bold = True
            run.font.size = Pt(10)

        for diff in differences:
            row = summary_table.add_row().cells
            row[0].text = diff.difference_id or "—"
            row[1].text = _format_diff_location(diff)
            row[2].text = diff.difference_type
            row[3].text = diff.description
            row[4].text = diff.suggested_action
            for c in row:
                for p in c.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(9)
    else:
        doc.add_paragraph("（无差异项）")
    doc.add_paragraph("")

    # 详情
    doc.add_heading("差异详情", level=2)
    for diff in differences:
        diff_id_display = diff.difference_id or "—"
        doc.add_heading(f"{diff_id_display} - {diff.difference_type}", level=3)

        # 关联定位
        p = doc.add_paragraph()
        p.add_run("关联定位:").bold = True
        if diff.difference_requirement_id:
            doc.add_paragraph(f"  SRS 需求 ID: {diff.difference_requirement_id}")
        if diff.difference_eoicd_entry_id:
            doc.add_paragraph(f"  EoICD 条目 ID: {diff.difference_eoicd_entry_id}")
        if not diff.difference_requirement_id and not diff.difference_eoicd_entry_id:
            doc.add_paragraph("  （无关联定位）")

        p = doc.add_paragraph()
        p.add_run("EoICD 条目化需求: ").bold = True
        p.add_run(diff.eoicd_requirement_text or "（无）")
        p = doc.add_paragraph()
        p.add_run("软件高层需求: ").bold = True
        p.add_run(diff.software_requirement_text or "（无）")
        p = doc.add_paragraph()
        p.add_run("差异描述: ").bold = True
        _render_description(doc, diff.description)
        p = doc.add_paragraph()
        p.add_run("建议处理方式: ").bold = True
        p.add_run(diff.suggested_action)
        doc.add_paragraph("")

    doc.add_heading("备注", level=2)
    doc.add_paragraph("本报告由 ICD工具原型Ver2.0 基于 DeepSeek comparison agent 自动生成，差异分析结果需人工复核。")
    doc.add_paragraph("差异类型包括：缺失、不一致、冗余、需确认。")

    out = job_dir / "EoICD与软件高层需求差异报告.docx"
    doc.save(str(out))
    return out


# 向后兼容旧 pipeline 调用的命名
def generate_requirement_docx(*args, **kwargs) -> Path:  # pragma: no cover
    raise NotImplementedError(
        "旧 generate_requirement_docx 已被 generate_best_docx 替代；"
        "请使用新接口。"
    )
