# -*- coding: utf-8 -*-
"""初始管理员播种。"""
from __future__ import annotations

from fastapi_users.exceptions import UserAlreadyExists

from app.auth.config import AuthSettings, get_auth_settings
from app.auth.manager import user_manager
from app.auth.models import User
from app.auth.schemas import UserCreateRequest


async def ensure_admin_seed(settings: AuthSettings | None = None) -> None:
    """用户集合为空时按环境变量创建初始管理员；已有用户则直接返回。"""
    settings = settings or get_auth_settings()

    if await User.find_all().count() > 0:
        return

    username = (settings.admin_username or '').strip()
    password = settings.admin_password or ''
    if not username or not password:
        raise RuntimeError(
            '用户集合为空：必须配置 ICD_ADMIN_USERNAME 和 ICD_ADMIN_PASSWORD'
            ' 以创建初始管理员'
        )
    if len(password) < 8:
        raise RuntimeError('ICD_ADMIN_PASSWORD 至少需要 8 个字符')

    try:
        await user_manager.create(
            UserCreateRequest(
                username=username,
                password=password,
                is_admin=True,
            )
        )
    except UserAlreadyExists:
        # 多进程同时启动时另一个进程已创建，视为成功。
        return
