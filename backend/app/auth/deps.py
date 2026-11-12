# -*- coding: utf-8 -*-
"""认证依赖封装。

业务路由统一从本模块取当前用户，避免各处直接依赖 fastapi-users 的工厂产物；
这里只做类型收窄与依赖转发，不含业务逻辑。
"""
from __future__ import annotations

from fastapi import Depends

from app.auth.backend import current_active_user, current_superuser
from app.auth.models import User


async def current_user(user: User = Depends(current_active_user)) -> User:
    """当前已激活用户；未登录或未激活时由 fastapi-users 返回 401。"""
    return user


async def current_admin(user: User = Depends(current_superuser)) -> User:
    """当前管理员；未登录 401，非管理员 403。"""
    return user
