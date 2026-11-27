# -*- coding: utf-8 -*-
"""Deadline abstraction shared by LLM transports and per-case budgets."""

from __future__ import annotations

import time
from typing import Protocol


class Deadline(Protocol):
    def hard_expired(self) -> bool: ...

    def remaining_hard(self) -> float: ...


class StaticDeadline:
    """A fixed wall-clock deadline; used when no case budget is supplied."""

    def __init__(self, at: float) -> None:
        self._at = at

    def hard_expired(self) -> bool:
        return time.monotonic() >= self._at

    def remaining_hard(self) -> float:
        return max(0.0, self._at - time.monotonic())
