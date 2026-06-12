"""
端到端流程编排（pipeline.py）。

串联解析、生成、评分、差异比对和输出文档生成流程。
"""

from pathlib import Path

from app.models import JobStatus, PipelineResult
from app.parsers import parse_inputs
from app.crew import generate_candidates, review_candidates, analyze_differences
from app.scoring import select_best_candidate
from app.docx import generate_requirement_docx, generate_difference_report_docx


def run_pipeline(
    job,
    eoicd_word_path: Path,
    eoicd_excel_paths: list[Path],
    sw_req_path: Path,
    job_dir: Path,
):
    """
    端到端流程：

    1. 解析输入文件 → UnifiedInputPackage
    2. 生成两份候选结果
    3. 对候选进行 crew 打分
    4. 融合评分决策最佳候选
    5. 差异比对
    6. 生成两个 Word 文档
    7. 更新 job result
    """
    job.update(JobStatus.RUNNING, '任务正在处理')

    try:
        # 1. 解析输入文件，构建统一分析输入包
        unified_package = parse_inputs(
            eoicd_word_path,
            eoicd_excel_paths,
            sw_req_path,
            job.job_id,
        )

        # 2. 生成两份候选结果
        candidates = generate_candidates(unified_package)

        # 3. 对候选进行 crew 打分
        agent_scores = review_candidates(candidates, unified_package)

        # 4. 融合评分，决策最佳候选
        scored_best = select_best_candidate(candidates, agent_scores)
        best_candidate = scored_best.candidate

        # 5. 差异比对
        differences = analyze_differences(best_candidate, unified_package)

        # 6. 生成输出文档
        req_docx_path = generate_requirement_docx(best_candidate, job_dir)
        diff_docx_path = generate_difference_report_docx(list(differences), job_dir)

        # 7. 更新 job result
        pipeline_result = PipelineResult(
            candidate_count=len(candidates),
            best_candidate_id=best_candidate.candidate_id,
            best_candidate_summary=best_candidate.summary,
            difference_count=len(differences),
            requirements_docx_path=str(req_docx_path),
            difference_report_docx_path=str(diff_docx_path),
        )

        job.result = {
            'requirement_count': len(best_candidate.entries),
            'difference_count': len(differences),
            'requirements_docx': req_docx_path.exists(),
            'difference_report_docx': diff_docx_path.exists(),
            'pipeline_result': pipeline_result.model_dump(),
        }
        job.update(JobStatus.COMPLETED, '任务处理完成')

    except Exception as e:
        job.update(JobStatus.FAILED, f'任务处理失败: {e}')
        raise