"""
crew/ 模块统一入口。

提供三类智能体 stub 的统一调用接口：
- generate_candidates：生成两份候选结果
- review_candidates：对候选结果进行打分
- analyze_differences：进行差异比对
"""

from app.models import AgentScoreResult, DifferenceItem, EoICDCandidate, UnifiedInputPackage
from app.crew.candidate_generator import generate_candidates
from app.crew.candidate_reviewer import review_candidates
from app.crew.difference_analyzer import analyze_differences

__all__ = [
    "generate_candidates",
    "review_candidates",
    "analyze_differences",
]