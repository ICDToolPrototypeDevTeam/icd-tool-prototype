"""
EoICD 主文件解析模块（stub → chunk 列表）。

当前版本不实现真实 Word 解析逻辑，仅返回结构化 stub 数据，
但已按 chunk-level 设计封装为 List[EoICDChunk]，便于后续多 chunk 扩展。
"""

from pathlib import Path

from app.models import EoICDChunk, ParsedEoICDInterface


def parse_eoicd_word(word_path: Path) -> list[EoICDChunk]:
    """
    解析 EoICD Word 主文件，返回 EoICDChunk 列表。

    本 Issue 默认将整个 EoICD 主文件封装为 1 个 chunk-001。
    后续 parser 升级为多 chunk 时，下游 crew / scoring / docx 流程不需大改。

    Args:
        word_path: EoICD Word 文件路径

    Returns:
        EoICDChunk 列表（本 Issue 默认长度为 1）
    """
    # Stub 数据：模拟解析后的接口列表
    interfaces = [
        ParsedEoICDInterface(
            interface_name="Engine_Status_Report",
            interface_direction="发送",
            signal_name="EngineSpeed",
            data_type="uint16",
            transfer_cycle="10ms",
            source_file=str(word_path),
            description="发动机转速报告信号",
        ),
        ParsedEoICDInterface(
            interface_name="Engine_Status_Report",
            interface_direction="发送",
            signal_name="EngineTorque",
            data_type="uint16",
            transfer_cycle="10ms",
            source_file=str(word_path),
            description="发动机扭矩报告信号",
        ),
        ParsedEoICDInterface(
            interface_name="Vehicle_Speed_Report",
            interface_direction="发送",
            signal_name="VehicleSpeed",
            data_type="uint16",
            transfer_cycle="100ms",
            source_file=str(word_path),
            description="车速报告信号",
        ),
        ParsedEoICDInterface(
            interface_name="Brake_Request",
            interface_direction="接收",
            signal_name="BrakePedalPosition",
            data_type="uint8",
            transfer_cycle="10ms",
            source_file=str(word_path),
            description="制动踏板位置请求",
        ),
        ParsedEoICDInterface(
            interface_name="Gear_Command",
            interface_direction="接收",
            signal_name="TargetGear",
            data_type="uint8",
            transfer_cycle="事件触发",
            source_file=str(word_path),
            description="目标挡位命令",
        ),
    ]

    content = _format_interfaces_as_content(interfaces)
    context_summary = (
        "本 chunk 包含 5 个 EoICD 接口信号，覆盖发动机状态、车速、制动踏板、"
        "目标挡位等典型车辆控制接口。"
    )

    chunk = EoICDChunk(
        chunk_id="chunk-001",
        chunk_title="EoICD 主文件（默认单 chunk）",
        source_file=str(word_path),
        source_section="全篇",
        source_page_range="全文",
        content=content,
        tables=[],
        interfaces=interfaces,
        context_summary=context_summary,
    )

    return [chunk]


def _format_interfaces_as_content(interfaces: list[ParsedEoICDInterface]) -> str:
    """把接口列表格式化为可读文本，作为 chunk.content。

    这样 crew 生成/评分/对比 agent 在没有真实 Word 解析结果时，
    仍能从 chunk.content 看到结构化描述。
    """
    lines = ["# EoICD 接口定义（解析自 Word 主文件）\n"]
    for i, it in enumerate(interfaces, 1):
        lines.append(
            f"{i}. 接口名: {it.interface_name}\n"
            f"   - 方向: {it.interface_direction}\n"
            f"   - 信号名: {it.signal_name}\n"
            f"   - 数据类型: {it.data_type}\n"
            f"   - 传输周期: {it.transfer_cycle or '未指定'}\n"
            f"   - 描述: {it.description or '无'}\n"
        )
    return "\n".join(lines)