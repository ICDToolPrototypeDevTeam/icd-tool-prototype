# -*- coding: utf-8 -*-
"""POST /api/v4/coverage-analysis 上传与任务创建。

ADR-001 Issue A：
- multipart 字段：hlr_word_file 必填；publisher/subscriber 二选一；traceability_files 可选；
- judge_providers / use_mock_llm 由前端可选；use_mock_llm 由 runner 在本任务线程内
  绑定运行上下文（见 app/runtime_context.py；不写进程 env，支持并发任务）；
- 仅校验文件扩展名 / 白名单 provider；不做深度字段解析（V4 pipeline 内部自检）。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

import openpyxl
from docx import Document
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.v4.runner import launch_v4_pipeline
from app.api.v4.schemas import V4AnalyzeResponse
from app.auth.deps import current_user
from app.auth.models import User
from app.job_manager import job_manager
from app.v4.config import get_output_root
from app.v4.hlr_schema_store import (
    SchemaNotFoundError,
    SchemaValidationError,
    resolve_schema_reference,
)
from app.v4.parsers import registered_extensions
from app.v4.parsers.hlr_schema import HLRSchemaValidationError
from app.v4.parsers.zip_entry_normalize import ensure_standard_path
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
# ``generic`` = 其他系统（通用）：解析完全由自定义表格样式驱动，必须显式选择
# 且同时提供样式；自动识别不会返回它（profile 未声明 auto_detect）。
ALLOWED_CONTROLLER_PROFILES = {"ams", "fgmc", "hscu", "rpdu", "generic"}

# 文件名安全字符：保留中英数 + . _ - + 空格 + 各种括号（合法 FS 字符）
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-一-龥\s()（）\[\]【】]")
MAX_FILE_BYTES = 50 * 1024 * 1024          # 50 MB 单文件
MAX_REQUEST_BYTES = 200 * 1024 * 1024      # 200 MB 总

# 流式写盘分块：20 并发上传时不能每个请求都把 50MB 一次性读进内存
_UPLOAD_CHUNK_BYTES = 1024 * 1024


class _RequestBudget:
    """单次请求所有上传文件的总字节预算。

    FastAPI 的 multipart 解析本身也会落盘/占内存，这里至少保证业务层不再
    额外把整份文件读进内存，并把「单请求总量」限制在 ``MAX_REQUEST_BYTES``。
    """

    def __init__(self, limit: int = MAX_REQUEST_BYTES) -> None:
        self._limit = int(limit)
        self.used = 0

    def add(self, size: int) -> None:
        self.used += int(size)
        if self.used > self._limit:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"request too large: uploaded files exceed "
                    f"{self._limit} bytes"
                ),
            )


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


async def _save_upload(
    file: UploadFile, dest_dir: Path, budget: Optional[_RequestBudget] = None
) -> Path:
    """流式保存上传文件：1MB 分块，不在内存里保留整份文件。

    先写 ``.part`` 临时文件，成功后再 ``os.replace`` 原子替换；中途超限 /
    客户端断开时删除临时文件，不会留下半份可被管线读到的输入。
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="uploaded file has no filename")
    safe = _safe_filename(file.filename)
    dest = dest_dir / safe
    tmp = dest.with_name(f".{dest.name}.part")
    total = 0
    try:
        with tmp.open("wb") as out:
            while True:
                chunk = await file.read(_UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"file too large: {safe} "
                            f"({total} > {MAX_FILE_BYTES})"
                        ),
                    )
                if budget is not None:
                    budget.add(len(chunk))
                out.write(chunk)
        os.replace(tmp, dest)
    finally:
        # 成功时 tmp 已被 os.replace 移走（missing_ok 吞掉）；失败/客户端断开时
        # 清理半份文件，避免管线读到不完整输入。finally 也覆盖 BaseException。
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return dest


