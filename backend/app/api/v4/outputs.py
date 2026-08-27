# -*- coding: utf-8 -*-
"""GET /api/v4/jobs/{job_id}/outputs/* 下载。

ADR-001 Issue A：
- 仅 3 类对外：eoicd-xlsx / consistency/{model} / consensus-docx；
- {model} 白名单 ∈ {deepseek,minimax,qwen}；非法 → 400。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.v4.runner import FORWARD_OUTPUT_FILES, V4_OUTPUT_FILES
from app.job_manager import job_manager


router = APIRouter()

ALLOWED_MODELS = {"deepseek", "minimax", "qwen"}

MEDIA_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MEDIA_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _output_root(job_id: str, expected_task_type: str) -> Path:
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='job not found')
    if job.task_type != expected_task_type:
        raise HTTPException(
            status_code=404,
            detail=f'job task_type is {job.task_type}, not {expected_task_type}; this output belongs to a different analysis',
        )
    root = Path(__file__).resolve().parent.parent.parent.parent / 'output' / 'v4' / job_id / 'output'
    if not root.exists():
        raise HTTPException(status_code=404, detail='output dir does not exist (job likely failed before pipeline produced files)')
    return root


@router.get('/jobs/{job_id}/outputs/eoicd-xlsx')
def download_eoicd_xlsx(job_id: str):
    root = _output_root(job_id, "correctness")
    f = root / V4_OUTPUT_FILES["eoicd_xlsx"]
    if not f.exists():
        raise HTTPException(status_code=404, detail='eoicd xlsx not generated (job may be running or failed)')
    return FileResponse(
        path=f,
        filename=V4_OUTPUT_FILES["eoicd_xlsx"],
        media_type=MEDIA_XLSX,
    )


@router.get('/jobs/{job_id}/outputs/consensus-docx')
def download_consensus_docx(job_id: str):
    root = _output_root(job_id, "correctness")
    f = root / V4_OUTPUT_FILES["consensus_docx"]
    if not f.exists():
        raise HTTPException(status_code=404, detail='consensus docx not generated (job may be running or failed)')
    return FileResponse(
        path=f,
        filename=V4_OUTPUT_FILES["consensus_docx"],
        media_type=MEDIA_DOCX,
    )


@router.get('/jobs/{job_id}/outputs/consistency/{model}')
def download_consistency_docx(job_id: str, model: str):
    if model not in ALLOWED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid model: {model}; allowed: deepseek|minimax|qwen",
        )
    root = _output_root(job_id, "correctness")
    key = f"consistency_{model}_docx"
    if key not in V4_OUTPUT_FILES:
        raise HTTPException(status_code=400, detail=f"unknown output kind: {key}")
    f = root / V4_OUTPUT_FILES[key]
    if not f.exists():
        raise HTTPException(
            status_code=404,
            detail=f'consistency {model} docx not generated (job may be running or failed)',
        )
    return FileResponse(
        path=f,
        filename=V4_OUTPUT_FILES[key],
        media_type=MEDIA_DOCX,
    )


@router.get('/jobs/{job_id}/outputs/forward-xlsx')
def download_forward_xlsx(job_id: str):
    root = _output_root(job_id, "completeness")
    f = root / FORWARD_OUTPUT_FILES["forward_xlsx"]
    if not f.exists():
        raise HTTPException(status_code=404, detail='forward xlsx not generated (job may be running or failed)')
    return FileResponse(
        path=f,
        filename=FORWARD_OUTPUT_FILES["forward_xlsx"],
        media_type=MEDIA_XLSX,
    )


@router.get('/jobs/{job_id}/outputs/forward-docx')
def download_forward_docx(job_id: str):
    root = _output_root(job_id, "completeness")
    f = root / FORWARD_OUTPUT_FILES["forward_docx"]
    if not f.exists():
        raise HTTPException(status_code=404, detail='forward docx not generated (job may be running or failed)')
    return FileResponse(
        path=f,
        filename=FORWARD_OUTPUT_FILES["forward_docx"],
        media_type=MEDIA_DOCX,
    )
