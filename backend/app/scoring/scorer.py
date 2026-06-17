"""
scoring/scorer.py —— chunk-level 评分与择优。

公式：final = agent_score * 0.6 + python_rule_score * 0.4
"""

from __future__ import annotations

from app.models import (
    BestChunkResult,
    ChunkAgentScoreResult,
    ChunkCandidate,
    ChunkPythonScoreResult,
)


# 评分公式权重（与 docs/project/workflow.md 第 6 节保持一致）
AGENT_WEIGHT = 0.6
PYTHON_RULE_WEIGHT = 0.4

# Python 硬规则四维满分
RULE_MAX = {
    "structure_score": 25,
    "completeness_score": 25,
    "traceability_score": 25,
    "consistency_score": 25,
}
RULE_TOTAL_MAX = sum(RULE_MAX.values())  # 100


def python_rule_score(candidate: ChunkCandidate) -> ChunkPythonScoreResult:
    """对一个 ChunkCandidate 执行 Python 硬规则评分，返回 0-100 分。

    四维规则：
    - structure_score：每条 entry 必填字段是否齐全
    - completeness_score：entry 数量、覆盖度
    - traceability_score：source / source_chunk_id 是否存在
    - consistency_score：entry_id 是否唯一、命名规范
    """
    rule_details = {key: 0 for key in RULE_MAX}
    entries = candidate.entries or []

    # 1. structure_score：每条 entry 必填字段
    required_keys = ("entry_id", "description", "interface_name", "signal_name", "source")
    if entries:
        well_structured = 0
        for e in entries:
            if all(k in e and e.get(k) for k in required_keys):
                well_structured += 1
        rule_details["structure_score"] = round(
            RULE_MAX["structure_score"] * well_structured / len(entries), 2
        )

    # 2. completeness_score：entry 数量 + 描述非空
    if entries:
        non_empty_desc = sum(1 for e in entries if e.get("description", "").strip())
        rule_details["completeness_score"] = round(
            RULE_MAX["completeness_score"] * non_empty_desc / len(entries), 2
        )

    # 3. traceability_score：candidate.source_chunk_id 存在 + entries 有 source
    if candidate.source_chunk_id:
        rule_details["traceability_score"] = RULE_MAX["traceability_score"] * 0.5
    if entries and all(e.get("source") for e in entries):
        rule_details["traceability_score"] += RULE_MAX["traceability_score"] * 0.5

    # 4. consistency_score：entry_id 唯一
    if entries:
        ids = [e.get("entry_id", "") for e in entries]
        unique_ratio = len(set(ids)) / len(ids) if ids else 0
        rule_details["consistency_score"] = round(
            RULE_MAX["consistency_score"] * unique_ratio, 2
        )

    total = sum(rule_details.values())
    return ChunkPythonScoreResult(
        candidate_id=candidate.candidate_id,
        chunk_id=candidate.chunk_id,
        score=round(total, 2),
        rule_details=rule_details,
    )


def _avg_agent_score(
    candidate_id: str,
    agent_scores: list[ChunkAgentScoreResult],
) -> float:
    """对同一 candidate_id 的所有 agent 评分取平均；缺失则返回 0。"""
    relevant = [s for s in agent_scores if s.candidate_id == candidate_id]
    if not relevant:
        return 0.0
    return sum(s.score for s in relevant) / len(relevant)


def select_best_for_chunk(
    chunk_id: str,
    candidates: list[ChunkCandidate],
    agent_scores: list[ChunkAgentScoreResult],
) -> BestChunkResult:
    """对单个 chunk 的多份候选，融合 agent score + python rule score，选出最佳。

    公式：final = avg(agent_score) * 0.6 + python_rule_score * 0.4
    """
    scored: list[BestChunkResult] = []
    for cand in candidates:
        agent_avg = _avg_agent_score(cand.candidate_id, agent_scores)
        rule = python_rule_score(cand)
        final = agent_avg * AGENT_WEIGHT + rule.score * PYTHON_RULE_WEIGHT
        scored.append(
            BestChunkResult(
                chunk_id=chunk_id,
                candidate=cand,
                agent_score=ChunkAgentScoreResult(
                    candidate_id=cand.candidate_id,
                    chunk_id=chunk_id,
                    score=round(agent_avg, 2),
                    reasoning="avg over agent scores",
                ),
                python_rule_score=rule,
                final_score=round(final, 2),
                is_best=False,
            )
        )

    if not scored:
        raise ValueError(f"No candidates for chunk_id={chunk_id}")

    best = max(scored, key=lambda x: x.final_score)
    best.is_best = True
    return best


def select_best_candidate(
    candidates: list[ChunkCandidate],
    agent_scores: list[ChunkAgentScoreResult],
) -> BestChunkResult:
    """向后兼容旧 pipeline 入口：默认使用 candidates[0].chunk_id。

    旧 pipeline 一次只处理一个虚拟 chunk；新 pipeline 改为按 chunk 显式调用
    select_best_for_chunk。
    """
    if not candidates:
        raise ValueError("No candidates to score")
    return select_best_for_chunk(candidates[0].chunk_id, candidates, agent_scores)
