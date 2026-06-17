"""
crew/candidate_reviewer.py —— scoring crew pipeline 入口。

对单个 EoICD chunk 的 MiniMax 候选和 DeepSeek 候选调用 build_scoring_crew，
返回每个 agent 的评分结果（list[ChunkAgentScoreResult]）。
"""

from __future__ import annotations

from app.crew.crews import build_scoring_crew
from app.models import ChunkAgentScoreResult, ChunkCandidate, EoICDChunk, ScoringEntry


def review_for_chunk(
    chunk: EoICDChunk,
    minimax_cand: ChunkCandidate,
    deepseek_cand: ChunkCandidate,
) -> list[ChunkAgentScoreResult]:
    """对单个 chunk 的两份候选调用 scoring crew，返回 agent 评分列表。

    每个 scoring agent 输出 2 条评分（同时评 MiniMax / DeepSeek 候选）；
    两个 agent 共返回 4 条评分。Python 端只取与 candidate_id 对应的部分
    传给后续 scoring 融合。

    Args:
        chunk: 单个 EoICD chunk
        minimax_cand: MiniMax generation 候选
        deepseek_cand: DeepSeek generation 候选

    Returns:
        ChunkAgentScoreResult 列表（来自两个 scoring agent 的输出）
    """
    crew = build_scoring_crew(chunk, minimax_cand, deepseek_cand)
    result = crew.kickoff()

    agent_scores: list[ChunkAgentScoreResult] = []
    for t in result.tasks_output:
        if t.pydantic is None:
            raise RuntimeError(
                f"Scoring Task 未返回 Pydantic 输出：{t.description[:80]!r}"
            )
        for entry in t.pydantic.scores:
            agent_scores.append(
                ChunkAgentScoreResult(
                    candidate_id=entry.candidate_id,
                    chunk_id=chunk.chunk_id,
                    score=entry.score,
                    reasoning=entry.reasoning,
                    recommended_is_best=entry.recommended_is_best,
                )
            )

    return agent_scores
