# -*- coding: utf-8 -*-
"""Qwen API client via OpenAI-compatible chat/completions (DashScope)."""

from __future__ import annotations

import time
import requests


class QwenClient:
    """Calls Qwen (DashScope) API with timeout, basic retry, and JSON extraction."""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 1024,
        timeout: int = 120,
        max_retries: int = 2,
    ) -> "ChatResponse":
        """Send a chat completion request. Retries on network errors only."""
        from app.v4.llm.factory import MAX_TOKEN_CAP, ChatResponse

        # Idempotent /v1拼接：若 base_url 已经以 /v1 收尾则不再叠一次
        base = self._base_url
        if base.endswith("/v1"):
            base = base[: -3]
        url = f"{base}/v1/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                token_budget = max_tokens
                request_timeout = timeout  # 截断重试翻倍预算时同步放大，见下
                for _ in range(3):
                    payload["max_tokens"] = token_budget
                    resp = requests.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=request_timeout,
                    )
                    resp.raise_for_status()
                    body = resp.json()
                    content = body["choices"][0]["message"]["content"]
                    usage = body.get("usage", {})
                    finish_reason = body["choices"][0].get("finish_reason", "")
                    if finish_reason != "length" or token_budget >= MAX_TOKEN_CAP:
                        break
                    token_budget = min(token_budget * 2, MAX_TOKEN_CAP)
                    # 输出预算翻倍后生成耗时同步翻倍：仍用 120s 会让"翻倍必然超时"
                    # （16384 tokens 正常生成 >2min），超时即整链重试重来，大 case
                    # 反复截断→超时→空响应/error。timeout 随预算一起 ×2。
                    request_timeout = int(request_timeout * 2)
                    import sys
                    print(
                        f"  [qwen] WARNING: response truncated (finish_reason=length), "
                        f"retrying with max_tokens={token_budget}, "
                        f"timeout={request_timeout}s",
                        file=sys.stderr,
                    )
                return ChatResponse(content=content, usage=usage)
            except requests.RequestException as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(2.0)
                continue

        raise last_error  # type: ignore[misc]
