# -*- coding: utf-8 -*-
"""审计事件的构造、入队与查询。"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId
from starlette.requests import Request

from app.audit import catalog
from app.audit.models import AuditLog
from app.audit.schemas import (
    AuditCountItem,
    AuditHourPoint,
    AuditLogDetail,
    AuditLogItem,
    AuditLogListResponse,
    AuditMetaResponse,
    AuditOption,
    AuditOverviewResponse,
    AuditTotals,
)
from app.audit.writer import audit_writer
from app.auth.roles import (
    ROLE_ANONYMOUS,
    ROLE_SYSTEM,
    effective_role,
    role_label,
)
from app.execution import control


def elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _json_safe(value: Any) -> Any:
    """把日志 detail 归一成可写入 BSON 的基本类型。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _client_ip(request: Request | None) -> str | None:
    """只认直连地址；未引入可信代理配置前不解析 X-Forwarded-For。"""
    if request is None:
        return None
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return str(host) if host else None


def build_event(
    *,
    request: Request | None,
    category: str,
    module: str,
    action: str,
    summary: str,
    user=None,
    actor_username: str | None = None,
    actor_role: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
    detail: dict[str, Any] | None = None,
    result: str = catalog.RESULT_SUCCESS,
    error: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """构造一条审计文档（不写库）。"""
    actor_id: str | None = None
    if user is not None:
        actor_id = str(getattr(user, "id", "") or "") or None
        if actor_username is None:
            actor_username = getattr(user, "email", None)
        role = actor_role or effective_role(user)
    else:
        if actor_role is not None:
            role = actor_role
        elif actor_username:
            role = ROLE_ANONYMOUS
        else:
            role = ROLE_SYSTEM

    action_label, action_type, _ = catalog.action_meta(action)
    module_label_text = catalog.module_label(module)
    role_label_text = role_label(role)
    result_label_text = catalog.result_label(result)
    category_label = catalog.CATEGORY_LABELS.get(category, category)
    action_type_label = catalog.action_type_label(action_type)

    user_agent = None
    request_method = None
    request_path = None
    ip = None
    if request is not None:
        user_agent = request.headers.get("user-agent")
        request_method = request.method
        request_path = request.url.path
        ip = _client_ip(request)

    client = catalog.parse_user_agent(user_agent)
    safe_detail = _json_safe(detail or {})
    if not isinstance(safe_detail, dict):
        safe_detail = {"内容": safe_detail}

    search_text = catalog.build_search_text(
        actor_username=actor_username,
        actor_role_label=role_label_text,
        module_label_text=module_label_text,
        action_label_text=action_label,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        summary=summary,
        detail=safe_detail,
        ip=ip,
        client=client,
        result_label_text=result_label_text,
        error=error,
    )

    return {
        "ts": datetime.now(timezone.utc),
        "category": category,
        "category_label": category_label,
        "module": module,
        "module_label": module_label_text,
        "action": action,
        "action_label": action_label,
        "action_type": action_type,
        "action_type_label": action_type_label,
        "actor_id": actor_id,
        "actor_username": actor_username,
        "actor_role": role,
        "actor_role_label": role_label_text,
        "target_type": target_type,
        "target_id": target_id,
        "target_label": target_label,
        "summary": summary,
        "detail": safe_detail,
        "ip": ip,
        "user_agent": user_agent,
        "client": client,
        "result": result,
        "result_label": result_label_text,
        "error": error,
        "duration_ms": duration_ms,
        "request_method": request_method,
        "request_path": request_path,
        "search_text": search_text,
    }


def record_event(**kwargs: Any) -> None:
    """构造并入队；任何异常都不能影响业务请求。"""
    try:
        audit_writer.submit(build_event(**kwargs))
    except Exception as exc:  # noqa: BLE001
        print(
            f"[audit] build/queue failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------- 查询


def _to_item(doc: AuditLog) -> AuditLogItem:
    return AuditLogItem(
        id=str(doc.id),
        ts=doc.ts,
        category=doc.category,
        category_label=doc.category_label,
        module=doc.module,
        module_label=doc.module_label,
        action=doc.action,
        action_label=doc.action_label,
        action_type=doc.action_type,
        action_type_label=doc.action_type_label,
        actor_username=doc.actor_username,
        actor_role=doc.actor_role,
        actor_role_label=doc.actor_role_label,
        target_type=doc.target_type,
        target_id=doc.target_id,
        target_label=doc.target_label,
        summary=doc.summary,
        result=doc.result,
        result_label=doc.result_label,
        error=doc.error,
        duration_ms=doc.duration_ms,
        ip=doc.ip,
        client=doc.client,
        request_method=doc.request_method,
        request_path=doc.request_path,
    )


def _to_detail(doc: AuditLog) -> AuditLogDetail:
    base = _to_item(doc)
    return AuditLogDetail(
        **base.model_dump(),
        detail=doc.detail or {},
        user_agent=doc.user_agent,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_filter(
    *,
    start: datetime,
    end: datetime,
    category: str | None,
    module: str | None,
    action: str | None,
    result: str | None,
    actor: str | None,
    role: str | None,
    ip: str | None,
    keyword: str | None,
) -> dict[str, Any]:
    query: dict[str, Any] = {"ts": {"$gte": start, "$lte": end}}
    if category:
        query["category"] = category
    if module:
        query["module"] = module
    if action:
        query["action"] = action
    if result:
        query["result"] = result
    if role:
        query["actor_role"] = role
    if actor and actor.strip():
        query["actor_username"] = {
            "$regex": re.escape(actor.strip()),
            "$options": "i",
        }
    if ip and ip.strip():
        query["ip"] = {"$regex": re.escape(ip.strip()), "$options": "i"}
    if keyword and keyword.strip():
        query["search_text"] = {
            "$regex": re.escape(keyword.strip()),
            "$options": "i",
        }
    return query


async def query_logs(
    *,
    start: datetime | None,
    end: datetime | None,
    category: str | None,
    module: str | None,
    action: str | None,
    result: str | None,
    actor: str | None,
    role: str | None,
    ip: str | None,
    keyword: str | None,
    page: int,
    page_size: int,
) -> AuditLogListResponse:
    now = datetime.now(timezone.utc)
    end_utc = _as_utc(end) or now
    start_utc = _as_utc(start) or (end_utc - timedelta(days=7))
    if start_utc > end_utc:
        start_utc, end_utc = end_utc, start_utc

    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    query = _build_filter(
        start=start_utc,
        end=end_utc,
        category=category,
        module=module,
        action=action,
        result=result,
        actor=actor,
        role=role,
        ip=ip,
        keyword=keyword,
    )

    total, docs = await asyncio.gather(
        AuditLog.find(query).count(),
        AuditLog.find(query)
        .sort("-ts")
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(),
    )
    return AuditLogListResponse(
        items=[_to_item(doc) for doc in docs],
        total=int(total),
        page=page,
        page_size=page_size,
    )


async def get_log(log_id: str) -> AuditLogDetail | None:
    try:
        object_id = PydanticObjectId(log_id)
    except (InvalidId, TypeError):
        return None
    doc = await AuditLog.get(object_id)
    if doc is None:
        return None
    return _to_detail(doc)


async def _aggregate(pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collection = AuditLog.get_motor_collection()
    cursor = collection.aggregate(pipeline)
    return await cursor.to_list(length=None)


async def overview(hours: int = 24) -> AuditOverviewResponse:
    hours = min(168, max(1, int(hours)))
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    collection = AuditLog.get_motor_collection()

    totals_pipeline = [
        {"$match": {"ts": {"$gte": since}}},
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "login_success": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$category", catalog.CATEGORY_LOGIN]},
                                    {"$eq": ["$result", catalog.RESULT_SUCCESS]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "login_failure": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$category", catalog.CATEGORY_LOGIN]},
                                    {"$eq": ["$result", catalog.RESULT_FAILURE]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "operation_success": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$category",
                                            catalog.CATEGORY_OPERATION,
                                        ]
                                    },
                                    {"$eq": ["$result", catalog.RESULT_SUCCESS]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "operation_failure": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$category",
                                            catalog.CATEGORY_OPERATION,
                                        ]
                                    },
                                    {"$eq": ["$result", catalog.RESULT_FAILURE]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "actors": {"$addToSet": "$actor_username"},
            }
        },
    ]

    by_hour_pipeline = [
        {"$match": {"ts": {"$gte": since}}},
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d %H:00",
                        "date": "$ts",
                        "timezone": "+08:00",
                    }
                },
                "total": {"$sum": 1},
                "failure": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$result", catalog.RESULT_FAILURE]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {"$sort": {"_id": 1}},
    ]

    by_module_pipeline = [
        {"$match": {"ts": {"$gte": since}}},
        {
            "$group": {
                "_id": "$module",
                "label": {"$first": "$module_label"},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"count": -1}},
        {"$limit": 8},
    ]

    by_role_pipeline = [
        {"$match": {"ts": {"$gte": since}}},
        {
            "$group": {
                "_id": "$actor_role",
                "label": {"$first": "$actor_role_label"},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"count": -1}},
    ]

    (
        total_rows,
        hour_rows,
        module_rows,
        role_rows,
        recent_docs,
        active_users,
    ) = await asyncio.gather(
        _aggregate(totals_pipeline),
        _aggregate(by_hour_pipeline),
        _aggregate(by_module_pipeline),
        _aggregate(by_role_pipeline),
        AuditLog.find({"ts": {"$gte": since}}).sort("-ts").limit(8).to_list(),
        collection.distinct(
            "actor_username",
            {
                "ts": {"$gte": now - timedelta(minutes=30)},
                "category": catalog.CATEGORY_LOGIN,
                "result": catalog.RESULT_SUCCESS,
                "actor_username": {"$nin": [None, ""]},
            },
        ),
    )

    total_row = total_rows[0] if total_rows else {}
    actors = [item for item in total_row.get("actors") or [] if item]

    cn_tz = timezone(timedelta(hours=8))
    current_hour = datetime.now(cn_tz).replace(minute=0, second=0, microsecond=0)
    buckets = [
        current_hour - timedelta(hours=offset)
        for offset in range(hours - 1, -1, -1)
    ]
    hour_map = {str(row.get("_id")): row for row in hour_rows}
    by_hour: list[AuditHourPoint] = []
    for bucket in buckets:
        key = bucket.strftime("%Y-%m-%d %H:00")
        row = hour_map.get(key, {})
        by_hour.append(
            AuditHourPoint(
                hour=key,
                total=int(row.get("total") or 0),
                failure=int(row.get("failure") or 0),
            )
        )

    running_jobs: int | None
    try:
        running_jobs = control.active_count()
    except control.RedisUnavailable:
        running_jobs = None

    return AuditOverviewResponse(
        range_hours=hours,
        totals=AuditTotals(
            total=int(total_row.get("total") or 0),
            login_success=int(total_row.get("login_success") or 0),
            login_failure=int(total_row.get("login_failure") or 0),
            operation_success=int(total_row.get("operation_success") or 0),
            operation_failure=int(total_row.get("operation_failure") or 0),
            unique_actors=len(actors),
            active_users_30m=len([item for item in active_users if item]),
            running_jobs=running_jobs,
        ),
        by_hour=by_hour,
        by_module=[
            AuditCountItem(
                value=str(row.get("_id") or "unknown"),
                label=str(row.get("label") or catalog.module_label(row.get("_id"))),
                count=int(row.get("count") or 0),
            )
            for row in module_rows
        ],
        by_role=[
            AuditCountItem(
                value=str(row.get("_id") or "unknown"),
                label=str(
                    row.get("label") or catalog.ROLE_FILTER_LABELS.get(row.get("_id"), "未知")
                ),
                count=int(row.get("count") or 0),
            )
            for row in role_rows
        ],
        recent=[_to_item(doc) for doc in recent_docs],
    )


def meta() -> AuditMetaResponse:
    return AuditMetaResponse(
        categories=[
            AuditOption(value=value, label=label)
            for value, label in catalog.CATEGORY_LABELS.items()
        ],
        modules=[
            AuditOption(value=value, label=label)
            for value, label in catalog.MODULE_LABELS.items()
        ],
        actions=[
            AuditOption(
                value=action,
                label=label,
                module=module,
                module_label=catalog.module_label(module),
            )
            for action, (label, _action_type, module) in catalog.ACTION_META.items()
        ],
        roles=[
            AuditOption(value=value, label=label)
            for value, label in catalog.ROLE_FILTER_LABELS.items()
        ],
        results=[
            AuditOption(value=value, label=label)
            for value, label in catalog.RESULT_LABELS.items()
        ],
        time_presets=[
            AuditOption(value="1d", label="最近 24 小时"),
            AuditOption(value="7d", label="最近 7 天"),
            AuditOption(value="30d", label="最近 30 天"),
        ],
    )
