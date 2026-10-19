# -*- coding: utf-8 -*-
"""运行时模型设置：前端可修改，落盘后热应用到进程环境。

背景：服务器部署完成后，现场无法直接编辑 ``backend/.env``；只为改三个模型名
就让服务器人员重新部署一次，成本过高。本模块把模型名持久化到
``OUTPUT_DIR/settings.json``（与历史结果同卷，容器重建不丢），保存时立即写入
``os.environ``；启动时回读并重新应用。

为什么无需重启即可生效：
- 反向管线：``v4/llm/factory.get_llm()`` 每次调用都读 ``*_MODEL`` 环境变量；
- 正向管线：``forward/config_builder.build_real_config()`` 每个任务开始时读环境变量；
- 缓存 key：``llm_cache.resolve_model()`` 走同一个工厂，模型名变化后自然 miss。

约束：
- 只管理 ``DEEPSEEK_MODEL`` / ``MINIMAX_MODEL`` / ``QWEN_MODEL`` 三个模型名；
  API key、base_url、``JUDGE_PROVIDERS`` 仍由部署配置管理。
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

SETTINGS_FILE_NAME = "settings.json"

# 前端字段名 → (环境变量名, factory 中的 provider 键)。顺序即页面展示顺序。
MODEL_FIELDS: dict[str, tuple[str, str]] = {
    "deepseek_model": ("DEEPSEEK_MODEL", "deepseek"),
    "minimax_model": ("MINIMAX_MODEL", "minimax"),
    "qwen_model": ("QWEN_MODEL", "qwen"),
}

# 模型标识的上限：正常模型名远小于该值，超长基本是误粘贴
MAX_MODEL_NAME_LENGTH = 200

# 服务器时区常为 UTC；updated_at 固定带东八区偏移，与历史结果的时间口径一致
_CN_TZ = timezone(timedelta(hours=8))

_lock = threading.Lock()


class SettingsValidationError(ValueError):
    """模型名不合法（空 / 过长 / 含控制字符 / 类型不对）。"""


@dataclass(frozen=True)
class ModelSettings:
    """当前生效的模型设置。

    ``customized=False`` 表示没有自定义落盘值，三个名字来自部署配置 / factory
    默认值；``updated_at`` 仅在自定义时为 ISO 字符串。
    """

    deepseek_model: str
    minimax_model: str
    qwen_model: str
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


def _model_values(**raw: object) -> dict[str, str]:
    """按 MODEL_FIELDS 校验并归一化三个模型名。"""
    return {
        field: _clean_model_name(field, raw.get(field))
        for field in MODEL_FIELDS
    }


def _warn(message: str) -> None:
    print(f"[settings] {message}", file=sys.stderr)


def _read_persisted() -> Optional[dict]:
    """读取并校验落盘设置；文件不存在 / 损坏 / 字段不合法 → None（回落 env）。"""
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
    updated_at = raw.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        updated_at = None
    return {**values, "updated_at": updated_at}


def _apply(values: dict[str, str]) -> None:
    """把模型名写入进程环境；后续 get_llm / 正向任务立即读到新值。"""
    for field, (env_name, _provider) in MODEL_FIELDS.items():
        os.environ[env_name] = values[field]


def _to_model_settings(values: dict[str, str], *, customized: bool,
                       updated_at: Optional[str]) -> ModelSettings:
    return ModelSettings(
        deepseek_model=values["deepseek_model"],
        minimax_model=values["minimax_model"],
        qwen_model=values["qwen_model"],
        customized=customized,
        updated_at=updated_at,
    )


def _effective_from_env() -> dict[str, str]:
    """未自定义时的当前生效值：env（.env / 容器 env_file）优先，否则 factory 默认。"""
    return {
        field: os.getenv(env_name, DEFAULT_MODELS[provider])
        for field, (env_name, provider) in MODEL_FIELDS.items()
    }


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
                          qwen_model: object) -> ModelSettings:
    """校验 → 原子落盘 → 立即应用。写盘失败抛 OSError，由路由转 500。"""
    values = _model_values(
        deepseek_model=deepseek_model,
        minimax_model=minimax_model,
        qwen_model=qwen_model,
    )
    updated_at = datetime.now(_CN_TZ).isoformat(timespec="seconds")
    path = settings_path()
    with _lock:
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
        f"minimax={loaded.minimax_model}, qwen={loaded.qwen_model}"
    )
    return loaded
