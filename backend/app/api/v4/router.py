# -*- coding: utf-8 -*-
"""V4.0 FastAPI Router 聚合。

- 顶层装载：`app.include_router(router, prefix='/api/v4')`；
- 包含 health + coverage + jobs + outputs 共 5 个对外路由。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v4 import completeness, coverage, jobs, outputs


router = APIRouter()


@router.get('/health')
def v4_health():
    """V4 专用健康检查；返回 V4 入口可达性。"""
    return {'status': 'ok', 'api_version': 'v4'}


router.include_router(coverage.router)
router.include_router(completeness.router)
router.include_router(jobs.router)
router.include_router(outputs.router)
