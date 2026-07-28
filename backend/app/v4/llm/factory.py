# -*- coding: utf-8 -*-
"""LLM factory: provider-agnostic LLM client creation."""

from __future__ import annotations

import os
from typing import Protocol, TypedDict


class ChatResponse(TypedDict):
    content: str
    usage: dict


class LLMClient(Protocol):
    """Unified LLM call interface. All providers implement this."""

    def chat(self, messages: list[dict], **kwargs) -> ChatResponse:
        """Send messages and return content + usage info."""
        ...


def use_mock_llm() -> bool:
    """USE_MOCK_LLM=1 -> True, default 0."""
    return os.getenv("USE_MOCK_LLM", "0") == "1"


def get_llm(provider: str = "deepseek") -> LLMClient:
    """Create an LLM client by provider name.

    Supported providers: deepseek, minimax, qwen, mock

    USE_MOCK_LLM=1 overrides all providers and returns MockLLMClient.

    Minimax and Qwen currently return MockLLMClient (Phase 2-3 scaffolding).
    Provide real API keys to enable them.
    """
    if use_mock_llm():
        from app.v4.llm.mock_llm import MockLLMClient
        return MockLLMClient()

    if provider == "deepseek":
        from app.v4.llm.deepseek_client import DeepSeekClient
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY not set. Check your .env file "
                "or set USE_MOCK_LLM=1 for offline development."
            )
        return DeepSeekClient(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        )

    # Phase 2-3 scaffolding: minimax and qwen return mock for now
    if provider in ("minimax", "qwen"):
        from app.v4.llm.mock_llm import MockLLMClient
        return MockLLMClient()

    raise ValueError(
        f"Unknown provider: {provider!r}, available: deepseek, minimax, qwen"
    )
