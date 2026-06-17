"""
crew/crews.py —— CrewAI Crew 工厂。

3 个 Crew：
- generation_crew：2 个 Agent（M MiniMax + DeepSeek）+ 2 个 Task
- scoring_crew：2 个 Agent（M MiniMax + DeepSeek）+ 2 个 Task
- comparison_crew：1 个 Agent（DeepSeek）+ 1 个 Task
"""

from __future__ import annotations

from crewai import Crew, Process  # type: ignore

from app.crew.agents import (
    build_deepseek_comparison_agent,
    build_deepseek_generation_agent,
    build_deepseek_scoring_agent,
    build_minimax_generation_agent,
    build_minimax_scoring_agent,
)
from app.crew.tasks import (
    build_deepseek_comparison_task,
    build_deepseek_generation_task,
    build_deepseek_scoring_task,
    build_minimax_generation_task,
    build_minimax_scoring_task,
)
from app.llm.factory import crewai_verbose
from app.models import (
    ChunkCandidate,
    EoICDChunk,
    MergedRequirementResult,
    ParsedSoftwareRequirements,
)


def _common_crew_kwargs() -> dict:
    return {
        "process": Process.sequential,
        "verbose": crewai_verbose(),
    }


def build_generation_crew(chunk: EoICDChunk) -> Crew:
    """对单个 chunk 的 generation crew：M MiniMax + DeepSeek 各生成一份候选。"""
    a_minimax = build_minimax_generation_agent()
    a_deepseek = build_deepseek_generation_agent()
    return Crew(
        agents=[a_minimax, a_deepseek],
        tasks=[
            build_minimax_generation_task(a_minimax, chunk),
            build_deepseek_generation_task(a_deepseek, chunk),
        ],
        **_common_crew_kwargs(),
    )


def build_scoring_crew(
    chunk: EoICDChunk,
    minimax_cand: ChunkCandidate,
    deepseek_cand: ChunkCandidate,
) -> Crew:
    """对单个 chunk 的 scoring crew：M MiniMax + DeepSeek 同时评两份候选。"""
    a_minimax = build_minimax_scoring_agent()
    a_deepseek = build_deepseek_scoring_agent()
    return Crew(
        agents=[a_minimax, a_deepseek],
        tasks=[
            build_minimax_scoring_task(a_minimax, chunk, minimax_cand, deepseek_cand),
            build_deepseek_scoring_task(a_deepseek, chunk, minimax_cand, deepseek_cand),
        ],
        **_common_crew_kwargs(),
    )


def build_comparison_crew(
    merged: MergedRequirementResult,
    sw_req: ParsedSoftwareRequirements,
) -> Crew:
    """comparison crew：仅 DeepSeek，做差异分析。"""
    a_deepseek = build_deepseek_comparison_agent()
    return Crew(
        agents=[a_deepseek],
        tasks=[build_deepseek_comparison_task(a_deepseek, merged, sw_req)],
        **_common_crew_kwargs(),
    )
