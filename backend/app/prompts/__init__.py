"""
prompts/ 模块统一入口。

提供 load_prompt 函数，加载 prompt Markdown 文本资产。
"""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """
    加载指定名称的 prompt 文本资产。

    Args:
        name: prompt 名称（不含 .md 后缀），如 "generation_prompt"

    Returns:
        prompt 文本内容
    """
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")