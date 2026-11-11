# -*- coding: utf-8 -*-
"""任务归属统一访问校验。

所有 job 级接口（状态、结果、日志、下载、中间产物）都从这里判断访问权限：

- 权限键只认 ``owner_id``（``str(User.id)``）；``owner_username`` 仅用于展示；
- 非管理员访问未归属或他人任务一律 404，不暴露任务是否存在；
- 管理员放行全部任务。

本模块只依赖 ``job_manager`` / ``get_output_root`` / ``MANIFEST_NAME``，
不导入 ``app.api.v4`` 下的路由模块，避免循环依赖。
"""
from __future__ import annotations

import json
import re

from fastapi import HTTPException

from app.auth.models import User
from app.job_manager import MANIFEST_NAME, job_manager
from app.v4.config import get_output_root


# job_id 白名单：只接受 UUID 的规范文本形式。既用于定位磁盘 manifest，也用于
# 下载路径拼接前的校验（``..`` / 路径分隔符 / 编码混淆都匹配不上）。
_JOB_ID_RE = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)


def is_valid_job_id(job_id: str) -> bool:
    """job_id 是否为规范 UUID 文本（防止拼出 ``output/v4/`` 之外的路径）。"""
    return bool(_JOB_ID_RE.match(job_id or ''))


def get_job_owner(job_id: str) -> tuple[str | None, str | None]:
    """取任务归属 ``(owner_id, owner_username)``。

    先查内存（本进程新建 / 启动扫描恢复的任务）；内存没有则读磁盘
    ``output/v4/{job_id}/job.json``。job_id 非法、manifest 缺失或损坏时返回
    ``(None, None)``：调用方按「未归属」处理（普通用户 404；管理员放行后由
    后续的文件存在性检查决定是否 404）。
    """
    job = job_manager.get_job(job_id)
    if job is not None:
        return job.owner_id, job.owner_username
    if not is_valid_job_id(job_id):
        return None, None
    manifest = get_output_root() / 'v4' / job_id / MANIFEST_NAME
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001 — 目录被删 / JSON 损坏一律按「未归属」处理
        return None, None
    if not isinstance(data, dict):
        return None, None
    owner_id = data.get('owner_id')
    owner_username = data.get('owner_username')
    return (
        owner_id if isinstance(owner_id, str) else None,
        owner_username if isinstance(owner_username, str) else None,
    )


def ensure_owner(owner_id: str | None, user: User) -> None:
    """确认 ``owner_id`` 属于 ``user``；管理员直接放行。

    未归属（``None`` / 空串）或归属不是当前用户时统一 404 —— 不区分「任务不存在」
    与「无权访问」，避免用 404/403 差异探测他人任务。
    """
    if user.is_superuser:
        return
    if not owner_id or owner_id != str(user.id):
        raise HTTPException(status_code=404, detail='job not found')


def require_job_access(job_id: str, user: User) -> None:
    """job 级接口的统一入口校验；不通过时抛 404。"""
    owner_id, _ = get_job_owner(job_id)
    ensure_owner(owner_id, user)
