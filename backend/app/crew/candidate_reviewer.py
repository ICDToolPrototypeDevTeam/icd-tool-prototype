"""
候选结果打分多智能体 stub（crew/candidate_reviewer.py）。

当前版本不实现真实 CrewAI 编排逻辑，仅返回固定评分结果。
"""

from app.models import AgentScoreResult, EoICDCandidate, UnifiedInputPackage
from app.prompts import load_prompt
from app.skills import load_skill


def review_candidates(
    candidates: list[EoICDCandidate],
    unified_package: UnifiedInputPackage,
) -> list[AgentScoreResult]:
    """
    对两份候选结果进行打分/互评。

    Args:
        candidates: 候选结果列表（两份）
        unified_package: 统一分析输入包

    Returns:
        每份候选的 AgentScoreResult 列表
    """
    # 加载 prompt 和 skill（stub 加载，实际不使用 LLM）
    _ = load_prompt("scoring_prompt")
    _ = load_skill("scoring_skill")

    # Stub 固定评分：
    # 候选1：完整性高（接口级独立条目），但略冗余
    # 候选2：可读性好，但部分细节有遗漏
    results = [
        AgentScoreResult(
            candidate_id="candidate-1",
            score=82.0,
            reasoning="接口级条目化完整覆盖所有信号，追溯性强，但部分描述略显冗余。",
        ),
        AgentScoreResult(
            candidate_id="candidate-2",
            score=78.0,
            reasoning="功能级条目化表述简洁，可读性好，但合并描述导致部分细节信息丢失。",
        ),
    ]

    return results