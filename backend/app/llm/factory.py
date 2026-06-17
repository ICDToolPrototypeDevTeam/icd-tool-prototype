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


def get_minimax_llm():
    """获取 MiniMax LLM 实例。

    mock 模式下返回中性 role 的 MockLLM，由 agent builder 在使用时
    通过 `MockLLM(role=...)` 覆盖到具体场景（generation / scoring）。
    """
    if use_mock_llm():
        return MockLLM(model="minimax-mock", role="minimax_neutral")
    cfg = _read_provider_config("MINIMAX_")
    missing = _required_keys_for_real_mode(cfg, "MINIMAX_")
    if missing:
        raise RuntimeError(
            "USE_MOCK_LLM=0 但 MiniMax 真实模式必需配置缺失："
            + ", ".join(missing)
            + "。请在 backend/.env 或环境变量中填写，或设置 USE_MOCK_LLM=1 使用 mock。"
        )
    return _build_real_llm(cfg, "MINIMAX_")


def get_deepseek_llm():
    """获取 DeepSeek LLM 实例。

    mock 模式下返回中性 role 的 MockLLM，由 agent builder 在使用时
    通过 `MockLLM(role=...)` 覆盖到具体场景（generation / scoring / comparison）。
    """
    if use_mock_llm():
        return MockLLM(model="deepseek-mock", role="deepseek_neutral")
    cfg = _read_provider_config("DEEPSEEK_")
    missing = _required_keys_for_real_mode(cfg, "DEEPSEEK_")
    if missing:
        raise RuntimeError(
            "USE_MOCK_LLM=0 但 DeepSeek 真实模式必需配置缺失："
            + ", ".join(missing)
            + "。请在 backend/.env 或环境变量中填写，或设置 USE_MOCK_LLM=1 使用 mock。"
        )
    return _build_real_llm(cfg, "DEEPSEEK_")
