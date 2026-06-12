"""
评分与择优模块（scoring/scorer.py）。

当前版本不执行真实 Python 硬规则评分逻辑，仅返回固定评分和决策结果。
"""

from app.models import AgentScoreResult, EoICDCandidate, PythonRuleScoreResult, ScoredCandidate


def score_and_select(
    candidates: list[EoICDCandidate],
    agent_scores: list[AgentScoreResult],
) -> ScoredCandidate:
    """
    接收 crew 打分结果，执行 Python 规则评分，融合分数并选择最佳候选。

    融合公式：最终分数 = crew评分平均值 × 0.6 + Python规则评分 × 0.4

    Args:
        candidates: 候选结果列表（两份）
        agent_scores: crew 多智能体评分结果列表

    Returns:
        ScoredCandidate，包含最终评分最高的候选结果
    """
    # 构建 candidate_id -> score 映射
    agent_score_map = {s.candidate_id: s for s in agent_scores}

    scored_candidates: list[ScoredCandidate] = []

    for candidate in candidates:
        agent_score = agent_score_map.get(candidate.candidate_id)
        if agent_score is None:
            continue

        # Stub Python 规则评分：固定 80 分（满分100）
        python_rule_score = PythonRuleScoreResult(
            candidate_id=candidate.candidate_id,
            score=80.0,
            rule_details={
                "structure_score": 20,
                "completeness_score": 20,
                "traceability_score": 20,
                "consistency_score": 20,
            },
        )

        # 融合分数
        final_score = agent_score.score * 0.6 + python_rule_score.score * 0.4

        scored_candidates.append(
            ScoredCandidate(
                candidate=candidate,
                agent_score=agent_score,
                python_rule_score=python_rule_score,
                final_score=final_score,
                is_best=False,
            )
        )

    # 选择最佳候选（分数最高者）
    if scored_candidates:
        best = max(scored_candidates, key=lambda x: x.final_score)
        best.is_best = True
        return best

    # 兜底（不应发生）
    raise ValueError("No candidates to score")