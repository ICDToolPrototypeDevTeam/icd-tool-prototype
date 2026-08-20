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

from docx import Document
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.v4.runner import launch_v4_pipeline
from app.api.v4.schemas import V4AnalyzeResponse
from app.job_manager import job_manager


router = APIRouter()


def _detect_system_type(hlr_file: UploadFile) -> str:
    """Auto-detect HLR file system type from table structure.

    Detection logic:
    1. Parse HLR file tables
    2. Search for requirement table matching HVAC (8 rows, "需求ID") or Fuel (13 rows, "ID")
    3. HVAC: 8 rows × 2 cols, first cell contains "需求ID"
    4. Fuel: 13 rows × 2 cols, first cell contains "ID"

    Returns: "hvac" | "fuel"
    Raises: ValueError if detection fails
    """
    import tempfile

    content = hlr_file.file.read()
    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        doc = Document(str(tmp_path))
        if len(doc.tables) < 2:
            raise ValueError("HLR 文件表格数量不足，无法识别系统类型")

        for table in doc.tables:
            rows, cols = len(table.rows), len(table.columns)
            if rows == 8 and cols == 2:
                cell0 = table.cell(0, 0).text.strip()
                if "需求ID" in cell0:
                    return "hvac"

            if rows == 13 and cols == 2:
                cell0 = table.cell(0, 0).text.strip()
                if "ID" in cell0:
                    return "fuel"

        raise ValueError(
            f"无法识别 HLR 文件所属系统类型，请手动选择系统类型上传。"
        )
    finally:
        tmp_path.unlink(missing_ok=True)
        hlr_file.file.seek(0)


def _validate_hlr_format(hlr_path: Path, system_type: str) -> None:
    """Validate HLR file format matches the system configuration.

    Raises: ValueError if format doesn't match
    """
    from app.v4.config import get_hlr_system_config

    system_config = get_hlr_system_config(system_type)
    doc = Document(str(hlr_path))

    table_index = system_config["glossary_table_index"]
    if len(doc.tables) <= table_index:
        raise ValueError(
            f"HLR 文件术语表位置不符合 {system_config['name']} 格式，"
            f"请确认系统类型选择正确"
        )

    requirement_rows = system_config["requirement_rows"]
    found_valid = False
    for table in doc.tables[table_index + 1:]:
        if len(table.rows) == requirement_rows and len(table.columns) == 2:
            found_valid = True
            break

    if not found_valid:
        raise ValueError(
            f"HLR 文件需求表行数不符合 {system_config['name']} 格式（应为{requirement_rows}行），"
            f"请确认系统类型选择正确"
        )


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
    system_type: Optional[str] = Form(None),
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

    # —— 确定系统类型 ——
    if system_type is None:
        detected_type = _detect_system_type(hlr_word_file)
        system_type = detected_type
        print(f"自动识别系统类型: {system_type}")
    else:
        if system_type not in ("hvac", "fuel"):
            raise HTTPException(status_code=422, detail=f"Unsupported system_type: {system_type}")

    # —— 验证 HLR 文件格式 ——
    try:
        _validate_hlr_format(hlr_path, system_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

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
        system_type=system_type,
    )

    return V4AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='V4 反向管线任务已创建',
    )
