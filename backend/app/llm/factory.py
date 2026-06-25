"""
LLM 工厂（factory.py）。

统一从环境变量读取模型配置；USE_MOCK_LLM=1 或缺失关键配置时返回 MockLLM。
真实模式（USE_MOCK_LLM=0）下若关键配置缺失，给出明确错误，不静默使用错误默认值。

环境变量前缀：
- MINIMAX_*：MiniMax 模型配置
- DEEPSEEK_*：DeepSeek 模型配置
- USE_MOCK_LLM：是否使用 mock（1/0，缺省 0）
- CREWAI_VERBOSE：CrewAI 详细日志（1/0，缺省 0）
"""

import os
from typing import Any

from app.llm.mock_llm import MockLLM


def use_mock_llm() -> bool:
    """是否使用 mock LLM。

    规则（确认方案 A）：
    - USE_MOCK_LLM=1 → mock
    - USE_MOCK_LLM=0 → 真实模式
    - USE_MOCK_LLM 未设置 → 默认 0
    """
    return os.getenv("USE_MOCK_LLM", "0") == "1"


def crewai_verbose() -> bool:
    """CrewAI 详细日志开关（CREWAI_VERBOSE=1/0，缺省 0）。"""
    return os.getenv("CREWAI_VERBOSE", "0") == "1"


def _read_optional_str(env_key: str) -> str:
    return os.getenv(env_key, "").strip()


def _read_optional_float(env_key: str) -> float | None:
    raw = os.getenv(env_key, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(
            f"环境变量 {env_key}={raw!r} 不是合法 float"
        ) from e


def _read_optional_int(env_key: str) -> int | None:
    raw = os.getenv(env_key, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(
            f"环境变量 {env_key}={raw!r} 不是合法 int"
        ) from e


def _read_optional_bool(env_key: str) -> bool | None:
    raw = os.getenv(env_key, "").strip().lower()
    if not raw:
        return None
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(
        f"环境变量 {env_key}={raw!r} 不是合法 bool (1/0/true/false)"
    )


def _read_provider_config(prefix: str) -> dict[str, Any]:
    """读取某个 provider 的完整配置（不连接任何外部服务）。"""
    cfg: dict[str, Any] = {
        "api_key": _read_optional_str(f"{prefix}API_KEY"),
        "base_url": _read_optional_str(f"{prefix}BASE_URL"),
        "model": _read_optional_str(f"{prefix}MODEL"),
        "provider": _read_optional_str(f"{prefix}PROVIDER"),
        "temperature": _read_optional_float(f"{prefix}TEMPERATURE"),
        "top_p": _read_optional_float(f"{prefix}TOP_P"),
        "max_tokens": _read_optional_int(f"{prefix}MAX_TOKENS"),
        "timeout": _read_optional_int(f"{prefix}TIMEOUT"),
        "max_retries": _read_optional_int(f"{prefix}MAX_RETRIES"),
        "retry_backoff": _read_optional_float(f"{prefix}RETRY_BACKOFF"),
        "stream": _read_optional_bool(f"{prefix}STREAM"),
    }
    return cfg


def _required_keys_for_real_mode(cfg: dict[str, Any], prefix: str) -> list[str]:
    """真实模式必需的环境变量。空值即视为缺失。"""
    missing: list[str] = []
    for key in ("api_key", "model"):
        if not cfg.get(key):
            missing.append(f"{prefix}{key.upper()}")
    return missing


_completion_patched = False


def _patch_crewai_completion_for_unsupported_models() -> None:
    """Monkey-patch CrewAI OpenAICompletion._handle_completion 以支持非 OpenAI 原生模型。

    CrewAI 的 _handle_completion 在 response_model 存在时调用
    beta.chat.completions.parse() → response_format={"type":"json_schema",...}。
    MiniMax M2.7 和 DeepSeek 的 API 支持 response_format={"type":"json_object"}
    但不支持 json_schema 格式，且 MiniMax M2.7 的推理内容 <think> 会混入
    message.content 导致 beta.parse() 的客户端 JSON 解析失败。

    本 patch 检测模型名含 "minimax" 时，改为发送
    response_format={"type":"json_object"} + 清洗 <think> 前缀后手动解析，
    其余参数和事件完全复用原链路。
    DeepSeek 已统一使用 provider=openai，走 InternalInstructor 路径，
    不经过此 patch。
    """
    global _completion_patched
    if _completion_patched:
        return

    from crewai.llms.providers.openai.completion import OpenAICompletion  # type: ignore

    _original_handle_completion = OpenAICompletion._handle_completion

    def _patched_handle_completion(
        self,
        params: dict,
        available_functions=None,
        from_task=None,
        from_agent=None,
        response_model=None,
    ):
        model = str(params.get("model", "")).lower()
        needs_bypass = "minimax" in model
        if not needs_bypass or response_model is None:
            return _original_handle_completion(
                self, params, available_functions, from_task, from_agent, response_model
            )

        import re
        import json

        from crewai.llms.providers.openai.completion import LLMCallType

        # 用原生 response_format=json_object 替代 beta.parse 的 json_schema
        params_copy = {k: v for k, v in params.items() if k != "response_format"}
        params_copy["response_format"] = {"type": "json_object"}

        response = self._get_sync_client().chat.completions.create(**params_copy)
        content = response.choices[0].message.content or ""

        # MiniMax M2.7 的推理内容以 <think>...</think> 包裹在正文前
        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()

        # 部分模型可能将 JSON 包裹在 ```json 代码块中
        md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if md_match:
            content = md_match.group(1).strip()

        data = json.loads(content) if content else {}
        parsed_object = response_model.model_validate(data)

        usage = self._extract_openai_token_usage(response)
        self._track_token_usage_internal(usage)

        finish_reason, response_id = self._extract_chat_finish_reason_and_id(response)

        self._emit_call_completed_event(
            response=parsed_object.model_dump_json(),
            call_type=LLMCallType.LLM_CALL,
            from_task=from_task,
            from_agent=from_agent,
            messages=params["messages"],
            usage=usage,
            finish_reason=finish_reason,
            response_id=response_id,
        )
        return parsed_object

    OpenAICompletion._handle_completion = _patched_handle_completion
    _completion_patched = True


_instructor_patched = False


def _patch_crewai_instructor_for_unsupported_models() -> None:
    """Monkey-patch CrewAI InternalInstructor 以兼容 MiniMax M2.7/DeepSeek V4。

    CrewAI 的 LLM._handle_non_streaming_response 在 is_litellm=True 且
    response_model 存在时使用 InternalInstructor，其默认 TOOLS mode 发送
    tool_calls 获取结构化输出。

    MiniMax M2.7 在 API 层面支持 tool_calls，但行为是间歇性的：
    即使 instructor 正确发送了 tool_choice={'type':'function',...}，
    模型仍有概率将 JSON 放在 message.content（finish_reason=stop）
    而非 message.tool_calls（finish_reason=tool_calls）。
    当 schema 含 $defs 嵌套引用时几乎必现。

    DeepSeek V4（deepseek-v4-flash/v4-pro）默认 thinking mode，
    直接拒绝 tool_choice → litellm.BadRequestError。
    本 patch 对 V4 模型注入 thinking=disabled 关闭推理，
    恢复 TOOLS mode 兼容性。

    对 MiniMax / 旧版 DeepSeek：TOOLS mode + content fallback 兜底，
    当 tool_calls 为空但 content 含合法 JSON 时自动提取包装。
    """
    global _instructor_patched
    if _instructor_patched:
        return

    from crewai.utilities.internal_instructor import InternalInstructor  # type: ignore
    import instructor as _instructor
    from instructor import Mode
    import re as _re
    import json as _json

    _original_init = InternalInstructor.__init__

    def _patched_init(self, content, model, agent=None, llm=None):
        _original_init(self, content, model, agent=agent, llm=llm)

        if llm is not None and hasattr(llm, "model"):
            model_name = str(llm.model).lower()
        elif isinstance(llm, str):
            model_name = llm.lower()
        else:
            model_name = ""
        if any(m in model_name for m in ("minimax", "deepseek")):
            # MiniMax: TOOLS mode 间歇性失败 → content fallback 兜底
            # DeepSeek: TOOLS mode 原生可靠，fallback 基本不触发但仍保留
            from litellm import completion as litellm_completion

            _original_litellm = litellm_completion

            def _litellm_with_fallback(**kwargs):
                # 按模型名注入正确凭证，避免多模型共用 env var 冲突
                mdl = str(kwargs.get("model", "")).lower()
                for key, creds in _provider_creds.items():
                    if key in mdl:
                        if "api_key" not in kwargs:
                            kwargs["api_key"] = creds["api_key"]
                        if "api_base" not in kwargs:
                            kwargs["api_base"] = creds.get("api_base", "")
                        break
                # DeepSeek V4 默认 thinking mode → 不支持 tool_choice
                # 传 thinking=disabled 关闭推理模式，恢复 TOOLS 兼容性
                if "deepseek-v4" in mdl or "deepseek_v4" in mdl:
                    kwargs.setdefault("extra_body", {})
                    kwargs["extra_body"]["thinking"] = {"type": "disabled"}
                resp = _original_litellm(**kwargs)
                choice = resp.choices[0]
                msg = choice.message

                if msg.tool_calls is not None:
                    return resp  # tool_calls 正常返回，不干预

                # fallback：从 content 提取 JSON 包装为 tool_call
                content_text = msg.content or ""
                content_text = _re.sub(
                    r"<think>.*?</think>\s*", "", content_text, flags=_re.DOTALL
                ).strip()
                md_match = _re.search(
                    r"```(?:json)?\s*\n?(.*?)\n?```", content_text, _re.DOTALL
                )
                if md_match:
                    content_text = md_match.group(1).strip()

                # 模型偶尔在 content 中输出多 JSON 拼接（如 prompt 误要求多候选时），
                # 用 raw_decode 提取第一个 JSON 对象 + 后面多余部分忽略。
                # 根本修复在 Prompt 端 —— generation_prompt.md 应只要求 1 份候选。
                data = None
                try:
                    data = _json.loads(content_text)
                except (_json.JSONDecodeError, TypeError):
                    try:
                        decoder = _json.JSONDecoder()
                        data, _end = decoder.raw_decode(content_text)
                    except (_json.JSONDecodeError, TypeError):
                        return resp  # 无法解析，交给 instructor 原生错误处理

                tool_name = (
                    kwargs.get("tools", [{}])[0]
                    .get("function", {})
                    .get("name", "fallback_tool")
                )
                # 使用 OpenAI 原生类型构造 tool_call，兼容 litellm 日志层
                from openai.types.chat import (
                    ChatCompletionMessageToolCall as _ToolCall,
                )
                from openai.types.chat.chat_completion_message_tool_call import (
                    Function as _Func,
                )

                fake_tc = _ToolCall(
                    id="fallback_tc_001",
                    type="function",
                    function=_Func(
                        name=tool_name,
                        arguments=_json.dumps(data, ensure_ascii=False),
                    ),
                )
                msg.tool_calls = [fake_tc]
                return resp

            # 将 LLM 的 max_tokens 传入 instructor client 作为默认值。
            # InternalInstructor.to_pydantic() 仅传 model/response_model/messages，
            # 不会显式传 max_tokens，导致 instructor 使用内置默认 4096。
            # 这里注入真实 max_tokens 后，instructor.handle_kwargs 会在每次
            # create() 调用时自动合并该值。
            _instructor_kwargs: dict[str, Any] = {}
            if llm is not None and not isinstance(llm, str) and hasattr(llm, "max_tokens"):
                _mt = llm.max_tokens
                if _mt is not None:
                    _instructor_kwargs["max_tokens"] = _mt

            self._client = _instructor.from_litellm(
                _litellm_with_fallback, mode=Mode.TOOLS, **_instructor_kwargs
            )

    InternalInstructor.__init__ = _patched_init
    _instructor_patched = True

_provider_creds: dict[str, dict[str, str]] = {}


def _store_provider_creds(provider_key: str, api_key: str, base_url: str | None) -> None:
    """存储 provider 凭证，供 InternalInstructor patch 注入 litellm 调用。"""
    creds: dict[str, str] = {"api_key": api_key}
    if base_url:
        creds["api_base"] = base_url
    _provider_creds[provider_key] = creds


def _build_real_llm(cfg: dict[str, Any], prefix: str) -> Any:
    """根据 provider 配置构建真实 LLM 实例。

    provider 取值说明（用户已确认"不要替我判断 MiniMax 是否 OpenAI-compatible"）：
    - 未设置 provider：默认按 openai 协议处理（DeepSeek 兼容良好；MiniMax 由用户自行决定）
    - provider=openai：使用 LLM(model="openai/<model>")
    - provider=deepseek：使用 LLM(model="deepseek/<model>")
    - provider=<其它>：直接拼 "<provider>/<model>"

    CrewAI 真实 LLM 依赖未在本 Issue 中验证；如出现兼容问题，由后续 Issue 处理。
    """
    # 延迟导入 crewai，避免非 LLM 路径产生依赖
    from crewai import LLM  # type: ignore

    provider = cfg.get("provider") or "openai"
    model_name = cfg["model"]
    model_str = f"{provider}/{model_name}"

    # 统一使用 provider=openai，两个模型走相同的 LLM → InternalInstructor 路径。
    # 凭证存入 _provider_creds，由 InternalInstructor patch 动态注入 litellm 调用，
    # 避免多模型共用 OPENAI_API_KEY/OPENAI_BASE_URL 环境变量冲突。
    if cfg.get("api_key"):
        _store_provider_creds(
            model_str.lower(), cfg["api_key"], cfg.get("base_url")
        )

    # Patch 1) _handle_completion：仅 MiniMax 触发
    # MiniMax M2.7 <think> 混入 content → json_object + 清洗
    _patch_crewai_completion_for_unsupported_models()
    # Patch 2) InternalInstructor：TOOLS mode + content fallback
    # MiniMax: TOOLS 间歇性失败 → fallback 兜底
    # DeepSeek: TOOLS 原生可靠，fallback 不触发，仅做凭证注入
    _patch_crewai_instructor_for_unsupported_models()

    kwargs: dict[str, Any] = {
        "model": model_str,
        "api_key": cfg["api_key"],
    }
    if cfg.get("base_url"):
        kwargs["base_url"] = cfg["base_url"]
    for key in (
        "temperature",
        "top_p",
        "max_tokens",
        "timeout",
        "max_retries",
    ):
        if cfg.get(key) is not None:
            kwargs[key] = cfg[key]
    # stream / retry_backoff 在不同 crewai 版本中支持不同，留给真实调用时再处理
    return LLM(**kwargs)


def get_minimax_llm(overrides: dict[str, Any] | None = None):
    """获取 MiniMax LLM 实例。

    mock 模式下返回中性 role 的 MockLLM，由 agent builder 在使用时
    通过 `MockLLM(role=...)` 覆盖到具体场景（generation / scoring）。

    overrides: 可选的配置覆盖 dict，调用方传入的 key 会覆盖 env 读到的值。
               mock 模式下忽略。
    """
    if use_mock_llm():
        return MockLLM(model="minimax-mock", role="minimax_neutral")
    cfg = _read_provider_config("MINIMAX_")
    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    missing = _required_keys_for_real_mode(cfg, "MINIMAX_")
    if missing:
        raise RuntimeError(
            "USE_MOCK_LLM=0 但 MiniMax 真实模式必需配置缺失："
            + ", ".join(missing)
            + "。请在 backend/.env 或环境变量中填写，或设置 USE_MOCK_LLM=1 使用 mock。"
        )
    return _build_real_llm(cfg, "MINIMAX_")


def get_deepseek_llm(overrides: dict[str, Any] | None = None):
    """获取 DeepSeek LLM 实例。

    mock 模式下返回中性 role 的 MockLLM，由 agent builder 在使用时
    通过 `MockLLM(role=...)` 覆盖到具体场景（generation / scoring / comparison）。

    overrides: 可选的配置覆盖 dict，调用方传入的 key 会覆盖 env 读到的值。
               mock 模式下忽略。
    """
    if use_mock_llm():
        return MockLLM(model="deepseek-mock", role="deepseek_neutral")
    cfg = _read_provider_config("DEEPSEEK_")
    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    missing = _required_keys_for_real_mode(cfg, "DEEPSEEK_")
    if missing:
        raise RuntimeError(
            "USE_MOCK_LLM=0 但 DeepSeek 真实模式必需配置缺失："
            + ", ".join(missing)
            + "。请在 backend/.env 或环境变量中填写，或设置 USE_MOCK_LLM=1 使用 mock。"
        )
    return _build_real_llm(cfg, "DEEPSEEK_")
