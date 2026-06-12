"""
高层需求对比单智能体 stub（crew/difference_analyzer.py）。

当前版本不实现真实 CrewAI 编排逻辑，仅返回固定差异项列表。
"""

from app.models import DifferenceItem, EoICDCandidate, UnifiedInputPackage
from app.prompts import load_prompt
from app.skills import load_skill


def analyze_differences(
    best_candidate: EoICDCandidate,
    unified_package: UnifiedInputPackage,
) -> list[DifferenceItem]:
    """
    将最佳 EoICD 条目化需求与软件高层需求进行差异比对。

    Args:
        best_candidate: 评分后的最佳候选结果
        unified_package: 统一分析输入包

    Returns:
        差异项列表（约5条）
    """
    # 加载 prompt 和 skill（stub 加载，实际不使用 LLM）
    _ = load_prompt("comparison_prompt")
    _ = load_skill("comparison_skill")

    # Stub 差异项（固定5条，模拟真实差异场景）
    differences = [
        DifferenceItem(
            difference_id="diff-1",
            difference_type="缺失",
            requirement_text="REQ-003：系统应每100ms采集并报告车速信号（VehicleSpeed）。",
            software_requirement_text="SRS-003：车速信息应每100ms更新一次，供车身稳定系统使用。",
            description="EoICD 条目化需求描述了接口定义和传输周期，但软件高层需求未明确信号名称和数据类型。",
            suggested_action="建议在软件高层需求中补充信号名称和数据类型定义，保持与 EoICD 一致。",
        ),
        DifferenceItem(
            difference_id="diff-2",
            difference_type="不一致",
            requirement_text="REQ-004：系统应接收制动踏板位置信号，精度不低于8位。",
            software_requirement_text="SRS-004：制动系统应接收制动踏板位置信号，信号精度不低于8位。",
            description="两者精度要求一致，但 EoICD 强调了系统侧接收，软件需求强调了制动系统侧处理，视角不同。",
            suggested_action="建议统一需求描述视角，明确信号流向和职责边界。",
        ),
        DifferenceItem(
            difference_id="diff-3",
            difference_type="需确认",
            requirement_text="REQ-005：系统应接收目标挡位指令，挡位信号数据类型为uint8。",
            software_requirement_text="SRS-005：换挡策略由整车控制器决定，TCU应接收目标挡位指令并执行。",
            description="EoICD 定义了 uint8 数据类型，但软件需求未明确数据类型；两者对挡位控制策略描述一致。",
            suggested_action="建议在软件高层需求中补充挡位信号数据类型定义，与 EoICD 保持一致。",
        ),
        DifferenceItem(
            difference_id="diff-4",
            difference_type="冗余",
            requirement_text="",
            software_requirement_text="SRS-006：变速箱应监控油温，油温信号应每1s更新一次。",
            description="软件高层需求中包含油温监控要求，但 EoICD 中未定义相关接口和信号。",
            suggested_action="建议补充 EoICD 接口定义，或说明油温监控为新增需求且尚未完成接口定义。",
        ),
        DifferenceItem(
            difference_id="diff-5",
            difference_type="缺失",
            requirement_text="",
            software_requirement_text="SRS-007：故障诊断应支持ISO 14229标准。",
            description="软件高层需求要求支持ISO 14229诊断标准，但 EoICD 中未定义相关诊断接口和服务。",
            suggested_action="建议在 EoICD 中补充诊断相关接口定义，或在软件需求中明确暂不包含诊断能力。",
        ),
    ]

    return differences