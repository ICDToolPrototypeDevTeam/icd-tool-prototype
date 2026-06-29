"""
端到端流程编排（pipeline.py，chunk-level CrewAI 版本）。

串联：
1. 解析输入文件 → UnifiedInputPackage（含 eoicd_chunks）
2. for chunk in eoicd_chunks:
   - generate_for_chunk → (minimax_cand, deepseek_cand)
   - review_for_chunk   → agent_scores
   - select_best_for_chunk → BestChunkResult
3. merge_best_chunks / merge_model_candidates → 最终条目化需求
4. analyze_differences(merged, sw_req)
5. generate_minimax_docx / generate_deepseek_docx / generate_best_docx / generate_difference_report_docx
6. 更新 job.result
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.crew import (
    analyze_differences,
    generate_for_chunk,
    review_for_chunk,
)
from app.docx import (
    generate_best_docx,
    generate_deepseek_docx,
    generate_difference_report_docx,
    generate_minimax_docx,
)
from app.merge import merge_best_chunks, merge_model_candidates
from app.models import ChunkCandidate, JobStatus, PipelineResult
from app.parsers import parse_inputs
from app.scoring import select_best_for_chunk


def run_pipeline(
    job,
    eoicd_word_path: Optional[Path],
    eoicd_excel_paths: list[Path],
    sw_req_path: Path,
    job_dir: Path,
) -> None:
    """端到端流程编排。

    1. 解析输入 → UnifiedInputPackage
    2. for chunk: generate / review / select
    3. merge → 最终条目化需求
    4. compare → 差异项
    5. docx → 4 份 Word
    6. 更新 job.result
    """
    job.update(JobStatus.RUNNING, "建立处理任务，当前正在解析输入")

    try:
        # 1. 解析输入
        unified_package = parse_inputs(
            eoicd_word_path,
            eoicd_excel_paths,
            sw_req_path,
            job.job_id,
        )
        chunks = unified_package.eoicd_chunks
        if not chunks:
            raise RuntimeError("UnifiedInputPackage.eoicd_chunks 为空")

        # 2. chunk-level 循环：生成 / 评分 / 择优
        job.update(JobStatus.RUNNING, "AI正在进行结果生成、评分、择优")
        best_results = []
        minimax_candidates: list[ChunkCandidate] = []
        deepseek_candidates: list[ChunkCandidate] = []

        for chunk in chunks:
            minimax_cand, deepseek_cand = generate_for_chunk(chunk)
            agent_scores = review_for_chunk(chunk, minimax_cand, deepseek_cand)
            best = select_best_for_chunk(chunk.chunk_id, [minimax_cand, deepseek_cand], agent_scores)
            best_results.append(best)
            minimax_candidates.append(minimax_cand)
            deepseek_candidates.append(deepseek_cand)

        # 3. 跨 chunk 合并
        merged_best = merge_best_chunks(best_results)
        minimax_merged = merge_model_candidates(minimax_candidates, model_name="MiniMax")
        deepseek_merged = merge_model_candidates(deepseek_candidates, model_name="DeepSeek")

        # 4. 差异比对（DeepSeek comparison）
        job.update(JobStatus.RUNNING, "AI正在检查需求一致性")
        differences = analyze_differences(merged_best, unified_package.software_requirements)

        # 5. 生成 4 份 docx
        minimax_path = generate_minimax_docx(minimax_merged, job_dir)
        deepseek_path = generate_deepseek_docx(deepseek_merged, job_dir)
        best_path = generate_best_docx(merged_best, job_dir)
        diff_path = generate_difference_report_docx(differences, job_dir)

        # 6. 更新 job.result
        best_candidate = best_results[0] if best_results else None
        pipeline_result = PipelineResult(
            candidate_count=len(best_results) * 2,
            best_candidate_id=best_candidate.candidate.candidate_id if best_candidate else "",
            best_candidate_summary=merged_best.summary,
            difference_count=len(differences),
            chunk_count=len(chunks),
            minimax_docx_path=str(minimax_path),
            deepseek_docx_path=str(deepseek_path),
            best_docx_path=str(best_path),
            requirements_docx_path=str(job_dir / "EoICD条目化需求.docx"),
            difference_report_docx_path=str(diff_path),
        )

        job.result = {
            "requirement_count": len(merged_best.entries),
            "difference_count": len(differences),
            "requirements_docx": (job_dir / "EoICD条目化需求.docx").exists(),
            "difference_report_docx": diff_path.exists(),
            "minimax_docx": minimax_path.exists(),
            "deepseek_docx": deepseek_path.exists(),
            "pipeline_result": pipeline_result.model_dump(),
        }
        job.update(JobStatus.COMPLETED, "任务处理完成")

    except Exception as e:
        job.update(JobStatus.FAILED, f"任务处理失败: {e}")
        raise
