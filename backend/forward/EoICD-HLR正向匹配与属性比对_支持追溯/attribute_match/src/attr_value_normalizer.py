# -*- coding: utf-8 -*-
"""attr_value_normalizer.py — 属性值形态识别与结构化归一（确定性层，纯函数）

背景
----
属性比对的脚本层用 `default_compare`（字符串相等 + 单位后缀）比对 EoICD 属性值与
HLR 属性值。真实数据里大量"不一致"其实是**同一套编码的两种表述**，字符串比必然判不等：

  EoICD: 0=Not Failed;1=Failed              （单信号的两值枚举）
  HLR  : 通风AFTBFAN可用标志为有效→bit28=0；…无效→bit28=1   （整张报文的 bit 规则表）

本模块把这些值解析成**可比较的结构**，让脚本层能判断"结构等价"。

实测形态分布（3 judge × pub/sub，409 项待判项）：
  - 数字枚举 × bit规则表        76 项  → 本模块主战场
  - 自由文本 × 自由文本          253 项  → SDI 通道编码（双方都是完整编码表）
  - 纯数值 × 纯数值              24 项  → 真数值差异（如 SDIExpected 2 vs 1）
  - 数字枚举 × bit规则表(另一方向) 32 项 → 同上，箭头方向不同

设计原则（跨输入稳健，不依赖某一版数据）
----------------------------------------
1. **纯函数、无 I/O、无全局状态**：所有输入显式传入，输出可复现。
2. **不认识就返回 None**：解析不了就交回上层（AI 复核）处理，**绝不猜**。
   宁可漏判为"需 AI 看"，也不要把真不一致误判成一致。
3. **只做"结构等价"判定，不做语义等价判定**：
   结构等价 = 编码表的键值集合在归一后完全一致（且顺序无关）。
   语义等价（如 "Failed" ↔ "故障有效"）需要语言理解，**不属于本模块**，
   交 AI 或上层规则处理。
4. **bit 号是主坐标**：`(bus, label, bit)` 相同才允许把 EoICD 单信号属性
   与 HLR 整表中的某一条对齐。

对外接口
--------
- classify_value(v) -> str
      形态标识：'none' / 'empty' / 'number' / 'numeric_enum' / 'bit_rule' /
                'bit_pattern' / 'text'
- parse_enum(v) -> dict[str, str] | None
      解析 "0=Not Failed;1=Failed" → {'0': 'Not Failed', '1': 'Failed'}
- parse_bit_rules(v) -> list[BitRule] | None
      解析 HLR 侧 bit 规则表 → 按 bit 号升序的规则列表（同时支持
      "bit22=1→中文" 与 "中文→bit28=0" 两种箭头方向）
- parse_bit_pattern(v) -> dict[str, str] | None
      解析 "bit8=1,bit9=1" → {'8': '1', '9': '1'}（SDI 二进制展开用）
- select_bit_rule(rules, bit) -> BitRule | None
      从整张规则表里挑出指定 bit 的那一条
- codeset_equivalent(eoicd_value, hlr_value, eoicd_bit, hlr_bit) -> bool | None
      主判定：EoICD 单信号枚举 与 HLR 规则表中该 bit 那一条 是否结构等价。
      None = 无法判定，交 AI。

用法与自测见文件末尾 `_self_test()`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "BitRule", "classify_value", "parse_enum", "parse_bit_rules", "parse_bit_pattern",
    "select_bit_rule", "codeset_equivalent", "normalize_bit_ref",
]


# ---------------------------------------------------------------- 形态标识

SHAPE_NONE = "none"
SHAPE_EMPTY = "empty"
SHAPE_NUMBER = "number"
SHAPE_NUMERIC_ENUM = "numeric_enum"
SHAPE_BIT_RULE = "bit_rule"
SHAPE_BIT_PATTERN = "bit_pattern"
SHAPE_TEXT = "text"


# 正则（全部允许中英文标点、空格差异）
_RE_PURE_NUM = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
# "0=Not Failed;1=Failed" —— 键必须是数字，允许值里含 '=' 以外任意非分号内容
_RE_NUM_ENUM_ITEM = re.compile(r"^\s*(\d+)\s*=\s*([^;]+?)\s*$")
# "bit22=1→中文"  或  "bit8=1，bit9=0" 里的单个 bit
_RE_BIT_EQ_NUM = re.compile(r"bit\s*(\d+)\s*=\s*(\d+)", re.I)
# "中文→bit28=0"
_RE_TEXT_TO_BIT = re.compile(r"([^；;，,]+?)\s*→\s*bit\s*(\d+)\s*=\s*(\d+)", re.I)
# "bit22=1→中文"
_RE_BIT_TO_TEXT = re.compile(r"bit\s*(\d+)\s*=\s*(\d+)\s*→\s*([^；;，,]+)", re.I)


@dataclass(frozen=True)
class BitRule:
    """HLR bit 规则表中的一条：bit_no=bit 编号，value=bit 取值，text=对应中文含义。"""
    bit_no: int
    value: int
    text: str


def classify_value(v) -> str:
    """识别属性值的形态。"""
    if v is None:
        return SHAPE_NONE
    s = str(v).strip()
    if not s:
        return SHAPE_EMPTY
    if _RE_PURE_NUM.match(s):
        return SHAPE_NUMBER
    if _RE_TEXT_TO_BIT.search(s) or _RE_BIT_TO_TEXT.search(s):
        return SHAPE_BIT_RULE
    if _RE_NUM_ENUM_ITEM.match(s) and ";" in s:
        return SHAPE_NUMERIC_ENUM
    if re.fullmatch(r"\d+\s*=\s*[^;]+(\s*;\s*\d+\s*=\s*[^;]+)*", s):
        return SHAPE_NUMERIC_ENUM
    if len(_RE_BIT_EQ_NUM.findall(s)) >= 1 and "=" in s and "→" not in s:
        return SHAPE_BIT_PATTERN
    return SHAPE_TEXT


# ---------------------------------------------------------------- 数值枚举

def parse_enum(v) -> Optional[dict[str, str]]:
    """解析 "0=Not Failed;1=Failed" → {'0': 'Not Failed', '1': 'Failed'}。

    非枚举形态返回 None（不猜）。
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # 去掉可能存在的 [通道] 前缀与 "(二进制:...)" 尾巴后仍是枚举的情况不处理（形态不同）
    parts = [p for p in re.split(r"[;；]", s) if p.strip()]
    if not parts:
        return None
    out: dict[str, str] = {}
    for p in parts:
        m = _RE_NUM_ENUM_ITEM.match(p)
        if not m:
            return None
        key, val = m.group(1), m.group(2).strip()
        if key in out:          # 重复键 → 无法可靠解析
            return None
        out[key] = val
    return out or None


# ---------------------------------------------------------------- bit 规则表

def parse_bit_rules(v) -> Optional[list[BitRule]]:
    """解析 HLR 侧 bit 规则表 → BitRule 列表（按 bit_no 升序，同 bit 保持出现顺序）。

    支持两种写法（真实数据里都存在）：
      - "bit22=1→压调...有效;bit23=1→..."      （bit→中文）
      - "通风...为有效→bit28=0；通风...为无效→bit28=1"  （中文→bit）

    解析不出任何规则时返回 None。

    注意：**同一 bit 号可以有多条规则**（"bit28=0 表示有效" 与 "bit28=1 表示无效"
    是两条不同规则），因此不能用 dict 按 bit_no 去重，否则后一条会覆盖前一条。
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    rules: list[BitRule] = []
    seen: set[tuple[int, int, str]] = set()

    def _add(bit_no: int, bit_val: int, text: str) -> None:
        key = (bit_no, bit_val, text)
        if key in seen:
            return
        seen.add(key)
        rules.append(BitRule(bit_no, bit_val, text))

    # 先剥出 "bitN=M" 形式的片段作为切分依据
    for m in _RE_BIT_TO_TEXT.finditer(s):
        _add(int(m.group(1)), int(m.group(2)), m.group(3).strip())
    for m in _RE_TEXT_TO_BIT.finditer(s):
        _add(int(m.group(2)), int(m.group(3)), m.group(1).strip())

    if not rules:
        return None
    return sorted(rules, key=lambda r: (r.bit_no, r.value))


def select_bit_rule(rules: list[BitRule], bit, value=None) -> Optional[BitRule]:
    """从规则表里挑出指定 bit 的那一条。

    bit   : EoICD 侧 / HLR 侧的 bit 引用（int / "22" / "22-22"）
    value : 可选，指定 bit 的取值（如 0 / 1）。同一 bit 有多条规则时靠它精确选中
            （"bit28=0 表示有效" 与 "bit28=1 表示无效" 必须靠取值区分）。
            不给则返回该 bit 的第一条。
    """
    if not rules or bit is None:
        return None
    b = normalize_bit_ref(bit)
    if b is None:
        return None
    lo, hi = b
    cands = [r for r in rules if lo <= r.bit_no <= hi]
    if not cands:
        return None
    if value is not None:
        for r in cands:
            if r.value == int(value):
                return r
        return None
    return cands[0]


def normalize_bit_ref(bit) -> Optional[tuple[int, int]]:
    """把 bit 引用统一成 (lo, hi) 闭区间。

    支持：8 → (8,8)；"8" → (8,8)；"8-9" → (8,9)；"22-22" → (22,22)。
    无法解析返回 None。
    """
    if bit is None:
        return None
    if isinstance(bit, (int, float)):
        try:
            i = int(bit)
        except (ValueError, OverflowError):
            return None
        return (i, i)
    s = str(bit).strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d+)\s*[-~]\s*(\d+)", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (min(a, b), max(a, b))
    m = re.fullmatch(r"(\d+)", s)
    if m:
        i = int(m.group(1))
        return (i, i)
    return None


# ---------------------------------------------------------------- bit 模式

def parse_bit_pattern(v) -> Optional[dict[str, str]]:
    """解析 "bit8=1,bit9=1" → {'8': '1', '9': '1'}。

    用于 SDI 通道编码的二进制展开部分比对。
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    pairs = _RE_BIT_EQ_NUM.findall(s)
    if not pairs:
        return None
    return {b: val for b, val in pairs}


# ---------------------------------------------------------------- 主判定

def codeset_equivalent(eoicd_value, hlr_value, eoicd_bit=None, hlr_bit=None) -> Optional[bool]:
    """判定 EoICD 单信号编码 与 HLR 规则表中对应 bit 那一条 是否**结构等价**。

    返回 True / False / None（None = 无法判定，交 AI 复核）。

    判定依据（严格，避免误判为一致）：
      1. EoICD 侧必须是"数字枚举"形态，HLR 侧必须是"bit 规则表"形态，否则 None。
      2. 必须能用 bit 号在 HLR 规则表里定位到唯一一条（eoicd_bit / hlr_bit），
         定位不到 → None。
      3. 取该条规则：bit 取值 v、含义 text。
         - EoICD 枚举里必须存在键 "v"（即该 bit 取 v 时的编码含义）；
           不存在 → 结构不等价 → False。
         - 该键的含义文本 与 text 做**归一化后比对**（去空白/去引号/大小写）。
         - 含义文本**一致或一方是另一方的子串** → True。
         - **含义文本不同 → None（交 AI），不是 False。**
           理由：文本不同不等于语义不同。"Failed"（EoICD 英文枚举）与
           "压调OFVTRV失效在关位故障有效标志有效"（HLR 中文规则）文本完全不同
           但语义等价；把它判成 False 就是**把真一致误判为不一致**。
           确定性层无法区分"语义不同"与"表述不同"，因此一律交 AI。
    """
    enum = parse_enum(eoicd_value)
    if not enum:
        return None
    rules = parse_bit_rules(hlr_value)
    if not rules:
        return None

    rule = select_bit_rule(rules, _effective_bit_ref(hlr_bit, eoicd_bit))
    if rule is None:
        return None

    key = str(rule.value)
    if key not in enum:
        # 该 bit 取值在 EoICD 枚举里没有对应项 → 结构不等价
        return False

    eoicd_meaning = enum[key]
    hlr_meaning = rule.text

    a = _norm_text(eoicd_meaning)
    b = _norm_text(hlr_meaning)
    if not a or not b:
        return None
    if a == b:
        return True
    # 一方是另一方的子串 → 视为等价（"Not Failed" vs "Not Failed (故障)"）
    if a in b or b in a:
        return True
    # 语义等价判不了 → 交 AI
    return None


def _effective_bit_ref(hlr_bit, eoicd_bit):
    """选定位 bit 引用：优先 HLR 侧 bit，HLR 侧缺失/不可解析时回退 EoICD 侧。

    注意不能用 `hlr_bit is not None` 判断——空串 "" 也会让 is not None 成立，
    但它解析不出区间，应回退到 EoICD 侧。
    """
    if normalize_bit_ref(hlr_bit) is not None:
        return hlr_bit
    return eoicd_bit


def _norm_text(s) -> str:
    """文本归一：去空白、去中文引号、去英文引号、统一大小写。"""
    if s is None:
        return ""
    t = str(s).strip()
    for ch in ('“', '”', '"', "'", '‘', '’', '（', '）', '(', ')'):
        t = t.replace(ch, "")
    t = re.sub(r"\s+", "", t)
    return t.lower()


# ---------------------------------------------------------------- 自测

def _self_test() -> None:
    """自测：形态识别 / 枚举解析 / 规则表解析（两种箭头方向）/ bit 选择 / 主判定。

    覆盖的高频回归点：
      - 两种箭头方向都能解析（真实数据两种都有）
      - 解析失败必须返回 None（不许猜）
      - codeset_equivalent 的 True / False / None 三态边界正确
    """
    # --- 形态识别 ---
    assert classify_value(None) == SHAPE_NONE
    assert classify_value("  ") == SHAPE_EMPTY
    assert classify_value("15") == SHAPE_NUMBER
    assert classify_value("0=Not Failed;1=Failed") == SHAPE_NUMERIC_ENUM
    assert classify_value("通风AFTBFAN可用标志为有效→bit28=0") == SHAPE_BIT_RULE
    assert classify_value("bit22=1→压调OFVTRV失效在关位故障有效标志有效") == SHAPE_BIT_RULE
    assert classify_value("bit8=1,bit9=1") == SHAPE_BIT_PATTERN
    assert classify_value("Failed") == SHAPE_TEXT

    # --- 枚举解析 ---
    assert parse_enum("0=Not Failed;1=Failed") == {"0": "Not Failed", "1": "Failed"}
    assert parse_enum("1=Fail") == {"1": "Fail"}
    assert parse_enum("Failed") is None          # 不是枚举 → None
    assert parse_enum("") is None
    assert parse_enum(None) is None
    assert parse_enum("0=A;0=B") is None         # 重复键 → None

    # --- bit 规则表：两种箭头方向 ---
    r1 = parse_bit_rules("bit22=1→压调A有效;bit23=1→压调B有效")
    assert r1 and [(r.bit_no, r.value) for r in r1] == [(22, 1), (23, 1)]
    assert r1[0].text == "压调A有效"
    r2 = parse_bit_rules("通风AFTBFAN可用标志为有效→bit28=0；通风AFTBFAN可用标志为无效→bit28=1")
    assert r2 and [(r.bit_no, r.value) for r in r2] == [(28, 0), (28, 1)]
    assert r2[0].text == "通风AFTBFAN可用标志为有效"
    assert parse_bit_rules("Failed") is None
    assert parse_bit_rules(None) is None

    # --- 同一 bit 多条规则必须都保留（回归点：曾用 dict 按 bit 去重导致后者覆盖前者）---
    assert len(r2) == 2, f"同 bit 的多条规则应全部保留，实际 {len(r2)} 条"
    assert select_bit_rule(r2, 28, value=0).text == "通风AFTBFAN可用标志为有效"
    assert select_bit_rule(r2, 28, value=1).text == "通风AFTBFAN可用标志为无效"
    assert select_bit_rule(r2, 28, value=2) is None      # 该 bit 无此取值
    assert select_bit_rule(r2, 28).value == 0            # 不给 value 时取第一条

    # --- bit 选择 ---
    assert select_bit_rule(r1, "22").bit_no == 22
    assert select_bit_rule(r1, 22).bit_no == 22
    assert select_bit_rule(r1, "22-23").bit_no == 22
    assert select_bit_rule(r1, 99) is None
    assert select_bit_rule(r1, None) is None

    # --- bit 引用归一 ---
    assert normalize_bit_ref(8) == (8, 8)
    assert normalize_bit_ref("8") == (8, 8)
    assert normalize_bit_ref("8-9") == (8, 9)
    assert normalize_bit_ref("9-8") == (8, 9)
    assert normalize_bit_ref("22-22") == (22, 22)
    assert normalize_bit_ref(None) is None
    assert normalize_bit_ref("abc") is None

    # --- 主判定 ---
    # True：EoICD 枚举里 bit22=1 的含义 "Failed"，HLR bit22 含义含 "Failed"
    e = "0=Not Failed;1=Failed"
    h = "bit22=1→Failed"
    assert codeset_equivalent(e, h, eoicd_bit="22-22") is True
    # 子串也算等价："Not Failed" vs "Not Failed(故障)"
    assert codeset_equivalent("0=Not Failed", "bit28=0→Not Failed 故障", eoicd_bit=28) is True
    # False：bit 取值在 EoICD 枚举里没有对应项
    assert codeset_equivalent("0=Not Failed;1=Failed", "bit22=1→Failed", eoicd_bit="22") is True
    assert codeset_equivalent("5=A;6=B", "bit22=1→X", eoicd_bit="22") is False
    # None：形态不对 / 定位不到 / 语义无法判定
    assert codeset_equivalent("Failed", "bit22=1→X", eoicd_bit=22) is None
    assert codeset_equivalent(e, "Failed", eoicd_bit=22) is None
    assert codeset_equivalent(e, h, eoicd_bit=None) is None       # 无 bit 无法定位
    assert codeset_equivalent(e, "bit99=1→X", eoicd_bit=22) is None
    # 含义文本不同（Failed vs 故障有效）→ 判不了 → None，交 AI
    assert codeset_equivalent("0=Not Failed;1=Failed",
                              "bit22=1→压调OFVTRV失效在关位故障有效标志有效",
                              eoicd_bit="22-22") is None
    # 含义文本明确不同 → 仍是 None（不是 False）：确定性层分不清"语义不同"与"表述不同"，
    # 判 False 会把真一致误判为不一致。护栏：任何"文本不同"都必须返回 None。
    assert codeset_equivalent("0=Not Failed;1=Failed",
                              "bit22=1→SomethingElse",
                              eoicd_bit="22-22") is None
    # hlr_bit 为空串时必须回退 eoicd_bit（不能用 `is not None` 判断，空串会误伤）
    assert codeset_equivalent("0=Not Failed;1=Failed",
                              "bit22=1→Failed",
                              eoicd_bit="22-22", hlr_bit="") is True
    assert codeset_equivalent("0=Not Failed;1=Failed",
                              "bit22=1→Failed",
                              eoicd_bit="22-22", hlr_bit=None) is True

    print("attr_value_normalizer 自测通过（形态识别/枚举解析/规则表双箭头方向/bit选择/"
          "bit归一/主判定三态 均符合预期）")


if __name__ == "__main__":  # pragma: no cover
    _self_test()
