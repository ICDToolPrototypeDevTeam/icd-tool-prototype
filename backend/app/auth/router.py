# -*- coding: utf-8 -*-
"""认证路由：Cookie 登录 / 登出 / 当前用户 / 管理员账号管理。

只挂载登录与登出；注册、邮箱验证、找回密码不在原型范围内（账号由管理员创建）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from beanie import PydanticObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_users.exceptions import UserAlreadyExists

from app.auth.backend import auth_backend, fastapi_users
from app.auth.deps import current_admin, current_user
from app.auth.manager import user_manager
from app.auth.models import User
from app.auth.schemas import (
    MeResponse,
    UserCreateRequest,
    UserRead,
    UserUpdateRequest,
)


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


def _to_user_read(user: User) -> UserRead:
    """文档模型转接口模型；不暴露密码哈希与内部字段。"""
    return UserRead(
        id=str(user.id),
        username=user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        created_at=user.created_at,
    )


@router.get('/admin/users', response_model=list[UserRead])
async def list_users(_admin: User = Depends(current_admin)) -> list[UserRead]:
    """按创建时间升序返回全部账号。"""
    users = await User.find_all(sort='created_at').to_list()
    return [_to_user_read(user) for user in users]


@router.post(
    '/admin/users',
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: UserCreateRequest,
    _admin: User = Depends(current_admin),
) -> UserRead:
    """管理员创建账号；用户名大小写不敏感，重复时返回 409。"""
    try:
        user = await user_manager.create(payload)
    except UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='用户名已存在',
        ) from None
    return _to_user_read(user)


@router.patch('/admin/users/{user_id}', response_model=UserRead)
async def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    _admin: User = Depends(current_admin),
) -> UserRead:
    """管理员重置密码、禁用/启用账号、设置/取消管理员。

    最后一个活跃管理员不能被禁用或降级；其余账号的变更即时生效，
    被禁用用户的既有 Cookie 会在下一次请求时被拒绝。
    """
    try:
        object_id = PydanticObjectId(user_id)
    except InvalidId:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='用户不存在',
        ) from None

    user = await User.get(object_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='用户不存在',
        )

    next_is_active = user.is_active if payload.is_active is None else payload.is_active
    next_is_superuser = (
        user.is_superuser if payload.is_superuser is None else payload.is_superuser
    )
    was_active_admin = user.is_active and user.is_superuser
    stays_active_admin = next_is_active and next_is_superuser
    if was_active_admin and not stays_active_admin:
        active_admin_count = await User.find(
            User.is_superuser == True,  # noqa: E712 — Beanie 查询表达式
            User.is_active == True,  # noqa: E712
        ).count()
        if active_admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='系统必须至少保留一个活跃管理员',
            )

    set_fields: dict[str, object] = {}
    revoke = False

    if payload.password is not None:
        set_fields["hashed_password"] = user_manager.password_helper.hash(
            payload.password
        )
        revoke = True
    if payload.is_active is not None:
        if user.is_active and payload.is_active is False:
            revoke = True
        set_fields["is_active"] = payload.is_active
    if payload.is_superuser is not None:
        set_fields["is_superuser"] = payload.is_superuser

    if set_fields:
        set_fields["updated_at"] = datetime.now(timezone.utc)
        update: dict[str, dict[str, object]] = {"$set": set_fields}
        if revoke:
            update["$inc"] = {"token_version": 1}
        await user.update(update)
        refreshed = await User.get(object_id)
        if refreshed is not None:
            user = refreshed

    return _to_user_read(user)
