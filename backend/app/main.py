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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v4.router import router as v4_router
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


app = FastAPI(title='ICD工具原型', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:3000'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# V4: /api/v4/* 路由（V4 反向管线 + 3 类下载）
app.include_router(v4_router, prefix='/api/v4')

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
            candidate = _static_dir / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(_static_dir / 'index.html')
