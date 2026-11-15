# -*- coding: utf-8 -*-
"""JWT 策略、Cookie 传输与 FastAPIUsers 实例。

本模块在导入期构造 `auth_backend`，因此要求 `AUTH_JWT_SECRET` 已就绪；
`main.py` 会在导入路由前加载 `backend/.env`。
"""
from __future__ import annotations

from typing import Optional

import jwt
from beanie import PydanticObjectId
from fastapi_users import BaseUserManager, FastAPIUsers, exceptions, models
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy
from fastapi_users.jwt import decode_jwt, generate_jwt

from app.auth.config import get_auth_settings
from app.auth.manager import UserManager, user_manager
from app.auth.models import User

_settings = get_auth_settings()


class VersionedJWTStrategy(JWTStrategy):
    """在标准 JWT 之外附带每用户 token_version，用于会话吊销。"""

    async def write_token(self, user: models.UP) -> str:
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            "ver": getattr(user, "token_version", 0),
        }
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm
        )

    async def read_token(
        self,
        token: Optional[str],
        user_manager: BaseUserManager[models.UP, models.ID],
    ) -> Optional[models.UP]:
        if token is None:
            return None
        try:
            data = decode_jwt(
                token,
                self.decode_key,
                self.token_audience,
                algorithms=[self.algorithm],
            )
            user_id = data.get("sub")
            if user_id is None:
                return None
        except jwt.PyJWTError:
            return None

        try:
            user = await user_manager.get(user_manager.parse_id(user_id))
        except (exceptions.UserNotExists, exceptions.InvalidID):
            return None

        if data.get("ver", 0) != getattr(user, "token_version", 0):
            return None
        return user


def get_jwt_strategy() -> VersionedJWTStrategy:
    return VersionedJWTStrategy(
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
