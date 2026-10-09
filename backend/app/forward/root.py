# -*- coding: utf-8 -*-
"""正向集成层路径与产物固定名常量。"""
from __future__ import annotations

from pathlib import Path

# backend/forward/（与 backend/app 平级）；__file__ = backend/app/forward/root.py
FORWARD_ROOT = Path(__file__).resolve().parents[2] / "forward"

# 正向入口与 Excel 脚本（相对 FORWARD_ROOT）
ENTRY_SCRIPT = "multi_judge_runner.py"
EXCEL_SCRIPT = ("multi_judge协同", "gen_excel_report.py")

# 交付物固定名：必须与 app/api/v4/runner.py 的 FORWARD_OUTPUT_FILES 一致
# （下载接口/历史结果按名取文件）；一致性由 tests/test_forward_artifacts.py 断言。
DELIVERABLE_XLSX = "EoICD至HLR正向完整性分析明细.xlsx"
DELIVERABLE_DOCX = "EoICD至HLR正向完整性分析报告.docx"

# 适配层写进 <job>/output/ 的汇总快照（供重启后反读，非对外下载）
SUMMARY_JSON = "forward_summary.json"
