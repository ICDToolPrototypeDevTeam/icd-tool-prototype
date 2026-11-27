# -*- coding: utf-8 -*-
"""OpenAI-compatible chat completions transport with optional SSE streaming.

The transport owns exactly one HTTP request.  Retry policy (transport retries,
truncation retries, max_tokens doubling) lives in :func:`chat_with_retry` so
the semantic layer never multiplies attempts again.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import requests
from requests.exceptions import (
    ChunkedEncodingError,
    ConnectionError as RequestsConnectionError,
    RequestException,
    Timeout as RequestsTimeout,
)

from app.token_usage import record_token_usage
from app.v4.llm.deadline import Deadline, StaticDeadline
from app.v4.llm.factory import ChatResponse


class StreamError(Exception):
    def __init__(self, message: str, error_type: str = "UNKNOWN"):
        super().__init__(message)
        self.error_type = error_type


class StreamHTTPError(StreamError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"HTTP {status_code}: {body[:200]}", "NETWORK")
        self.status_code = status_code
        self.body = body


class StreamInterrupted(StreamError):
    """The stream started but failed before a usable completion arrived."""


class StreamAborted(StreamError):
    """The caller's total budget expired; stop without retrying."""

    def __init__(self, message: str):
        super().__init__(message, "STREAM_ABORTED")


def _response_text(resp: Any) -> str:
    try:
        return resp.text or ""
    except Exception:  # noqa: BLE001 — body text is diagnostic only
        return ""


def _completion_from_json(
    data: Any, provider: str, *, streamed: bool
) -> ChatResponse:
    """Build a ChatResponse from a non-streaming completion JSON body."""
    try:
        choice = (data.get("choices") or [])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        finish_reason = choice.get("finish_reason") or ""
        usage = data.get("usage") or {}
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise StreamInterrupted(f"invalid response shape: {exc}", "BAD_OUTPUT") from exc

    record_token_usage(usage, provider=provider)
    return ChatResponse(
        content=content,
        usage=usage,
        finish_reason=finish_reason,
        reasoning_content=reasoning,
        streamed=streamed,
    )


