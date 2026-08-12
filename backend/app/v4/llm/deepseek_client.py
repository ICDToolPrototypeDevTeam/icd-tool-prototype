# -*- coding: utf-8 -*-
"""DeepSeek API client via OpenAI-compatible chat/completions."""

from __future__ import annotations

import json
import time
import requests


class DeepSeekClient:
    """Calls DeepSeek API with timeout, basic retry, and JSON extraction."""

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
    ) -> ChatResponse:
        """Send a chat completion request. Retries on network errors only."""
        from app.v4.llm.factory import ChatResponse

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
            "max_tokens": max_tokens,
        }

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                token_budget = max_tokens
                for _ in range(3):
                    payload["max_tokens"] = token_budget
                    resp = requests.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=timeout,
                    )
                    resp.raise_for_status()
                    body = resp.json()
                    content = body["choices"][0]["message"]["content"]
                    usage = body.get("usage", {})
                    finish_reason = body["choices"][0].get("finish_reason", "")
                    if finish_reason != "length" or token_budget >= 16384:
                        break
                    token_budget = min(token_budget * 2, 16384)
                    import sys
                    print(
                        f"  [deepseek] WARNING: response truncated (finish_reason=length), "
                        f"retrying with max_tokens={token_budget}",
                        file=sys.stderr,
                    )
                return ChatResponse(content=content, usage=usage)
            except requests.RequestException as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(2.0)
                continue

        raise last_error  # type: ignore[misc]
