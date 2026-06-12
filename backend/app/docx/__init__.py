"""
docx/ 模块统一入口。

提供两个文档生成函数：
- generate_requirement_docx：生成 EoICD 条目化需求文档
- generate_difference_report_docx：生成差异报告文档
"""

from pathlib import Path

from app.models import DifferenceItem, EoICDCandidate
from app.docx.generator import generate_requirement_docx, generate_difference_report_docx

__all__ = ["generate_requirement_docx", "generate_difference_report_docx"]