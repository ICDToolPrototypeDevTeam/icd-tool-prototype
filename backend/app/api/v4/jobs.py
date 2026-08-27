# -*- coding: utf-8 -*-
"""GET /api/v4/jobs/{job_id}  状态 / 结果查询。"""
from __future__ import annotations

from typing import Union

from fastapi import APIRouter, HTTPException

from app.api.v4.runner import (
    _parse_progress,
    derive_consensus_summary,
    derive_eoicd_hlr_counts,
    derive_forward_outputs,
    derive_forward_summary,
    derive_mock_models,
    derive_outputs,
    V4_INTERMEDIATE_JSON,
)
from app.api.v4.schemas import (
    V4ForwardJobOutputs,
    V4ForwardJobResultResponse,
    V4ForwardJobResultSummary,
    V4JobOutputs,
    V4JobResultResponse,
    V4JobResultSummary,
    V4JobStatusResponse,
)
from app.job_manager import JobStatus, job_manager

import json
from pathlib import Path


router = APIRouter()


def _get_job(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='job not found')
    return job


@router.get('/jobs/{job_id}', response_model=V4JobStatusResponse)
def get_v4_job_status(job_id: str):
    job = _get_job(job_id)
    progress = _parse_progress(job.message)
    mock_models: list[str] = []
    if job.result and "mock_models" in job.result:
        mock_models = list(job.result["mock_models"])  # type: ignore[arg-type]
    return V4JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        task_type=job.task_type,
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


def _base_outputs_dir(job_id: str) -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / 'output' / 'v4' / job_id / 'output'


def _reverse_result(job, base_outputs_dir: Path) -> V4JobResultResponse:
    """组装正确性（反向）结果响应。"""
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
        task_type=job.task_type,
        summary=summary,
        outputs=job_outputs,
        mock_models=res.get('mock_models', mock_models) or [],
        errors=res.get('errors', []) or [],
    )


def _forward_result(job, base_outputs_dir: Path) -> V4ForwardJobResultResponse:
    """组装完整性（正向）结果响应。"""
    outputs = derive_forward_outputs(base_outputs_dir)
    summary = derive_forward_summary(base_outputs_dir)
    counts = derive_eoicd_hlr_counts(base_outputs_dir)
    res = job.result or {}

    result_summary = V4ForwardJobResultSummary(
        analysis_mode=res.get('analysis_mode', summary['analysis_mode']) or '',
        total_blocks=int(res.get('total_blocks', summary['total_blocks']) or 0),
        covered_direct=int(res.get('covered_direct', summary['covered_direct']) or 0),
        covered_aggregate=int(res.get('covered_aggregate', summary['covered_aggregate']) or 0),
        parent_referenced=int(res.get('parent_referenced', summary['parent_referenced']) or 0),
        possible=int(res.get('possible', summary['possible']) or 0),
        uncovered=int(res.get('uncovered', summary['uncovered']) or 0),
        unsupported=int(res.get('unsupported', summary['unsupported']) or 0),
        input_error=int(res.get('input_error', summary['input_error']) or 0),
        ai_reviewed=int(res.get('ai_reviewed', summary['ai_reviewed']) or 0),
        eoicd_count=int(res.get('eoicd_count', counts['eoicd_count']) or 0),
        hlr_count=int(res.get('hlr_count', counts['hlr_count']) or 0),
    )

    job_outputs = V4ForwardJobOutputs(
        forward_xlsx=outputs['forward_xlsx'],
        forward_docx=outputs['forward_docx'],
    )

    return V4ForwardJobResultResponse(
        job_id=job.job_id,
        status=job.status,
        task_type=job.task_type,
        summary=result_summary,
        outputs=job_outputs,
        errors=res.get('errors', []) or [],
    )


@router.get('/jobs/{job_id}/result', response_model=Union[V4JobResultResponse, V4ForwardJobResultResponse])
def get_v4_job_result(job_id: str):
    """共用结果接口：按 job.task_type 分发到正确性 / 完整性两种响应 schema。"""
    job = _get_job(job_id)
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail=f'job not finished: status={job.status.value}')

    base_outputs_dir = _base_outputs_dir(job_id)
    if job.task_type == "completeness":
        return _forward_result(job, base_outputs_dir)
    # 默认走正确性（反向）分支，保证旧反向调用方（task_type=correctness）向后兼容
    return _reverse_result(job, base_outputs_dir)
