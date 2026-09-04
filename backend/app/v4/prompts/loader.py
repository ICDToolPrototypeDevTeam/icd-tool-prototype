# -*- coding: utf-8 -*-
"""Prompt loader: reads .md files from the prompts/ directory."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Load a prompt template by name.

    name ∈ {"reverse_judge", "reverse_judge_rpdu", "consensus", "re_review"}

    Raises FileNotFoundError if the .md file does not exist.
    """
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")
