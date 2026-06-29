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


def _nest_row(row: dict) -> dict:
    """将扁平 entity.field → value 的行转换为嵌套 {entity: {field: value}}。"""
    nested: dict = {}
    for key, value in row.items():
        entity, field = key.split(".", 1)
        nested.setdefault(entity, {})[field] = value
    return nested


def _derive_hierarchy(row: dict) -> list[str]:
    """从 flat row 的 keys 中按出现顺序提取实体层级。"""
    seen: list[str] = []
    for key in row:
        entity = key.split(".", 1)[0]
        if entity not in seen:
            seen.append(entity)
    return seen


def build_nested_sheets(parsed_excel: ParsedEoICDExcel) -> list[dict]:
    """将 ParsedEoICDExcel 的所有 Sheet 转换为嵌套三层结构。

    publisher 和 subscriber 按 index 配对放入同一 row，避免 pub/sub 关联被拆开。

    Returns:
        [
          {
            "sheet_name": "A825-RP",
            "bus_type": "A825",
            "hierarchy": {
              "publisher": ["Software", "LogicalPort", "CANMessage", "A429Word", "DP"],
              "subscriber": ["Software", "LogicalPort", ...],
            },
            "rows": [
              {
                "publisher": {"Software": {...}, "LogicalPort": {...}, ...},
                "subscriber": {"Software": {...}, "LogicalPort": {...}, ...},
              },
              ...
            ]
          },
          ...
        ]
    """
    result: list[dict] = []
    for s in parsed_excel.sheets:
        pub_hierarchy = s.hierarchy_chain
        sub_hierarchy = (
            _derive_hierarchy(s.subscriber_rows[0])
            if s.subscriber_rows
            else []
        )
        pub_nested = [_nest_row(r) for r in s.publisher_rows]
        sub_nested = [_nest_row(r) for r in s.subscriber_rows]
        max_rows = max(len(pub_nested), len(sub_nested))
        rows: list[dict] = []
        for i in range(max_rows):
            row: dict = {}
            if i < len(pub_nested):
                row["publisher"] = pub_nested[i]
            if i < len(sub_nested):
                row["subscriber"] = sub_nested[i]
            rows.append(row)
        result.append({
            "sheet_name": s.sheet_name,
            "bus_type": s.bus_type,
            "hierarchy": {
                "publisher": pub_hierarchy,
                "subscriber": sub_hierarchy,
            },
            "rows": rows,
        })
    return result


def _excel_to_chunk(parsed_excel: ParsedEoICDExcel) -> EoICDChunk:
    """将所有 Excel sheet 聚合成一个 EoICDChunk。

    Args:
        parsed_excel: EoICD Excel 附件整体解析结果

    Returns:
        一个包含全部 Excel 解析内容的 EoICDChunk
    """
    sheets = parsed_excel.sheets

    # 上下文摘要
    source_files = parsed_excel.source_files
    sheet_names = [s.sheet_name for s in sheets]
    bus_types = sorted({s.bus_type or "未识别" for s in sheets})
    context_summary = (
        f"从 {len(source_files)} 个 Excel 文件解析得到 {len(sheets)} 个 Sheet，"
        f"Sheet 列表: {', '.join(sheet_names)}；"
        f"总线类型: {', '.join(bus_types)}；"
        f"来源: {', '.join(source_files)}"
    )

    # 构建按 bus_type 归类的 Markdown 摘要
    lines = ["# EoICD Excel 附件解析结果\n"]
    lines.append(f"共解析 {len(sheets)} 个 Sheet。\n")
    bus_type_groups: dict[str, list] = {}
    for s in sheets:
        bt = s.bus_type or "未识别"
        bus_type_groups.setdefault(bt, []).append(s)
    for bt, group in bus_type_groups.items():
        lines.append(f"## 总线类型: {bt}\n")
        for s in group:
            lines.append(f"### Sheet: {s.sheet_name}")
            if s.hierarchy_chain:
                lines.append(f"- 层级链: {' > '.join(s.hierarchy_chain)}")
            lines.append(f"- Publisher 数据: {len(s.publisher_rows)} 行")
            lines.append(f"- Subscriber 数据: {len(s.subscriber_rows)} 行")
            lines.append("")

    return EoICDChunk(
        chunk_id="excel-chunk-001",
        chunk_title="EoICD Excel 附件解析汇总",
        source_file=", ".join(source_files),
        source_section="Excel附件解析",
        source_page_range="",
        content="\n".join(lines),
        tables=[],
        interfaces=[],
        context_summary=context_summary,
        excel_data=build_nested_sheets(parsed_excel),
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
            eoicd_chunks[0].excel_data = build_nested_sheets(eoicd_excel)
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
