# -*- coding: utf-8 -*-
"""ICD 工具原型 FastAPI 入口。

- 顶层 FastAPI app 仅做 CORS、子 router 装载、启动扫描与静态前端 serve；
- V4 路由通过 `app.include_router(v4_router, prefix="/api/v4")` 装载（来自 `app.api.v4.router`）；
- 不在 main.py 写业务逻辑；所有路由逻辑在子 router 文件中。
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v4.router import router as v4_router
from app.job_manager import job_manager
from app.v4.config import get_output_root


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动扫描：把上一个进程遗留的未完成任务标记为 interrupted，供前端选择
    # 继续 / 放弃。任何异常都不得影响启动。
    try:
        job_manager.load_interrupted(get_output_root() / 'v4')
    except Exception as e:  # noqa: BLE001
        print(f'[job] interrupted-job scan failed: {type(e).__name__}: {e}', file=sys.stderr)
    yield


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

# 桌面打包环境：从可执行文件同级 static/ 目录挂载前端静态资源 + SPA fallback。
# 仅当 PyInstaller 打包（sys.frozen=True）且 static/ 存在时生效；Docker / 开发
# 环境（非 frozen）不执行，后端保持纯 API 服务，行为不变。
if getattr(sys, 'frozen', False):
    _static_dir = Path(sys.executable).resolve().parent / 'static'
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
