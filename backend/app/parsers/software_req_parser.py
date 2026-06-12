"""
软件高层需求解析模块（stub）。

当前版本不实现真实 Word 解析逻辑，仅返回结构化 stub 数据。
"""

from pathlib import Path

from app.models import ParsedSoftwareRequirements, ParsedSoftwareRequirement


def parse_software_requirement(req_path: Path) -> ParsedSoftwareRequirements:
    """
    解析软件高层需求文档。

    Args:
        req_path: 软件高层需求 Word 文件路径

    Returns:
        ParsedSoftwareRequirements 结构化解析结果
    """
    # Stub 数据：模拟解析后的软件高层需求条目
    requirements = [
        ParsedSoftwareRequirement(
            requirement_id="SRS-001",
            requirement_text="车辆应实时监控发动机转速，转速信号应每10ms更新一次。",
            source_file=str(req_path),
        ),
        ParsedSoftwareRequirement(
            requirement_id="SRS-002",
            requirement_text="车辆应实时监控发动机扭矩，扭矩信号应每10ms更新一次。",
            source_file=str(req_path),
        ),
        ParsedSoftwareRequirement(
            requirement_id="SRS-003",
            requirement_text="车速信息应每100ms更新一次，供车身稳定系统使用。",
            source_file=str(req_path),
        ),
        ParsedSoftwareRequirement(
            requirement_id="SRS-004",
            requirement_text="制动系统应接收制动踏板位置信号，信号精度不低于8位。",
            source_file=str(req_path),
        ),
        ParsedSoftwareRequirement(
            requirement_id="SRS-005",
            requirement_text="换挡策略由整车控制器决定，TCU应接收目标挡位指令并执行。",
            source_file=str(req_path),
        ),
        # 以下两条用于制造差异项，模拟软件需求中缺失的内容
        ParsedSoftwareRequirement(
            requirement_id="SRS-006",
            requirement_text="变速箱应监控油温，油温信号应每1s更新一次。",
            source_file=str(req_path),
        ),
        ParsedSoftwareRequirement(
            requirement_id="SRS-007",
            requirement_text="故障诊断应支持ISO 14229标准。",
            source_file=str(req_path),
        ),
    ]

    return ParsedSoftwareRequirements(requirements=requirements)