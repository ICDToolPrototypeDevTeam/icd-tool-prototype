# -*- coding: utf-8 -*-
"""DeepSeek API client via OpenAI-compatible chat/completions."""

from __future__ import annotations

from app.v4.llm.deadline import Deadline


class DeepSeekClient:
    """Calls DeepSeek API with timeout, retry, and JSON extraction."""

    PROVIDER = "deepseek"

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

        # 幂等拼接 /v1：若 base_url 已经以 /v1 收尾则不再叠一次（修 Bug：.env 写成
        # https://api.deepseek.com/v1 + client 再拼 /v1 时变成 /v1/v1/chat/completions 404）
        base = self._base_url
        if base.endswith("/v1"):
            base = base[: -3]
        url = f"{base}/v1/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        # 思考模式由前端「模型设置」页控制，默认开启。
        # 开启：deepseek-v4-flash/pro 默认开启思考且 reasoning 计入 max_tokens；
        # 曾显式关闭思考换取低 error 率，但符号位/位对组装类等价推理质量下降
        # （covered 翻成 needs_review/inconsistent）。折中：开启思考 +
        # reasoning_effort=low，在 8192 预算内既保留关键推理又控制输出长度
        # （20-block case 实测 reasoning≈3k tokens、finish=stop）。
        # 关闭：只发 thinking disabled；无思考时 reasoning_effort 无意义，不发送。
        if self._thinking:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = "low"
        else:
            payload["thinking"] = {"type": "disabled"}

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
