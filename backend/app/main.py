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
from app.job_log import install_log_tee, set_config_lines
from app.job_manager import job_manager
from app.v4.config import get_output_root

# 安装 stdout/stderr Tee（幂等）。必须尽早执行，使应用自身的 print 都能被
# 任务日志面板采集。uvicorn 的 logging handler 可能在本模块之后构造，其访问
# 日志会流经 Tee，但运行在事件循环线程（无 job 归属）→ 只落进全局 buffer，
# 不会污染任何任务的日志，而全局 buffer 不对外暴露。
install_log_tee()


def _config_snapshot() -> list[str]:
    """启动配置自检：只报「有没有」，绝不打印 key 内容。

    依据：实测中曾因「本地容器未关闭」误判 mock 生效情况，无从核对运行配置。
    本行会写进每个任务的日志头部，让「跑的是哪个模式 / 哪份配置」在 UI 上始终可见。
    """
    key_state = ' '.join(
        f'{name}={"yes" if (os.getenv(f"{name}_API_KEY") or "").strip() else "no"}'
        for name in ('DEEPSEEK', 'MINIMAX', 'QWEN')
    )
    return [
        '[config] USE_MOCK_LLM={} (容器配置) | {} | JUDGE_PROVIDERS={} | '
        'OUTPUT_DIR={} | STATIC_DIR={}'.format(
            os.getenv('USE_MOCK_LLM', '0'),
            key_state,
            os.getenv('JUDGE_PROVIDERS', '(默认)'),
            get_output_root(),
            os.getenv('ICD_STATIC_DIR', '(未设置)'),
        )
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动扫描：把上一个进程遗留的未完成任务标记为 interrupted，供前端选择
    # 继续 / 放弃。任何异常都不得影响启动。
    try:
        job_manager.load_interrupted(get_output_root() / 'v4')
    except Exception as e:  # noqa: BLE001
        print(f'[job] interrupted-job scan failed: {type(e).__name__}: {e}', file=sys.stderr)
    # 启动配置自检：写进全局日志缓冲，并在每个任务日志开头重复
    set_config_lines(_config_snapshot())
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
