# -*- coding: utf-8 -*-
"""GET /api/v4/jobs/{job_id}  状态 / 结果查询。

ADR-001 Issue A：
- V3 / V4 通过 `job.kind` 严格分发；跨版本查询返回 404 + 提示；
- V4JobStatusResponse / V4JobResultResponse 不 import 任何 V3 schema 类。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v4.runner import _parse_progress, derive_consensus_summary, derive_mock_models, derive_outputs, V4_INTERMEDIATE_JSON
from app.api.v4.schemas import (
    V4JobOutputs,
    V4JobResultResponse,
    V4JobResultSummary,
    V4JobStatusResponse,
)
from app.job_manager import job_manager
from app.models import JobStatus

import json
from pathlib import Path


router = APIRouter()


def _ensure_v4_job(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='job not found')
    if job.kind != "v4":
        raise HTTPException(
            status_code=404,
            detail=f'job belongs to v{("3" if job.kind == "v3" else "?")}; use /api/jobs/{job_id} instead',
        )
    return job


@router.get('/jobs/{job_id}', response_model=V4JobStatusResponse)
def get_v4_job_status(job_id: str):
    job = _ensure_v4_job(job_id)
    progress = _parse_progress(job.message)
    mock_models: list[str] = []
    if job.result and "mock_models" in job.result:
        mock_models = list(job.result["mock_models"])  # type: ignore[arg-type]
    return V4JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        stage=progress["stage"],
        stage_index=progress["stage_index"],
        stage_total=progress["stage_total"],
        case_index=progress["case_index"],
        case_total=progress["case_total"],
        message=job.message,
        mock_models=mock_models,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


@router.get('/jobs/{job_id}/result', response_model=V4JobResultResponse)
def get_v4_job_result(job_id: str):
    job = _ensure_v4_job(job_id)
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail=f'job not finished: status={job.status.value}')

    # 反读落盘 JSON 以补全 summary（V4 router 端用；pipeline runner 已写过 result dict，本接口做兜底）
    base_outputs_dir = Path(__file__).resolve().parent.parent.parent.parent / 'output' / 'v4' / job_id / 'output'
    consensus = derive_consensus_summary(base_outputs_dir)
    mock_models = derive_mock_models(base_outputs_dir)

    outputs = derive_outputs(base_outputs_dir)
    res = job.result or {}

    summary = V4JobResultSummary(
        eoicd_count=int(res.get('eoicd_count', 0)),
        eoicd_blocks_total=int(res.get('eoicd_blocks_total', 0)),
        eoicd_blocks_matched=int(res.get('eoicd_blocks_matched', 0)),
        hlr_count=int(res.get('hlr_count', 0)),
        matched_count=int(res.get('matched_count', 0)),
        pending_count=int(res.get('pending_count', 0)),
        unmatched_count=int(res.get('unmatched_count', 0)),
        judged_count=int(res.get('judged_count', 0)),
        agreement_distribution=res.get('agreement_distribution', consensus["agreement_distribution"]) or {},
        star_distribution=res.get('star_distribution', consensus["star_distribution"]) or {},
        status_distribution=res.get('status_distribution', consensus["status_distribution"]) or {},
        average_star_rating=float(res.get('average_star_rating', consensus["average_star_rating"]) or 0.0),
    )

    job_outputs = V4JobOutputs(
        eoicd_xlsx=outputs["eoicd_xlsx"],
        consistency_deepseek_docx=outputs["consistency_deepseek_docx"],
        consistency_minimax_docx=outputs["consistency_minimax_docx"],
        consistency_qwen_docx=outputs["consistency_qwen_docx"],
        consensus_docx=outputs["consensus_docx"],
    )

    return V4JobResultResponse(
        job_id=job.job_id,
        status=job.status,
        summary=summary,
        outputs=job_outputs,
        mock_models=res.get('mock_models', mock_models) or [],
        errors=res.get('errors', []) or [],
    )
