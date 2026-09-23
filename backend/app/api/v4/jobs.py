# -*- coding: utf-8 -*-
"""任务状态 / 结果查询 + 中断任务列表与继续/放弃。

- GET  /api/v4/jobs/{job_id}          状态查询
- GET  /api/v4/jobs/{job_id}/result   结果查询
- GET  /api/v4/jobs                   任务列表（可选 status / task_type 过滤）
- POST /api/v4/jobs/{job_id}/resume   继续被中断/被终止的任务（按参数快照重新执行）
- POST /api/v4/jobs/{job_id}/abandon  放弃被中断/被终止的任务（不删除文件）
- POST /api/v4/jobs/{job_id}/cancel  终止运行中的任务（不删除文件）
- GET  /api/v4/jobs/{job_id}/logs    任务日志增量拉取
"""
from __future__ import annotations

from typing import Optional, Union

from fastapi import APIRouter, HTTPException

from app.api.v4.runner import (
    _merged_progress,
    derive_consensus_summary,
    derive_eoicd_hlr_counts,
    derive_forward_outputs,
    derive_forward_summary,
    derive_mock_models,
    derive_outputs,
    job_input_filenames,
    relaunch_from_manifest,
    V4_INTERMEDIATE_JSON,
)
from app.api.v4.schemas import (
    V4AnalyzeResponse,
    V4ForwardJobOutputs,
    V4ForwardJobResultResponse,
    V4ForwardJobResultSummary,
    V4JobListItem,
    V4JobLogsResponse,
    V4JobOutputs,
    V4JobResultResponse,
    V4JobResultSummary,
    V4JobStatusResponse,
)
from app.job_log import LOG_FILE_NAME, job_log_store
from app.job_manager import JobStatus, job_manager
from app.v4.config import get_output_root

import json
from pathlib import Path


router = APIRouter()


def _get_job(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='job not found')
    return job


def _job_log_path(job) -> Path:
    job_dir = job.job_dir if job.job_dir is not None else (get_output_root() / 'v4' / job.job_id)
    return Path(job_dir) / LOG_FILE_NAME


@router.get('/jobs', response_model=list[V4JobListItem])
def list_v4_jobs(status: Optional[str] = None, task_type: Optional[str] = None):
    """任务列表（内存中的任务：本进程新建 + 启动扫描恢复的中断任务）。

    按创建时间倒序。典型用法：`?status=interrupted&task_type=correctness`
    取出待用户选择「继续 / 放弃」的任务。
    """
    jobs = job_manager.list_jobs()
    if status:
        jobs = [j for j in jobs if j.status.value == status]
    if task_type:
        jobs = [j for j in jobs if j.task_type == task_type]
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [
        V4JobListItem(
            job_id=j.job_id,
            task_type=j.task_type,
            status=j.status,
            message=j.message,
            created_at=j.created_at.isoformat(),
            updated_at=j.updated_at.isoformat(),
            input_files=job_input_filenames(j),
        )
        for j in jobs
    ]


@router.get('/jobs/{job_id}', response_model=V4JobStatusResponse)
def get_v4_job_status(job_id: str):
    job = _get_job(job_id)
    # 优先用 pipeline 上报的结构化进度；老 manifest 没有该字段时回落到
    # 从 message 正则解析（保证向后兼容，行为与改动前一致）。
    progress = _merged_progress(job)

    mock_models: list[str] = []
    if job.result and "mock_models" in job.result:
        mock_models = list(job.result["mock_models"])  # type: ignore[arg-type]
    return V4JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        task_type=job.task_type,
        stage=progress.get("stage") or "",
        stage_index=progress.get("stage_index"),
        stage_total=progress.get("stage_total"),
        case_index=progress.get("case_index"),
        case_total=progress.get("case_total"),
        message=job.message,
        resumed=job.resumed,
        reuse=job.reuse,
        mock_models=mock_models,
        mock=bool(job.mock),
        cancel_requested=bool(job.cancel_requested),
        error=job.error or None,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


def _base_outputs_dir(job_id: str) -> Path:
    return get_output_root() / 'v4' / job_id / 'output'


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