def _load_hlr_tables(hlr_path: Path) -> list[list[list[str]]]:
    """Load HLR file as list of 2D cell-text arrays (one per table/sheet).

    .docx → list of Word tables; .xlsx → list of sheets. Each table/sheet
    becomes a 2D array of stripped cell text strings. Lets the downstream
    ``_match_auto_detect`` operate on a format-agnostic structure.
    """
    # 自动识别在请求内同步跑，一旦读不出来就是未捕获异常 → 裸 500（任务还没创建，
    # 前端连日志都没有）。WPS 等第三方存盘的包条目名可能是反斜杠，先归一化再读。
    hlr_path = ensure_standard_path(hlr_path)

    ext = hlr_path.suffix.lower()
    if ext == ".docx":
        doc = Document(str(hlr_path))
        return [
            [[cell.text.strip() for cell in row.cells] for row in table.rows]
            for table in doc.tables
        ]
    if ext == ".xlsx":
        wb = openpyxl.load_workbook(str(hlr_path), data_only=True)
        try:
            return [
                [
                    ["" if c.value is None else str(c.value).strip() for c in row]
                    for row in ws.iter_rows(values_only=False)
                ]
                for ws in wb.worksheets
            ]
        finally:
            wb.close()
    raise ValueError(f"Unsupported SWHLR extension for auto-detect: {ext}")


def _match_auto_detect(tables: list[list[list[str]]], auto_detect: dict) -> bool:
    """Match a single auto_detect rule against a list of 2D cell-text arrays.

    Schema (same as AMS/FGMC/HSCU docx profiles):
      - ``required_rows`` (optional int): exact row count to match (legacy field)
      - ``min_rows`` (optional int): minimum row count to match (≥ N; used by
        profiles whose sample row count varies, e.g. RPDU has both 13 and 581
        row test files)
      - ``required_cols`` (optional int): exact column count to match
      - ``cell_patterns``: ``{"<col_idx>": {"contains"|"starts_with": "...",
        "row": N}}`` — 0-based column key, 0-based row offset (default 0)
    """
    required_rows = auto_detect.get("required_rows")
    min_rows = auto_detect.get("min_rows")
    required_cols = auto_detect.get("required_cols")
    cell_patterns = auto_detect.get("cell_patterns", {})

    for table in tables:
        rows = len(table)
        cols = max((len(row) for row in table), default=0)
        if required_rows is not None and rows != required_rows:
            continue
        if min_rows is not None and rows < min_rows:
            continue
        if required_cols is not None and cols != required_cols:
            continue

        matched = True
        for cell_pos, pattern in cell_patterns.items():
            row_idx = pattern.get("row", 0)
            col_idx = int(cell_pos)
            if row_idx >= rows or col_idx >= cols:
                matched = False
                break
            try:
                cell_text = table[row_idx][col_idx]
            except IndexError:
                matched = False
                break
            if "contains" in pattern and pattern["contains"] not in cell_text:
                matched = False
                break
            if "starts_with" in pattern and not cell_text.startswith(pattern["starts_with"]):
                matched = False
                break
        if matched:
            return True
    return False


def _detect_system_type(hlr_path: Path) -> str:
    """Auto-detect HLR file system type by scanning tables against profile auto_detect configs.

    Loads the HLR file (.docx or .xlsx) into a uniform list-of-2D-arrays via
    ``_load_hlr_tables`` and matches each profile's auto_detect rule.

    Ensures registry is initialized before scanning (coverage.py is synchronous,
    while init_registry() is called in the background runner thread).
    """
    if not _registry._profiles:
        init_registry(Path(__file__).resolve().parents[2] / "v4" / "profiles")

    tables = _load_hlr_tables(hlr_path)
    reg = get_registry()

    for pid in reg.list_ids():
        cfg = reg.get(pid)
        ad = cfg.auto_detect
        if not ad:
            continue
        if _match_auto_detect(tables, ad):
            return pid
    raise ValueError("无法识别 SWHLR 文件所属系统类型，请手动选择系统类型上传。")


