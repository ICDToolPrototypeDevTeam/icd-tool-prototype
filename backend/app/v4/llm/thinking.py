# -*- coding: utf-8 -*-
"""各 provider 的思考模式运行时开关。

前端「模型设置」页保存后由 ``runtime_settings`` 写入环境变量；反向管线每次
构造 client 时读取，因此改完无需重启。默认开启（兼容升级前行为）。

约定：
- 只有显式的关闭值（0/false/no/off）才视为关闭；未配置、空串或非法值一律
  按开启处理，避免部署侧手误把思考模式意外关掉。
- 缓存层只对「关闭」生成 key 变体，开启路径沿用历史 key，保证升级后默认
  配置仍能命中旧缓存。
"""

from __future__ import annotations

import os

THINKING_ENV = {
    "deepseek": "DEEPSEEK_THINKING",
    "minimax": "MINIMAX_THINKING",
    "qwen": "QWEN_THINKING",
}

_FALSE_VALUES = {"0", "false", "no", "off"}


def thinking_enabled(provider: str) -> bool:
    """该 provider 当前是否开启思考；未知 provider / 非法值 → True。"""
    env_name = THINKING_ENV.get(provider)
    if not env_name:
        return True
    raw = (os.getenv(env_name) or "").strip().lower()
    return raw not in _FALSE_VALUES


def cache_variant(provider: str) -> dict[str, str]:
    """缓存 key 的思考模式变体：仅关闭时返回标记，开启时为空。"""
    return {} if thinking_enabled(provider) else {"_thinking": "disabled"}
