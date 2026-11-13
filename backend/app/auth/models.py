# -*- coding: utf-8 -*-
"""用户文档模型。"""
from __future__ import annotations

from datetime import datetime, timezone

from beanie import Document, Indexed
from pydantic import Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Document):
    """账号文档。

    以 `email` 字段承载“用户名”（保持 fastapi-users 的字段约定），
    写入前统一转小写，因此登录用户名大小写不敏感。
    """

    email: Indexed(str, unique=True)
    hashed_password: str
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = True
    token_version: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        # 固定集合名，供运维文档与 mongosh 恢复命令使用。
        name = 'users'
