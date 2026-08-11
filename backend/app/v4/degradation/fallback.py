# -*- coding: utf-8 -*-
"""Fallback result factory, exception classification, and custom errors."""

from __future__ import annotations

import json

import requests


def make_error_judgment(provider: str, reason: str, error_type: str) -> dict:
    """Build a standardized error judgment dict matching _judge_with_provider output."""
    return {
        "agent_name": provider,
        "coverage_status": "error",
        "difference_type": "",
        "missing_points": [],
        "inconsistent_points": [],
        "analysis": f"[{error_type}] {reason}",
        "suggested_action": "",
        "confidence": 0.0,
        "raw_response": "",
    }


def classify_exception(exc: Exception) -> str:
    """Map exception to error_type for circuit-breaker decision.

    Returns one of: TIMEOUT, NETWORK, AUTH, RATE_LIMITED, BAD_OUTPUT, UNKNOWN.
    """
    name = type(exc).__name__
    msg = str(exc).lower()

    if name == "TimeoutError" or "timeout" in msg:
        return "TIMEOUT"

    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", 0)
        if status in (401, 403):
            return "AUTH"
        if status == 429:
            return "RATE_LIMITED"

    if isinstance(exc, requests.RequestException):
        return "NETWORK"

    if isinstance(exc, (json.JSONDecodeError, KeyError, IndexError, ValueError)):
        return "BAD_OUTPUT"

    return "UNKNOWN"


class AllProvidersUnhealthyError(Exception):
    """Raised when every configured provider is in unhealthy state."""

    def __init__(self, unhealthy_providers: list[str]):
        self.unhealthy_providers = unhealthy_providers
        super().__init__(
            f"All providers unhealthy: {', '.join(unhealthy_providers)}"
        )
