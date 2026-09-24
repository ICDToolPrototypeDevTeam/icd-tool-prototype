# -*- coding: utf-8 -*-
"""Excel generator: EoICD itemization."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from app.job_manager import raise_if_cancelled
from app.v4.models import EoICDOutput


_HEADER_FONT = Font(name="微软雅黑", size=10, bold=True)
_HEADER_FILL = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center")
_CELL_FONT = Font(name="微软雅黑", size=9)
_WRAP_ALIGNMENT = Alignment(vertical="top", wrap_text=True)

# 取消检查点间隔：逐单元格写入是纯 Python 循环，行边界即可响应终止
# （取消是协作式的，见 app.job_manager.raise_if_cancelled）
_CANCEL_CHECK_ROWS = 5000


def generate_eoicd_excel(
    eoicd_json_path: Path,
    output_path: Path,
    eoicd_out: Optional[EoICDOutput] = None,
) -> None:
    """Generate EoICD itemization Excel workbook.

    Columns: 序号 | IRD ID | 描述

    ``eoicd_out`` 传解析结果对象时直接复用管线内存里的数据；不传则回退为读
    ``eoicd_json_path``（CLI 路径）。12 万条时重新读一遍 JSON 实测多占 410MB
    峰值，896MB 的服务器上足以把这一步拖入换页（BUG-20260923-007）。
    """
    if eoicd_out is not None:
        requirements: list = eoicd_out.requirements
        rows: Iterator[tuple[str, str]] = ((r.ird_id, r.description) for r in requirements)
    else:
        import json
        requirements = json.loads(eoicd_json_path.read_text(encoding="utf-8")).get("requirements", [])
        rows = ((r.get("ird_id", ""), r.get("description", "")) for r in requirements)
    total = len(requirements)

    print(f"  Generating EoICD Excel ({total} rows)...")

    wb = Workbook()
    ws = wb.active
    ws.title = "EoICD条目化清单"

    # Header row
    headers = ["序号", "IRD ID", "描述"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _HEADER_ALIGNMENT

    # Data rows
    for i, (ird_id, description) in enumerate(rows, 1):
        # 取消检查点：本循环是纯 Python，行边界即可响应终止
        if i % _CANCEL_CHECK_ROWS == 0:
            raise_if_cancelled()
        ws.cell(row=i + 1, column=1, value=i).font = _CELL_FONT
        ws.cell(row=i + 1, column=2, value=ird_id).font = _CELL_FONT
        ws.cell(row=i + 1, column=3, value=description).font = _CELL_FONT
        ws.cell(row=i + 1, column=3).alignment = _WRAP_ALIGNMENT

    # Column widths
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["C"].width = 90

    # Freeze header + auto-filter
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:C{total + 1}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output_path))
    print(f"  EoICD Excel: {output_path}")
