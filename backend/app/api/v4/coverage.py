# -*- coding: utf-8 -*-
"""POST /api/v4/coverage-analysis 上传与任务创建。

ADR-001 Issue A：
- multipart 字段：hlr_word_file 必填；publisher/subscriber 二选一；traceability_files 可选；
- judge_providers / use_mock_llm 由前端可选；线程内通过 runner 写入 env 并恢复；
- 仅校验文件扩展名 / 白名单 provider；不做深度字段解析（V4 pipeline 内部自检）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.v4.runner import launch_v4_pipeline
from app.api.v4.schemas import V4AnalyzeResponse
from app.job_manager import job_manager


router = APIRouter()

# ADR-001 §2 校验白名单
ALLOWED_JUDGE_PROVIDERS = {"deepseek", "minimax", "qwen"}

# 文件名安全字符：仅保留中英数 + . _ -
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-一-龥]")
MAX_FILE_BYTES = 50 * 1024 * 1024          # 50 MB 单文件
MAX_REQUEST_BYTES = 200 * 1024 * 1024      # 200 MB 总


def _safe_filename(name: str) -> str:
    base = Path(name).name
    base = base.replace(" ", "_")
    cleaned = _SAFE_NAME_RE.sub("_", base)
    if not cleaned:
        raise HTTPException(status_code=422, detail=f"invalid filename: {name}")
    return cleaned


async def _save_upload(file: UploadFile, dest_dir: Path) -> Path:
    if not file.filename:
        raise HTTPException(status_code=422, detail="uploaded file has no filename")
    safe = _safe_filename(file.filename)
    dest = dest_dir / safe
    content = await file.read()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"file too large: {safe} ({len(content)} > {MAX_FILE_BYTES})")
    dest.write_bytes(content)
    return dest


@router.post('/coverage-analysis', response_model=V4AnalyzeResponse)
async def coverage_analysis(
    hlr_word_file: UploadFile = File(...),
    eoicd_publisher_file: Optional[UploadFile] = File(None),
    eoicd_subscriber_file: Optional[UploadFile] = File(None),
    traceability_files: list[UploadFile] = File(default=[]),
    use_mock_llm: bool = Form(False),
    judge_providers: list[str] = Form(default_factory=lambda: ["deepseek"]),
    enable_traceability_prefilter: bool = Form(False),
):
    # —— 字段校验 ——
    if not hlr_word_file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=422, detail="hlr_word_file must be .docx")

    if eoicd_publisher_file is None and eoicd_subscriber_file is None:
        raise HTTPException(status_code=422, detail="at least one of eoicd_publisher_file or eoicd_subscriber_file is required")

    for ef in (eoicd_publisher_file, eoicd_subscriber_file):
        if ef is not None and not ef.filename.lower().endswith(".xlsx"):
            raise HTTPException(status_code=422, detail=f"{ef.filename} must be .xlsx")

    if judge_providers:
        for p in judge_providers:
            if p not in ALLOWED_JUDGE_PROVIDERS:
                raise HTTPException(
                    status_code=422,
                    detail=f"judge_providers: unsupported provider '{p}'; allowed: deepseek, minimax, qwen",
                )

    # —— 创建 V4 Kind Job 与目录（V4 路径：backend/output/v4/{job_id}/input/ + output/）——
    job = job_manager.create_job(kind="v4")
    job_dir = Path(__file__).resolve().parent.parent.parent.parent / 'output' / 'v4' / job.job_id
    input_dir = job_dir / 'input'
    input_dir.mkdir(parents=True, exist_ok=True)

    # —— 保存上传文件到 input/ ——
    hlr_path = await _save_upload(hlr_word_file, input_dir)
    pub_path = await _save_upload(eoicd_publisher_file, input_dir) if eoicd_publisher_file else None
    sub_path = await _save_upload(eoicd_subscriber_file, input_dir) if eoicd_subscriber_file else None

    trace_dir: Optional[Path] = None
    if enable_traceability_prefilter and traceability_files:
        trace_dir = input_dir / 'traceability'
        trace_dir.mkdir(parents=True, exist_ok=True)
        for tf in traceability_files:
            if not tf.filename.lower().endswith(".xlsx"):
                raise HTTPException(status_code=422, detail=f"traceability file {tf.filename} must be .xlsx")
            await _save_upload(tf, trace_dir)

    # —— 后台线程跑 V4 管线（使用 runner 的 env 保存/恢复保护） ——
    launch_v4_pipeline(
        job=job,
        job_dir=job_dir,
        hlr_path=hlr_path,
        publisher_path=pub_path,
        subscriber_path=sub_path,
        trace_dir=trace_dir,
        judge_providers=judge_providers,
        use_mock_llm=use_mock_llm,
    )

    return V4AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='V4 反向管线任务已创建',
    )
