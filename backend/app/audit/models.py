# -*- coding: utf-8 -*-
"""审计日志 MongoDB 文档模型。

日志默认永久保留，不提供删除接口。所有展示字段在写入时已翻译为中文，
``search_text`` 是关键词检索用的拼接文本。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Document):
    ts: datetime = Field(default_factory=_utcnow)
    category: str
    category_label: str
    module: str
    module_label: str
    action: str
    action_label: str
    action_type: str
    action_type_label: str

    actor_id: str | None = None
    actor_username: str | None = None
    actor_role: str = "anonymous"
    actor_role_label: str = "未登录"

    target_type: str | None = None
    target_id: str | None = None
    target_label: str | None = None

    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    ip: str | None = None
    user_agent: str | None = None
    client: str | None = None
    result: str = "success"
    result_label: str = "成功"
    error: str | None = None
    duration_ms: int | None = None
    request_method: str | None = None
    request_path: str | None = None
    search_text: str = ""

    class Settings:
        name = "audit_logs"
        indexes = [
            IndexModel([("ts", DESCENDING)], name="audit_ts_desc"),
            IndexModel(
                [("category", ASCENDING), ("ts", DESCENDING)],
                name="audit_category_ts",
            ),
            IndexModel(
                [("actor_username", ASCENDING), ("ts", DESCENDING)],
                name="audit_actor_ts",
            ),
            IndexModel(
                [("module", ASCENDING), ("ts", DESCENDING)],
                name="audit_module_ts",
            ),
            IndexModel(
                [("action", ASCENDING), ("ts", DESCENDING)],
                name="audit_action_ts",
            ),
            IndexModel(
                [("result", ASCENDING), ("ts", DESCENDING)],
                name="audit_result_ts",
            ),
        ]
