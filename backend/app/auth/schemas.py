# -*- coding: utf-8 -*-
"""认证接口使用的请求/响应数据结构。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

RoleValue = Literal["user", "admin", "auditor"]


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r'^\S+$')
    password: str = Field(min_length=8, max_length=128)
    # 新字段；旧前端只传 is_admin 时按 is_admin 推导角色。
    role: RoleValue | None = None
    is_admin: bool = False

    @model_validator(mode="after")
    def _check_role_conflict(self) -> "UserCreateRequest":
        if self.role is not None and self.is_admin and self.role != "admin":
            raise ValueError("is_admin 与 role 冲突")
        return self

    @property
    def resolved_role(self) -> str:
        if self.role is not None:
            return self.role
        return "admin" if self.is_admin else "user"


class UserUpdateRequest(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None
    # 兼容旧前端；新前端统一传 role。
    is_superuser: bool | None = None
    role: RoleValue | None = None

    @model_validator(mode="after")
    def _check_role_conflict(self) -> "UserUpdateRequest":
        if self.role is not None and self.is_superuser is not None:
            if self.is_superuser != (self.role == "admin"):
                raise ValueError("is_superuser 与 role 冲突")
        return self


class UserRead(BaseModel):
    id: str
    username: str
    is_active: bool
    is_superuser: bool
    role: RoleValue
    role_label: str
    is_auditor: bool
    created_at: datetime


class MeResponse(BaseModel):
    id: str
    username: str
    is_admin: bool
    role: RoleValue
    role_label: str
    is_auditor: bool
