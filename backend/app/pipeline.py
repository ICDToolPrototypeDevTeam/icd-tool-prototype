"""
端到端流程骨架。

各阶段业务实现使用 mock 或占位逻辑，具体能力在后续 Issue 中逐步填充。
"""

import time
from pathlib import Path

from app.models import JobStatus


def run_pipeline(
    job,
    eoicd_word_path: Path,
    eoicd_excel_paths: list[Path],
    sw_req_path: Path,
    job_dir: Path,
):
    """
    端到端流程骨架：

    1. 更新状态为 running
    2. 解析输入文件（mock）
    3. 生成候选结果（mock）
    4. 评分与择优（mock）
    5. 差异比对（mock）
    6. 生成输出文档（python-docx 占位文件）
    7. 更新状态为 completed
    """

    # 1. 开始处理
    job.update(JobStatus.RUNNING, '任务正在处理')
    time.sleep(1)

    try:
        # 2. 解析输入文件（mock 占位）
        from app.parsers.placeholder import parse_inputs
        parse_inputs(eoicd_word_path, eoicd_excel_paths, sw_req_path)
        time.sleep(1)

        # 3. 生成候选结果（mock 占位）
        from app.crew.placeholder import generate_candidates
        generate_candidates()
        time.sleep(1)

        # 4. 评分与择优（mock 占位）
        from app.scoring.placeholder import score_and_select
        score_and_select()
        time.sleep(1)

        # 5. 差异比对（mock 占位）
        from app.crew.placeholder import run_difference_analysis
        run_difference_analysis()
        time.sleep(1)

        # 6. 生成输出文档（python-docx 占位文件）
        from app.docx.placeholder import generate_output_docx
        req_docx_path = generate_output_docx(
            job_dir,
            'EoICD条目化需求.docx',
            'EoICD条目化需求（占位文档）',
        )
        diff_docx_path = generate_output_docx(
            job_dir,
            'EoICD与软件高层需求差异报告.docx',
            'EoICD与软件高层需求差异报告（占位文档）',
        )

        # 7. 更新任务结果
        job.result = {
            'requirement_count': 3,
            'difference_count': 2,
            'requirements_docx': req_docx_path.exists(),
            'difference_report_docx': diff_docx_path.exists(),
        }
        job.update(JobStatus.COMPLETED, '任务处理完成')

    except Exception as e:
        job.update(JobStatus.FAILED, f'任务处理失败: {e}')