"""
crew/ 模块统一入口（chunk-level CrewAI 编排）。

提供：
- generate_for_chunk(chunk)：对单个 EoICD chunk 生成 MiniMax + DeepSeek 候选
- review_for_chunk(chunk, minimax_cand, deepseek_cand)：对单个 chunk 的两份候选评分
- analyze_differences(merged, sw_req)：对最终最优条目化需求 vs 软件高层需求做差异比对
"""

from app.crew.candidate_generator import generate_for_chunk
from app.crew.candidate_reviewer import review_for_chunk
from app.crew.difference_analyzer import analyze_differences

__all__ = [
    "generate_for_chunk",
    "review_for_chunk",
    "analyze_differences",
]
