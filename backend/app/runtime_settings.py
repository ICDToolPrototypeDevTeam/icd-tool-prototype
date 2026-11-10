# -*- coding: utf-8 -*-
"""运行时模型设置：前端可修改，落盘后热应用到进程环境。

背景：服务器部署完成后，现场无法直接编辑 ``backend/.env``；只为改三个模型名
或思考开关就让服务器人员重新部署一次，成本过高。本模块把设置持久化到
``OUTPUT_DIR/settings.json``（与历史结果同卷，容器重建不丢），保存时立即写入
``os.environ``；启动时回读并重新应用。

为什么无需重启即可生效：
- 反向管线：``v4/llm/factory.get_llm()`` 每次调用都读 ``*_MODEL`` /
  ``*_THINKING`` 环境变量；
- 正向管线：``forward/config_builder.build_real_config()`` 每个任务开始时读模型名；
  ``*_THINKING`` 随进程环境传给 judge 子进程（runner 再注入 ``AI_PROVIDER``），
  由各 LLM 请求点读取；
- 缓存 key：``llm_cache.resolve_model()`` 走同一个工厂，模型名变化后自然 miss；
  思考开关的关闭值另行编入 key（见 ``llm/thinking.cache_variant``）。

约束：
- 只管理 ``DEEPSEEK_MODEL`` / ``MINIMAX_MODEL`` / ``QWEN_MODEL`` 三个模型名，
  以及 ``DEEPSEEK_THINKING`` / ``MINIMAX_THINKING`` / ``QWEN_THINKING`` 三个
  思考开关；API key、base_url、``JUDGE_PROVIDERS`` 仍由部署配置管理。
- 持久化值优先于 ``.env``：启动回读发生在 ``load_dotenv`` 之后。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.v4.config import get_output_root
from app.v4.llm.factory import DEFAULT_MODELS
from app.v4.llm.thinking import thinking_enabled

SETTINGS_FILE_NAME = "settings.json"

# 前端字段名 → (环境变量名, factory 中的 provider 键)。顺序即页面展示顺序。
MODEL_FIELDS: dict[str, tuple[str, str]] = {
    "deepseek_model": ("DEEPSEEK_MODEL", "deepseek"),
    "minimax_model": ("MINIMAX_MODEL", "minimax"),
    "qwen_model": ("QWEN_MODEL", "qwen"),
}

# 思考模式开关：值为 bool，默认开启。旧 settings.json 缺这些字段时回落 env。
THINKING_FIELDS: dict[str, tuple[str, str]] = {
    "deepseek_thinking": ("DEEPSEEK_THINKING", "deepseek"),
    "minimax_thinking": ("MINIMAX_THINKING", "minimax"),
    "qwen_thinking": ("QWEN_THINKING", "qwen"),
}

# 模型标识的上限：正常模型名远小于该值，超长基本是误粘贴
MAX_MODEL_NAME_LENGTH = 200

# 服务器时区常为 UTC；updated_at 固定带东八区偏移，与历史结果的时间口径一致
_CN_TZ = timezone(timedelta(hours=8))

_lock = threading.Lock()


class SettingsValidationError(ValueError):
    """设置不合法（模型名空 / 过长 / 含控制字符 / 类型不对，开关不是 bool）。"""


@dataclass(frozen=True)
class ModelSettings:
    """当前生效的模型设置。

    ``customized=False`` 表示没有自定义落盘值，模型名来自部署配置 / factory
    默认值，思考开关来自部署配置 / 默认开启；``updated_at`` 仅在自定义时为
    ISO 字符串。
    """

    deepseek_model: str
    minimax_model: str
    qwen_model: str
    deepseek_thinking: bool = True
    minimax_thinking: bool = True
    qwen_thinking: bool = True
    customized: bool = False
    updated_at: Optional[str] = None


def settings_path() -> Path:
    """设置文件位置：输出根目录下（Docker 中即挂载的 /app/output）。"""
    return get_output_root() / SETTINGS_FILE_NAME


def _clean_model_name(field: str, value: object) -> str:
    if not isinstance(value, str):
        raise SettingsValidationError(f"{field} 必须是字符串")
    cleaned = value.strip()
    if not cleaned:
        raise SettingsValidationError(f"{field} 不能为空")
    if len(cleaned) > MAX_MODEL_NAME_LENGTH:
        raise SettingsValidationError(
            f"{field} 长度不能超过 {MAX_MODEL_NAME_LENGTH} 个字符"
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise SettingsValidationError(f"{field} 不能包含换行或控制字符")
    return cleaned


def _clean_thinking(field: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    # 手改 settings.json 时可能写成 0/1；兼容这两种整数写法
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise SettingsValidationError(f"{field} 必须是布尔值")


def _model_values(**raw: object) -> dict[str, str]:
    """按 MODEL_FIELDS 校验并归一化三个模型名。"""
    return {
        field: _clean_model_name(field, raw.get(field))
        for field in MODEL_FIELDS
    }


def _warn(message: str) -> None:
    print(f"[settings] {message}", file=sys.stderr)


def _thinking_values(raw: dict) -> dict[str, bool]:
    """读取三个思考开关；缺失或单个字段无效时按 env / 默认开启处理。

    旧版 settings.json 只有三个模型名，这里回落 env，保证升级后行为不变。
    """
    values: dict[str, bool] = {}
    for field, (_env_name, provider) in THINKING_FIELDS.items():
        if field in raw:
            try:
                values[field] = _clean_thinking(field, raw[field])
                continue
            except SettingsValidationError as exc:
                _warn(f"忽略无效的 {field}，按部署配置处理: {exc}")
        values[field] = thinking_enabled(provider)
    return values


def _read_persisted() -> Optional[dict]:
    """读取并校验落盘设置；文件不存在 / 损坏 / 模型名不合法 → None（回落 env）。"""
    path = settings_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _warn(f"忽略不可读的设置文件 {path}: {type(exc).__name__}: {exc}")
        return None
    if not isinstance(raw, dict):
        _warn(f"忽略格式错误的设置文件 {path}: 顶层不是对象")
        return None
    try:
        values = _model_values(**raw)
    except SettingsValidationError as exc:
        _warn(f"忽略无效的设置文件 {path}: {exc}")
        return None
    values.update(_thinking_values(raw))
    updated_at = raw.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        updated_at = None
    return {**values, "updated_at": updated_at}


def _apply(values: dict[str, object]) -> None:
    """把模型名与思考开关写入进程环境；后续调用立即读到新值。"""
    for field, (env_name, _provider) in MODEL_FIELDS.items():
        os.environ[env_name] = str(values[field])
    for field, (env_name, _provider) in THINKING_FIELDS.items():
        os.environ[env_name] = "1" if values[field] else "0"


def _to_model_settings(values: dict[str, object], *, customized: bool,
                       updated_at: Optional[str]) -> ModelSettings:
    return ModelSettings(
        deepseek_model=str(values["deepseek_model"]),
        minimax_model=str(values["minimax_model"]),
        qwen_model=str(values["qwen_model"]),
        deepseek_thinking=bool(values["deepseek_thinking"]),
        minimax_thinking=bool(values["minimax_thinking"]),
        qwen_thinking=bool(values["qwen_thinking"]),
        customized=customized,
        updated_at=updated_at,
    )


def _effective_from_env() -> dict[str, object]:
    """未自定义时的当前生效值：env（.env / 容器 env_file）优先，否则默认值。"""
    values: dict[str, object] = {
        field: os.getenv(env_name, DEFAULT_MODELS[provider])
        for field, (env_name, provider) in MODEL_FIELDS.items()
    }
    for field, (_env_name, provider) in THINKING_FIELDS.items():
        values[field] = thinking_enabled(provider)
    return values


def get_model_settings() -> ModelSettings:
    """读取当前生效设置（不回写 env）。"""
    with _lock:
        persisted = _read_persisted()
    if persisted is not None:
        return _to_model_settings(
            persisted,
            customized=True,
            updated_at=persisted.get("updated_at"),
        )
    return _to_model_settings(
        _effective_from_env(),
        customized=False,
        updated_at=None,
    )


def update_model_settings(*, deepseek_model: object, minimax_model: object,
                          qwen_model: object,
                          deepseek_thinking: object = None,
                          minimax_thinking: object = None,
                          qwen_thinking: object = None) -> ModelSettings:
    """校验 → 原子落盘 → 立即应用。写盘失败抛 OSError，由路由转 500。

    思考开关传 ``None`` 表示本次不改，保留当前生效值；这样旧前端 / 旧脚本
    只提交三个模型名时行为与改动前一致。
    """
    values: dict[str, object] = _model_values(
        deepseek_model=deepseek_model,
        minimax_model=minimax_model,
        qwen_model=qwen_model,
    )
    requested_thinking = {
        "deepseek_thinking": deepseek_thinking,
        "minimax_thinking": minimax_thinking,
        "qwen_thinking": qwen_thinking,
    }
    updated_at = datetime.now(_CN_TZ).isoformat(timespec="seconds")
    path = settings_path()
    with _lock:
        # 省略的开关以当前生效值为准（持久化优先，否则 env / 默认）
        base = _read_persisted() or _effective_from_env()
        for field in THINKING_FIELDS:
            value = requested_thinking[field]
            values[field] = (
                bool(base[field]) if value is None else _clean_thinking(field, value)
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        try:
            tmp_path.write_text(
                json.dumps(
                    {**values, "updated_at": updated_at},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(tmp_path, path)
        except OSError:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        _apply(values)
    return _to_model_settings(values, customized=True, updated_at=updated_at)


def load_persisted_settings() -> Optional[ModelSettings]:
    """启动时回读并应用落盘设置；无文件或文件无效时返回 None。"""
    with _lock:
        persisted = _read_persisted()
        if persisted is None:
            return None
        _apply(persisted)
    loaded = _to_model_settings(
        persisted,
        customized=True,
        updated_at=persisted.get("updated_at"),
    )
    print(
        "[settings] 已加载运行时模型设置 "
        f"({settings_path()}): deepseek={loaded.deepseek_model}, "
        f"minimax={loaded.minimax_model}, qwen={loaded.qwen_model}, "
        "thinking(deepseek/minimax/qwen)="
        f"{int(loaded.deepseek_thinking)}/{int(loaded.minimax_thinking)}/"
        f"{int(loaded.qwen_thinking)}"
    )
    return loaded
