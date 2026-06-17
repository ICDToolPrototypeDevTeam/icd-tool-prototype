"""
llm/ 模块统一入口。

提供：
- get_minimax_llm() / get_deepseek_llm()：根据环境变量返回 LLM 实例
- MockLLM：结构化 mock LLM（不联网、可复现）
- build_agent_context()：在 Python 端组装传给 Agent / Task 的上下文，
  不修改原 prompts/*.md / skills/*.md 文本资产
"""

from app.llm.factory import get_deepseek_llm, get_minimax_llm
from app.llm.mock_llm import MockLLM
from app.llm.prompt_loader import build_agent_context, build_task_context

__all__ = [
    "get_minimax_llm",
    "get_deepseek_llm",
    "MockLLM",
    "build_agent_context",
    "build_task_context",
]
