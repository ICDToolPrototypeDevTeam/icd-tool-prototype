# -*- coding: utf-8 -*-
"""ICD 工具原型 FastAPI 入口。

- 顶层 FastAPI app 仅做 CORS 与子 router 装载；
- V4 路由通过 `app.include_router(v4_router, prefix="/api/v4")` 装载（来自 `app.api.v4.router`）；
- 不在 main.py 写业务逻辑；所有路由逻辑在子 router 文件中。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v4.router import router as v4_router


app = FastAPI(title='ICD工具原型')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:3000'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# V4: /api/v4/* 路由（V4 反向管线 + 3 类下载）
app.include_router(v4_router, prefix='/api/v4')
