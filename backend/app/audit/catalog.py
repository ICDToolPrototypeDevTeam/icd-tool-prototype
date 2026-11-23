# -*- coding: utf-8 -*-
"""审计日志的固定词表与中文标签。

所有英文代码（模块、动作、角色、结果）在落库时就翻译成中文标签，查询接口
直接返回标签，避免前端散落翻译表。新增埋点时先在这里登记，保证筛选下拉框
与日志内容使用同一份词表。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.auth.roles import (
    ROLE_ADMIN,
    ROLE_ANONYMOUS,
    ROLE_AUDITOR,
    ROLE_SYSTEM,
    ROLE_USER,
)

CATEGORY_LOGIN = "login"
CATEGORY_OPERATION = "operation"

CATEGORY_LABELS = {
    CATEGORY_LOGIN: "登录日志",
    CATEGORY_OPERATION: "操作日志",
}

RESULT_SUCCESS = "success"
RESULT_FAILURE = "failure"

RESULT_LABELS = {
    RESULT_SUCCESS: "成功",
    RESULT_FAILURE: "失败",
}

MODULE_LABELS = {
    "auth": "登录认证",
    "users": "账号管理",
    "jobs": "任务管理",
    "outputs": "结果下载",
    "history": "历史结果",
    "settings": "模型设置",
    "hlr": "HLR 表格样式",
}

ACTION_TYPE_LABELS = {
    "login": "登录",
    "logout": "登出",
    "create": "创建",
    "update": "修改",
    "delete": "删除",
    "control": "任务控制",
    "download": "下载",
    "view": "查看",
    "config": "配置",
}

# action -> (中文动作, 动作类型, 所属模块)
ACTION_META: dict[str, tuple[str, str, str]] = {
    "login_success": ("登录成功", "login", "auth"),
    "login_failed": ("登录失败", "login", "auth"),
    "logout": ("退出登录", "logout", "auth"),
    "user_create": ("创建账号", "create", "users"),
    "user_update": ("修改账号", "update", "users"),
    "user_password_reset": ("重置密码", "update", "users"),
    "task_create": ("创建分析任务", "create", "jobs"),
    "task_resume": ("继续任务", "control", "jobs"),
    "task_abandon": ("放弃任务", "control", "jobs"),
    "task_cancel": ("终止任务", "control", "jobs"),
    "task_force_cancel": ("强制终止任务", "control", "jobs"),
    "output_download": ("下载结果文件", "download", "outputs"),
    "log_download": ("下载运行日志", "download", "history"),
    "reverse_matches_view": ("查看反向匹配明细", "view", "history"),
    "history_delete": ("删除历史结果", "delete", "history"),
    "settings_update": ("修改模型设置", "config", "settings"),
    "hlr_schema_create": ("新建表格样式", "create", "hlr"),
    "hlr_schema_update": ("修改表格样式", "update", "hlr"),
    "hlr_schema_delete": ("删除表格样式", "delete", "hlr"),
}

ROLE_FILTER_LABELS = {
    ROLE_USER: "业务用户",
    ROLE_ADMIN: "系统管理员",
    ROLE_AUDITOR: "审计管理员",
    ROLE_ANONYMOUS: "未登录",
    ROLE_SYSTEM: "系统",
}

TASK_TYPE_LABELS = {
    "correctness": "正确性分析",
    "completeness": "完整性分析",
}

TASK_STATUS_LABELS = {
    "pending": "等待中",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "interrupted": "已中断",
    "abandoned": "已放弃",
    "canceled": "已终止",
    "unknown": "未知",
}

PROFILE_LABELS = {
    "ams": "AMS 系统",
    "fgmc": "FGMC 系统",
    "hscu": "HSCU 系统",
    "rpdu": "RPDU 系统",
    "generic": "通用系统",
}

MODEL_LABELS = {
    "deepseek": "DeepSeek",
    "minimax": "MiniMax",
    "qwen": "Qwen",
}


def module_label(module: str | None) -> str:
    return MODULE_LABELS.get(module or "", module or "未知模块")


def action_meta(action: str | None) -> tuple[str, str, str]:
    return ACTION_META.get(
        action or "", (action or "未知操作", "view", "system")
    )


def action_label(action: str | None) -> str:
    return action_meta(action)[0]


def action_type_label(action_type: str | None) -> str:
    return ACTION_TYPE_LABELS.get(action_type or "", action_type or "其他")


def result_label(result: str | None) -> str:
    return RESULT_LABELS.get(result or "", "未知")


def task_type_label(task_type: str | None) -> str:
    return TASK_TYPE_LABELS.get(task_type or "", task_type or "未知任务")


def task_status_label(status: str | None) -> str:
    return TASK_STATUS_LABELS.get(status or "", status or "未知")


def profile_label(profile: str | None) -> str:
    return PROFILE_LABELS.get(profile or "", profile or "未知系统")


def model_label(model: str | None) -> str:
    return MODEL_LABELS.get(model or "", model or "未知模型")


def format_bytes(size: Any) -> str:
    """把字节数转成便于阅读的中文单位；非法值返回「未知」。"""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "未知"
    if value < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def parse_user_agent(user_agent: str | None) -> str | None:
    """把 User-Agent 压成「浏览器 / 操作系统」的中文短描述。

    只做展示用途，不参与任何安全判断；无法识别时返回「未知客户端」。
    """
    if not user_agent:
        return None
    ua = user_agent.strip()
    if not ua:
        return None
    if ua.lower() in {"testclient", "python-httpx", "httpx"}:
        return "接口测试客户端"

    browser = "未知浏览器"
    patterns = (
        (r"Edg/(\d+)", "Edge"),
        (r"OPR/(\d+)", "Opera"),
        (r"Firefox/(\d+)", "Firefox"),
        (r"Chrome/(\d+)", "Chrome"),
        (r"Version/(\d+).*Safari/", "Safari"),
        (r"curl/([\d.]+)", "curl"),
        (r"python-requests/([\d.]+)", "Python 请求库"),
    )
    for pattern, name in patterns:
        match = re.search(pattern, ua, re.IGNORECASE)
        if match:
            version = match.group(1).split(".")[0]
            browser = f"{name} {version}" if name not in {"curl", "Python 请求库"} else name
            break

    os_name = "未知系统"
    os_patterns = (
        (r"Windows NT 10\.0", "Windows 10/11"),
        (r"Windows NT 6\.3", "Windows 8.1"),
        (r"Windows NT 6\.1", "Windows 7"),
        (r"Windows", "Windows"),
        (r"Android[ /]([\d.]+)", "Android"),
        (r"iPhone", "iOS"),
        (r"iPad", "iPadOS"),
        (r"Mac OS X", "macOS"),
        (r"Linux", "Linux"),
    )
    for pattern, name in os_patterns:
        if re.search(pattern, ua, re.IGNORECASE):
            os_name = name
            break
    return f"{browser} / {os_name}"


def _value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (str, int, float)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 — 仅用于搜索文本，不能影响写日志
        return str(value)


def build_search_text(
    *,
    actor_username: str | None,
    actor_role_label: str,
    module_label_text: str,
    action_label_text: str,
    target_type: str | None,
    target_id: str | None,
    target_label: str | None,
    summary: str,
    detail: dict[str, Any],
    ip: str | None,
    client: str | None,
    result_label_text: str,
    error: str | None,
) -> str:
    """拼接关键词检索字段：日志可见内容都能被关键词命中。"""
    parts: list[str] = [
        actor_username or "",
        actor_role_label,
        module_label_text,
        action_label_text,
        target_type or "",
        target_id or "",
        target_label or "",
        summary,
        ip or "",
        client or "",
        result_label_text,
        error or "",
    ]
    for key, value in (detail or {}).items():
        parts.append(str(key))
        parts.append(_value_to_text(value))
    text = " | ".join(part for part in parts if part)
    return text[:4000]
