"""
crew/candidate_generator.py —— generation crew pipeline 入口。

对单个 EoICD chunk 调用 build_generation_crew(MiniMax + DeepSeek)，
返回 (minimax_candidate, deepseek_candidate) 两份 ChunkCandidate。
"""

from __future__ import annotations

from app.crew.crews import build_generation_crew
from app.models import ChunkCandidate, EoICDChunk


def generate_for_chunk(chunk: EoICDChunk) -> tuple[ChunkCandidate, ChunkCandidate]:
    """对单个 EoICD chunk 调用 generation crew，返回 (minimax_cand, deepseek_cand)。

    CrewAI 编排真实存在；USE_MOCK_LLM=1 时底层走 MockLLM，
    USE_MOCK_LLM=0 时走真实 LLM Provider（MiniMax + DeepSeek）。

    Args:
        chunk: 单个 EoICD chunk

    Returns:
        (minimax_candidate, deepseek_candidate) 元组
    """
    crew = build_generation_crew(chunk)
    result = crew.kickoff()

    cands: list[ChunkCandidate] = []
    for t in result.tasks_output:
        if t.pydantic is None:
            raise RuntimeError(
                f"Generation Task 未返回 Pydantic 输出：{t.description[:80]!r}"
            )
        cand: ChunkCandidate = t.pydantic
        cand.source_chunk_id = chunk.chunk_id
        cands.append(cand)

    if len(cands) != 2:
        raise RuntimeError(
            f"Generation crew 应返回 2 份候选，实际 {len(cands)} 份"
        )

    minimax_cand = next((c for c in cands if c.model_name == "MiniMax"), cands[0])
    deepseek_cand = next((c for c in cands if c.model_name == "DeepSeek"), cands[1])
    return minimax_cand, deepseek_cand
