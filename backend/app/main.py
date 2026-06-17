import os
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.models import (
    AnalyzeResponse,
    JobStatus,
    JobStatusResponse,
    JobResultResponse,
    JobResultSummary,
    JobOutputs,
)
from app.job_manager import job_manager
from app.pipeline import run_pipeline

TASK_DIR = Path(__file__).parent / 'output'
TASK_DIR.mkdir(exist_ok=True)

app = FastAPI(title='ICD工具原型')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:3000'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.post('/api/eoicd/analyze', response_model=AnalyzeResponse)
async def analyze(
    eoicd_word_file: UploadFile = File(...),
    eoicd_excel_files: list[UploadFile] = File(default=[]),
    software_requirement_file: UploadFile = File(...),
):
    job = job_manager.create_job()
    job_dir = TASK_DIR / job.job_id
    job_dir.mkdir(exist_ok=True)

    # 保存上传文件
    eoicd_word_path = job_dir / eoicd_word_file.filename
    sw_req_path = job_dir / software_requirement_file.filename
    excel_paths = []
    with eoicd_word_path.open('wb') as f:
        f.write(await eoicd_word_file.read())
    with sw_req_path.open('wb') as f:
        f.write(await software_requirement_file.read())
    for ef in eoicd_excel_files:
        p = job_dir / ef.filename
        excel_paths.append(p)
        with p.open('wb') as f:
            f.write(await ef.read())

    # 后台运行 pipeline
    def background():
        run_pipeline(job, eoicd_word_path, excel_paths, sw_req_path, job_dir)

    threading.Thread(target=background, daemon=True).start()

    return AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='分析任务已创建',
    )


@app.get('/api/jobs/{job_id}', response_model=JobStatusResponse)
def get_job_status(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='任务不存在')
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        message=job.message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@app.get('/api/jobs/{job_id}/result', response_model=JobResultResponse)
def get_job_result(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='任务不存在')
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


@app.get('/api/jobs/{job_id}/outputs/requirements')
def download_requirements(job_id: str):
    """下载"最优条目化需求"（物理文件 EoICD条目化需求.docx，语义重映射为最优）。"""
    return FileResponse(
        _output_path(job_id, 'EoICD条目化需求.docx'),
        filename='EoICD条目化需求.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )


@app.get('/api/jobs/{job_id}/outputs/minimax-requirements')
def download_minimax_requirements(job_id: str):
    """下载 MiniMax 条目化需求。"""
    return FileResponse(
        _output_path(job_id, 'MiniMax条目化需求.docx'),
        filename='MiniMax条目化需求.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )


@app.get('/api/jobs/{job_id}/outputs/deepseek-requirements')
def download_deepseek_requirements(job_id: str):
    """下载 DeepSeek 条目化需求。"""
    return FileResponse(
        _output_path(job_id, 'DeepSeek条目化需求.docx'),
        filename='DeepSeek条目化需求.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )


@app.get('/api/jobs/{job_id}/outputs/difference-report')
def download_difference_report(job_id: str):
    return FileResponse(
        _output_path(job_id, 'EoICD与软件高层需求差异报告.docx'),
        filename='EoICD与软件高层需求差异报告.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )