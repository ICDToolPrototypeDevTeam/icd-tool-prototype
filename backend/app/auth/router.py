# -*- coding: utf-8 -*-
"""认证路由：Cookie 登录 / 登出 / 当前用户。

只挂载登录与登出；注册、邮箱验证、找回密码不在原型范围内（账号由管理员创建）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.backend import auth_backend, fastapi_users
from app.auth.deps import current_user
from app.auth.models import User
from app.auth.schemas import MeResponse


router = APIRouter()

router.include_router(fastapi_users.get_auth_router(auth_backend), prefix='/auth')


@router.get('/auth/me', response_model=MeResponse)
async def read_current_user(user: User = Depends(current_user)) -> MeResponse:
    """返回当前登录用户；`username` 对应 `User.email`（写入时已小写）。"""
    return MeResponse(
        id=str(user.id),
        username=user.email,
        is_admin=user.is_superuser,
    )
