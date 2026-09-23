# -*- coding: utf-8 -*-
"""归一化不规范的 OOXML 包（zip 条目名用反斜杠）。

背景（BUG-20260923-001）：WPS 等第三方 Office 保存 .docx / .xlsx 时，zip 中央目录里
的条目名可能被写成 ``word\\document.xml``（反斜杠），而 OOXML 规范要求正斜杠。CPython
的 ``zipfile`` 只在 ``os.sep != "/"`` 时把反斜杠替换成正斜杠 —— 也就是说**只有 Windows
容忍这类包**。Linux（容器、服务器）不做替换，python-docx / openpyxl 按
``word/document.xml`` 查不到部件，任务报「文件无法解析」。

实测（2026-09-23，一份 WPS 保存的 HLR .docx，23,858 字节，15 个条目中 14 个用反斜杠）：
Windows 上解析出 16 条需求；``os.sep="/"`` 下抛 KeyError 报无法解析。其 Word 原件
（27,235 字节，条目名全部规范）在两个平台上都通过，且两份文件的 ``document.xml``
逐字节相同 —— 所以问题不在内容，只在条目名的分隔符。

因此解析前先检查条目名：不规范就重写一份规范包（**文件名保持不变**，落在原目录的
``_normalized/`` 下）并用它解析；规范包原路径直通、零额外行为。检测按中央目录的原始
字节做，不依赖当前平台的 ``os.sep`` —— 否则行为会随平台漂移，正是本次 bug 的成因。
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path

_EOCD_SIG = b"PK\x05\x06"
_CD_SIG = b"PK\x01\x02"
_MAX_COMMENT = 65535  # zip 尾注释上限，EOCD 只可能出现在最后 22+65535 字节内


def _central_directory_entries(path: Path) -> list[bytes]:
    """按 zip 结构读出中央目录里的条目名**原始字节**（不做任何平台归一化）。

    不能用 ``zipfile`` 的 ``infolist()``：CPython 在 Windows 上会把条目名里的反斜杠
    替换成正斜杠，检测会静默失效 —— 恰恰是本次 bug 的成因。

    读不到（文件过小 / 不是 zip / 结构损坏）时返回空列表，把报错留给原本的解析路径，
    本函数不改变任何错误行为。
    """
    try:
        size = path.stat().st_size
        if size < 22:
            return []
        tail_len = min(size, 22 + _MAX_COMMENT)
        with path.open("rb") as fh:
            fh.seek(size - tail_len)
            tail = fh.read(tail_len)
            eocd = tail.rfind(_EOCD_SIG)
            if eocd < 0 or eocd + 22 > len(tail):
                return []
            cd_size, cd_off = struct.unpack_from("<II", tail, eocd + 12)
            if cd_off + cd_size > size:
                return []
            fh.seek(cd_off)
            blob = fh.read(cd_size)
    except OSError:
        return []

    names: list[bytes] = []
    pos = 0
    while pos + 46 <= len(blob):
        if blob[pos : pos + 4] != _CD_SIG:
            break
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", blob, pos + 28)
        name = blob[pos + 46 : pos + 46 + name_len]
        if len(name) < name_len:
            break
        names.append(name)
        pos += 46 + name_len + extra_len + comment_len
    return names


def needs_normalization(path: Path) -> bool:
    """条目名里是否含反斜杠（即非规范 OOXML 包）。"""
    return any(b"\\" in name for name in _central_directory_entries(path))


def ensure_standard_path(path: Path) -> Path:
    """只要「用于读取的路径」、不需要日志标记时的轻量包装。

    供那些直接开文件、读失败就是硬错误的位置使用（提交接口的系统类型识别、
    追溯表读取）；管线解析入口仍用 ``ensure_standard_zip``，以便打出 ``[fix]`` 日志。
    """
    return ensure_standard_zip(path)[0]


def ensure_standard_zip(path: Path) -> tuple[Path, bool]:
    """条目名不规范时返回规范化副本的路径；规范时原路径直通。

    Returns:
        ``(用于解析的路径, 是否做了归一化)``。副本与原文件**同名**，只是落在
        ``<原目录>/_normalized/`` 下 —— 解析结果里的 ``source_file`` 用的是文件名，
        改名会让报告中的「来源文件」跟着变。
    """
    if not needs_normalization(path):
        return path, False

    dest = path.parent / "_normalized" / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(
        dest, "w", zipfile.ZIP_DEFLATED
    ) as out:
        for item in src.infolist():
            # 按条目自身名字读取（Linux 下名字仍带反斜杠，zipfile 查表用的是原名字）
            out.writestr(item.filename.replace("\\", "/"), src.read(item.filename))
    return dest, True
