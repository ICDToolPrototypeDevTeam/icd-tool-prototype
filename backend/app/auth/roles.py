# -*- coding: utf-8 -*-
"""账号角色定义与兼容读取。

角色三值：

- ``user``    业务用户：使用分析工具；
- ``admin``   系统管理员：账号管理、模型设置，**不能**进入审计中心；
- ``auditor`` 审计管理员：**只能**使用审计中心。

历史账号（本次改动前创建）没有 ``role`` 字段，仅有 ``is_superuser``。读取时
``effective_role`` 会按 ``is_superuser`` 回退，启动时 ``ensure_user_roles`` 再
把缺失字段补齐并同步 ``is_superuser``，两者共同保证老账号行为不变。
"""
from __future__ import annotations

ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_AUDITOR = "auditor"
ROLE_ANONYMOUS = "anonymous"
ROLE_SYSTEM = "system"

VALID_ROLES = frozenset({ROLE_USER, ROLE_ADMIN, ROLE_AUDITOR})

ROLE_LABELS = {
    ROLE_USER: "业务用户",
    ROLE_ADMIN: "系统管理员",
    ROLE_AUDITOR: "审计管理员",
    ROLE_ANONYMOUS: "未登录",
    ROLE_SYSTEM: "系统",
}


def effective_role(user) -> str:
    """读取账号的有效角色；字段缺失/非法时按 ``is_superuser`` 兼容回退。"""
    raw = getattr(user, "role", None)
    if raw in VALID_ROLES:
        return raw
    return ROLE_ADMIN if bool(getattr(user, "is_superuser", False)) else ROLE_USER


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "", role or "未知")


def is_admin(user) -> bool:
    return effective_role(user) == ROLE_ADMIN


def is_auditor(user) -> bool:
    return effective_role(user) == ROLE_AUDITOR


def role_to_superuser(role: str) -> bool:
    """``is_superuser`` 是历史权限字段，必须与角色保持同步。"""
    return role == ROLE_ADMIN
