# -*- coding: utf-8 -*-
"""V4 parser subpackage — HLR / EoICD input parsers.

The HLR parser family supports two implementations dispatched by
``create_hlr_parser``:

  - ``.docx`` -> ``HLRWordParser`` (existing, profile-driven).
  - ``.xlsx`` -> ``HLRExcelParser`` (RPDU-style controllers).

EoICD Excel parsing is unchanged (``EoICDExcelParser``).
"""

from app.v4.parsers.hlr_parser_base import HLRParserBase
from app.v4.parsers.hlr_parser_factory import (
    create_hlr_parser,
    registered_extensions,
)

__all__ = [
    "HLRParserBase",
    "create_hlr_parser",
    "registered_extensions",
]