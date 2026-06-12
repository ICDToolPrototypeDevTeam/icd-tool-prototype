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


class ParsedEoICD(BaseModel):
    """解析后的 EoICD 主文件结构"""
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
    """统一分析输入包"""
    eoicd: ParsedEoICD
    software_requirements: ParsedSoftwareRequirements
    job_id: str = ""


class EoICDCandidate(BaseModel):
    """EoICD 条目化需求候选结果"""
    candidate_id: str  # 如 "candidate-1", "candidate-2"
    entries: list[dict] = []  # 每个 entry 包含条目编号、需求描述、关联来源等
    summary: str = ""  # 候选结果摘要说明


class AgentScoreResult(BaseModel):
    """多智能体评分结果（crew/candidate_reviewer.py 输出）"""
    candidate_id: str
    score: float  # 0-100
    reasoning: str = ""  # 评分理由


class PythonRuleScoreResult(BaseModel):
    """Python 硬规则评分结果（scoring/scorer.py 输出）"""
    candidate_id: str
    score: float  # 0-100
    rule_details: dict = {}  # 各规则维度得分


class ScoredCandidate(BaseModel):
    """评分并融合后的候选结果"""
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
    """Pipeline 最终结果摘要"""
    candidate_count: int
    best_candidate_id: str
    best_candidate_summary: str
    difference_count: int
    requirements_docx_path: str = ""
    difference_report_docx_path: str = ""