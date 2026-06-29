"""
EoICD 主文件解析模块。

当前作为兼容入口，委托给 eoicd_word_parser 进行真实 Word 解析。
"""

from pathlib import Path

from app.models import EoICDChunk
from app.parsers.eoicd_word_parser import parse_eoicd_word as _parse_word


def parse_eoicd_word(word_path: Path) -> list[EoICDChunk]:
    """解析 EoICD Word 主文件，返回 EoICDChunk 列表。

    委托给 eoicd_word_parser 进行真实解析（支持 .doc 和 .docx）。

    Args:
        word_path: EoICD Word 文件路径

    Returns:
        EoICDChunk 列表（当前版本默认 1 个 chunk-001）
    """
    return _parse_word(word_path)
