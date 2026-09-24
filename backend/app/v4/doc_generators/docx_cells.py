# -*- coding: utf-8 -*-
"""python-docx 表格单元格的快路径。

``_Row.cells`` → ``Table.row_cells(idx)`` → ``Table._cells`` 会在**每次调用**时用
``iter_tcs()`` 把整张表的单元格网格重建一遍（还带 vMerge / gridSpan 回溯）。逐行
填表时每行取 6 次单元格，整表因此是 O(行数²)：1618 行的正向报告实测卡住 5.8 分钟
不产出文件（BUG-20260923-009；2026-09-24 同 N 对照：800 行 19.57s → 1.13s，
document.xml 逐字节相同）。

:func:`row_cells` 直接取本行 ``tr`` 里的 ``tc``，逐行开销只与本行列数有关，整表回到
O(行数·列数)。前提是本行**没有横向合并**（合并时一个 ``tc`` 占多个网格列，``tc_lst``
比列数短）；一旦不满足就回落到 python-docx 的原路径，保证结果不因快路径而不同。
"""
from __future__ import annotations

from typing import List

from docx.table import _Cell, _Row


def row_cells(row: _Row) -> List[_Cell]:
    """本行的单元格序列（等价于 ``row.cells``，但逐行开销是 O(列数)）。"""
    tcs = row._tr.tc_lst
    # 两条前提：tc 个数等于网格列数，且没有跨列。任一不成立说明有横向合并，
    # 回落原路径（合并单元格会重复引用同一个 tc，这里不能自行拼网格）
    if len(tcs) != row.table._column_count or any(tc.grid_span != 1 for tc in tcs):
        return list(row.cells)
    return [_Cell(tc, row) for tc in tcs]
