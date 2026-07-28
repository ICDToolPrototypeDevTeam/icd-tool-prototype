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
        timeout: int = 60,
        max_retries: int = 2,
    ) -> ChatResponse:
        """Send a chat completion request. Retries on network errors only."""
        from app.llm.factory import ChatResponse

        url = f"{self._base_url}/v1/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error = None
        for attempt in range(max_retries + 1):
            try:
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
                return ChatResponse(content=content, usage=usage)
            except requests.RequestException as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(2.0)
                continue

        raise last_error  # type: ignore[misc]
