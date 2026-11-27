# -*- coding: utf-8 -*-
"""MiniMax API client via OpenAI-compatible chat/completions."""

from __future__ import annotations

from app.v4.llm.deadline import Deadline


class MiniMaxClient:
    """Calls MiniMax API with timeout, retry, and JSON extraction."""

    PROVIDER = "minimax"

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

        # 幂等拼接 /v1：若 base_url 已经以 /v1 收尾则不再叠一次
        base = self._base_url
        if base.endswith("/v1"):
            base = base[: -3]
        url = f"{base}/v1/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        # 本通道计划切换为 Qwen 兼容接口：按 Qwen 的 enable_thinking 协议发送。
        # 开启时保持历史请求体不变（不发送 enable_thinking）；关闭时发送 false。
        # 当前真实 MiniMax OpenAI 兼容接口会忽略该字段（思考无法关闭），切换
        # 到 Qwen 接口后生效；PROVIDER 仍为 minimax，token 记账与报告标签不变。
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
