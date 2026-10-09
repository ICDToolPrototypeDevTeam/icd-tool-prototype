# -*- coding: utf-8 -*-
"""POST /api/v4/completeness-analysis 正向完整性分析上传与任务创建。

正向分析（EoICD → HLR）回答「HLR 是否漏写了某个 EoICD 业务对象」，与反向分析
（HLR → EoICD，正确性比对）互补。字段约定：

- hlr_word_file 必填（.docx）；
- eoicd_publisher_file / eoicd_subscriber_file 二选一（.xlsx）；
- use_mock_llm 由前端可选，由 runner 在本任务线程内绑定运行上下文
  （见 app/runtime_context.py；不写进程 env，支持并发任务）。

正向缺陷修正 #5：analysis_mode 不再由前端指定（删除该字段，前端仍可透传但被忽略）。
分析模式按上传的追溯表自动判定：
  - 未上传追溯表        → full（全量完整性分析）
  - 上传任意张（0-N）    → trace（追溯范围完整性分析）

追溯表为单字段多文件 ``trace_files``（照反向 coverage 端点的 traceability_files
模式）：几张、各是什么表（EoICD↔需求追溯表 / 需求矩阵）由正向树按表头关键字与
需求编号规则自动识别（见 backend/forward/EoICD侧数据处理/config/traceability.yaml），
接口不做配对或张数约束——EPS 是 4 层链（HLR→ERD→SRD→EoICD），需要 3 张表。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.v4.coverage import _save_upload
from app.api.v4.runner import launch_forward_pipeline
from app.api.v4.schemas import V4AnalyzeResponse
from app.job_manager import job_manager
from app.v4.config import get_output_root


router = APIRouter()


@router.post('/completeness-analysis', response_model=V4AnalyzeResponse)
async def completeness_analysis(
    hlr_word_file: UploadFile = File(...),
    eoicd_publisher_file: Optional[UploadFile] = File(None),
    eoicd_subscriber_file: Optional[UploadFile] = File(None),
    trace_files: list[UploadFile] = File(default=[]),
    # 系统类型（正向项目）：空 = 按上传文件名自动识别（见 app/forward/projects.py）
    controller_profile: Optional[str] = Form(None),
    # Mock 仅由 .env 的 USE_MOCK_LLM 控制；此字段仅作显式覆盖（None = 不动 env）
    use_mock_llm: Optional[bool] = Form(None),
):
    # —— 字段校验 ——
    if not hlr_word_file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=422, detail="hlr_word_file must be .docx")

    if eoicd_publisher_file is None and eoicd_subscriber_file is None:
        raise HTTPException(status_code=422, detail="at least one of eoicd_publisher_file or eoicd_subscriber_file is required")

    for ef in (eoicd_publisher_file, eoicd_subscriber_file):
        if ef is not None and not ef.filename.lower().endswith(".xlsx"):
            raise HTTPException(status_code=422, detail=f"{ef.filename} must be .xlsx")

    # —— 正向缺陷修正 #5：按上传的追溯表自动判定分析模式（0 张 = 全量，≥1 张 = 追溯） ——
    analysis_mode = "trace" if trace_files else "full"

    for tf in trace_files:
        if not tf.filename.lower().endswith(".xlsx"):
            raise HTTPException(status_code=422, detail=f"trace_files: {tf.filename} must be .xlsx")

    # —— 系统类型校验：非空且不在正向项目表内 → 422（与反向 ALLOWED_CONTROLLER_PROFILES 同款早失败） ——
    if controller_profile:
        from app.forward.projects import ForwardProjectError, project_ids, resolve_project

        try:
            controller_profile = resolve_project(controller_profile)
        except ForwardProjectError as e:
            raise HTTPException(status_code=422, detail=f"{e}（可选：{', '.join(project_ids())}）")

    # —— 创建 Job 与目录（正向与反向共用 output/v4/{job_id}/ 结构）——
    # 不登记：保存上传可能抛错（413/422），登记了就会留下停在「等待开始」的
    # 幽灵任务。登记推迟到 launch_forward_pipeline（见 JobManager.new_job）。
    job = job_manager.new_job(task_type="completeness")
    job_dir = get_output_root() / 'v4' / job.job_id
    input_dir = job_dir / 'input'
    input_dir.mkdir(parents=True, exist_ok=True)

    # —— 保存上传文件到 input/ ——
    hlr_path = await _save_upload(hlr_word_file, input_dir)
    pub_path = await _save_upload(eoicd_publisher_file, input_dir) if eoicd_publisher_file else None
    sub_path = await _save_upload(eoicd_subscriber_file, input_dir) if eoicd_subscriber_file else None

    trace_paths: list[Path] = []
    for tf in trace_files:
        trace_paths.append(await _save_upload(tf, input_dir))

    # —— 后台线程跑正向管线 ——
    launch_forward_pipeline(
        job=job,
        job_dir=job_dir,
        hlr_path=hlr_path,
        publisher_path=pub_path,
        subscriber_path=sub_path,
        analysis_mode=analysis_mode,
        trace_files=tuple(trace_paths),
        use_mock_llm=use_mock_llm,
        controller_profile=controller_profile,
    )

    return V4AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='V4 正向完整性分析任务已创建',
    )
