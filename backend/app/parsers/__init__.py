"""
parsers/ 模块统一入口。

提供 parse_inputs 函数，构建统一分析输入包（chunk-level 版本）。
"""

from pathlib import Path
from typing import Optional

from app.models import EoICDChunk, ParsedEoICDExcel, UnifiedInputPackage
from app.parsers.eoicd_parser import parse_eoicd_word
from app.parsers.eoicd_excel_parser import parse_eoicd_excel
from app.parsers.software_req_parser import parse_software_requirement


def _excel_to_chunk(parsed_excel: ParsedEoICDExcel) -> EoICDChunk:
    """将所有 Excel sheet 聚合成一个 EoICDChunk。

    Args:
        parsed_excel: EoICD Excel 附件整体解析结果

    Returns:
        一个包含全部 Excel 解析内容的 EoICDChunk
    """
    sheets = parsed_excel.sheets

    # 构建 Markdown 文本内容
    lines = ["# EoICD Excel 附件解析结果\n"]
    lines.append(f"共解析 {len(sheets)} 个 Sheet。\n")

    # 按 bus_type 归类整理
    bus_types = {}
    for s in sheets:
        bt = s.bus_type or "未识别"
        bus_types.setdefault(bt, []).append(s)

    for bus_type, group in bus_types.items():
        lines.append(f"## 总线类型: {bus_type}\n")
        for s in group:
            lines.append(f"### Sheet: {s.sheet_name}")
            if s.hierarchy_chain:
                lines.append(f"- 层级链: {' > '.join(s.hierarchy_chain)}")
            lines.append(f"- Publisher 数据: {len(s.publisher_rows)} 行")
            lines.append(f"- Subscriber 数据: {len(s.subscriber_rows)} 行")
            if s.publisher_headers:
                lines.append(f"- Publisher 字段: {', '.join(s.publisher_headers)}")
            if s.subscriber_headers:
                lines.append(f"- Subscriber 字段: {', '.join(s.subscriber_headers)}")
            lines.append("")
        lines.append("")

    # 聚合所有 sheet 的表格数据
    all_tables: list[dict] = []
    for s in sheets:
        table_info = {
            "sheet_name": s.sheet_name,
            "bus_type": s.bus_type,
            "hierarchy_chain": s.hierarchy_chain,
            "publisher_headers": s.publisher_headers,
            "subscriber_headers": s.subscriber_headers,
            "publisher_rows": s.publisher_rows,
            "subscriber_rows": s.subscriber_rows,
        }
        all_tables.append(table_info)

    # 上下文摘要
    bus_type_list = list(bus_types.keys())
    sheet_names = [s.sheet_name for s in sheets]
    source_files = parsed_excel.source_files
    context_summary = (
        f"从 {len(source_files)} 个 Excel 文件解析得到 {len(sheets)} 个 Sheet，"
        f"Sheet 列表: {', '.join(sheet_names)}；"
        f"总线类型: {', '.join(bus_type_list)}；"
        f"来源: {', '.join(source_files)}"
    )

    return EoICDChunk(
        chunk_id="excel-chunk-001",
        chunk_title="EoICD Excel 附件解析汇总",
        source_file=", ".join(source_files),
        source_section="Excel附件解析",
        source_page_range="",
        content="\n".join(lines),
        tables=all_tables,
        interfaces=[],
        context_summary=context_summary,
        excel_data=parsed_excel,
    )


def parse_inputs(
    eoicd_word_path: Optional[Path],
    eoicd_excel_paths: list[Path],
    sw_req_path: Path,
    job_id: str,
) -> UnifiedInputPackage:
    """解析所有输入文件，构建统一分析输入包（chunk-level）。

    Args:
        eoicd_word_path: EoICD Word 主文件路径（可选，None 时从 Excel 构建 chunk）
        eoicd_excel_paths: EoICD Excel 附件路径列表
        sw_req_path: 软件高层需求文件路径
        job_id: 任务标识

    Returns:
        UnifiedInputPackage（包含 eoicd_chunks、software_requirements 与 eoicd_excel）
    """
    # 解析 EoICD Excel 附件（无论是否有 Word，均解析）
    eoicd_excel = None
    if eoicd_excel_paths:
        eoicd_excel = parse_eoicd_excel(eoicd_excel_paths)

    # 解析 EoICD Word 主文件 → chunk 列表（如果提供）
    if eoicd_word_path is not None:
        eoicd_chunks = parse_eoicd_word(eoicd_word_path)
        # 将 Excel 数据附加到第一个 chunk
        if eoicd_chunks and eoicd_excel:
            eoicd_chunks[0].excel_data = eoicd_excel
    elif eoicd_excel is not None and eoicd_excel.sheets:
        # Excel-only 路径：从全部 Excel sheet 构建一个 chunk
        eoicd_chunks = [_excel_to_chunk(eoicd_excel)]
    else:
        eoicd_chunks = []

    # 解析软件高层需求
    software_requirements = parse_software_requirement(sw_req_path)

    return UnifiedInputPackage(
        eoicd_chunks=eoicd_chunks,
        software_requirements=software_requirements,
        job_id=job_id,
        eoicd_excel=eoicd_excel,
    )
