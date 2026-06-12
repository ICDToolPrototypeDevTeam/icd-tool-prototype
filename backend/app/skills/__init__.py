"""
skills/ 模块统一入口。

提供 load_skill 函数，加载 skill Markdown 文本资产。
"""

from pathlib import Path

SKILLS_DIR = Path(__file__).parent


def load_skill(name: str) -> str:
    """
    加载指定名称的 skill 文本资产。

    Args:
        name: skill 名称（不含 .md 后缀），如 "generation_skill"

    Returns:
        skill 文本内容
    """
    path = SKILLS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill not found: {path}")
    return path.read_text(encoding="utf-8")