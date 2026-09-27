# -*- coding: utf-8 -*-
"""V4.0 FastAPI Pydantic Schemas。

ADR-001 Issue A：
- V4Job* 响应 schema 不与 V3 Job* schema 互通（独立 import、无依赖）；
- `mock_models` 取值规则严格按 ADR-001 D5。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from app.job_manager import JobStatus


class SystemType(str, Enum):
    HVAC = "hvac"
    FUEL = "fuel"
    HYDRAULIC = "hscu"


# ============================================================================
# 上传接口响应
# ============================================================================


class V4AnalyzeResponse(BaseModel):
    """POST /api/v4/coverage-analysis 同步返回；后台任务在 thread 中执行。"""

    job_id: str
    status: str
    message: str


# ============================================================================
# 状态接口响应
# ============================================================================


class V4ReuseStats(BaseModel):
    """恢复运行中的实时复用计数（判定条数：命中复用 / 重新分析）。"""

    reused: int = 0
    rerun: int = 0


class V4JobError(BaseModel):
    """结构化失败信息。

    问题 5 的根因修复：后端此前只把原因塞进一句 message，且 `/result` 在非
    completed 时返回 409，前端拿不到 → 所有失败在 UI 上同形。
    """

    category: str = 'INTERNAL'
    title: str = ''
    stage: str = ''
    stage_index: Optional[int] = None
    error_type: str = ''
    message: str = ''
    detail: str = ''
    traceback_tail: str = ''
    hint: str = ''
    at: str = ''


class V4LogLine(BaseModel):
    """任务日志单行。seq 在单个任务内单调递增，供增量拉取做偏移。"""

    seq: int
    ts: str = ''
    level: str = 'info'
    text: str


class V4JobLogsResponse(BaseModel):
    """GET /api/v4/jobs/{job_id}/logs 响应。"""

    job_id: str
    lines: list[V4LogLine] = []
    next_offset: int = 0
    truncated: bool = False


class V4JobStatusResponse(BaseModel):
    """GET /api/v4/jobs/{job_id} 响应。"""

    job_id: str
    status: JobStatus
    task_type: str = "correctness"
    stage: str = ""
    stage_index: Optional[int] = None
    stage_total: Optional[int] = None
    case_index: Optional[int] = None
    case_total: Optional[int] = None
    message: Optional[str] = None
    resumed: bool = False
    reuse: Optional[V4ReuseStats] = None
    mock_models: list[str] = []
    # 本次运行是否 MOCK 模式（结果页据此提示「模拟数据不可用于验收」）
    mock: bool = False
    # 已请求终止、等待管线在检查点停止
    cancel_requested: bool = False
    # 结构化失败信息；成功 / 运行中为 None
    error: Optional[V4JobError] = None
    created_at: str
    updated_at: str


class V4JobListItem(BaseModel):
    """GET /api/v4/jobs 列表项（轻量，不含结果字段）。"""

    job_id: str
    task_type: str = "correctness"
    status: JobStatus
    message: Optional[str] = None
    created_at: str
    updated_at: str
    input_files: list[str] = []


# ============================================================================
# 结果接口响应
# ============================================================================


class V4JobOutputs(BaseModel):
    """V4 输出文件存在性布尔（仅 3 类对外）。"""

    eoicd_xlsx: bool
    consistency_deepseek_docx: bool
    consistency_minimax_docx: bool
    consistency_qwen_docx: bool
    consensus_docx: bool


class V4JobResultSummary(BaseModel):
    """V4 反向管线结果摘要。"""

    eoicd_count: int = 0
    eoicd_blocks_total: int = 0
    eoicd_blocks_matched: int = 0
    hlr_count: int = 0
    matched_count: int = 0
    pending_count: int = 0
    unmatched_count: int = 0
    judged_count: int = 0
    agreement_distribution: dict = {}
    star_distribution: dict = {}
    status_distribution: dict = {}
    average_star_rating: float = 0.0


class V4JobResultResponse(BaseModel):
    """GET /api/v4/jobs/{job_id}/result 响应。"""

    job_id: str
    status: JobStatus
    task_type: str = "correctness"
    summary: V4JobResultSummary
    outputs: V4JobOutputs
    mock_models: list[str]
    errors: list[str]


# ============================================================================
# 正向完整性分析（EoICD → HLR）结果接口响应
# ============================================================================


class V4ForwardJobOutputs(BaseModel):
    """正向完整性分析输出文件存在性布尔（2 类对外）。"""

    forward_xlsx: bool
    forward_docx: bool


class V4ForwardJobResultSummary(BaseModel):
    """V4 正向完整性分析结果摘要。"""

    analysis_mode: str = ""
    total_blocks: int = 0
    covered_direct: int = 0
    covered_aggregate: int = 0
    parent_referenced: int = 0
    possible: int = 0
    uncovered: int = 0
    unsupported: int = 0
    input_error: int = 0
    ai_reviewed: int = 0
    eoicd_count: int = 0
    hlr_count: int = 0


class V4ForwardJobResultResponse(BaseModel):
    """GET /api/v4/jobs/{job_id}/result（task_type=completeness）响应。"""

    job_id: str
    status: JobStatus
    task_type: str = "completeness"
    summary: V4ForwardJobResultSummary
    outputs: V4ForwardJobOutputs
    errors: list[str]


# ============================================================================
# 历史结果：列表与删除
# ============================================================================


class V4HistoryOutputs(BaseModel):
    """历史结果里各类产物的存在性（反向 5 类 + 正向 2 类，与下载接口一一对应）。"""

    eoicd_xlsx: bool = False
    consistency_deepseek_docx: bool = False
    consistency_minimax_docx: bool = False
    consistency_qwen_docx: bool = False
    consensus_docx: bool = False
    forward_xlsx: bool = False
    forward_docx: bool = False


class V4HistoryItem(BaseModel):
    """GET /api/v4/history 列表项。

    **磁盘口径**：列的是 ``output/v4/`` 下的任务目录，因此内存里没有的任务
    （进程重启前就跑完的）同样会出现 —— 这正是历史结果页与 ``/jobs`` 的区别。
    """

    job_id: str
    task_type: str = ""
    # JobStatus 的取值；目录里没有可读 manifest（上传失败留下的残留目录）时为 "unknown"
    status: str = "unknown"
    message: Optional[str] = None
    created_at: str
    updated_at: str
    finished_at: Optional[str] = None
    input_files: list[str] = []
    outputs: V4HistoryOutputs = V4HistoryOutputs()
    # 该任务目录占用的字节数（含上传的输入文件与中间产物），删除前据此告知释放空间
    size_bytes: int = 0
    # 本次运行是否 MOCK（含义同状态接口：模拟数据不可用于验收）
    mock: bool = False


class V4HistoryDeleteRequest(BaseModel):
    """POST /api/v4/history/delete 请求体。

    ``confirm`` 必须显式为 ``true``：删除是整目录 ``rmtree``，不可恢复。
    """

    job_ids: list[str]
    confirm: bool = False


class V4HistoryDeleteResult(BaseModel):
    """单个任务删除成功的结果。"""

    job_id: str
    freed_bytes: int = 0


class V4HistoryDeleteFailure(BaseModel):
    """单个任务删除失败的结果（不影响同批其它任务）。"""

    job_id: str
    error: str


class V4HistoryDeleteResponse(BaseModel):
    """POST /api/v4/history/delete 响应。"""

    deleted: list[V4HistoryDeleteResult] = []
    failed: list[V4HistoryDeleteFailure] = []
    total_freed_bytes: int = 0
