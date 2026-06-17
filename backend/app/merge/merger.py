"""
merge/merger.py —— 跨 chunk 合并。

- merge_best_chunks：所有 chunk 最佳候选 → MergedRequirementResult（最终最优）
- merge_model_candidates：某个模型在所有 chunk 的候选 → ModelRequirementResult

合并时对 entry_id 统一重新编号为 REQ-001..，保留 source_chunk_id。
"""

from __future__ import annotations

import copy
from typing import Iterable

from app.models import (
    BestChunkResult,
    ChunkCandidate,
    MergedRequirementResult,
    ModelRequirementResult,
)


def _renumber_entries(
    entries: Iterable[dict],
    prefix: str = "REQ-",
) -> list[dict]:
    """把一组 entry 重新编号为 REQ-001, REQ-002, ...

    保留 source_chunk_id 字段，便于追溯。
    """
    out: list[dict] = []
    for i, e in enumerate(entries, start=1):
        new_e = copy.deepcopy(e)
        new_e["entry_id"] = f"{prefix}{i:03d}"
        out.append(new_e)
    return out


def merge_best_chunks(best_results: list[BestChunkResult]) -> MergedRequirementResult:
    """合并所有 chunk 的最佳候选为最终最优 EoICD 条目化需求。

    - 按 best_results 顺序拼接 entries
    - 重新编号 entry_id 为 REQ-001..
    - 保留 source_chunk_id
    """
    all_entries: list[dict] = []
    summary_parts: list[str] = []
    for best in best_results:
        all_entries.extend(best.candidate.entries or [])
        summary_parts.append(
            f"chunk={best.chunk_id}: final_score={best.final_score}, "
            f"entries={len(best.candidate.entries or [])}"
        )

    renumbered = _renumber_entries(all_entries)
    summary = (
        f"本合并结果包含 {len(best_results)} 个 chunk 的最佳候选，"
        f"合计 {len(renumbered)} 条条目化需求。各 chunk 评分："
        + "; ".join(summary_parts)
    )

    return MergedRequirementResult(
        entries=renumbered,
        summary=summary,
        chunk_count=len(best_results),
        best_per_chunk=best_results,
    )


def merge_model_candidates(
    candidates: list[ChunkCandidate],
    model_name: str,
) -> ModelRequirementResult:
    """合并某个模型在所有 chunk 的候选为 ModelRequirementResult。

    例如把所有 MiniMax chunk 候选合并为一份"全量 MiniMax 结果"。
    """
    all_entries: list[dict] = []
    for c in candidates:
        all_entries.extend(c.entries or [])

    renumbered = _renumber_entries(all_entries)

    return ModelRequirementResult(
        model_name=model_name,
        entries=renumbered,
        summary=(
            f"{model_name} 模型在 {len(candidates)} 个 chunk 上的全量候选，"
            f"合计 {len(renumbered)} 条条目化需求。"
        ),
    )
