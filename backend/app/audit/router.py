# -*- coding: utf-8 -*-
"""审计中心接口。

全部接口只允许审计管理员访问；系统管理员与业务用户均返回 403。
审计日志查询本身不再写审计日志（否则每看一次日志就多一条日志）。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.audit import service, system
from app.audit.schemas import (
    AuditHistoryResponse,
    AuditLogDetail,
    AuditLogListResponse,
    AuditMetaResponse,
    AuditOverviewResponse,
    AuditSystemResponse,
)
from app.auth.deps import current_auditor
from app.auth.models import User


router = APIRouter(prefix='/audit', tags=['审计中心'])


@router.get('/logs', response_model=AuditLogListResponse)
async def list_logs(
    start: datetime | None = Query(None, description='开始时间（ISO 8601）'),
    end: datetime | None = Query(None, description='结束时间（ISO 8601）'),
    category: str | None = Query(None, description='日志类别：login / operation'),
    module: str | None = Query(None, description='操作模块'),
    action: str | None = Query(None, description='操作动作'),
    result: str | None = Query(None, description='结果：success / failure'),
    actor: str | None = Query(None, description='操作人账号（模糊匹配）'),
    role: str | None = Query(None, description='操作人角色'),
    ip: str | None = Query(None, description='IP 地址（模糊匹配）'),
    keyword: str | None = Query(None, description='关键词（全文匹配可见内容）'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _auditor: User = Depends(current_auditor),
) -> AuditLogListResponse:
    return await service.query_logs(
        start=start,
        end=end,
        category=category,
        module=module,
        action=action,
        result=result,
        actor=actor,
        role=role,
        ip=ip,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.get('/logs/{log_id}', response_model=AuditLogDetail)
async def get_log(
    log_id: str,
    _auditor: User = Depends(current_auditor),
) -> AuditLogDetail:
    detail = await service.get_log(log_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='日志不存在',
        )
    return detail


@router.get('/meta', response_model=AuditMetaResponse)
async def get_meta(
    _auditor: User = Depends(current_auditor),
) -> AuditMetaResponse:
    """筛选下拉框词表；与日志内容使用同一份中文标签表。"""
    return service.meta()


@router.get('/overview', response_model=AuditOverviewResponse)
async def get_overview(
    hours: int = Query(24, ge=1, le=168, description='统计时间范围（小时）'),
    _auditor: User = Depends(current_auditor),
) -> AuditOverviewResponse:
    return await service.overview(hours)


@router.get('/system', response_model=AuditSystemResponse)
async def get_system(
    _auditor: User = Depends(current_auditor),
) -> AuditSystemResponse:
    """系统运行状态：容器资源、任务进程、队列、依赖与告警。"""
    return await system.build_system_payload()


@router.get('/system/history', response_model=AuditHistoryResponse)
def get_system_history(
    minutes: int = Query(30, ge=1, le=60, description='回看时长（分钟）'),
    _auditor: User = Depends(current_auditor),
) -> AuditHistoryResponse:
    return system.build_history(minutes)
