# -*- coding: utf-8 -*-
"""GET /api/v4/jobs/{job_id}/outputs/* 下载。

ADR-001 Issue A：
- 仅 3 类对外：eoicd-xlsx / consistency/{model} / consensus-docx；
- {model} 白名单 ∈ {deepseek,minimax,qwen}；非法 → 400。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.v4.runner import FORWARD_OUTPUT_FILES, V4_OUTPUT_FILES
from app.job_manager import MANIFEST_NAME, job_manager
from app.v4.config import get_output_root


router = APIRouter()

ALLOWED_MODELS = {"deepseek", "minimax", "qwen"}

MEDIA_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MEDIA_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _manifest_task_type(job_dir: Path) -> Optional[str]:
    """读磁盘 manifest 里的 task_type；文件缺失 / JSON 损坏时返回 None。"""
    try:
        data = json.loads((job_dir / MANIFEST_NAME).read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001 — 目录被删、manifest 损坏一律按「不知道」处理
        return None
    task_type = data.get('task_type') if isinstance(data, dict) else None
    return task_type if isinstance(task_type, str) and task_type else None


def _output_root(job_id: str, expected_task_type: str) -> Path:
    """定位任务的 output 目录；任务类型不符时 404。

    任务类型的来源按优先级取：**内存**（本进程新建的任务）→ **磁盘 manifest**。
    进程重启后跑完的任务不在内存里（启动扫描只载入未完成的任务），修复前这里
    直接 404，于是「历史结果」点进去全是死链。两者都取不到（上传失败留下的残留
    目录没有 manifest）就不再校验类型：五类产物的文件名互不重名，下面每个下载
    接口按类型取的是各自的文件名，不存在把 A 类任务的产物当 B 类发出去的可能。
    """
    base = get_output_root() / 'v4' / job_id
    job = job_manager.get_job(job_id)
    actual_task_type = job.task_type if job is not None else _manifest_task_type(base)
    if actual_task_type is not None and actual_task_type != expected_task_type:
        raise HTTPException(
            status_code=404,
            detail=f'job task_type is {actual_task_type}, not {expected_task_type}; this output belongs to a different analysis',
        )
    root = base / 'output'
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
