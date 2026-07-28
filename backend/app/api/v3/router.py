# -*- coding: utf-8 -*-
"""V3 FastAPI Router（机械拆分自历史 backend/app/main.py）。

ADR-001 Issue A：
- 路由 URL、请求字段、响应结构、文件保存路径、下载逻辑、后台线程逻辑均保持原状。
- 本文件用 `APIRouter()` 而非 `FastAPI()` 实例；`/api/` 前缀由顶层 `app.main` 装载时传入。
- TASK_DIR 路径 `backend/output/v3/` 与原 main.py 中`backend/app/output/` 同源（迁移到 backend/output/v3 以隔离 V3 / V4 输出）。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.job_manager import job_manager
from app.models import (
    AnalyzeResponse,
    JobResultResponse,
    JobResultSummary,
    JobOutputs,
    JobStatus,
    JobStatusResponse,
)
from app.pipeline import run_pipeline

# —— 与历史 main.py 中 TASK_DIR 物理路径一致：backend/output/v3 ——
TASK_DIR = Path(__file__).resolve().parent.parent.parent.parent / 'output' / 'v3'
TASK_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()


@router.get('/health')
def health():
    return {'status': 'ok'}


@router.post('/eoicd/analyze', response_model=AnalyzeResponse)
async def analyze(
    eoicd_word_file: Optional[UploadFile] = File(None),
    eoicd_excel_files: list[UploadFile] = File(default=[]),
    software_requirement_file: UploadFile = File(...),
):
    job = job_manager.create_job()
    job_dir = TASK_DIR / job.job_id
    job_dir.mkdir(exist_ok=True)

    # 保存上传文件（逻辑与历史 main.py 完全一致）
    eoicd_word_path = None
    if eoicd_word_file is not None:
        eoicd_word_path = job_dir / eoicd_word_file.filename
        with eoicd_word_path.open('wb') as f:
            f.write(await eoicd_word_file.read())
    sw_req_path = job_dir / software_requirement_file.filename
    excel_paths = []
    with sw_req_path.open('wb') as f:
        f.write(await software_requirement_file.read())
    for ef in eoicd_excel_files:
        p = job_dir / ef.filename
        excel_paths.append(p)
        with p.open('wb') as f:
            f.write(await ef.read())

    # 后台运行 pipeline（与历史一致）
    def background():
        run_pipeline(job, eoicd_word_path, excel_paths, sw_req_path, job_dir)

    threading.Thread(target=background, daemon=True).start()

    return AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='分析任务已创建',
    )


@router.get('/jobs/{job_id}', response_model=JobStatusResponse)
def get_job_status(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='任务不存在')
    # ADR-001 D8 跨版本分发：V3 路由收到 V4 Job 时返回 404 + 提示
    if job.kind != 'v3':
        raise HTTPException(
            status_code=404,
            detail=f'job belongs to v{job.kind[-1]}; use /api/v{job.kind[-1]}/jobs/{job_id} instead',
        )
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        message=job.message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get('/jobs/{job_id}/result', response_model=JobResultResponse)
def get_job_result(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='任务不存在')
    # ADR-001 D8 跨版本分发：V3 路由收到 V4 Job 时返回 404 + 提示
    if job.kind != 'v3':
        raise HTTPException(
            status_code=404,
            detail=f'job belongs to v{job.kind[-1]}; use /api/v{job.kind[-1]}/jobs/{job_id}/result instead',
        )
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail='任务尚未完成')
    result = job.result or {}
    return JobResultResponse(
        job_id=job.job_id,
        status=job.status.value,
        summary=JobResultSummary(
            requirement_count=result.get('requirement_count', 0),
            difference_count=result.get('difference_count', 0),
        ),
        outputs=JobOutputs(
            requirements_docx=result.get('requirements_docx', False),
            difference_report_docx=result.get('difference_report_docx', False),
            minimax_docx=result.get('minimax_docx', False),
            deepseek_docx=result.get('deepseek_docx', False),
        ),
    )


def _output_path(job_id: str, filename: str) -> Path:
    p = TASK_DIR / job_id / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail='文件不存在')
    return p


@router.get('/jobs/{job_id}/outputs/requirements')
def download_requirements(job_id: str):
    """下载"最优条目化需求"（物理文件 EoICD条目化需求.docx，语义重映射为最优）。"""
    return FileResponse(
        _output_path(job_id, 'EoICD条目化需求.docx'),
        filename='EoICD条目化需求.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )


@router.get('/jobs/{job_id}/outputs/minimax-requirements')
def download_minimax_requirements(job_id: str):
    """下载 MiniMax 条目化需求。"""
    return FileResponse(
        _output_path(job_id, 'MiniMax条目化需求.docx'),
        filename='MiniMax条目化需求.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )


@router.get('/jobs/{job_id}/outputs/deepseek-requirements')
def download_deepseek_requirements(job_id: str):
    """下载 DeepSeek 条目化需求。"""
    return FileResponse(
        _output_path(job_id, 'DeepSeek条目化需求.docx'),
        filename='DeepSeek条目化需求.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )


@router.get('/jobs/{job_id}/outputs/difference-report')
def download_difference_report(job_id: str):
    return FileResponse(
        _output_path(job_id, 'EoICD与软件高层需求差异报告.docx'),
        filename='EoICD与软件高层需求差异报告.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )
