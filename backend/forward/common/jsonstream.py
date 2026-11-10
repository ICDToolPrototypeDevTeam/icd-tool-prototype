# -*- coding: utf-8 -*-
"""
common.jsonstream — 大 JSON 的分块流式读取
================================================================================
背景（2026-11-09）：
  「属性匹配」阶段产物 attribute_match_report_{pub,sub}.json 单份可达 446MB，
  其中 not_mentioned 组 184.5MB（264 个信号，每条约 700KB，内含
  not_mentioned_attributes[].missing_in_hlrs 全量追溯，每个属性可达 1098 条）。
  整树 json.load 的实测峰值：**2230MB**（JSON 文本 → Python 对象放大约 5×）。
  3 个 judge 并发跑 summary 阶段 ⇒ 峰值叠加 ⇒ 8G 服务器会 OOM。

本模块提供两项能力：
  1. load_small_sections(path, keys)  —— 整组读入体量小的顶层分组
     （meta / matched / mismatch / null_value，合计约 7MB）。
  2. iter_array_items(path, key_path)  —— 流式产出某个数组分组的元素，
     元素逐个解析、逐个 yield ⇒ 调用方处理完一个即可释放，
     常驻内存只与「单个元素大小」有关，与数组总大小无关。

★流式解析最容易踩的坑（务必留意）：
  **「先 f.read() 全文再逐条解析」不省内存** —— 文本本身还在内存里，
  峰值与整体载入完全一样（实测 1111.7MB vs 1111.7MB，一字不差）。
  必须是「边读边解析」：缓冲区固定大小，读进一块、解析掉一块、
  立刻回收已消费的部分，缓冲区永远不长大。

★定位键名必须按「所在层级」定位：
  同一个键名可能在嵌套对象里先出现（例：meta.summary 里有
  "not_mentioned": 1455870，顶层也有 "not_mentioned": {...}），
  全文 find 会命中前者，取到计数而非数组。本模块因此逐层下降，
  在每一层对象内精确匹配该层的键名。
"""
from __future__ import annotations

import io
import json
from typing import Any, Iterator

# 单次读取的块大小（字符数）。256KB 在"解析速度"与"常驻内存"之间比较平衡：
# 太小则系统调用频繁，太大则常驻文本偏多。单个元素若大于此值，
# 缓冲区会自动增长以容纳该元素（不可避免，但仅影响一个元素）。
CHUNK_SIZE = 256 * 1024


class _ChunkedText:
    """固定缓冲区的文本读取器：可增量补读、可回收已消费前缀。"""

    def __init__(self, fh, chunk_size: int = CHUNK_SIZE):
        self._fh = fh
        self._chunk = chunk_size
        self._buf = ""
        self._pos = 0        # 当前解析位置（在 _buf 内）
        self._eof = False

    # -- 缓冲区管理 --------------------------------------------------
    def fill(self) -> bool:
        """再读一块数据进缓冲区（先回收已消费前缀）。返回 False = 已到文件尾。"""
        if self._eof:
            return False
        data = self._fh.read(self._chunk)
        if not data:
            self._eof = True
            return False
        if self._pos:
            # 回收已消费部分 ⇒ 缓冲区不会无限增长
            self._buf = self._buf[self._pos:]
            self._pos = 0
        self._buf += data
        return True

    @property
    def buf(self) -> str:
        return self._buf

    @property
    def pos(self) -> int:
        return self._pos

    @pos.setter
    def pos(self, value: int) -> None:
        self._pos = value

    def ensure(self, need: int = 1) -> bool:
        """保证缓冲区中从当前位置起至少有 need 个字符可用（尽力）。"""
        while len(self._buf) - self._pos < need:
            if not self.fill():
                return False
        return True

    def at_eof(self) -> bool:
        return self._eof and self._pos >= len(self._buf)

    def skip_ws(self) -> None:
        """跳过当前位置之后的空白字符。"""
        while True:
            buf = self._buf
            n = len(buf)
            i = self._pos
            while i < n and buf[i] in " \t\r\n":
                i += 1
            self._pos = i
            if i < n:
                return
            if not self.fill():
                return

    def compact(self) -> None:
        """回收已消费前缀（元素解析完成后调用）。"""
        if self._pos:
            self._buf = self._buf[self._pos:]
            self._pos = 0

    # -- 低层读取 ----------------------------------------------------
    def read_string(self) -> str | None:
        """读取当前位置的一个 JSON 字符串字面量（含引号），返回其内容。

        失败返回 None。
        """
        self.skip_ws()
        self.ensure(1)
        if self.at_eof() or self.buf[self.pos] != '"':
            return None
        start = self.pos
        self.pos += 1
        while True:
            self.ensure(1)
            if self.at_eof():
                return None
            c = self.buf[self.pos]
            if c == "\\":
                self.pos += 2
                continue
            self.pos += 1
            if c == '"':
                break
        raw = self.buf[start:self.pos]
        try:
            return json.loads(raw)
        except ValueError:
            return raw[1:-1]


def _skip_value(reader: _ChunkedText) -> None:
    """跳过当前位置的一个 JSON 值（对象 / 数组 / 标量），不做任何解析。

    只做括号配平扫描 ⇒ 内存开销与值大小无关。
    """
    reader.skip_ws()
    reader.ensure(1)
    if reader.at_eof():
        return
    ch = reader.buf[reader.pos]
    if ch not in "{[":
        # 标量：跳到分隔符
        while True:
            reader.ensure(1)
            if reader.at_eof():
                return
            c = reader.buf[reader.pos]
            if c in " \t\r\n,}]":
                return
            reader.pos += 1
    depth = 0
    in_str = False
    esc = False
    while True:
        reader.ensure(1)
        if reader.at_eof():
            return
        ch = reader.buf[reader.pos]
        reader.pos += 1
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return


def _descend_to(reader: _ChunkedText, key_path: list[str]) -> bool:
    """逐层下降定位到 key_path 最后一级的值起点（数组的 '['）。

    每一步都在**当前层对象内**精确匹配该层的键名（不会命中嵌套层里的同名键）。
    中间层的值若是对象，则进入该对象继续找下一级键。

    成功返回 True，reader.pos 停在数组的 '[' 上。
    """
    # 进入根对象
    reader.skip_ws()
    reader.ensure(1)
    if reader.at_eof() or reader.buf[reader.pos] != "{":
        return False
    reader.pos += 1

    for depth_idx, key in enumerate(key_path):
        # 在当前层对象内找 key
        while True:
            reader.skip_ws()
            reader.ensure(1)
            if reader.at_eof():
                return False
            ch = reader.buf[reader.pos]
            if ch == "}":
                return False        # 当前层没有这个键
            if ch == ",":
                reader.pos += 1
                continue
            if ch != '"':
                return False
            name = reader.read_string()
            if name is None:
                return False
            reader.skip_ws()
            if reader.pos >= len(reader.buf) or reader.buf[reader.pos] != ":":
                return False
            reader.pos += 1
            reader.skip_ws()
            if name == key:
                break
            # 不是目标键 → 跳过它的值，继续找
            _skip_value(reader)
        # 找到该键：若其值是对象则进入（下一层键在里面）
        if depth_idx + 1 < len(key_path):
            reader.ensure(1)
            if reader.at_eof() or reader.buf[reader.pos] != "{":
                return False
            reader.pos += 1
    # 最后一级的值应是数组
    reader.ensure(1)
    if reader.at_eof() or reader.buf[reader.pos] != "[":
        return False
    return True


def iter_array_items(path, key_path: list[str]) -> Iterator[Any]:
    """流式产出 path 文件中 key_path 指向的数组的元素。

    key_path 形如 ["not_mentioned", "not_mentioned_by_signal"]。
    元素逐个解析、逐个 yield ⇒ 调用方处理完一个即可释放。
    """
    dec = json.JSONDecoder()
    with io.open(path, "r", encoding="utf-8") as fh:
        reader = _ChunkedText(fh)
        if not _descend_to(reader, key_path):
            return
        reader.pos += 1        # 越过 '['
        while True:
            reader.skip_ws()
            while reader.pos < len(reader.buf) and reader.buf[reader.pos] == ",":
                reader.pos += 1
                reader.skip_ws()
            reader.ensure(1)
            if reader.at_eof():
                return
            if reader.buf[reader.pos] == "]":
                return
            # 解析一个元素；可能因缓冲区边界被截断 ⇒ 补读后重试
            while True:
                try:
                    val, end = dec.raw_decode(reader.buf, reader.pos)
                    break
                except ValueError:
                    if not reader.fill():
                        return
            reader.pos = end
            yield val
            reader.compact()


def _read_value_at(reader: _ChunkedText, dec) -> Any:
    """解析当前位置的一个完整 JSON 值（缓冲区不足时补读重试）。

    该值会被整体载入内存 ⇒ 只用于体量小的分组（meta / matched 等）。
    """
    reader.skip_ws()
    while True:
        try:
            val, end = dec.raw_decode(reader.buf, reader.pos)
            reader.pos = end
            return val
        except ValueError:
            if not reader.fill():
                raise


def iter_attribute_report(path) -> Iterator[tuple]:
    """单趟流式扫描「属性匹配报告」，产出 (kind, payload)。

    kind 取值：
      "meta" / "matched" / "mismatch" / "null_value" → payload 为已解析的分组
      "signal"                                      → payload 为
        not_mentioned_by_signal 的**单个元素**（逐个产出、逐个可释放）

    一次遍历完成 ⇒ 不需要「先读全文再找键」，常驻内存只与最大单组有关。

    ★产出顺序说明：文件的顶层键物理顺序是
      meta / mismatch_by_attribute / matched / mismatch / null_value /
      not_mentioned / clustering
      —— **mismatch 在 matched 之前**。而下游 normalize_attribute_match 的
      规范处理顺序是 matched → mismatch → null_value → not_mentioned，
      两者不同 ⇒ 调用方不能直接按本函数的产出顺序拼接 comparisons
      （否则判定结果会变）。调用方须按下述方式归位：
        · 三组小分组（matched/mismatch/null_value）先缓存，按规范顺序使用；
        · not_mentioned 的元素在文件中位于三组之后 ⇒ 可在缓存完成后再消费。
    """
    dec = json.JSONDecoder()
    small_keys = ("meta", "matched", "mismatch", "null_value")
    with io.open(path, "r", encoding="utf-8") as fh:
        reader = _ChunkedText(fh)
        # 进入根对象
        reader.skip_ws()
        reader.ensure(1)
        if reader.at_eof() or reader.buf[reader.pos] != "{":
            return
        reader.pos += 1
        while True:
            reader.skip_ws()
            reader.ensure(1)
            if reader.at_eof():
                return
            ch = reader.buf[reader.pos]
            if ch == "}":
                return
            if ch == ",":
                reader.pos += 1
                continue
            if ch != '"':
                return
            name = reader.read_string()
            if name is None:
                return
            reader.skip_ws()
            if reader.pos >= len(reader.buf) or reader.buf[reader.pos] != ":":
                return
            reader.pos += 1
            reader.skip_ws()
            if name in small_keys:
                yield (name, _read_value_at(reader, dec))
                reader.compact()
                continue
            if name == "not_mentioned":
                # 该值是对象，内含 not_mentioned_by_signal 数组 → 逐元素产出
                reader.ensure(1)
                if reader.at_eof() or reader.buf[reader.pos] != "{":
                    return
                reader.pos += 1
                for item in _iter_signals_in_not_mentioned(reader, dec):
                    yield ("signal", item)
                continue
            # 其它键（如 clustering / mismatch_by_attribute）：跳过其值
            _skip_value(reader)


def _iter_signals_in_not_mentioned(reader: _ChunkedText, dec) -> Iterator[Any]:
    """在 not_mentioned 对象内流式产出 not_mentioned_by_signal 的元素。"""
    while True:
        reader.skip_ws()
        reader.ensure(1)
        if reader.at_eof():
            return
        ch = reader.buf[reader.pos]
        if ch == "}":
            return
        if ch == ",":
            reader.pos += 1
            continue
        if ch != '"':
            return
        name = reader.read_string()
        if name is None:
            return
        reader.skip_ws()
        if reader.pos >= len(reader.buf) or reader.buf[reader.pos] != ":":
            return
        reader.pos += 1
        reader.skip_ws()
        if name != "not_mentioned_by_signal":
            _skip_value(reader)
            continue
        # 数组 → 逐元素解析
        reader.ensure(1)
        if reader.at_eof() or reader.buf[reader.pos] != "[":
            return
        reader.pos += 1
        while True:
            reader.skip_ws()
            while reader.pos < len(reader.buf) and reader.buf[reader.pos] == ",":
                reader.pos += 1
                reader.skip_ws()
            reader.ensure(1)
            if reader.at_eof():
                return
            if reader.buf[reader.pos] == "]":
                return
            while True:
                try:
                    val, end = dec.raw_decode(reader.buf, reader.pos)
                    break
                except ValueError:
                    if not reader.fill():
                        return
            reader.pos = end
            yield val
            reader.compact()


def load_small_sections(path, wanted: list[str]) -> dict:
    """一次性读入顶层 dict 中 wanted 指定的几个小分组（整组 json.loads）。

    适用于 matched / mismatch / null_value / meta 这类体量小的分组。
    若 wanted 中含 not_mentioned 这类大分组，请改用 iter_array_items。

    ★实现说明：这里走 iter_attribute_report 的单趟流式扫描，
    **不读全文**——早期版本用 f.read() 读全文再切片，峰值反而是全文量级
    （实测 446MB 文件 → 峰值 2230MB，与整体 json.load 一样）。
    """
    wanted = set(wanted)
    out = {}
    for kind, payload in iter_attribute_report(path):
        if kind in wanted:
            out[kind] = payload
        if len(out) == len(wanted):
            break
    return out


def _find_value_start_text(text: str, key: str) -> int:
    """在整段文本中定位**顶层** dict 里 "key": 的值起始位置。找不到返回 -1。

    ★必须是「顶层」定位，不能用全文 find：同名键可能在嵌套对象里先出现
    （例：meta.summary 里的 "matched": 1997 vs 顶层的 "matched": [...]）。
    """
    needle = '"%s"' % key
    n = len(text)
    i = 0
    while i < n and text[i] != "{":
        i += 1
    if i >= n:
        return -1
    i += 1
    depth = 1
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            token = text[i:j + 1]
            if depth == 1 and token == needle:
                k = j + 1
                while k < n and text[k] in " \t\r\n":
                    k += 1
                if k < n and text[k] == ":":
                    k += 1
                    while k < n and text[k] in " \t\r\n":
                        k += 1
                    return k
            i = j + 1
            continue
        if ch in "{[":
            depth += 1
            i += 1
            continue
        if ch in "}]":
            depth -= 1
            if depth == 0:
                return -1
            i += 1
            continue
        i += 1
    return -1


def _scan_value_end_text(text: str, start: int) -> int:
    """扫描出 text[start:] 处一个 JSON 值的结束位置（不含结尾）。"""
    depth = 0
    in_str = False
    esc = False
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            elif ch in " \t\r\n,:" and depth == 0:
                return i
        i += 1
    return n
