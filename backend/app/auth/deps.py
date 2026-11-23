# -*- coding: utf-8 -*-
"""认证与角色依赖封装。

业务路由统一从本模块取当前用户，避免各处直接依赖 fastapi-users 的工厂产物；
这里只做类型收窄、角色判断与依赖转发，不含业务逻辑。

权限矩阵：

- 业务用户：业务功能，不能进入账号管理 / 审计中心；
- 系统管理员：业务功能 + 账号管理，**不能进入审计中心**；
- 审计管理员：仅审计中心（业务接口一律 403）。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.auth.backend import current_active_user
from app.auth.models import User
from app.auth.roles import is_admin, is_auditor


async def current_user(user: User = Depends(current_active_user)) -> User:
    """当前已激活用户；未登录或未激活时由 fastapi-users 返回 401。"""
    return user


async def current_admin(user: User = Depends(current_active_user)) -> User:
    """当前系统管理员；未登录 401，非系统管理员 403（审计管理员也在此被拒绝）。"""
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='需要系统管理员权限',
        )
    return user


async def current_auditor(user: User = Depends(current_active_user)) -> User:
    """当前审计管理员；未登录 401，非审计管理员 403（系统管理员也在此被拒绝）。"""
    if not is_auditor(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='需要审计管理员权限',
        )
    return user


async def require_business_access(
    user: User = Depends(current_active_user),
) -> User:
    """业务接口守卫：审计管理员只能使用审计中心，访问业务接口返回 403。"""
    if is_auditor(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='审计管理员仅可使用审计中心',
        )
    return user
