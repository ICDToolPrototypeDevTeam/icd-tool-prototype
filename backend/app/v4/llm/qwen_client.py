# -*- coding: utf-8 -*-
"""Qwen API client via OpenAI-compatible chat/completions (DashScope)."""

from __future__ import annotations

from app.v4.llm.deadline import Deadline


class QwenClient:
    """Calls Qwen (DashScope) API with timeout, retry, and JSON extraction."""

    PROVIDER = "qwen"

    def __init__(self, api_key: str, base_url: str, model: str,
                 default_timeout: int = 120, thinking: bool = True):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._default_timeout = default_timeout
        self._thinking = thinking

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 1024,
        timeout: int | None = None,
        max_retries: int = 1,
        deadline: Deadline | None = None,
        stream: bool | None = None,
    ) -> "ChatResponse":
        """Send a chat completion request. Retries on network errors only."""
        from app.v4.llm.factory import ChatResponse, llm_stream_for
        from app.v4.llm.streaming import chat_with_retry

        if timeout is None:
            timeout = self._default_timeout
        if stream is None:
            stream = llm_stream_for(self.PROVIDER)

        # Idempotent /v1拼接：若 base_url 已经以 /v1 收尾则不再叠一次
        base = self._base_url
        if base.endswith("/v1"):
            base = base[: -3]
        url = f"{base}/v1/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        # 思考模式由前端「模型设置」页控制：Qwen 默认开启，因此开启时保持
        # 历史请求体不变（不发送 enable_thinking）；关闭时显式发送 false。
        # 不发送 reasoning_effort——当前默认行为即不指定档位。
        # 关闭时同时发两处：DashScope 兼容接口认顶层 enable_thinking；自建
        # vLLM / SGLang 只认 chat_template_kwargs，顶层字段会被静默忽略。
        if not self._thinking:
            payload["enable_thinking"] = False
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        return chat_with_retry(
            url,
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self.PROVIDER,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
            deadline=deadline,
            stream=stream,
        )
