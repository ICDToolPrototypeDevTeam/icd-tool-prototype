# -*- coding: utf-8 -*-
"""JWT 策略、Cookie 传输与 FastAPIUsers 实例。

本模块在导入期构造 `auth_backend`，因此要求 `AUTH_JWT_SECRET` 已就绪；
`main.py` 会在导入路由前加载 `backend/.env`。
"""
from __future__ import annotations

from beanie import PydanticObjectId
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy

from app.auth.config import get_auth_settings
from app.auth.manager import UserManager, user_manager
from app.auth.models import User

_settings = get_auth_settings()


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=_settings.jwt_secret,
        lifetime_seconds=_settings.token_ttl_seconds,
    )


auth_backend = AuthenticationBackend(
    name='cookie',
    transport=CookieTransport(
        cookie_name=_settings.cookie_name,
        cookie_max_age=_settings.token_ttl_seconds,
        cookie_secure=_settings.cookie_secure,
        cookie_samesite='lax',
    ),
    get_strategy=get_jwt_strategy,
)


async def get_user_manager() -> UserManager:
    """FastAPI 依赖：返回全局 UserManager 单例。

    `FastAPIUsers` 需要的是依赖 callable；直接传 `user_manager` 实例会在
    路由注册时被 FastAPI 拒绝（not a callable object）。
    """
    return user_manager


fastapi_users = FastAPIUsers[User, PydanticObjectId](get_user_manager, [auth_backend])
current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
