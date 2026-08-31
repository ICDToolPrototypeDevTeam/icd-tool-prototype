# -*- coding: utf-8 -*-
"""HLR parser factory — pick the right parser based on file extension.

Adding a new device/format:
  1. Implement a new parser class (subclass ``HLRParserBase``, implement
     ``parse()``).
  2. Register the extension -> parser-class mapping below.

No changes required to ``pipeline``, ``cli``, or existing parsers.
"""
from __future__ import annotations

from pathlib import Path

from app.v4.parsers.hlr_excel_parser import HLRExcelParser
from app.v4.parsers.hlr_parser_base import HLRParserBase
from app.v4.parsers.hlr_word_parser import HLRWordParser

# Extension -> parser class registry.
_PARSER_REGISTRY: dict[str, type[HLRParserBase]] = {
    ".docx": HLRWordParser,  # duck-typed; signature accepts (Path, profile=).
    ".xlsx": HLRExcelParser,
}


def create_hlr_parser(
    source_path: Path,
    profile=None,
) -> HLRParserBase:
    """Create the parser that handles ``source_path``'s extension.

    Args:
        source_path: HLR file path (.docx or .xlsx).
        profile: Optional ``ControllerProfile`` passed to ``HLRWordParser``
            for field-map resolution. Ignored by ``HLRExcelParser`` (which
            has a fixed column mapping).

    Returns:
        A parser instance ready for ``.parse()``.

    Raises:
        ValueError: if the extension is not registered.
    """
    ext = source_path.suffix.lower()
    parser_cls = _PARSER_REGISTRY.get(ext)

    if parser_cls is None:
        supported = ", ".join(sorted(_PARSER_REGISTRY.keys()))
        raise ValueError(
            f"Unsupported HLR file format: {ext} (file: {source_path.name}). "
            f"Supported: {supported}"
        )

    # ``HLRWordParser`` requires a profile; ``HLRExcelParser`` ignores it.
    if parser_cls is HLRWordParser:
        if profile is None:
            raise ValueError(
                "HLRWordParser requires a ControllerProfile (profile=None given)"
            )
        return parser_cls(source_path, profile=profile)
    return parser_cls(source_path)


def registered_extensions() -> tuple[str, ...]:
    """Return the sorted tuple of extensions supported by the factory."""
    return tuple(sorted(_PARSER_REGISTRY.keys()))