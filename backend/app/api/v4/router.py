# -*- coding: utf-8 -*-
"""V4.0 FastAPI Router 聚合。

- 顶层装载：`app.include_router(router, prefix='/api/v4')`；
- 包含 health + auth + coverage + completeness + jobs + outputs + history +
  settings + hlr_schemas 共 9 个对外路由。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v4 import completeness, coverage, history, hlr_schemas, jobs, outputs, settings
from app.auth.db import ping_db
from app.auth.router import router as auth_router


router = APIRouter()


@router.get('/health')
async def v4_health():
    """V4 健康检查；连带探测 MongoDB 可用性（未登录可访问）。"""
    try:
        await ping_db()
    except Exception:
        raise HTTPException(status_code=503, detail='database unavailable')
    return {'status': 'ok', 'api_version': 'v4', 'database': 'ok'}


router.include_router(auth_router)
router.include_router(coverage.router)
router.include_router(completeness.router)
router.include_router(jobs.router)
router.include_router(outputs.router)
router.include_router(history.router)
router.include_router(settings.router)
router.include_router(hlr_schemas.router)
