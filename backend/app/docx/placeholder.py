"""
占位 DOCX 输出模块。

当前版本使用 python-docx 生成最小占位 Word 文件，供下载接口返回。
"""

from pathlib import Path

from docx import Document
from docx.shared import Pt


def generate_output_docx(
    output_dir: Path,
    filename: str,
    title: str,
) -> Path:
    """
    生成一个最小占位 Word 文档。

    Args:
        output_dir: 输出目录
        filename: 文件名
        title: 文档标题

    Returns:
        生成的文档路径
    """
    doc = Document()
    doc.add_heading(title, level=1)
    doc.add_paragraph('（此为占位文档，真实内容在后续 Issue 中生成）')
    doc.add_paragraph(f'生成时间: {__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

    output_path = output_dir / filename
    doc.save(str(output_path))
    return output_path