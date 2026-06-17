"""
prompt_loader.py —— 在 Python 端组装 Agent / Task 上下文。

按用户确认意见：
- 不修改 prompts/*.md / skills/*.md 文本资产（占位符也不加）
- 在 Python 端把 prompt / skill 文本 + 运行时上下文（chunk、candidate、merged 等）拼接
- 用 lru_cache 缓存 Markdown 文件
"""

from __future__ import annotations

import json
from typing import Any

from app.prompts import load_prompt
from app.skills import load_skill


def build_agent_context(skill_name: str) -> str:
    """Agent.backstory 使用：直接加载 skill 文本，不修改。

    CrewAI 会把 backstory 作为 Agent 角色说明发给模型。
    """
    return load_skill(skill_name)


def build_task_context(prompt_name: str, **context: Any) -> str:
    """Task.description 使用：prompt 文本 + 追加 Python 端上下文。

    Args:
        prompt_name: prompts/<prompt_name>.md（不含后缀）
        **context: 运行时上下文（chunk / candidate / merged / sw_req 等）
                   会被格式化为 Markdown 片段追加到 prompt 之后。

    Returns:
        完整 description 字符串
    """
    prompt_text = load_prompt(prompt_name)
    if not context:
        return prompt_text

    parts = [prompt_text, "\n\n## 运行时上下文（由 Python 端注入）\n"]
    for key, value in context.items():
        parts.append(f"### {key}\n")
        parts.append(_format_value(value))
        parts.append("\n")
    return "".join(parts)


def _format_value(value: Any) -> str:
    """把运行时上下文格式化为可读 Markdown 片段。"""
    if value is None:
        return "（无）"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        if not value:
            return "（空列表）"
        return "\n".join(f"- {_format_value(v)}" for v in value)
    if isinstance(value, dict):
        return "\n".join(f"- **{k}**: {_format_value(v)}" for k, v in value.items())
    if hasattr(value, "model_dump"):
        return _format_value(value.model_dump())
    return str(value)


def dump_json(value: Any) -> str:
    """把对象序列化为 JSON 字符串，便于嵌入 Task.description 上下文。"""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, ensure_ascii=False, indent=2)
