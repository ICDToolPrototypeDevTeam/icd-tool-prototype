# -*- coding: utf-8 -*-
"""MongoDB 客户端与 Beanie 初始化。"""
from __future__ import annotations

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from app.auth.config import AuthSettings, get_auth_settings
from app.auth.models import User

_client: AsyncIOMotorClient | None = None


async def init_db(settings: AuthSettings | None = None) -> None:
    """连接 Mongo 并初始化 Beanie；Mongo 不可达时抛异常让启动失败。"""
    global _client
    settings = settings or get_auth_settings()

    client = AsyncIOMotorClient(
        settings.mongo_url,
        serverSelectionTimeoutMS=3000,
    )
    try:
        await client.admin.command('ping')
        await init_beanie(
            database=client[settings.mongo_db],
            document_models=[User],
        )
    except Exception:
        client.close()
        raise
    _client = client


async def close_db() -> None:
    """关闭 Mongo 客户端（幂等）。"""
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def ping_db() -> None:
    """探活：未初始化或 ping 失败时抛异常。"""
    if _client is None:
        raise RuntimeError('MongoDB 未初始化')
    await _client.admin.command('ping')
