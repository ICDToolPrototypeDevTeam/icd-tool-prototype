"""
条目化需求生成多智能体 stub（crew/candidate_generator.py）。

当前版本不实现真实 CrewAI 编排逻辑，仅返回固定两份结构化候选结果。
"""

from app.models import EoICDCandidate, UnifiedInputPackage
from app.prompts import load_prompt
from app.skills import load_skill


def generate_candidates(unified_package: UnifiedInputPackage) -> list[EoICDCandidate]:
    """
    生成两份 EoICD 条目化需求候选结果。

    Args:
        unified_package: 统一分析输入包

    Returns:
        两份 EoICDCandidate 列表
    """
    # 加载 prompt 和 skill（stub 加载，实际不使用 LLM）
    _ = load_prompt("generation_prompt")
    _ = load_skill("generation_skill")

    # Stub 候选1：接口级条目化需求
    candidate_1_entries = [
        {
            "entry_id": "REQ-001",
            "description": "系统应每10ms采集并报告发动机转速信号（EngineSpeed），信号数据类型为uint16。",
            "interface_name": "Engine_Status_Report",
            "signal_name": "EngineSpeed",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": "REQ-002",
            "description": "系统应每10ms采集并报告发动机扭矩信号（EngineTorque），信号数据类型为uint16。",
            "interface_name": "Engine_Status_Report",
            "signal_name": "EngineTorque",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": "REQ-003",
            "description": "系统应每100ms采集并报告车速信号（VehicleSpeed），信号数据类型为uint16，供车身稳定系统使用。",
            "interface_name": "Vehicle_Speed_Report",
            "signal_name": "VehicleSpeed",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": "REQ-004",
            "description": "系统应接收制动踏板位置信号（BrakePedalPosition），信号数据类型为uint8，精度不低于8位。",
            "interface_name": "Brake_Request",
            "signal_name": "BrakePedalPosition",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": "REQ-005",
            "description": "系统应接收目标挡位指令（TargetGear），信号数据类型为uint8，换挡策略由整车控制器决定。",
            "interface_name": "Gear_Command",
            "signal_name": "TargetGear",
            "source": "EoICD Word 主文件接口定义",
        },
    ]

    # Stub 候选2：功能级条目化需求（合并同一接口多个信号）
    candidate_2_entries = [
        {
            "entry_id": "REQ-001",
            "description": "系统应实时监控发动机运行状态，包括转速和扭矩，转速和扭矩信号均应每10ms更新一次，数据类型为uint16。",
            "interface_name": "Engine_Status_Report",
            "signal_name": "EngineSpeed, EngineTorque",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": "REQ-002",
            "description": "系统应实时监控车速信息，车速信号应每100ms更新一次，数据类型为uint16，供车身稳定系统使用。",
            "interface_name": "Vehicle_Speed_Report",
            "signal_name": "VehicleSpeed",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": "REQ-003",
            "description": "制动系统应接收并处理制动踏板位置信号，信号精度不低于8位，响应时间应满足实时性要求。",
            "interface_name": "Brake_Request",
            "signal_name": "BrakePedalPosition",
            "source": "EoICD Word 主文件接口定义",
        },
        {
            "entry_id": "REQ-004",
            "description": "动力系统应接收并执行整车控制器下发的目标挡位指令，挡位信号数据类型为uint8，指令来源为整车控制器。",
            "interface_name": "Gear_Command",
            "signal_name": "TargetGear",
            "source": "EoICD Word 主文件接口定义",
        },
    ]

    candidate_1 = EoICDCandidate(
        candidate_id="candidate-1",
        entries=candidate_1_entries,
        summary="候选1采用接口级条目化方式，每个信号独立成条，描述精确，适合精确追溯场景。",
    )

    candidate_2 = EoICDCandidate(
        candidate_id="candidate-2",
        entries=candidate_2_entries,
        summary="候选2采用功能级条目化方式，同一接口多个信号合并描述，表述简洁，适合快速理解场景。",
    )

    return [candidate_1, candidate_2]