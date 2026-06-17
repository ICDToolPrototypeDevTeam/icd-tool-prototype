"""
crew/tasks.py —— CrewAI Task 工厂。

每个 Task 对应一个 Agent。Task.description 由 prompt 文本 + Python 端注入的
运行时上下文（chunk / candidate / merged / sw_req）拼接而成，**不修改**
原 prompts/*.md 文本资产。

Task.output_pydantic 用于把模型输出强制解析为 Pydantic 模型，便于 Python 端
拿到结构化结果。
"""

from __future__ import annotations

from crewai import Agent, Task  # type: ignore

from app.llm.prompt_loader import build_task_context, dump_json
from app.models import (
    ChunkCandidate,
    ComparisonOutput,
    EoICDChunk,
    MergedRequirementResult,
    ParsedSoftwareRequirements,
    ScoringOutput,
)


def build_minimax_generation_task(agent: Agent, chunk: EoICDChunk) -> Task:
    """generation Task：MiniMax 对单个 chunk 生成候选。"""
    return Task(
        description=build_task_context(
            "generation_prompt",
            chunk_id=chunk.chunk_id,
            chunk_title=chunk.chunk_title,
            chunk_content=chunk.content,
            interfaces=chunk.interfaces,
            context_summary=chunk.context_summary,
            model_name="MiniMax",
        ),
        expected_output=(
            "JSON 对象：包含 candidate_id / chunk_id / model_name / entries[] / summary。"
            "entries 每条必须包含 entry_id / description / interface_name / signal_name / source。"
        ),
        agent=agent,
        output_pydantic=ChunkCandidate,
    )


def build_deepseek_generation_task(agent: Agent, chunk: EoICDChunk) -> Task:
    """generation Task：DeepSeek 对单个 chunk 生成候选。"""
    return Task(
        description=build_task_context(
            "generation_prompt",
            chunk_id=chunk.chunk_id,
            chunk_title=chunk.chunk_title,
            chunk_content=chunk.content,
            interfaces=chunk.interfaces,
            context_summary=chunk.context_summary,
            model_name="DeepSeek",
        ),
        expected_output=(
            "JSON 对象：包含 candidate_id / chunk_id / model_name / entries[] / summary。"
            "entries 每条必须包含 entry_id / description / interface_name / signal_name / source。"
        ),
        agent=agent,
        output_pydantic=ChunkCandidate,
    )


def build_minimax_scoring_task(
    agent: Agent,
    chunk: EoICDChunk,
    minimax_cand: ChunkCandidate,
    deepseek_cand: ChunkCandidate,
) -> Task:
    """scoring Task：MiniMax 同时评两份候选。"""
    return Task(
        description=build_task_context(
            "scoring_prompt",
            chunk_id=chunk.chunk_id,
            chunk_context_summary=chunk.context_summary,
            minimax_candidate=dump_json(minimax_cand),
            deepseek_candidate=dump_json(deepseek_cand),
        ),
        expected_output=(
            "JSON 对象：包含 scores[]，每条含 candidate_id / score (0-100) / "
            "reasoning / recommended_is_best。"
        ),
        agent=agent,
        output_pydantic=ScoringOutput,
    )


def build_deepseek_scoring_task(
    agent: Agent,
    chunk: EoICDChunk,
    minimax_cand: ChunkCandidate,
    deepseek_cand: ChunkCandidate,
) -> Task:
    """scoring Task：DeepSeek 同时评两份候选。"""
    return Task(
        description=build_task_context(
            "scoring_prompt",
            chunk_id=chunk.chunk_id,
            chunk_context_summary=chunk.context_summary,
            minimax_candidate=dump_json(minimax_cand),
            deepseek_candidate=dump_json(deepseek_cand),
        ),
        expected_output=(
            "JSON 对象：包含 scores[]，每条含 candidate_id / score (0-100) / "
            "reasoning / recommended_is_best。"
        ),
        agent=agent,
        output_pydantic=ScoringOutput,
    )


def build_deepseek_comparison_task(
    agent: Agent,
    merged: MergedRequirementResult,
    sw_req: ParsedSoftwareRequirements,
) -> Task:
    """comparison Task：DeepSeek 对最终最优条目化需求与软件高层需求做对比。"""
    return Task(
        description=build_task_context(
            "comparison_prompt",
            merged_entries=dump_json(merged.entries),
            merged_summary=merged.summary,
            software_requirements=dump_json(
                [r.model_dump() for r in sw_req.requirements]
            ),
        ),
        expected_output=(
            "JSON 对象：包含 differences[]，每条含 difference_id / difference_type / "
            "requirement_text / software_requirement_text / description / suggested_action。"
        ),
        agent=agent,
        output_pydantic=ComparisonOutput,
    )
