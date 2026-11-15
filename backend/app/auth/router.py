# -*- coding: utf-8 -*-
"""认证路由：Cookie 登录 / 登出 / 当前用户 / 管理员账号管理。

登录与登出使用自定义路由（而不是 fastapi-users 自带路由），以便记录审计日志。
失败原因只写服务端审计日志，客户端统一返回「用户名或密码错误」，不泄露账号是否存在。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from beanie import PydanticObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.exceptions import UserAlreadyExists, UserNotExists

from app.audit import catalog
from app.audit.service import elapsed_ms, record_event
from app.auth.backend import auth_backend, current_active_user
from app.auth.config import get_auth_settings
from app.auth.deps import current_admin, current_user
from app.auth.manager import user_manager
from app.auth.models import User
from app.auth.roles import (
    ROLE_ADMIN,
    ROLE_ANONYMOUS,
    ROLE_AUDITOR,
    ROLE_USER,
    effective_role,
    is_admin,
    is_auditor,
    role_label,
    role_to_superuser,
)
from app.auth.schemas import (
    MeResponse,
    UserCreateRequest,
    UserRead,
    UserUpdateRequest,
)


router = APIRouter()

_settings = get_auth_settings()


@router.post('/auth/login')
async def login(
    credentials: OAuth2PasswordRequestForm = Depends(),
    request: Request = None,
):
    """Cookie 登录，并记录审计日志（成功与失败都记）。"""
    started = time.perf_counter()
    username = (credentials.username or '').strip().lower()
    user: User | None = None
    reason: str | None = None

    try:
        user = await user_manager.get_by_email(username)
    except UserNotExists:
        user = None
    except Exception:  # noqa: BLE001 — 查询异常按登录失败处理，错误写日志
        user = None
        reason = '账号查询异常'

    if user is None:
        # 账号不存在时也做一次哈希，避免「响应时间差」暴露账号是否存在。
        try:
            user_manager.password_helper.hash(credentials.password)
        except Exception:  # noqa: BLE001 — 仅用于抵消耗时
            pass
        reason = reason or '账号不存在'
    elif not user.is_active:
        reason = '账号已被禁用'
    else:
        verified, updated_hash = user_manager.password_helper.verify_and_update(
            credentials.password, user.hashed_password
        )
        if not verified:
            reason = '密码错误'
        elif updated_hash is not None:
            # 密码哈希算法升级：与 fastapi-users 的 authenticate 行为一致
            user.hashed_password = updated_hash
            user.updated_at = datetime.now(timezone.utc)
            await user.save()

    if reason is not None:
        record_event(
            request=request,
            category=catalog.CATEGORY_LOGIN,
            module='auth',
            action='login_failed',
            summary=f'账号「{username or "(空)"}」登录失败：{reason}',
            actor_username=username or None,
            actor_role=ROLE_ANONYMOUS,
            detail={'登录账号': username or '(空)', '失败原因': reason},
            result=catalog.RESULT_FAILURE,
            error=reason,
            duration_ms=elapsed_ms(started),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='用户名或密码错误',
        )

    assert user is not None
    strategy = auth_backend.get_strategy()
    response = await auth_backend.login(strategy, user)
    role = effective_role(user)
    record_event(
        request=request,
        category=catalog.CATEGORY_LOGIN,
        module='auth',
        action='login_success',
        summary=f'{user.email}（{role_label(role)}）登录成功',
        user=user,
        target_type='user',
        target_id=str(user.id),
        target_label=user.email,
        detail={
            '登录账号': user.email,
            '角色': role_label(role),
            '会话有效期': f'{max(1, _settings.token_ttl_seconds // 3600)} 小时',
        },
        duration_ms=elapsed_ms(started),
    )
    return response


@router.post('/auth/logout')
async def logout(
    user: User = Depends(current_active_user),
    request: Request = None,
):
    """退出登录并清除 Cookie。"""
    started = time.perf_counter()
    strategy = auth_backend.get_strategy()
    token = (
        request.cookies.get(auth_backend.transport.cookie_name)
        if request is not None
        else None
    )
    response = await auth_backend.logout(strategy, user, token)
    role = effective_role(user)
    record_event(
        request=request,
        category=catalog.CATEGORY_OPERATION,
        module='auth',
        action='logout',
        summary=f'{user.email}（{role_label(role)}）退出登录',
        user=user,
        target_type='user',
        target_id=str(user.id),
        target_label=user.email,
        detail={'账号': user.email, '角色': role_label(role)},
        duration_ms=elapsed_ms(started),
    )
    return response


@router.get('/auth/me', response_model=MeResponse)
async def read_current_user(user: User = Depends(current_user)) -> MeResponse:
    """返回当前登录用户；`username` 对应 `User.email`（写入时已小写）。"""
    role = effective_role(user)
    return MeResponse(
        id=str(user.id),
        username=user.email,
        is_admin=is_admin(user),
        role=role,
        role_label=role_label(role),
        is_auditor=is_auditor(user),
    )


def _to_user_read(user: User) -> UserRead:
    """文档模型转接口模型；不暴露密码哈希与内部字段。"""
    role = effective_role(user)
    return UserRead(
        id=str(user.id),
        username=user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        role=role,
        role_label=role_label(role),
        is_auditor=is_auditor(user),
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
    request: Request = None,
) -> UserRead:
    """管理员创建账号；用户名大小写不敏感，重复时返回 409。"""
    try:
        user = await user_manager.create(payload)
    except UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='用户名已存在',
        ) from None
    role = effective_role(user)
    record_event(
        request=request,
        category=catalog.CATEGORY_OPERATION,
        module='users',
        action='user_create',
        summary=f'创建账号「{user.email}」（{role_label(role)}）',
        user=_admin,
        target_type='user',
        target_id=str(user.id),
        target_label=user.email,
        detail={'账号': user.email, '角色': role_label(role), '账号状态': '启用'},
    )
    return _to_user_read(user)


@router.patch('/admin/users/{user_id}', response_model=UserRead)
async def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    _admin: User = Depends(current_admin),
    request: Request = None,
) -> UserRead:
    """管理员重置密码、禁用/启用账号、修改角色。

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

    current_role = effective_role(user)
    if payload.role is not None:
        next_role = payload.role
    elif payload.is_superuser is not None:
        if payload.is_superuser:
            next_role = ROLE_ADMIN
        else:
            # 旧前端「取消管理员」：审计账号保持审计角色，其余降为业务用户
            next_role = ROLE_AUDITOR if current_role == ROLE_AUDITOR else ROLE_USER
    else:
        next_role = current_role

    next_is_active = user.is_active if payload.is_active is None else payload.is_active
    was_active_admin = user.is_active and current_role == ROLE_ADMIN
    stays_active_admin = next_is_active and next_role == ROLE_ADMIN
    if was_active_admin and not stays_active_admin:
        # is_superuser 与 role 在创建/更新/启动补齐时保持同步，用它统计可兼容
        # 尚未回填 role 字段的历史账号（否则可能误判「还有管理员」）。
        active_admin_count = await User.find(
            User.is_superuser == True,  # noqa: E712 — Beanie 查询表达式
            User.is_active == True,  # noqa: E712
        ).count()
        if active_admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='系统必须至少保留一个活跃管理员',
            )

    changes: dict[str, str] = {}
    if payload.password is not None:
        user.hashed_password = user_manager.password_helper.hash(payload.password)
        changes['密码'] = '已重置'
    if payload.is_active is not None and payload.is_active != user.is_active:
        changes['账号状态'] = '启用' if payload.is_active else '禁用'
        user.is_active = payload.is_active
    if next_role != current_role:
        changes['角色'] = f'{role_label(current_role)} → {role_label(next_role)}'
        user.role = next_role
    user.is_superuser = role_to_superuser(next_role)

    changed = bool(changes)
    if changed:
        user.updated_at = datetime.now(timezone.utc)
        await user.save()

    if changed:
        action = (
            'user_password_reset'
            if set(changes) == {'密码'}
            else 'user_update'
        )
        summary = '；'.join(f'{key}{value}' for key, value in changes.items())
        record_event(
            request=request,
            category=catalog.CATEGORY_OPERATION,
            module='users',
            action=action,
            summary=f'修改账号「{user.email}」：{summary}',
            user=_admin,
            target_type='user',
            target_id=str(user.id),
            target_label=user.email,
            detail={'账号': user.email, **changes, '当前角色': role_label(effective_role(user))},
        )

    return _to_user_read(user)
