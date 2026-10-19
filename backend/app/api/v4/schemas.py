# -*- coding: utf-8 -*-
"""V4.0 FastAPI Pydantic Schemas。

ADR-001 Issue A：
- V4Job* 响应 schema 不与 V3 Job* schema 互通（独立 import、无依赖）；
- `mock_models` 取值规则严格按 ADR-001 D5。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

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


class V4TokenUsage(BaseModel):
    """任务累计 token 用量（只对外暴露总量）。

    ``None``（响应中该字段为 null）= 该任务没有统计口径（旧任务 / CLI）；
    ``{"total_tokens": 0}`` = 已统计但确实为 0（如 MOCK 模式）。前端据此
    区分「显示为空」与「显示 0」。
    """

    total_tokens: int = 0


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
    # 累计 token 用量；旧任务 / 尚未开始统计时为 None
    token_usage: Optional[V4TokenUsage] = None
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
    # 累计 token 用量；旧任务为 None
    token_usage: Optional[V4TokenUsage] = None


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
    # 累计 token 用量；旧任务为 None
    token_usage: Optional[V4TokenUsage] = None


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
    # 目录里是否有运行日志（job.log）。**不属于** outputs：那是「七类对外产物」的
    # 词汇表，而日志是任务跑过就有的过程记录 —— 失败 / 中断的任务往往一个产物都
    # 没有，日志却是它唯一能给出的东西，前端据此单独给一个下载入口。
    has_log: bool = False
    # 目录里是否有反向匹配中间产物（output/reverse_matches.json，Step 3 输出）。
    # 同样不属于 outputs；按 ADR-001 D7 预留的「新开子路由」方式为它单独开了
    # 只读查看入口（GET /jobs/{job_id}/intermediates/reverse-matches），其余
    # 6 类中间产物仍不暴露。只有反向任务会写它，正向任务恒为 False。
    has_reverse_matches: bool = False
    # 本次运行是否 MOCK（含义同状态接口：模拟数据不可用于验收）
    mock: bool = False
    # 累计 token 用量；旧 manifest 无该字段时为 None
    token_usage: Optional[V4TokenUsage] = None


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


# ============================================================================
# 运行时模型设置
# ============================================================================


class V4ModelSettingsUpdate(BaseModel):
    """PUT /api/v4/settings 请求体：三个模型名全量提交。

    ``str_strip_whitespace`` 先去空格再校验长度，避免「只输空格」通过
    ``min_length``；落盘前 ``runtime_settings`` 还会再做一次同样的校验。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    deepseek_model: str = Field(min_length=1, max_length=200)
    minimax_model: str = Field(min_length=1, max_length=200)
    qwen_model: str = Field(min_length=1, max_length=200)


class V4ModelSettings(BaseModel):
    """GET/PUT /api/v4/settings 响应：当前生效值 + 是否自定义 + 保存时间。"""

    deepseek_model: str
    minimax_model: str
    qwen_model: str
    # False = 沿用部署配置（backend/.env）或 factory 默认值
    customized: bool = False
    # 仅 customized=True 时有值（ISO 8601，带 +08:00 偏移）
    updated_at: Optional[str] = None
