# -*- coding: utf-8 -*-
"""POST /api/v4/completeness-analysis 正向完整性分析上传与任务创建。

正向分析（EoICD → HLR）回答「HLR 是否漏写了某个 EoICD 业务对象」，与反向分析
（HLR → EoICD，正确性比对）互补。字段约定：

- hlr_word_file 必填（.docx）；
- eoicd_publisher_file / eoicd_subscriber_file 二选一（.xlsx）；
- use_mock_llm 由前端可选，线程内通过 runner 写入 env 并恢复。

正向缺陷修正 #5：analysis_mode 不再由前端指定（删除该字段，前端仍可透传但被忽略）。
分析模式按上传的追溯表自动判定：
  - 无追溯表            → full（全量完整性分析）
  - 两张追溯表齐全       → trace（追溯范围完整性分析）
  - 仅一张追溯表         → 422（trace 必须成对）
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.v4.coverage import _save_upload
from app.api.v4.runner import launch_forward_pipeline
from app.api.v4.schemas import V4AnalyzeResponse
from app.job_manager import job_manager


router = APIRouter()


@router.post('/completeness-analysis', response_model=V4AnalyzeResponse)
async def completeness_analysis(
    hlr_word_file: UploadFile = File(...),
    eoicd_publisher_file: Optional[UploadFile] = File(None),
    eoicd_subscriber_file: Optional[UploadFile] = File(None),
    device_icd_trace_file: Optional[UploadFile] = File(None),
    system_device_trace_file: Optional[UploadFile] = File(None),
    use_mock_llm: bool = Form(False),
):
    # —— 字段校验 ——
    if not hlr_word_file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=422, detail="hlr_word_file must be .docx")

    if eoicd_publisher_file is None and eoicd_subscriber_file is None:
        raise HTTPException(status_code=422, detail="at least one of eoicd_publisher_file or eoicd_subscriber_file is required")

    for ef in (eoicd_publisher_file, eoicd_subscriber_file):
        if ef is not None and not ef.filename.lower().endswith(".xlsx"):
            raise HTTPException(status_code=422, detail=f"{ef.filename} must be .xlsx")

    # —— 正向缺陷修正 #5：按上传的追溯表自动判定分析模式 ——
    has_t1 = device_icd_trace_file is not None
    has_t2 = system_device_trace_file is not None
    if has_t1 != has_t2:
        raise HTTPException(
            status_code=422,
            detail="trace mode requires BOTH device_icd_trace_file and system_device_trace_file (only one provided)",
        )
    analysis_mode = "trace" if has_t1 else "full"

    if analysis_mode == "trace":
        for tf, field in (
            (device_icd_trace_file, "device_icd_trace_file"),
            (system_device_trace_file, "system_device_trace_file"),
        ):
            if not tf.filename.lower().endswith(".xlsx"):
                raise HTTPException(status_code=422, detail=f"{field} must be .xlsx")

    # —— 创建 Job 与目录（正向与反向共用 output/v4/{job_id}/ 结构）——
    job = job_manager.create_job(task_type="completeness")
    job_dir = Path(__file__).resolve().parent.parent.parent.parent / 'output' / 'v4' / job.job_id
    input_dir = job_dir / 'input'
    input_dir.mkdir(parents=True, exist_ok=True)

    # —— 保存上传文件到 input/ ——
    hlr_path = await _save_upload(hlr_word_file, input_dir)
    pub_path = await _save_upload(eoicd_publisher_file, input_dir) if eoicd_publisher_file else None
    sub_path = await _save_upload(eoicd_subscriber_file, input_dir) if eoicd_subscriber_file else None

    device_icd_path: Optional[Path] = None
    system_device_path: Optional[Path] = None
    if analysis_mode == "trace":
        device_icd_path = await _save_upload(device_icd_trace_file, input_dir)
        system_device_path = await _save_upload(system_device_trace_file, input_dir)

    # —— 后台线程跑正向管线 ——
    launch_forward_pipeline(
        job=job,
        job_dir=job_dir,
        hlr_path=hlr_path,
        publisher_path=pub_path,
        subscriber_path=sub_path,
        analysis_mode=analysis_mode,
        device_icd_trace_file=device_icd_path,
        system_device_trace_file=system_device_path,
        use_mock_llm=use_mock_llm,
    )

    return V4AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='V4 正向完整性分析任务已创建',
    )
