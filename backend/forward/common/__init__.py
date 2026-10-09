# -*- coding: utf-8 -*-
"""common — EoICD → HLR 正向检查集成项目的公共工具包。

对外暴露：
    safe_load_json        字符串安全的（可带注释）JSON 加载
    save_json             JSON 保存（自动创建父目录）
    strip_json_comments   字符串安全的 // 注释剥离
    load_yaml             YAML 配置加载
    PROJECTS              全项目统一的系统配置（新增系统只改这里）
    get_project / project_ids / describe   系统配置读取辅助函数

子模块（EoICD侧数据处理 / 匹配模块 / 单judge结果汇总）应复用这里的实现，
避免同一逻辑在多处复制导致「改一处漏一处」。
"""
from .jsonio import (
    load_yaml,
    safe_load_json,
    save_json,
    strip_json_comments,
)
from .projects import (
    PROJECTS,
    get_project,
    project_ids,
    describe,
)

__all__ = [
    "safe_load_json", "save_json", "strip_json_comments", "load_yaml",
    "PROJECTS", "get_project", "project_ids", "describe",
]
