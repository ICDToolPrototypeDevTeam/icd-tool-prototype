# -*- coding: utf-8 -*-
"""正向「系统类型（项目）」判定 —— 数据源唯一，指向 backend/forward/common/projects.py。

不复制项目表（需求 3 责任划分：正向数据只有一份），按绝对路径 importlib 加载；
该文件自身无包内 import，故无需改动 sys.path。

自动识别用于前端「系统类型 = 自动」：对所有上传文件名收集所有匹配到的项目
（注册文件名精确匹配 ∪ 项目 token 匹配，含别名），**唯一才返回**；命中 0 个或
≥2 个都判为无法识别，要求用户显式选择（多项目混传时静默选一个会拿错项目跑出
无意义的对比结果）。
"""
from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from app.forward.root import FORWARD_ROOT

# 注册名之外的补充 token（大小写不敏感）。实测样本里 eps/fgmc/hscu 的上传文件名与
# PROJECTS 注册名不一致（eps 的 HLR 名为 RPDU_*、fgmc 的表名是「燃油EoICD_*」、
# hscu 的表名不含 HSCU），故按系统补别名；这些 token 均出现在对应样本的文件名里。
PROJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "ams": ("AMS",),
    "eps": ("EPS", "RPDU", "ATA24EPS"),
    "fgmc": ("FGMC",),
    "hscu": ("HSCU",),
}

# 几乎所有文件名都含的通用 token，不参与识别（否则每个项目都会命中）
_GENERIC_TOKENS = {"EOICD", "HLR", "PUBLISHER", "SUBSCRIBER", "TABLE", "XLSX", "DOCX"}


class ForwardProjectError(ValueError):
    """无法确定系统类型（API 层映射 422；任务内映射失败原因）。"""


@lru_cache(maxsize=1)
def load_forward_projects() -> dict[str, dict]:
    """加载正向项目表；缺失/格式异常 → ForwardProjectError。"""
    path = FORWARD_ROOT / "common" / "projects.py"
    if not path.is_file():
        raise ForwardProjectError(f"正向项目表缺失：{path}")
    spec = importlib.util.spec_from_file_location("forward_common_projects", path)
    if spec is None or spec.loader is None:
        raise ForwardProjectError(f"正向项目表无法加载：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    table = getattr(module, "PROJECTS", None)
    if not isinstance(table, dict) or not table:
        raise ForwardProjectError(f"正向项目表格式异常（PROJECTS 缺失）：{path}")
    return table


def project_ids() -> list[str]:
    return list(load_forward_projects().keys())


def resolve_project(name: Optional[str]) -> str:
    """显式项目名 → 归一化 id；空值或未知名 → ForwardProjectError。"""
    pid = (name or "").strip().lower()
    table = load_forward_projects()
    if not pid:
        raise ForwardProjectError("未指定系统类型")
    if pid not in table:
        raise ForwardProjectError(
            f"未知系统类型 {name!r}；可选：{', '.join(table)}")
    return pid


def _tokens_for(pid: str, meta: dict) -> set[str]:
    """一个项目的识别 token：项目 id、别名、pub/sub 注册名的首段。"""
    toks = {pid.upper(), *PROJECT_ALIASES.get(pid, ())}
    for key in ("pub", "sub"):
        fn = str(meta.get(key) or "")
        if fn:
            toks.add(Path(fn).name.split("_")[0].upper())
    return {t for t in toks if len(t) >= 3 and t not in _GENERIC_TOKENS}


def _matches_for(name: str, table: dict) -> set[str]:
    """单个文件名命中的项目集合（精确注册名 ∪ token 子串）。"""
    upper = name.upper()
    hits: set[str] = set()
    for pid, meta in table.items():
        for key in ("pub", "sub"):
            fn = str(meta.get(key) or "")
            if fn and Path(fn).name == name:
                hits.add(pid)
        if any(tok in upper for tok in _tokens_for(pid, meta)):
            hits.add(pid)
    return hits


def detect_project(filenames: Iterable[str]) -> str:
    """按上传文件名自动识别系统类型；无法唯一定位 → ForwardProjectError。"""
    names = [Path(n).name for n in filenames if n]
    if not names:
        raise ForwardProjectError("没有可用于识别的文件名")
    table = load_forward_projects()

    hits: set[str] = set()
    for name in names:
        hits |= _matches_for(name, table)
    if len(hits) == 1:
        return next(iter(hits))
    if not hits:
        raise ForwardProjectError(
            f"无法自动识别系统类型（文件名：{names}）；请在页面「系统类型」中显式选择")
    raise ForwardProjectError(
        f"文件名同时命中多个系统类型 {sorted(hits)}；请在页面「系统类型」中显式选择")
