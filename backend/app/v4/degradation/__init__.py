# -*- coding: utf-8 -*-
"""Multi-agent degradation handling: timeout, circuit breaker, review downgrade."""

from app.v4.degradation.config import DegradationConfig
from app.v4.degradation.fallback import (
    AllProvidersUnhealthyError,
    classify_exception,
    make_error_judgment,
)
from app.v4.degradation.context import DegradationContext

__all__ = [
    "DegradationConfig",
    "DegradationContext",
    "AllProvidersUnhealthyError",
    "classify_exception",
    "make_error_judgment",
]
