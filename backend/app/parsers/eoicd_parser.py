"""
EoICD 主文件解析模块（stub）。

当前版本不实现真实 Word 解析逻辑，仅返回结构化 stub 数据。
"""

from pathlib import Path

from app.models import ParsedEoICD, ParsedEoICDInterface


def parse_eoicd_word(word_path: Path) -> ParsedEoICD:
    """
    解析 EoICD Word 主文件。

    Args:
        word_path: EoICD Word 文件路径

    Returns:
        ParsedEoICD 结构化解析结果
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

    return ParsedEoICD(interfaces=interfaces, source_file=str(word_path))