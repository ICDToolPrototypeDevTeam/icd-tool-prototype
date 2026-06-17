"""
crew/agents.py —— CrewAI Agent 工厂。

5 个 Agent：
- MiniMax generation agent
- DeepSeek generation agent
- MiniMax scoring agent
- DeepSeek scoring agent
- DeepSeek comparison agent

注意：
- 不修改 prompts/*.md / skills/*.md 文本资产
- backstory 直接引用对应 skill 文本
- llm 来自 app.llm 工厂（env 驱动 + mock fallback）
- mock 模式下，每个 agent builder 会显式覆盖 MockLLM.role，
  让 MockLLM 知道当前应输出哪种结构的 JSON。
"""

from __future__ import annotations

from crewai import Agent  # type: ignore

from app.llm import get_deepseek_llm, get_minimax_llm
from app.llm.mock_llm import MockLLM
from app.llm.prompt_loader import build_agent_context
from app.llm.factory import crewai_verbose


def _common_agent_kwargs() -> dict:
    """5 个 Agent 共享的 kwargs。"""
    return {
        "verbose": crewai_verbose(),
        "allow_delegation": False,
    }


def _llm_with_role(llm, role: str):
    """如果是 MockLLM，覆盖其 role；真实 LLM 保持原样。"""
    if isinstance(llm, MockLLM):
        llm.role = role
    return llm


def build_minimax_generation_agent() -> Agent:
    """MiniMax generation agent。"""
    return Agent(
        role="EoICD 条目化需求生成专家（MiniMax）",
        goal="基于当前 EoICD chunk 生成结构化、可追溯的条目化需求候选结果",
        backstory=build_agent_context("generation_skill"),
        llm=_llm_with_role(get_minimax_llm(), "minimax_generation"),
        **_common_agent_kwargs(),
    )


def build_deepseek_generation_agent() -> Agent:
    """DeepSeek generation agent。"""
    return Agent(
        role="EoICD 条目化需求生成专家（DeepSeek）",
        goal="基于当前 EoICD chunk 生成结构化、可追溯的条目化需求候选结果",
        backstory=build_agent_context("generation_skill"),
        llm=_llm_with_role(get_deepseek_llm(), "deepseek_generation"),
        **_common_agent_kwargs(),
    )


def build_minimax_scoring_agent() -> Agent:
    """MiniMax scoring agent。"""
    return Agent(
        role="EoICD 条目化需求质量评估专家（MiniMax）",
        goal="对同一 chunk 的两份候选结果进行客观评分，给出推荐",
        backstory=build_agent_context("scoring_skill"),
        llm=_llm_with_role(get_minimax_llm(), "minimax_scoring"),
        **_common_agent_kwargs(),
    )


def build_deepseek_scoring_agent() -> Agent:
    """DeepSeek scoring agent。"""
    return Agent(
        role="EoICD 条目化需求质量评估专家（DeepSeek）",
        goal="对同一 chunk 的两份候选结果进行客观评分，给出推荐",
        backstory=build_agent_context("scoring_skill"),
        llm=_llm_with_role(get_deepseek_llm(), "deepseek_scoring"),
        **_common_agent_kwargs(),
    )


def build_deepseek_comparison_agent() -> Agent:
    """DeepSeek comparison agent。"""
    return Agent(
        role="EoICD 与软件高层需求差异分析专家（DeepSeek）",
        goal="识别最终最优 EoICD 条目化需求与软件高层需求之间的差异项",
        backstory=build_agent_context("comparison_skill"),
        llm=_llm_with_role(get_deepseek_llm(), "deepseek_comparison"),
        **_common_agent_kwargs(),
    )
