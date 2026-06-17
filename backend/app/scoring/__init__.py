"""
scoring/ 模块统一入口。

提供：
- select_best_for_chunk(chunk_id, candidates, agent_scores)：单 chunk 内择优
- select_best_candidate(candidates, agent_scores)：向后兼容旧 pipeline
"""

from app.models import BestChunkResult, ChunkAgentScoreResult, ChunkCandidate
from app.scoring.scorer import (
    python_rule_score,
    select_best_candidate,
    select_best_for_chunk,
)

__all__ = [
    "python_rule_score",
    "select_best_for_chunk",
    "select_best_candidate",
]
