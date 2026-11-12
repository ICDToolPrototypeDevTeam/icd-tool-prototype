# -*- coding: utf-8 -*-
"""认证接口使用的请求/响应数据结构。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r'^\S+$')
    password: str = Field(min_length=8, max_length=128)
    is_admin: bool = False


class UserUpdateRequest(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None
    is_superuser: bool | None = None


class UserRead(BaseModel):
    id: str
    username: str
    is_active: bool
    is_superuser: bool
    created_at: datetime


class MeResponse(BaseModel):
    id: str
    username: str
    is_admin: bool
