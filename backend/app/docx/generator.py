"""
DOCX 输出文档生成模块（docx/generator.py）。

生成两个结构化 Word 文档：
- EoICD条目化需求.docx
- EoICD与软件高层需求差异报告.docx
"""

from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor
from docx.table import Table
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.models import DifferenceItem, EoICDCandidate


def generate_requirement_docx(
    best_candidate: EoICDCandidate,
    job_dir: Path,
) -> Path:
    """
    生成 EoICD 条目化需求 Word 文档。

    Args:
        best_candidate: 评分最佳候选结果
        job_dir: 任务输出目录

    Returns:
        生成的文档路径
    """
    doc = Document()

    # 文档标题
    title = doc.add_heading('EoICD 条目化需求', level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 文档信息
    doc.add_paragraph(f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    doc.add_paragraph(f'最佳候选: {best_candidate.candidate_id}')
    doc.add_paragraph('')

    # 摘要
    doc.add_heading('候选结果摘要', level=2)
    doc.add_paragraph(best_candidate.summary)
    doc.add_paragraph('')

    # 条目化需求表格
    doc.add_heading('条目化需求列表', level=2)

    table: Table = doc.add_table(rows=1, cols=5)
    table.style = 'Light Grid Accent 1'

    # 表头
    header_cells = table.rows[0].cells
    headers = ['条目编号', '需求描述', '接口名称', '信号名称', '来源']
    for i, header in enumerate(headers):
        header_cells[i].text = header
        run = header_cells[i].paragraphs[0].runs[0]
        run.bold = True
        run.font.size = Pt(10)

    # 数据行
    for entry in best_candidate.entries:
        row_cells = table.add_row().cells
        row_cells[0].text = entry.get('entry_id', '')
        row_cells[1].text = entry.get('description', '')
        row_cells[2].text = entry.get('interface_name', '')
        row_cells[3].text = entry.get('signal_name', '')
        row_cells[4].text = entry.get('source', '')
        for cell in row_cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9)

    doc.add_paragraph('')

    # 备注
    doc.add_heading('备注', level=2)
    doc.add_paragraph('本文档由 ICD工具原型Ver2.0 自动生成，需求内容需人工复核。')
    doc.add_paragraph('条目化需求用于辅助需求整理和差异分析，不替代人工工程判断。')

    output_path = job_dir / 'EoICD条目化需求.docx'
    doc.save(str(output_path))
    return output_path


def generate_difference_report_docx(
    differences: list[DifferenceItem],
    job_dir: Path,
) -> Path:
    """
    生成 EoICD 与软件高层需求差异报告 Word 文档。

    Args:
        differences: 差异项列表
        job_dir: 任务输出目录

    Returns:
        生成的文档路径
    """
    doc = Document()

    # 文档标题
    title = doc.add_heading('EoICD 与软件高层需求差异报告', level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 文档信息
    doc.add_paragraph(f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    doc.add_paragraph(f'差异项总数: {len(differences)}')
    doc.add_paragraph('')

    # 差异汇总表格
    doc.add_heading('差异汇总', level=2)

    table: Table = doc.add_table(rows=1, cols=4)
    table.style = 'Light Grid Accent 1'

    # 表头
    header_cells = table.rows[0].cells
    headers = ['差异编号', '差异类型', '差异描述', '建议处理方式']
    for i, header in enumerate(headers):
        header_cells[i].text = header
        run = header_cells[i].paragraphs[0].runs[0]
        run.bold = True
        run.font.size = Pt(10)

    # 数据行
    for diff in differences:
        row_cells = table.add_row().cells
        row_cells[0].text = diff.difference_id
        row_cells[1].text = diff.difference_type
        row_cells[2].text = diff.description
        row_cells[3].text = diff.suggested_action
        for cell in row_cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9)

    doc.add_paragraph('')

    # 差异详情
    doc.add_heading('差异详情', level=2)

    for diff in differences:
        doc.add_heading(f'{diff.difference_id} - {diff.difference_type}', level=3)

        p = doc.add_paragraph()
        p.add_run('EoICD 条目化需求: ').bold = True
        p.add_run(diff.requirement_text or '（无）')

        p = doc.add_paragraph()
        p.add_run('软件高层需求: ').bold = True
        p.add_run(diff.software_requirement_text or '（无）')

        p = doc.add_paragraph()
        p.add_run('差异描述: ').bold = True
        p.add_run(diff.description)

        p = doc.add_paragraph()
        p.add_run('建议处理方式: ').bold = True
        p.add_run(diff.suggested_action)

        doc.add_paragraph('')

    # 备注
    doc.add_heading('备注', level=2)
    doc.add_paragraph('本报告由 ICD工具原型Ver2.0 自动生成，差异分析结果需人工复核。')
    doc.add_paragraph('差异类型包括：缺失、不一致、冗余、需确认。')

    output_path = job_dir / 'EoICD与软件高层需求差异报告.docx'
    doc.save(str(output_path))
    return output_path