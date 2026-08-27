# -*- coding: utf-8 -*-
"""V4.0 FastAPI Pydantic Schemas。

ADR-001 Issue A：
- V4Job* 响应 schema 不与 V3 Job* schema 互通（独立 import、无依赖）；
- `mock_models` 取值规则严格按 ADR-001 D5。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.job_manager import JobStatus


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
    mock_models: list[str] = []
    created_at: str
    updated_at: str


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
