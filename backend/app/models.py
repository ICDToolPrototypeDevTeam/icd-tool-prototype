from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class JobStatus(str, Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'


class AnalyzeResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class JobResultSummary(BaseModel):
    requirement_count: int
    difference_count: int


class JobOutputs(BaseModel):
    requirements_docx: bool
    difference_report_docx: bool
    minimax_docx: bool = False
    deepseek_docx: bool = False


class JobResultResponse(BaseModel):
    job_id: str
    status: str
    summary: JobResultSummary
    outputs: JobOutputs


# ============================================================================
# 端到端原型数据模型
# ============================================================================


class ParsedEoICDInterface(BaseModel):
    """解析后的 EoICD 单个接口信息"""
    interface_name: str
    interface_direction: str  # 如 "发送"、"接收"、"双向"
    signal_name: str
    data_type: str  # 如 "boolean", "uint8", "uint16", "float"
    transfer_cycle: Optional[str] = None  # 如 "10ms", "100ms", "事件触发"
    source_file: str  # 来源文件路径
    description: Optional[str] = None


class EoICDChunk(BaseModel):
    """解析后的 EoICD chunk 单元。

    本 Issue 默认一个 EoICD 主文件整体封装为一个 chunk-001。
    后续 parser 升级为多 chunk 时，下游 crew / scoring / docx 不需要大改。
    """
    chunk_id: str  # 如 "chunk-001"
    chunk_title: str = ""  # chunk 标题，便于人工阅读
    source_file: str = ""  # 来源文件路径
    source_section: str = ""  # 来源章节描述
    source_page_range: str = ""  # 来源页码范围或等效信息
    content: str = ""  # chunk 文本内容（结构化或半结构化）
    tables: list[dict] = []  # 结构化表格信息（如 Excel sheet 列表）
    interfaces: list[ParsedEoICDInterface] = []  # 解析得到的接口列表
    context_summary: str = ""  # chunk 的简要摘要，便于模型快速理解


class ParsedEoICD(BaseModel):
    """解析后的 EoICD 主文件结构（保留向后兼容，新流程使用 EoICDChunk 列表）"""
    interfaces: list[ParsedEoICDInterface] = []
    source_file: str = ""


class ParsedSoftwareRequirement(BaseModel):
    """解析后的软件高层需求条目"""
    requirement_id: str
    requirement_text: str
    source_file: str = ""


class ParsedSoftwareRequirements(BaseModel):
    """解析后的软件高层需求集合"""
    requirements: list[ParsedSoftwareRequirement] = []


class UnifiedInputPackage(BaseModel):
    """统一分析输入包（chunk-level 版本）"""
    eoicd_chunks: list[EoICDChunk] = []  # 当前阶段默认 1 个 chunk-001
    software_requirements: ParsedSoftwareRequirements
    job_id: str = ""


# ============================================================================
# Chunk-level 多智能体输出模型
# ============================================================================


class ChunkCandidate(BaseModel):
    """单个 chunk 的 EoICD 条目化需求候选结果

    字段与 CrewAI Task.output_pydantic 严格对齐，便于 mock LLM 输出校验。
    """
    model_config = {"protected_namespaces": ()}

    candidate_id: str  # 如 "chunk-001@minimax"
    chunk_id: str
    model_name: str  # "MiniMax" 或 "DeepSeek"
    entries: list[dict] = []  # 每条含 entry_id/description/interface_name/signal_name/source
    summary: str = ""  # 该候选的简要说明
    source_chunk_id: str = ""  # 来源 chunk，便于追溯


class ChunkAgentScoreResult(BaseModel):
    """单个 chunk 内某条候选的 agent 评分结果"""
    candidate_id: str
    chunk_id: str
    score: float  # 0-100
    reasoning: str = ""
    recommended_is_best: bool = False  # 该 agent 是否推荐本候选为最佳


class ChunkPythonScoreResult(BaseModel):
    """单个 chunk 内某条候选的 Python 硬规则评分结果"""
    candidate_id: str
    chunk_id: str
    score: float  # 0-100
    rule_details: dict = {}  # 各规则维度得分


class BestChunkResult(BaseModel):
    """单个 chunk 的最佳候选（agent score + python rule 融合后）"""
    chunk_id: str
    candidate: ChunkCandidate
    agent_score: ChunkAgentScoreResult
    python_rule_score: ChunkPythonScoreResult
    final_score: float
    is_best: bool = True


# ============================================================================
# CrewAI Task output_pydantic 模型
# ============================================================================


class GenerationOutput(BaseModel):
    """generation Task 输出（crew.ai Task.output_pydantic）

    包含单个 chunk 的条目化需求候选结果。
    """
    model_config = {"protected_namespaces": ()}

    candidate_id: str
    chunk_id: str
    model_name: str
    entries: list[dict] = []
    summary: str = ""


class ScoringEntry(BaseModel):
    """scoring Task 单条评分输出"""
    candidate_id: str
    score: float  # 0-100
    reasoning: str = ""
    recommended_is_best: bool = False


class ScoringOutput(BaseModel):
    """scoring Task 输出

    对同一个 chunk 的 MiniMax 候选和 DeepSeek 候选同时评分。
    """
    scores: list[ScoringEntry] = []


class DifferenceEntry(BaseModel):
    """comparison Task 单条差异输出"""
    difference_id: str
    difference_type: str  # 缺失/不一致/冗余/需确认
    requirement_text: str = ""
    software_requirement_text: str = ""
    description: str = ""
    suggested_action: str = ""


class ComparisonOutput(BaseModel):
    """comparison Task 输出"""
    differences: list[DifferenceEntry] = []


# ============================================================================
# 跨 chunk 合并模型
# ============================================================================


class ModelRequirementResult(BaseModel):
    """某个模型（MiniMax 或 DeepSeek）跨所有 chunk 的合并结果"""
    model_config = {"protected_namespaces": ()}

    model_name: str  # "MiniMax" 或 "DeepSeek"
    entries: list[dict] = []  # 全量条目（含 source_chunk_id）
    summary: str = ""


class MergedRequirementResult(BaseModel):
    """所有 chunk 最佳候选的合并结果（最终最优 EoICD 条目化需求）"""
    entries: list[dict] = []
    summary: str = ""
    chunk_count: int = 0
    best_per_chunk: list[BestChunkResult] = []


class ComparisonReportResult(BaseModel):
    """最终差异报告模型"""
    differences: list["DifferenceItem"] = []


# ============================================================================
# 向后兼容旧模型（保留）
# ============================================================================


class EoICDCandidate(BaseModel):
    """EoICD 条目化需求候选结果（旧版，保留向后兼容）"""
    candidate_id: str  # 如 "candidate-1", "candidate-2"
    entries: list[dict] = []  # 每个 entry 包含条目编号、需求描述、关联来源等
    summary: str = ""  # 候选结果摘要说明


class AgentScoreResult(BaseModel):
    """多智能体评分结果（保留旧版）"""
    candidate_id: str
    score: float  # 0-100
    reasoning: str = ""  # 评分理由


class PythonRuleScoreResult(BaseModel):
    """Python 硬规则评分结果（保留旧版）"""
    candidate_id: str
    score: float  # 0-100
    rule_details: dict = {}  # 各规则维度得分


class ScoredCandidate(BaseModel):
    """评分并融合后的候选结果（保留旧版）"""
    candidate: EoICDCandidate
    agent_score: AgentScoreResult
    python_rule_score: PythonRuleScoreResult
    final_score: float  # 融合后的最终分数
    is_best: bool = False


class DifferenceItem(BaseModel):
    """差异比对结果条目"""
    difference_id: str  # 如 "diff-1"
    difference_type: str  # 如 "缺失", "不一致", "需确认"
    requirement_text: str = ""  # EoICD 条目化需求原文
    software_requirement_text: str = ""  # 软件高层需求原文
    description: str = ""  # 差异描述
    suggested_action: str = ""  # 建议处理方式


class PipelineResult(BaseModel):
    """Pipeline 最终结果摘要（chunk-level 扩展）"""
    candidate_count: int
    best_candidate_id: str
    best_candidate_summary: str
    difference_count: int
    chunk_count: int = 0
    minimax_docx_path: str = ""
    deepseek_docx_path: str = ""
    best_docx_path: str = ""
    requirements_docx_path: str = ""  # 向后兼容：同 best_docx_path
    difference_report_docx_path: str = ""