def _caused_by_timeout(exc: BaseException) -> bool:
    """Detect a mid-stream read timeout wrapped by requests.

    ``requests.Response.iter_content`` converts urllib3's
    ``ReadTimeoutError`` into ``requests.ConnectionError``, so the outer
    exception type alone cannot distinguish a slow/stalled gateway from a
    refused connection. Walk the cause chain and fall back to the message.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (RequestsTimeout, TimeoutError)):
            return True
        text = str(current).lower()
        if "timed out" in text or "timeout" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def _request_timeout(
    read_timeout: float,
    connect_timeout: float,
    deadline: Deadline | None,
) -> tuple[float, float]:
    read = float(read_timeout)
    if deadline is not None:
        remaining = deadline.remaining_hard()
        if remaining <= 0:
            raise StreamAborted("total budget exhausted before request")
        read = min(read, max(1.0, remaining))
    return (float(connect_timeout), read)


def chat_completion(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    provider: str,
    *,
    deadline: Deadline | None,
    read_timeout: float,
    connect_timeout: float,
    stream: bool,
    include_usage: bool = True,
) -> ChatResponse:
    """Perform one chat-completion HTTP call (streaming or non-streaming)."""
    timeout = _request_timeout(read_timeout, connect_timeout, deadline)

    if not stream:
        return _non_stream(
            url, headers, payload, provider,
            deadline=deadline, timeout=timeout,
        )

    body = dict(payload)
    # The gateway selects SSE from the JSON body flag; requests' stream=True
    # only controls HTTP-level buffering on our side.
    body["stream"] = True
    if include_usage:
        body["stream_options"] = {"include_usage": True}
    return _stream(
        url, headers, body, provider,
        deadline=deadline, timeout=timeout,
    )


def _non_stream(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    provider: str,
    *,
    deadline: Deadline | None,
    timeout: tuple[float, float],
) -> ChatResponse:
    body = dict(payload)
    body.pop("stream_options", None)
    resp = requests.post(url, headers=headers, json=body, timeout=timeout, stream=False)
    try:
        if resp.status_code >= 400:
            raise StreamHTTPError(resp.status_code, _response_text(resp))
        try:
            data = resp.json()
        except (ValueError, TypeError) as exc:
            raise StreamInterrupted(f"invalid JSON response: {exc}", "BAD_OUTPUT") from exc
        return _completion_from_json(data, provider, streamed=False)
    except RequestsTimeout as exc:
        raise StreamInterrupted(str(exc), "TIMEOUT") from exc
    except RequestException as exc:
        raise StreamInterrupted(str(exc), "NETWORK") from exc
    finally:
        resp.close()


def _stream(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    provider: str,
    *,
    deadline: Deadline | None,
    timeout: tuple[float, float],
) -> ChatResponse:
    resp = requests.post(url, headers=headers, json=body, timeout=timeout, stream=True)
    try:
        # Some OpenAI-compatible gateways reject stream_options even though they
        # support streaming. Retry once without it before declaring streaming
        # unsupported.
        if resp.status_code == 400 and "stream_options" in body:
            resp.close()
            body = dict(body)
            body.pop("stream_options", None)
            resp = requests.post(
                url, headers=headers, json=body, timeout=timeout, stream=True,
            )

        if resp.status_code >= 400:
            raise StreamHTTPError(resp.status_code, _response_text(resp))
        if deadline is not None and deadline.hard_expired():
            raise StreamAborted("total budget exhausted while streaming")

        # Some self-hosted OpenAI-compatible gateways ignore the body
        # stream=true flag and answer with a plain completion. Parse it as a
        # non-stream response instead of silently returning an empty result
        # (the same silent-failure class as the ignored thinking switch).
        headers = getattr(resp, "headers", None) or {}
        content_type = (headers.get("Content-Type") or "").lower()
        if content_type and "text/event-stream" not in content_type:
            text = _response_text(resp)
            try:
                data = json.loads(text)
            except (ValueError, TypeError) as exc:
                raise StreamInterrupted(
                    f"stream requested but got non-SSE body: {text[:200]}",
                    "BAD_OUTPUT",
                ) from exc
            print(
                f"  [{provider}] WARNING: gateway ignored stream=true "
                f"(Content-Type={content_type!r}), falling back to non-stream parse",
                file=sys.stderr,
            )
            return _completion_from_json(data, provider, streamed=False)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: dict = {}
        finish_reason = ""

        for raw in resp.iter_lines():
            if deadline is not None and deadline.hard_expired():
                raise StreamAborted("total budget exhausted while streaming")
            if not raw:
                continue

            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue

            data_text = line[5:].strip()
            if data_text == "[DONE]":
                break
            try:
                chunk = json.loads(data_text)
            except json.JSONDecodeError:
                # Gateways occasionally emit a comment/keepalive line that still
                # starts with data:. One bad line must not discard the stream.
                continue
            if not isinstance(chunk, dict):
                continue

            if chunk.get("usage"):
                usage = chunk["usage"]

            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
            reasoning_piece = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning_piece:
                reasoning_parts.append(reasoning_piece)
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

        record_token_usage(usage, provider=provider)
        return ChatResponse(
            content="".join(content_parts),
            usage=usage,
            finish_reason=finish_reason,
            reasoning_content="".join(reasoning_parts),
            streamed=True,
        )
    except RequestsTimeout as exc:
        raise StreamInterrupted(str(exc), "TIMEOUT") from exc
    except ChunkedEncodingError as exc:
        error_type = "TIMEOUT" if _caused_by_timeout(exc) else "NETWORK"
        raise StreamInterrupted(str(exc), error_type) from exc
    except RequestsConnectionError as exc:
        error_type = "TIMEOUT" if _caused_by_timeout(exc) else "NETWORK"
        raise StreamInterrupted(str(exc), error_type) from exc
    except RequestException as exc:
        raise StreamInterrupted(str(exc), "NETWORK") from exc
    finally:
        resp.close()


def chat_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    provider: str,
    *,
    max_tokens: int,
    timeout: float,
    max_retries: int,
    deadline: Deadline | None,
    stream: bool,
) -> ChatResponse:
    """Run one chat completion with transport retries and truncation growth.

    ``max_retries`` counts *transport* retries only.  Truncated responses may
    grow ``max_tokens`` up to three times, bounded by the hard deadline.
    Read timeouts are raised immediately: retrying a slow model only burns the
    same budget again.

    ``timeout`` is the non-streaming whole-response read timeout.  Streaming
    is bounded per chunk by ``LLM_STREAM_READ_TIMEOUT`` instead, so a slow
    first token has its own budget.
    """
    from app.v4.llm.factory import (
        MAX_TOKEN_CAP,
        llm_connect_timeout_seconds,
        llm_stream_read_timeout_seconds,
        llm_total_budget_seconds,
    )

    if deadline is None:
        deadline = StaticDeadline(time.monotonic() + llm_total_budget_seconds())

    attempts = max(1, max_retries) + 1
    last_error: Exception | None = None

    for attempt in range(attempts):
        if deadline.hard_expired():
            raise StreamAborted("total budget exhausted before request")

        token_budget = max_tokens
        request_timeout = (
            llm_stream_read_timeout_seconds() if stream else timeout
        )
        try:
            for _ in range(3):
                if deadline.hard_expired():
                    raise StreamAborted(
                        "total budget exhausted before truncation retry"
                    )
                body = dict(payload)
                body["max_tokens"] = token_budget
                resp = chat_completion(
                    url,
                    headers,
                    body,
                    provider,
                    deadline=deadline,
                    read_timeout=request_timeout,
                    connect_timeout=llm_connect_timeout_seconds(),
                    stream=stream,
                    include_usage=True,
                )
                if (
                    resp["finish_reason"] != "length"
                    or token_budget >= MAX_TOKEN_CAP
                ):
                    return resp

                token_budget = min(token_budget * 2, MAX_TOKEN_CAP)
                if not stream:
                    # Preserve the historical non-streaming behaviour: grow the
                    # read timeout with the output budget, capped by the hard
                    # deadline so a truncation retry cannot outlive the case.
                    request_timeout = min(
                        int(request_timeout * 2),
                        max(1, int(deadline.remaining_hard())),
                    )
                print(
                    f"  [{provider}] WARNING: response truncated "
                    f"(finish_reason=length), retrying with "
                    f"max_tokens={token_budget}, timeout={request_timeout}s, "
                    f"stream={stream}",
                    file=sys.stderr,
                )
            return resp
        except StreamAborted:
            raise
        except StreamHTTPError as exc:
            if exc.status_code in (400, 401, 403, 404, 422):
                raise
            last_error = exc
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(2.0, deadline.remaining_hard()))
        except StreamInterrupted as exc:
            if exc.error_type in {"TIMEOUT", "BAD_OUTPUT"}:
                raise
            last_error = exc
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(2.0, deadline.remaining_hard()))
        except RequestsTimeout:
            raise
        except RequestException as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(2.0, deadline.remaining_hard()))

    raise last_error or StreamAborted("retry loop exhausted")
