# -*- coding: utf-8 -*-
"""认证相关环境变量配置。

只读环境变量，不做业务逻辑。`main.py` 会在导入路由前显式加载 `backend/.env`；
这里的 `load_dotenv()` 是兜底，保证单独导入本模块（如本地测试脚本）时也能读到。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

_TRUE_VALUES = {'1', 'true', 'yes'}


@dataclass(frozen=True)
class AuthSettings:
    mongo_url: str
    mongo_db: str
    jwt_secret: str
    token_ttl_seconds: int
    cookie_name: str
    cookie_secure: bool
    admin_username: str
    admin_password: str
    docs_enabled: bool


def _env_bool(name: str, default: str) -> bool:
    return (os.getenv(name) or default).strip().lower() in _TRUE_VALUES


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    mongo_url = (os.getenv('MONGO_URL') or '').strip()
    if not mongo_url:
        raise RuntimeError('MONGO_URL 未配置')

    jwt_secret = (os.getenv('AUTH_JWT_SECRET') or '').strip()
    if not jwt_secret:
        raise RuntimeError('AUTH_JWT_SECRET 未配置')
    if len(jwt_secret) < 32:
        raise RuntimeError('AUTH_JWT_SECRET 至少需要 32 个字符')

    return AuthSettings(
        mongo_url=mongo_url,
        mongo_db=(os.getenv('MONGO_DB') or 'icd').strip() or 'icd',
        jwt_secret=jwt_secret,
        token_ttl_seconds=int(os.getenv('AUTH_TOKEN_TTL_SECONDS') or '28800'),
        cookie_name=(os.getenv('AUTH_COOKIE_NAME') or 'icd_auth').strip() or 'icd_auth',
        cookie_secure=_env_bool('AUTH_COOKIE_SECURE', '0'),
        admin_username=(os.getenv('ICD_ADMIN_USERNAME') or '').strip(),
        admin_password=os.getenv('ICD_ADMIN_PASSWORD') or '',
        docs_enabled=_env_bool('ICD_DOCS_ENABLED', '1'),
    )
