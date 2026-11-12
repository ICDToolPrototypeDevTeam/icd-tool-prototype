# -*- coding: utf-8 -*-
"""ICD 工具原型 FastAPI 入口。

- 顶层 FastAPI app 仅做 CORS、子 router 装载、启动扫描与静态前端 serve；
- V4 路由通过 `app.include_router(v4_router, prefix="/api/v4")` 装载（来自 `app.api.v4.router`）；
- 不在 main.py 写业务逻辑；所有路由逻辑在子 router 文件中。
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError

# 必须在导入 app.api.v4.router 之前加载 backend/.env：auth/backend.py 在导入期
# 构造 auth_backend，需要 AUTH_JWT_SECRET 等环境变量。容器内由 compose env_file 注入。
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

from app.api.v4.router import router as v4_router
from app.auth.db import close_db, init_db
from app.auth.seed import ensure_admin_seed
from app.execution.config import (
    config_snapshot,
    use_celery,
    validate_celery_runtime,
)
from app.job_log import install_log_tee, set_config_lines
from app.job_manager import job_manager
from app.runtime_settings import load_persisted_settings
from app.v4.config import get_output_root

# 安装 stdout/stderr Tee（幂等）。必须尽早执行，使应用自身的 print 都能被
# 任务日志面板采集。uvicorn 的 logging handler 可能在本模块之后构造，其访问
# 日志会流经 Tee，但运行在事件循环线程（无 job 归属）→ 只落进全局 buffer，
# 不会污染任何任务的日志，而全局 buffer 不对外暴露。
install_log_tee()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 先回读前端保存的模型设置，使其覆盖 .env 并体现在下面的配置自检里。
    # 设置文件损坏 / 无权限时只告警，回落 .env，不影响启动。
    try:
        load_persisted_settings()
    except Exception as e:  # noqa: BLE001
        print(f'[settings] load failed: {type(e).__name__}: {e}', file=sys.stderr)
    # Mongo 是登录与账号管理的硬依赖：不可达或初始管理员创建失败时直接让启动失败，
    # 不做静默降级（依据用户鉴权与隔离设计 D12）。
    try:
        await init_db()
        await ensure_admin_seed()
    except Exception as e:  # noqa: BLE001
        print(f'[auth] init failed: {type(e).__name__}: {e}', file=sys.stderr)
        raise
    # Celery 模式自检：缺依赖只告警（history 等只读接口仍可用），
    # 真正提交任务时 dispatcher 会返回 503 并在任务日志里说明原因。
    if use_celery():
        problems = validate_celery_runtime()
        if problems:
            for problem in problems:
                print(f'[execution] {problem}', file=sys.stderr)
        else:
            print('[execution] celery mode enabled; broker config loaded')
    # 启动扫描：把上一个进程遗留的未完成任务按执行模式处理（见方法 docstring），
    # 供前端选择继续 / 放弃。任何异常都不得影响启动。
    try:
        job_manager.load_interrupted(get_output_root() / 'v4')
    except Exception as e:  # noqa: BLE001
        print(f'[job] interrupted-job scan failed: {type(e).__name__}: {e}', file=sys.stderr)
    # 启动配置自检：写进全局日志缓冲，并在每个任务日志开头重复
    set_config_lines(config_snapshot())

    # Celery 模式：API 侧巡检失联 Worker（inline 模式没有跨进程任务，不需要）
    reaper_stop = None
    if use_celery():
        from app.execution.reaper import start_reaper

        reaper_stop = start_reaper(get_output_root() / 'v4')
    yield
    if reaper_stop is not None:
        reaper_stop.set()
    await close_db()


# 交互式文档默认开启，便于开发；服务器部署通过 ICD_DOCS_ENABLED=0/false/no 关闭，
# 避免把 /docs、/openapi.json 暴露在生产端口上（设计 D11）。
_docs_enabled = (
    os.getenv('ICD_DOCS_ENABLED', '1').strip().lower() not in ('0', 'false', 'no')
)

app = FastAPI(
    title='ICD工具原型',
    lifespan=lifespan,
    docs_url='/docs' if _docs_enabled else None,
    redoc_url='/redoc' if _docs_enabled else None,
    openapi_url='/openapi.json' if _docs_enabled else None,
)


@app.exception_handler(PyMongoError)
async def _mongo_unavailable_handler(request, exc: PyMongoError) -> JSONResponse:
    """运行中 Mongo 失联时，认证依赖抛出的 PyMongoError 统一转 503，不返回 500 堆栈。"""
    return JSONResponse(status_code=503, content={'detail': 'database unavailable'})


app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:3000'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# V4: /api/v4/* 路由（V4 反向管线 + 3 类下载）
app.include_router(v4_router, prefix='/api/v4')


def _static_path_is_unsafe(static_dir: Path, full_path: str) -> bool:
    """解码后的路径含 `..` 段，或解析后越出静态目录时为 True。

    服务器部署中 output/ 与 static/ 同级，SPA 兜底若直接拼接路径，可通过
    `../output/...`（含 `%2e%2e` 编码形式）绕过 API 鉴权读取任务产物或 .env。
    设计 D11 要求这类请求直接 404，不做路径纠正（不回落 index.html）。
    """
    decoded = unquote(full_path)
    if '..' in Path(decoded).parts:
        return True
    try:
        resolved_dir = static_dir.resolve()
        (resolved_dir / decoded).resolve().relative_to(resolved_dir)
    except (OSError, ValueError):
        return True
    return False


def _safe_static_candidate(static_dir: Path, full_path: str) -> Path | None:
    """把请求路径解析到静态目录内；越界、含 `..` 段或文件不存在时返回 None。"""
    if _static_path_is_unsafe(static_dir, full_path):
        return None
    resolved = (static_dir.resolve() / unquote(full_path)).resolve()
    return resolved if resolved.is_file() else None


# 前端静态资源挂载 + SPA fallback，两种触发方式：
#   1) 桌面打包：PyInstaller 运行时（sys.frozen=True），取可执行文件同级 static/；
#   2) 单端口部署：ICD_STATIC_DIR 环境变量指定目录（前端产物由后端同源托管）。
# 两者都不满足时（本地开发 / 双容器 compose）不挂载，后端保持纯 API 服务，行为不变。
_static_env = os.getenv('ICD_STATIC_DIR')
if _static_env or getattr(sys, 'frozen', False):
    _static_dir = (
        Path(_static_env) if _static_env
        else Path(sys.executable).resolve().parent / 'static'
    )
    if _static_dir.exists():
        _assets_dir = _static_dir / 'assets'
        if _assets_dir.exists():
            app.mount('/assets', StaticFiles(directory=_assets_dir), name='assets')

        @app.get('/{full_path:path}', include_in_schema=False)
        async def _spa(full_path: str):
            # /api 未匹配路由必须走 JSON 404，不能回落成前端 index.html。
            if full_path == 'api' or full_path.startswith('api/'):
                raise HTTPException(status_code=404, detail='Not Found')
            # 文档关闭时，这几个路径也不能被 SPA 兜底接住。
            if not _docs_enabled and full_path in ('docs', 'redoc', 'openapi.json'):
                raise HTTPException(status_code=404, detail='Not Found')
            # 穿越 / 越界请求一律 404，不能回落 index.html（设计 D11「不做纠正」）。
            if _static_path_is_unsafe(_static_dir, full_path):
                raise HTTPException(status_code=404, detail='Not Found')
            candidate = _safe_static_candidate(_static_dir, full_path)
            if candidate is not None:
                return FileResponse(candidate)
            index_file = _static_dir / 'index.html'
            if index_file.is_file():
                return FileResponse(index_file)
            raise HTTPException(status_code=404, detail='Not Found')
