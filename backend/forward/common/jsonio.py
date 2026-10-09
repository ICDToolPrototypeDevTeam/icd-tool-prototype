# -*- coding: utf-8 -*-
"""
common.jsonio — 统一的 JSON / YAML 读写工具
================================================================================
背景：
  项目中「匹配模块」「单judge结果汇总」等子模块各自实现了一份
  「加载 JSON / 加载配置 / 保存 JSON」，且带注释的 JSON 解析普遍使用
  ``line.split("//")[0]``——该写法会把字符串值里包含的 ``//``
  （例如 "http://example.com"、带 // 的路径）一并截断，导致配置被静默损坏。

本模块提供唯一实现，供所有子模块复用：
  - strip_json_comments(text) : 字符串安全的 // 行注释剥离
  - safe_load_json(path)      : 加载（可带注释的）JSON
  - save_json(data, path)     : 保存 JSON，自动创建父目录
  - load_yaml(path)           : 加载 YAML 配置
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def strip_json_comments(text: str) -> str:
    """删除 JSON 文本中的 // 行注释。

    只在「字符串之外」识别 //，因此不会误伤字符串值内部的 //
    （如 "http://example.com"）。这是相对 line.split("//")[0] 的关键修正。
    """
    result: list[str] = []
    in_string = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            result.append(ch)
        else:
            if ch == '"':
                in_string = True
                result.append(ch)
            elif ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
                # 跳过到行尾
                while i < len(text) and text[i] != "\n":
                    i += 1
                if i < len(text):
                    result.append(text[i])
            else:
                result.append(ch)
        i += 1
    return "".join(result)


def safe_load_json(path) -> Any:
    """加载 JSON 文件；自动剥离 // 行注释（字符串安全）。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return json.loads(strip_json_comments(text))


def save_json(data: Any, path, indent: int = 2, ensure_ascii: bool = False) -> Path:
    """保存数据为 JSON；自动创建父目录。返回写入的 Path。"""
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)
    return p


def load_yaml(path) -> Any:
    """加载 YAML 配置文件（延迟导入 pyyaml，未安装时才报错）。"""
    import yaml  # 局部导入：无 pyyaml 的环境不会在 import 阶段整体失败

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
