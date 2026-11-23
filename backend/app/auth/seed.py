# -*- coding: utf-8 -*-
"""初始管理员播种与历史账号角色补齐。"""
from __future__ import annotations

import sys

from fastapi_users.exceptions import UserAlreadyExists

from app.auth.config import AuthSettings, get_auth_settings
from app.auth.manager import user_manager
from app.auth.models import User
from app.auth.roles import ROLE_ADMIN, ROLE_AUDITOR, ROLE_USER, VALID_ROLES
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
                role=ROLE_ADMIN,
            )
        )
    except UserAlreadyExists:
        # 多进程同时启动时另一个进程已创建，视为成功。
        return


async def ensure_user_roles() -> None:
    """把历史账号补齐到三值角色，并同步 ``is_superuser``。

    只处理 ``role`` 缺失或非法的文档；已有合法角色的账号不动，避免覆盖管理员
    在页面上做的调整。任何异常只告警：``effective_role`` 仍能按 is_superuser
    回退，登录与权限判断不会因补齐失败而中断。
    """
    try:
        collection = User.get_motor_collection()
        valid = sorted(VALID_ROLES)
        # 1) 历史管理员 / 普通账号：只有 is_superuser，没有 role
        await collection.update_many(
            {'role': {'$exists': False}, 'is_superuser': True},
            {'$set': {'role': ROLE_ADMIN}},
        )
        await collection.update_many(
            {'role': {'$exists': False}},
            {'$set': {'role': ROLE_USER}},
        )
        # 2) 非法值（例如手工写入的空串 / 数字）
        await collection.update_many(
            {'role': {'$nin': valid}, 'is_superuser': True},
            {'$set': {'role': ROLE_ADMIN}},
        )
        await collection.update_many(
            {'role': {'$nin': valid}},
            {'$set': {'role': ROLE_USER}},
        )
        # 3) 角色与历史权限字段同步：admin=True，其余=False
        await collection.update_many(
            {'role': ROLE_ADMIN, 'is_superuser': {'$ne': True}},
            {'$set': {'is_superuser': True}},
        )
        await collection.update_many(
            {'role': {'$in': [ROLE_USER, ROLE_AUDITOR]}, 'is_superuser': True},
            {'$set': {'is_superuser': False}},
        )
    except Exception as exc:  # noqa: BLE001 — 补齐失败不阻断启动
        print(
            f'[auth] role backfill failed: {type(exc).__name__}: {exc}',
            file=sys.stderr,
        )
