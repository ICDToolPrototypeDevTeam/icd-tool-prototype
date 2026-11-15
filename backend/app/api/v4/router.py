# -*- coding: utf-8 -*-
"""V4.0 FastAPI Router 聚合。

- 顶层装载：`app.include_router(router, prefix='/api/v4')`；
- 包含 health + auth + audit + coverage + completeness + jobs + outputs +
  history + settings + hlr_schemas；
- 业务路由统一挂 ``require_business_access``：审计管理员只能使用审计中心。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.v4 import completeness, coverage, history, hlr_schemas, jobs, outputs, settings
from app.audit.router import router as audit_router
from app.auth.db import ping_db
from app.auth.deps import require_business_access
from app.auth.router import router as auth_router


router = APIRouter()

_business_only = [Depends(require_business_access)]


@router.get('/health')
async def v4_health():
    """V4 健康检查；连带探测 MongoDB 可用性（未登录可访问）。"""
    try:
        await ping_db()
    except Exception:
        raise HTTPException(status_code=503, detail='database unavailable')
    return {'status': 'ok', 'api_version': 'v4', 'database': 'ok'}


router.include_router(auth_router)
router.include_router(audit_router)
router.include_router(coverage.router, dependencies=_business_only)
router.include_router(completeness.router, dependencies=_business_only)
router.include_router(jobs.router, dependencies=_business_only)
router.include_router(outputs.router, dependencies=_business_only)
router.include_router(history.router, dependencies=_business_only)
router.include_router(settings.router, dependencies=_business_only)
router.include_router(hlr_schemas.router, dependencies=_business_only)
