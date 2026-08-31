# -*- coding: utf-8 -*-
"""HLR parser abstract base — unified contract for all HLR parsers.

Existing ``HLRWordParser`` already satisfies this interface (duck-typed);
no changes are required for it. New parsers (e.g. ``HLRExcelParser`` for
RPDU) inherit from this class to gain type-level conformance and to
participate in ``create_hlr_parser(source_path)`` dispatch.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.v4.models import HLROutput


class HLRParserBase(ABC):
    """Abstract base for every HLR parser implementation."""

    def __init__(self, source_path: Path):
        self.source_path = source_path
        self.source_name = source_path.name

    @abstractmethod
    def parse(self) -> HLROutput:
        """Parse the HLR source and return a standard ``HLROutput``."""
        raise NotImplementedError