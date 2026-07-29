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

    Supported providers: deepseek, minimax, qwen

    USE_MOCK_LLM=1 overrides all providers and returns MockLLMClient.
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

    if provider == "qwen":
        from app.v4.llm.qwen_client import QwenClient
        api_key = os.getenv("QWEN_API_KEY", "")
        if not api_key:
            raise ValueError(
                "QWEN_API_KEY not set. Check your .env file "
                "or set USE_MOCK_LLM=1 for offline development."
            )
        return QwenClient(
            api_key=api_key,
            base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            model=os.getenv("QWEN_MODEL", "qwen-plus"),
        )

    if provider == "minimax":
        from app.v4.llm.minimax_client import MiniMaxClient
        api_key = os.getenv("MINIMAX_API_KEY", "")
        if not api_key:
            raise ValueError(
                "MINIMAX_API_KEY not set. Check your .env file "
                "or set USE_MOCK_LLM=1 for offline development."
            )
        return MiniMaxClient(
            api_key=api_key,
            base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat"),
            model=os.getenv("MINIMAX_MODEL", "abab7-chat"),
        )

    raise ValueError(
        f"Unknown provider: {provider!r}, available: deepseek, minimax, qwen"
    )


def get_available_providers() -> list[str]:
    """Return providers whose API keys are configured in env.

    Used for auto-degradation: if only 1 provider is available,
    skip review agent (no consensus needed).
    """
    key_map = {
        "deepseek": "DEEPSEEK_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "qwen": "QWEN_API_KEY",
    }
    available = []
    for provider, env_key in key_map.items():
        if os.getenv(env_key):
            available.append(provider)
    return available