@router.post('/jobs/{job_id}/resume', response_model=V4AnalyzeResponse)
def resume_v4_job(job_id: str):
    """继续一个被中断或被用户终止的任务。

    按 manifest 中落盘的参数快照重新执行整条管线（已上传的输入文件直接复用，
    Step 2 HLR 标签走缓存）。任务状态不是 ``interrupted`` / ``canceled`` → 409；
    输入文件缺失 → 409，且任务状态保持不变。续跑前由 ``relaunch_from_manifest``
    复位取消标志，否则第一个检查点会立刻再次取消。
    """
    job = _get_job(job_id)
    if job.status not in (JobStatus.INTERRUPTED, JobStatus.CANCELED):
        raise HTTPException(
            status_code=409,
            detail=f'job not resumable: status={job.status.value}',
        )

    job_dir = get_output_root() / 'v4' / job_id
    try:
        relaunch_from_manifest(job, job_dir)
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=409,
            detail=f'cannot resume, input or trace file missing: {e}',
        )

    return V4AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='任务已继续执行（将重新运行分析流程）',
    )


@router.post('/jobs/{job_id}/abandon', response_model=V4AnalyzeResponse)
def abandon_v4_job(job_id: str):
    """放弃一个被中断或被用户终止的任务；只改状态，不删除输入与中间产物。

    ``abandoned`` 是这套状态机里的**终态**：任务被中断/终止后，用户要么续跑，
    要么放弃。因此 ``canceled`` 同样可以放弃 —— 否则已终止的任务没有收尾方式。

    另有一类 ``pending`` 也可放弃：**从未启动过**的任务（``job_dir is None``，
    即管线线程还没跑过 ``set_dir``）。它没有线程，「终止」对它无效（只是置一个
    没人检查的标志），若不在这里放行，这条记录会一直挂在任务列表里且没有任何
    操作能去掉它。``job_dir`` 非空的 ``pending`` 仍在启动窗口内，那属于运行中
    的任务，只能先终止（``cancel``）—— 于是这里的门槛实际是「没有任何线程在跑」。
    """
    job = _get_job(job_id)
    never_launched = job.status == JobStatus.PENDING and job.job_dir is None
    if job.status not in (JobStatus.INTERRUPTED, JobStatus.CANCELED) and not never_launched:
        raise HTTPException(
            status_code=409,
            detail=f'job not abandonable: status={job.status.value}',
        )

    job.update(JobStatus.ABANDONED, '用户已放弃继续')
    return V4AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='任务已放弃（文件保留在输出目录，未删除）',
    )


@router.post('/jobs/{job_id}/cancel', response_model=V4AnalyzeResponse)
def cancel_v4_job(job_id: str):
    """终止一个运行中的任务（协作式）。

    只置取消标志，管线在下一个检查点（步骤开头 / 每个 case）抛出
    ``JobCancelled`` 并以 ``canceled`` 结束。**不删除**已产出的文件，也不写
    ``job.result``（避免半成品被当成结果展示）。

    返回体里的 ``status`` 是**当前真实状态**（``running`` / ``pending``），不是
    虚构的 ``canceling``：前端靠 ``cancel_requested`` 展示「正在终止…」，
    避免再出现「同一个词表示两件事」的歧义。
    """
    job = _get_job(job_id)
    if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        raise HTTPException(
            status_code=409,
            detail=f'job not cancelable: status={job.status.value}',
        )

    job.request_cancel()
    return V4AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='已请求终止，任务将在当前步骤/Case 边界停止（已产出文件保留）',
    )


@router.get('/jobs/{job_id}/logs', response_model=V4JobLogsResponse)
def get_v4_job_logs(job_id: str, offset: int = 0, limit: int = 500):
    """任务日志增量拉取。

    ``offset`` 传上一次返回的 ``next_offset``（首次传 0）。内存 buffer 为空
    （进程重启后）时，``register`` 会从输出目录的 job.log 尾部恢复，因此
    服务器重启后仍能看到重启前的日志。
    """
    job = _get_job(job_id)
    safe_limit = max(1, min(limit, 2000))
    safe_offset = max(0, offset)
    buf = job_log_store.register(job_id, _job_log_path(job))
    data = buf.read(offset=safe_offset, limit=safe_limit)
    return V4JobLogsResponse(job_id=job_id, **data)
