"""
docx/ 模块统一入口。

提供：
- generate_minimax_docx(minimax_merged, job_dir)
- generate_deepseek_docx(deepseek_merged, job_dir)
- generate_best_docx(merged, job_dir)（同时落"最优条目化需求.docx"和"EoICD条目化需求.docx"）
- generate_difference_report_docx(differences, job_dir)
"""

from app.docx.generator import (
    generate_best_docx,
    generate_deepseek_docx,
    generate_difference_report_docx,
    generate_minimax_docx,
)

__all__ = [
    "generate_minimax_docx",
    "generate_deepseek_docx",
    "generate_best_docx",
    "generate_difference_report_docx",
]