@router.post('/coverage-analysis', response_model=V4AnalyzeResponse)
async def coverage_analysis(
    hlr_word_file: UploadFile = File(...),
    eoicd_publisher_file: Optional[UploadFile] = File(None),
    eoicd_subscriber_file: Optional[UploadFile] = File(None),
    traceability_files: list[UploadFile] = File(default=[]),
    # Mock 仅由 .env 的 USE_MOCK_LLM 控制；此字段仅作显式覆盖（None = 不动 env）
    use_mock_llm: Optional[bool] = Form(None),
    judge_providers: list[str] = Form(default_factory=lambda: ["deepseek"]),
    enable_traceability_prefilter: bool = Form(False),
    controller_profile: Optional[str] = Form(None),
    no_refine: bool = Form(False),
    hlr_schema: Optional[str] = Form(None),
    # 未登录由 fastapi-users 返回 401；归属写入创建的任务（后续任务按 owner_id 隔离）
    user: User = Depends(current_user),
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

    # —— 自定义表格样式：必须在创建任务目录之前解析校验 ——
    # 否则非法样式会留下一个永远不会启动的任务目录（幽灵任务）。
    resolved_schema = None
    # 直接调用路由函数的场景不经过 FastAPI 表单解析，缺省值仍是 Form(...) 哨兵；
    # 只有真正的字符串才算「传了样式」（空串按未传处理）。
    if isinstance(hlr_schema, str) and hlr_schema:
        if _hlr_ext != ".docx":
            raise HTTPException(status_code=422, detail="自定义表格样式仅支持 Word 文档")
        if not controller_profile:
            raise HTTPException(status_code=422, detail="使用自定义样式时请选择系统类型")
        try:
            resolved = resolve_schema_reference(hlr_schema)
        except (SchemaValidationError, HLRSchemaValidationError, SchemaNotFoundError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        if resolved.mode == "native" and resolved.profile_id != controller_profile:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"内置模板 {resolved.template_id} 属于 {resolved.profile_id}，"
                    f"与系统类型 {controller_profile} 不一致"
                ),
            )
        resolved_schema = resolved.to_snapshot()

    # 通用系统没有内置解析规则：必须由自定义表格样式提供行标签映射。
    if controller_profile == "generic":
        if resolved_schema is None:
            raise HTTPException(
                status_code=422,
                detail="使用通用系统时请配置自定义表格样式",
            )
        if enable_traceability_prefilter and traceability_files:
            raise HTTPException(
                status_code=422,
                detail="通用系统暂不支持追溯预筛，请关闭后重试",
            )

    # —— 创建 Job 与目录（V4 路径：backend/output/v4/{job_id}/input/ + output/）——
    # 此刻**不登记**进任务列表：下面的保存上传与自动识别都可能抛错（413/422/500），
    # 那样请求就结束了、线程不会启动，登记了就是一条永远停在「等待开始」的幽灵任务。
    # 登记推迟到 launch_v4_pipeline（JobManager.new_job 有详细说明）。
    job = job_manager.new_job(
        task_type="correctness",
        owner_id=str(user.id),
        owner_username=user.email,
    )
    job_dir = get_output_root() / 'v4' / job.job_id
    input_dir = job_dir / 'input'
    input_dir.mkdir(parents=True, exist_ok=True)

    # —— 保存上传文件到 input/（1MB 分块流式写盘 + 单请求总预算）——
    budget = _RequestBudget()
    hlr_path = await _save_upload(hlr_word_file, input_dir, budget)
    pub_path = (
        await _save_upload(eoicd_publisher_file, input_dir, budget)
        if eoicd_publisher_file else None
    )
    sub_path = (
        await _save_upload(eoicd_subscriber_file, input_dir, budget)
        if eoicd_subscriber_file else None
    )

    trace_dir: Optional[Path] = None
    if enable_traceability_prefilter and traceability_files:
        trace_dir = input_dir / 'traceability'
        trace_dir.mkdir(parents=True, exist_ok=True)
        for tf in traceability_files:
            if not tf.filename.lower().endswith(".xlsx"):
                raise HTTPException(status_code=422, detail=f"traceability file {tf.filename} must be .xlsx")
            await _save_upload(tf, trace_dir, budget)

    # 自定义样式快照落盘（供追溯与续跑；未传样式时不写，任务目录内容不变）
    if resolved_schema is not None:
        (input_dir / "hlr_schema.json").write_text(
            json.dumps(resolved_schema, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # —— 自动识别或校验 controller_profile ——
    if controller_profile is None:
        # 读 docx/xlsx 是同步阻塞的；放线程池，避免 20 并发上传时卡住事件循环
        # （卡住期间所有轮询任务状态的请求也会一起变慢）。
        detected = await run_in_threadpool(_detect_system_type, hlr_path)
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

    # —— 后台线程跑 V4 管线（use_mock_llm 由 runner 在线程内绑定运行上下文） ——
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
        no_refine=no_refine,
        hlr_schema=resolved_schema,
    )

    return V4AnalyzeResponse(
        job_id=job.job_id,
        status=job.status.value,
        message='V4 反向管线任务已创建',
    )