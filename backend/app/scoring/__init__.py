"""
scoring/ 模块统一入口。

提供 select_best_candidate 函数，融合 crew 评分和 Python 规则评分，决策最佳候选。
"""

from app.models import AgentScoreResult, EoICDCandidate, ScoredCandidate
from app.scoring.scorer import score_and_select

__all__ = ["select_best_candidate"]


def select_best_candidate(
    candidates: list[EoICDCandidate],
    agent_scores: list[AgentScoreResult],
) -> ScoredCandidate:
    """
    融合评分并选择最佳候选。

    Args:
        candidates: 候选结果列表
        agent_scores: crew 多智能体评分结果列表

    Returns:
        评分最高的 ScoredCandidate
    """
    return score_and_select(candidates, agent_scores)