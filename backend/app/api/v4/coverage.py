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
from app.v4.parsers import registered_extensions
from app.v4.profiles import get_registry, init_registry, _registry


router = APIRouter()

# ADR-001 §2 校验白名单
ALLOWED_JUDGE_PROVIDERS = {"deepseek", "minimax", "qwen"}
# Issue #74: HLR file extensions come from the parser factory. New
# parsers registered in ``parsers/hlr_parser_factory.py`` are accepted
# here without changing the API surface.
SUPPORTED_HLR_EXTENSIONS = frozenset(registered_extensions())
# Issue #63 / Task 12 + Issue #74: v4 controller profile whitelist.
# Must match the directories under ``backend/app/v4/profiles/``.
ALLOWED_CONTROLLER_PROFILES = {"ams", "fgmc", "hscu", "rpdu"}

# 文件名安全字符：保留中英数 + . _ - + 空格 + 各种括号（合法 FS 字符）
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-一-龥\s()（）\[\]【】]")
MAX_FILE_BYTES = 50 * 1024 * 1024          # 50 MB 单文件
MAX_REQUEST_BYTES = 200 * 1024 * 1024      # 200 MB 总


def _safe_filename(name: str) -> str:
    base = Path(name).name
    # Windows + curl 中文路径会在 multipart filename 字段做 GBK-as-latin1 mojibake。
    # 反向修复:把 latin-1 unicode 字符还原为 GBK 字节再按 GBK 解出真中文。
    # 仅在含非 ASCII 时尝试,失败则保持原值。
    if any(ord(c) > 127 for c in base):
        try:
            base = base.encode("latin-1").decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
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


def _match_auto_detect(doc: Document, auto_detect: dict) -> bool:
    """Match a single auto_detect rule against a Word document's tables."""
    required_rows = auto_detect.get("required_rows")
    required_cols = auto_detect.get("required_cols")
    cell_patterns = auto_detect.get("cell_patterns", {})

    for table in doc.tables:
        rows, cols = len(table.rows), len(table.columns)
        if required_rows and rows != required_rows:
            continue
        if required_cols and cols != required_cols:
            continue

        matched = True
        for cell_pos, pattern in cell_patterns.items():
            row_idx = pattern.get("row", 0)
            col_idx = int(cell_pos)
            if row_idx >= rows or col_idx >= cols:
                matched = False
                break
            cell_text = table.cell(row_idx, col_idx).text.strip()
            # contains 匹配
            if "contains" in pattern and pattern["contains"] not in cell_text:
                matched = False
                break
            # starts_with 匹配
            if "starts_with" in pattern and not cell_text.startswith(pattern["starts_with"]):
                matched = False
                break
        if matched:
            return True
    return False


def _detect_system_type(hlr_path: Path) -> str:
    """Auto-detect HLR file system type by scanning Word tables against profile auto_detect configs.

    Ensures registry is initialized before scanning (coverage.py is synchronous,
    while init_registry() is called in the background runner thread).
    """
    if not _registry._profiles:
        init_registry(Path(__file__).resolve().parents[2] / "v4" / "profiles")

    doc = Document(str(hlr_path))
    reg = get_registry()

    for pid in reg.list_ids():
        cfg = reg.get(pid)
        ad = cfg.auto_detect
        if not ad:
            continue
        if _match_auto_detect(doc, ad):
            return pid
    raise ValueError("无法识别 HLR 文件所属系统类型，请手动选择系统类型上传。")


@router.post('/coverage-analysis', response_model=V4AnalyzeResponse)
async def coverage_analysis(
    hlr_word_file: UploadFile = File(...),
    eoicd_publisher_file: Optional[UploadFile] = File(None),
    eoicd_subscriber_file: Optional[UploadFile] = File(None),
    traceability_files: list[UploadFile] = File(default=[]),
    use_mock_llm: bool = Form(False),
    judge_providers: list[str] = Form(default_factory=lambda: ["deepseek"]),
    enable_traceability_prefilter: bool = Form(False),
    controller_profile: Optional[str] = Form(None),
):
    # —— 字段校验 ——
    _hlr_ext = Path(hlr_word_file.filename or "").suffix.lower()
    if _hlr_ext not in SUPPORTED_HLR_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"hlr_word_file extension must be one of "
                f"{sorted(SUPPORTED_HLR_EXTENSIONS)} (got {_hlr_ext!r})"
            ),
        )

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

    # —— 创建 Job 与目录（V4 路径：backend/output/v4/{job_id}/input/ + output/）——
    job = job_manager.create_job(task_type="correctness")
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

    # —— 自动识别或校验 controller_profile ——
    if controller_profile is None:
        detected = _detect_system_type(hlr_path)
        controller_profile = detected
        print(f"自动识别系统类型: {controller_profile}")
    else:
        if controller_profile not in ALLOWED_CONTROLLER_PROFILES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"controller_profile: unsupported '{controller_profile}'; "
                    f"allowed: {', '.join(sorted(ALLOWED_CONTROLLER_PROFILES))}"
                ),
            )

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
        controller_profile=controller_profile,
    )

    return V4AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='V4 反向管线任务已创建',
    )