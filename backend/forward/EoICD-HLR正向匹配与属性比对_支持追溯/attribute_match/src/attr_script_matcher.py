"""
attr_script_matcher.py — 属性匹配：脚本预匹配

功能：
  1. HLR 属性按 req_id 聚合
  2. 遍历 attribute_mapping，逐一比对 EoICD 与 HLR 属性值
  3. 统一规则：字符串比较（不区分大小写，去空格）
  3.1 编码表述归一（2026-10-28）：字符串不等时，若属"同一套编码的两种写法"
      （EoICD 数字枚举 ↔ HLR bit 规则表），先用 attr_value_normalizer 做确定性归一，
      归一判等价 → matched；判不了 → 保持 mismatch 交 AI 复核。
  4. 结果四分：matched / not_mentioned / mismatch / null_value
  5. SDI 信号通道信息注入 CodedSet（在匹配前预处理）

输入：match_report.json（身份匹配阶段输出）
输出：{
  "stats": {"matched", "not_mentioned", "mismatch", "total_checks"},
  "matched_pairs": [...],
  "not_mentioned_pairs": [...],
  "mismatch_pairs": [...],
}
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from attr_value_normalizer import codeset_equivalent

# 需求原文送进 AI prompt 时的最大字符数（2026-10-28）。
# 实测需求原文长度中位 138 / 最长 535；截断到 400 可覆盖绝大多数接口清单与规则，
# 同时把 prompt 体积控制在可接受范围。
REQ_TEXT_MAX = 400


# ============================ SDI 通道信息提取 ============================

def extract_channel(full_name: str) -> str | None:
    """
    从 SDI 信号的 FullName 中提取通道信息（A/B/C/S 通道）。

    扫描 FullName 的前三个层级，按优先级依次匹配：

    优先级1: _OA_, _OB_, _OC_, _OS_
      例: po429_OA_500, po429_OB_Msg → A通道, B通道

    优先级2: CHANELA, CHANELB, CHANELC, CHANELS
      例: po429_CHANELA_100 → A通道（不区分大小写）

    优先级3: CHANNELA, CHANNELB, CHANNELC, CHANNELS
      例: po429_CHANNELB_200 → B通道（不区分大小写）

    优先级4: CH_A, CH_B, CHC 等（仅含下划线，无减号）
      例: po429_CH_A_300 → A通道, po429_CHC → C通道
      [WARN] 带字母边界检查，避免误匹配 MACHINE / EXCHANGE 中的 CH

    返回: "A通道"/"B通道"/"C通道"/"S通道" 或 None（单通道）
    """
    parts = full_name.split(".")
    for part in parts[:3]:
        # P1: _OA_, _OB_, _OC_, _OS_
        m = re.search(r"_O([ABCS])_", part)
        if m:
            return f"{m.group(1)}通道"

        # P2: CHANELA, CHANELB 等
        m = re.search(r"CHANEL([ABCS])", part, re.IGNORECASE)
        if m:
            return f"{m.group(1).upper()}通道"

        # P3: CHANNELA, CHANNELB 等
        m = re.search(r"CHANNEL([ABCS])", part, re.IGNORECASE)
        if m:
            return f"{m.group(1).upper()}通道"

        # P4: CH_A, CH_B, CHC 等（仅下划线，字母边界避免误匹配）
        m = re.search(r"(?<![a-zA-Z])CH_?([ABCS])(?![a-zA-Z])", part, re.IGNORECASE)
        if m:
            return f"{m.group(1).upper()}通道"

    return None


def try_expand_binary(codedset_value: str, bit_info) -> str | None:
    """
    尝试将 EoICD CodedSet 的十进制编码值展开为二进制位描述。

    bit_info: 来自 eoicd_identity.bit，可能是:
      - 单值: 8（整数）
      - 范围: "8-9"（字符串）
      - null / None

    返回: 展开后的字符串 或 None（无法展开）
    """
    # 解析编码值（如 "3=AMSC2B" → 3）
    m = re.match(r"(\d+)=", str(codedset_value))
    if not m:
        return None

    code_value = int(m.group(1))

    # === bit 为 null / None ===
    if bit_info is None:
        return None  # 无法展开，标记为 "bit信息缺失"

    # === bit 为范围 "8-9" ===
    if isinstance(bit_info, str) and "-" in bit_info:
        start, end = map(int, bit_info.split("-"))
        num_bits = end - start + 1
        max_value = (1 << num_bits) - 1

        if code_value > max_value:
            return None  # 编码值超出 bit 范围，数据异常

        # 逐位展开（bit_start 为最低位）
        bits = []
        for i in range(num_bits):
            bit_val = (code_value >> i) & 1
            bits.append(f"bit{start + i}={bit_val}")

        bit_desc = ",".join(bits)
        meaning = str(codedset_value).split("=", 1)[1]
        return f"编码值{code_value}(二进制:{bit_desc})={meaning}"

    # === bit 为单值（整数或数字字符串）===
    try:
        bit_num = int(bit_info)
    except (ValueError, TypeError):
        return None

    if code_value > 1:
        return None  # 单 bit 但编码值>1，信息不足，标记为 "bit信息待补充"

    bit_val = code_value
    meaning = str(codedset_value).split("=", 1)[1]
    return f"编码值{code_value}(二进制:bit{bit_num}={bit_val})={meaning}"


def inject_channel_to_codedset(eoicd_attributes: dict, signal_name: str, signal_short_name: str, eoicd_identity: dict | None = None) -> dict:
    """
    对 SDI 信号的 CodedSet 属性注入通道信息和二进制展开。

    如果 signal_short_name 为 "SDI"，且能提取到通道信息，
    则将通道前缀和二进制展开注入到 eoicd_attributes.DP.CodedSet 中。

    返回：修改后的 attributes 字典（深拷贝，不污染原始数据）
    """
    # 只对 SDI 信号处理
    if signal_short_name != "SDI":
        return eoicd_attributes

    channel = extract_channel(signal_name)
    if not channel:
        return eoicd_attributes

    # 深拷贝，避免污染原始数据
    attrs = copy.deepcopy(eoicd_attributes)

    # 注入通道信息到 DP.CodedSet
    dp_layer = attrs.get("DP")
    if not isinstance(dp_layer, dict) or "CodedSet" not in dp_layer:
        return attrs

    original = dp_layer["CodedSet"]
    if original is None:
        return attrs

    # 尝试二进制展开
    bit_info = eoicd_identity.get("bit") if eoicd_identity else None
    # 兼容新数据：优先 bit，回退 bit_range
    if bit_info is None and eoicd_identity:
        bit_info = eoicd_identity.get("bit_range")
    expanded = try_expand_binary(original, bit_info)

    if expanded:
        dp_layer["CodedSet"] = f"[{channel}] {expanded}"
    else:
        # 无法展开，标记原因
        if bit_info is None:
            dp_layer["CodedSet"] = f"[{channel}] {original} (bit信息缺失)"
        else:
            dp_layer["CodedSet"] = f"[{channel}] {original} (bit信息待补充)"

    return attrs

class HLRUnitConverter:
    """
    HLR 单位换算工具（预留）
    目前仅做透传，后续确定换算规则后在此实现
    """
    def __init__(self, config: dict):
        self.enabled = config.get("unit_conversion", {}).get("enabled", False)
        self.rules = config.get("unit_conversion", {}).get("rules", {})

    def convert(self, value: str, target_unit: str = None) -> str:
        """目前直接返回原值，后续实现换算逻辑"""
        if not self.enabled or not self.rules:
            return value
        # TODO: 实现单位换算
        return value


# ============================ 属性提取 ============================

def get_nested_value(data: dict, path: str) -> Any:
    """
    从嵌套字典中提取值。
    path 格式："层级名.属性名"，如 "DP.BitOffsetWithinDS"
    """
    parts = path.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


# ============================ 默认比较规则 ============================

# ---------------------------------------------------------------------------
# 方案X（Tier1 单位归一化）
# 数值相等、且恰好一侧带单位后缀时判一致（如 EoICD '1000' vs HLR '1000ms'）。
# 仅处理"单位只是格式后缀"的情形；跨单位换算（'1s' vs '1000ms'）属 Tier2，需属性类隐式单位
# 标注，暂不做。此归一在脚本比对阶段生效，命中即判"一致"，不再流入 AI 冲突判定（治本）。
# ---------------------------------------------------------------------------
_UNIT_NUM_RE = re.compile(r'^\s*([+-]?\d+(?:\.\d+)?)\s*([a-zA-ZμΩ%°]*)\s*$')


def _split_num_unit(s: str):
    """拆出 (数值串, 单位后缀)。非"数值+可选单位"格式返回 None。"""
    m = _UNIT_NUM_RE.match(s)
    if not m:
        return None
    return m.group(1), m.group(2).lower()


def _unit_normalized_equal(eoicd_str: str, hlr_str: str) -> bool:
    a = _split_num_unit(eoicd_str)
    b = _split_num_unit(hlr_str)
    if a is None or b is None:
        return False
    num_a, unit_a = a
    num_b, unit_b = b
    if num_a != num_b:
        return False
    # 数值相等，且恰好一侧带单位后缀 → 单位差异视为格式差异（EoICD 一般省单位）
    return (unit_a == "") != (unit_b == "")


def default_compare(eoicd_value: Any, hlr_value: Any) -> bool:
    """
    默认比较规则：字符串相等（不区分大小写，去空格）。
    如果任一方为 None → 不比较（由调用方处理为 not_mentioned）。
    方案X Tier1：数值相等、仅一侧带单位后缀（'1000' vs '1000ms'）亦判一致。
    """
    if eoicd_value is None or hlr_value is None:
        return False  # 不触发 matched，由上层判定为 not_mentioned

    eoicd_str = str(eoicd_value).strip().lower()
    hlr_str = str(hlr_value).strip().lower()

    # 空字符串视为无值
    if not eoicd_str or not hlr_str:
        return False

    if eoicd_str == hlr_str:
        return True
    # 方案X Tier1：'1000' vs '1000ms' 类单位后缀伪分歧
    return _unit_normalized_equal(eoicd_str, hlr_str)


# ============================ HLR 属性聚合 ============================

def load_hlr_clustered_attrs(hlr_clustered_path: str) -> dict[str, dict]:
    """
    从 hlr_clustered.json 的 attribute_classes 数组中加载每个 req_id 的完整属性。

    返回: {req_id: {attr_class: [记录, ...]}, ...}
    记录结构: {"bit": bit号或None, "name": 接口名或None, "value": 属性值}

    ★2026-10-28 修复（原实现按 req_id 覆盖）：
      原实现是 `req_attrs[req_id][cls_name] = req.get("attr_value")`，
      而 hlr_clustered.json 中**同一 req_id 下同一属性类可能有多条记录**——
      一条需求描述整条报文的多个接口时，每个接口一条（如 HLR_547 的 CodedSet
      有 bit22~bit28 共 7 条，各含自己那个 bit 的规则）。
      覆盖导致**只留最后一条**，于是 EoICD 的 bit22 信号去跟 bit28 的属性值比，
      属性比对层把信号和错误的接口属性值配对了（judge_0 实测 88 条属性值被丢弃）。
      现改为保留全部记录，由 aggregate_hlr_by_req 按 name→bit 选出对应那条。
    """
    with open(hlr_clustered_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    req_attrs: dict[str, dict] = {}
    for attr_cls in data.get("attribute_classes", []):
        cls_name = attr_cls.get("attribute", "")
        if not cls_name:
            continue
        for req in attr_cls.get("requirements", []):
            req_id = req.get("req_id")
            if not req_id:
                continue
            if req_id not in req_attrs:
                req_attrs[req_id] = {}
            req_attrs[req_id].setdefault(cls_name, []).append({
                "bit": req.get("bit"),
                "name": req.get("name"),
                "value": req.get("attr_value"),
            })

    return req_attrs


def _load_req_texts(hlr_clustered_path: str) -> dict[str, str]:
    """从 hlr_clustered.json 顶层 requirements 取 {req_id: req_text}。

    ★2026-10-28：需求原文原先拿不到（attribute_classes.requirements 里没有
    req_text，只有顶层 requirements 有）。原文是 AI 判"语义等价"的必要上下文
    （如"当…故障有效标志为'有效'时，bit22=1" 才能说明 CodedSet 的极性）。

    req_text 可能很长（实测中位 138、最长 535 字符），此处按 REQ_TEXT_MAX 截断，
    避免 prompt 体积失控。截断在 AI 侧仍能拿到前半部分的接口清单与规则。
    """
    try:
        with open(hlr_clustered_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for req in data.get("requirements", []) or []:
        rid = req.get("req_id")
        if not rid:
            continue
        text = (req.get("req_text") or "").strip()
        if not text:
            continue
        out[rid] = text if len(text) <= REQ_TEXT_MAX else text[:REQ_TEXT_MAX] + "…"
    return out


def _select_attr_record(records: list[dict], hlr_identity: dict) -> tuple[dict | None, str]:
    """从同一 (req_id, 属性类) 的多条记录中选出与该 HLR 接口对应的那条。

    选值优先级（依据实测，3 judge × pub/sub 全量）：
      1. **name 精确匹配**（最精确，能区分同 req 下不同接口）
      2. **bit 匹配**（name 为空时的次选）
      3. **首条兜底**（name/bit 都定位不到时）

    返回 (选中的记录, 命中方式)。命中方式用于告警统计：
      'single' 单记录无歧义 / 'name' / 'name+bit' / 'bit' / 'fallback_first'（定位不到）
    """
    if not records:
        return None, "none"
    if len(records) == 1:
        return records[0], "single"

    name = (hlr_identity or {}).get("name")
    if name:
        by_name = [r for r in records if r.get("name") == name]
        if len(by_name) == 1:
            return by_name[0], "name"
        if len(by_name) > 1:
            # name 多命中 → 再用 bit 细分
            bit = (hlr_identity or {}).get("bit")
            if bit not in (None, ""):
                nb = [r for r in by_name if str(r.get("bit")) == str(bit)]
                if len(nb) == 1:
                    return nb[0], "name+bit"
            return by_name[0], "name_ambiguous"

    bit = (hlr_identity or {}).get("bit")
    if bit not in (None, ""):
        by_bit = [r for r in records if str(r.get("bit")) == str(bit)]
        if len(by_bit) == 1:
            return by_bit[0], "bit"
        if len(by_bit) > 1:
            return by_bit[0], "bit_ambiguous"

    # 定位不到 → 退回首条并记告警（绝不能因此丢掉该属性：
    # 丢属性会让它从比对中消失变成 not_mentioned，比取值错更糟）
    return records[0], "fallback_first"


def aggregate_hlr_by_req(matched_hlrs: list[dict], hlr_attr_map: dict[str, dict],
                         select_stats: dict | None = None) -> dict[str, dict]:
    """
    将 matched_hlrs 按 req_id 聚合，属性值从 hlr_clustered.json 的完整属性中读取。

    输入:
      - matched_hlrs: 身份匹配输出的 matched_hlrs 列表
      - hlr_attr_map: load_hlr_clustered_attrs() 加载的 {req_id: {attr_class: [记录,...]}}
      - select_stats: 可选，用于回填选值命中方式统计（'name'/'bit'/'single'/'fallback_first'…）

    输出: {
      "req_id": {
        "req_id": "...",
        "hlr_identity": {...},
        "attributes": {"attr_class": "value", ...}
      },
      ...
    }

    ★2026-10-28 修复：原实现直接 `hlr_attr_map[req_id].copy()` 取值，
    而该 map 原按 req_id 覆盖（同一 req 的多接口只留最后一条）→ 属性比对层
    把信号和错误的接口属性值配对。现按 hlr_identity 的 name→bit 精确选出对应那条。
    """
    hlr_by_req: dict[str, dict] = {}
    stats = select_stats if select_stats is not None else {}

    for hlr in matched_hlrs:
        req_id = hlr.get("req_id")
        if not req_id:
            continue

        if req_id not in hlr_by_req:
            hlr_by_req[req_id] = {
                "req_id": req_id,
                "hlr_identity": hlr.get("hlr_identity", {}),
                "attributes": {},
            }
        hlr_identity = hlr_by_req[req_id]["hlr_identity"]

        # 从 hlr_clustered.json 的完整属性中读取（优先于 matched_hlr 中的回填属性）
        if req_id in hlr_attr_map:
            for attr_cls, records in hlr_attr_map[req_id].items():
                rec, how = _select_attr_record(records, hlr_identity)
                if rec is None:
                    continue
                hlr_by_req[req_id]["attributes"][attr_cls] = rec.get("value")
                stats[how] = stats.get(how, 0) + 1
        else:
            # 回退：使用 matched_hlr 中的回填属性
            # 兼容新格式 attribute_classes(列表) 和旧格式 attribute_class(字符串)
            attr_classes = hlr.get("attribute_classes", [])
            if not attr_classes:
                # 旧格式兼容
                old_attr_class = hlr.get("attribute_class", "")
                if old_attr_class:
                    attr_classes = [old_attr_class]
            attr_value = hlr.get("attr_value")
            for attr_cls in attr_classes:
                if attr_cls:
                    hlr_by_req[req_id]["attributes"][attr_cls] = attr_value
            stats["fallback_matched_hlr"] = stats.get("fallback_matched_hlr", 0) + 1

    return hlr_by_req


# ============================ 脚本预匹配主函数 ============================

def run_script_matching(match_report: dict, config: dict) -> dict:
    """
    执行脚本预匹配。

    返回：{
      "stats": {"matched", "not_mentioned", "null_value", "mismatch", "total_checks"},
      "matched_pairs": [检查项对象...],
      "not_mentioned_pairs": [检查项对象...],
      "null_value_pairs": [检查项对象...],      // 新增
      "mismatch_pairs": [检查项对象...],
    }
    """
    attr_mapping = config.get("attribute_mapping", {})

    # 单位换算器（预留）
    converter = HLRUnitConverter(config)

    # 加载 HLR 聚类结果的完整属性（用于属性匹配阶段）
    # 优先使用配置中的 input_hlr（集成模式已由 run_integration.py 指向 workspace/data/input 副本）；
    # 相对路径回退到模块目录（独立调试场景），不再硬编码模块 data/input，避免集成流程因模块样本被清理而失败。
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    paths_cfg = config.get("paths", {}) if isinstance(config, dict) else {}
    hlr_clustered_path = paths_cfg.get("input_hlr") or os.path.join(project_root, "data", "input", "hlr_clustered.json")
    if not os.path.isabs(hlr_clustered_path):
        hlr_clustered_path = os.path.join(project_root, hlr_clustered_path)
    hlr_attr_map = load_hlr_clustered_attrs(hlr_clustered_path)
    # 需求原文（供 AI 判语义等价时用；见 _load_req_texts 说明）
    req_texts = _load_req_texts(hlr_clustered_path)

    matched_pairs = []
    not_mentioned_pairs = []
    null_value_pairs = []   # 新增：无具体值
    mismatch_pairs = []

    stats = {"matched": 0, "not_mentioned": 0, "null_value": 0, "mismatch": 0, "total_checks": 0}
    # 属性值选值命中方式统计（2026-10-28）：用于观测"多接口需求选值"是否正常。
    # fallback_first / *_ambiguous > 0 说明有记录定位不到，需人工看一眼。
    select_stats: dict[str, int] = {}
    # 编码表述归一统计（2026-10-28）：normalized_matched=归一后判一致（省了 AI 调用）
    # / structural_conflict=结构矛盾 / needs_ai=归一判不了、仍交 AI
    check_norm_stats: dict[str, int] = {}

    matches = match_report.get("matches", [])

    for eoicd_match in matches:
        eoicd_cluster_id = eoicd_match.get("eoicd_cluster_id", "unknown")
        eoicd_signal_name = eoicd_match.get("eoicd_signal_name", "")
        eoicd_signal_short_name = eoicd_match.get("eoicd_signal_short_name", "")
        eoicd_attributes = eoicd_match.get("eoicd_attributes", {})
        matched_hlrs = eoicd_match.get("matched_hlrs", [])

        # 预处理：SDI 信号注入通道信息到 CodedSet
        eoicd_identity = eoicd_match.get("eoicd_identity", {})
        eoicd_attributes = inject_channel_to_codedset(
            eoicd_attributes, eoicd_signal_name, eoicd_signal_short_name, eoicd_identity
        )

        # HLR 按 req_id 聚合（从 hlr_clustered.json 读取完整属性）
        hlr_by_req = aggregate_hlr_by_req(matched_hlrs, hlr_attr_map, select_stats)

        # 遍历每个映射的属性
        for eoicd_attr_path, hlr_attr_name in attr_mapping.items():
            # 1. 从 EoICD 提取值
            eoicd_value = get_nested_value(eoicd_attributes, eoicd_attr_path)

            # EoICD 无此属性 → 跳过（不检查）
            if eoicd_value is None:
                continue

            # 2. 统计所有 req_id 对该属性的覆盖情况
            has_attr_req_ids = []      # 有该属性类的 req_id
            missing_attr_req_ids = []  # 没有该属性类的 req_id

            for req_id, hlr_data in hlr_by_req.items():
                hlr_attrs = hlr_data.get("attributes", {})
                if hlr_attr_name in hlr_attrs:
                    has_attr_req_ids.append(req_id)
                else:
                    missing_attr_req_ids.append(req_id)

            # 3. 所有 HLR 都没有这个属性类 → not_mentioned（未提及）
            if len(has_attr_req_ids) == 0:
                for req_id in missing_attr_req_ids:
                    hlr_data = hlr_by_req[req_id]
                    not_mentioned_pairs.append({
                        "eoicd_cluster_id": eoicd_cluster_id,
                        "eoicd_signal_name": eoicd_signal_name,
                        "eoicd_signal_short_name": eoicd_signal_short_name,
                        "req_id": req_id,
                        "hlr_identity": hlr_data.get("hlr_identity", {}),
                        "attribute": eoicd_attr_path,
                        "hlr_attr_name": hlr_attr_name,
                        "eoicd_value": eoicd_value,
                        "hlr_value": None,
                        "result": "not_mentioned",
                    })
                    stats["total_checks"] += 1
                    stats["not_mentioned"] += 1
                continue

            # 4. 部分/全部 req_id 有该属性 → 对这些 req_id 做比较
            for req_id in has_attr_req_ids:
                hlr_data = hlr_by_req[req_id]
                hlr_identity = hlr_data.get("hlr_identity", {})
                hlr_attrs = hlr_data.get("attributes", {})

                stats["total_checks"] += 1

                hlr_value = hlr_attrs[hlr_attr_name]

                # HLR 单位换算（预留，目前透传）
                hlr_value = converter.convert(hlr_value)

                # HLR 值为 null 或空 → null_value（无具体值）
                if hlr_value is None or str(hlr_value).strip() == "":
                    null_value_pairs.append({
                        "eoicd_cluster_id": eoicd_cluster_id,
                        "eoicd_signal_name": eoicd_signal_name,
                        "eoicd_signal_short_name": eoicd_signal_short_name,
                        "req_id": req_id,
                        "hlr_identity": hlr_identity,
                        "attribute": eoicd_attr_path,
                        "hlr_attr_name": hlr_attr_name,
                        "eoicd_value": eoicd_value,
                        "hlr_value": hlr_value,
                        "result": "null_value",
                    })
                    stats["null_value"] += 1
                    continue

                # HLR 有具体值 → 走比较规则（统一字符串比较）
                is_match = default_compare(eoicd_value, hlr_value)

                # ── 编码表述归一（2026-10-28）────────────────────────────
                # 字符串不等不一定是真不一致：同一套编码的两种写法（如 EoICD 侧
                # "0=Not Failed;1=Failed" 与 HLR 侧 "…→bit22=1" 规则表）会被
                # default_compare 判不等。此时用确定性归一再判一次：
                #   True  → 结构等价，判 matched（省一次 AI 调用，且修正假不一致）
                #   None  → 语义等价与否无法确定 → 保持 mismatch，交 AI 复核
                #   False → 结构矛盾（如该 bit 取值在 EoICD 枚举里没有对应项）
                #           → 保持 mismatch（不改变结论，避免误伤）
                norm_verdict = None
                if not is_match:
                    norm_verdict = codeset_equivalent(
                        eoicd_value, hlr_value,
                        eoicd_bit=eoicd_identity.get("bit_range") or eoicd_identity.get("bit"),
                        hlr_bit=hlr_identity.get("bit"),
                    )
                    if norm_verdict is True:
                        is_match = True
                        check_norm_stats["normalized_matched"] = check_norm_stats.get("normalized_matched", 0) + 1
                    elif norm_verdict is False:
                        check_norm_stats["structural_conflict"] = check_norm_stats.get("structural_conflict", 0) + 1
                    else:
                        check_norm_stats["needs_ai"] = check_norm_stats.get("needs_ai", 0) + 1

                # 判定结果
                check_item = {
                    "eoicd_cluster_id": eoicd_cluster_id,
                    "eoicd_signal_name": eoicd_signal_name,
                    "eoicd_signal_short_name": eoicd_signal_short_name,
                    "req_id": req_id,
                    "hlr_identity": hlr_identity,
                    "attribute": eoicd_attr_path,
                    "hlr_attr_name": hlr_attr_name,
                    "eoicd_value": eoicd_value,
                    "hlr_value": hlr_value,
                    # ── 送 AI 复核所需的上下文（2026-10-28）──
                    # eoicd_bit：EoICD 信号的 bit 区间（"22-22"）
                    # req_text：需求原文，AI 判"语义等价"的必要上下文
                    "eoicd_bit": eoicd_identity.get("bit_range") or eoicd_identity.get("bit"),
                    "req_text": req_texts.get(req_id, ""),
                }
                if norm_verdict is not None:
                    check_item["norm_verdict"] = norm_verdict

                if is_match:
                    check_item["result"] = "matched"
                    matched_pairs.append(check_item)
                    stats["matched"] += 1
                else:
                    check_item["result"] = "mismatch"
                    mismatch_pairs.append(check_item)
                    stats["mismatch"] += 1

    return {
        "stats": stats,
        "matched_pairs": matched_pairs,
        "not_mentioned_pairs": not_mentioned_pairs,
        "null_value_pairs": null_value_pairs,   # 新增
        "mismatch_pairs": mismatch_pairs,
        # 属性值选值命中方式（2026-10-28）：观测多接口需求的选值是否正常
        "select_stats": select_stats,
        # 编码表述归一统计（2026-10-28）
        "norm_stats": check_norm_stats,
    }


# ============================ 自测 ============================

def _self_test() -> None:
    """自测：多接口需求的属性值选值（name→bit→首条兜底）。

    覆盖的高频回归点：
      - 同一 req_id 下同一属性类有多条记录时，必须按 name/bit 选对那条（原实现按
        req_id 覆盖 → 只留最后一条 → 信号与错误接口的属性值配对）。
      - 单记录场景行为不变。
      - 选不中时退回首条（不丢属性）。
    """
    # --- 构造一份 hlr_clustered.json：一 req 多接口（bit22/bit23），同名属性类 ---
    import json as _json
    import os as _os
    import tempfile as _tf
    d = _tf.mkdtemp()
    p = _os.path.join(d, "hlr_clustered.json")
    _json.dump({
        "attribute_classes": [
            {"attribute": "CodedSet", "requirements": [
                {"req_id": "R1", "bus": "A429", "label": "L11", "name": "信号甲", "bit": "22",
                 "attr_value": "甲有效→bit22=1"},
                {"req_id": "R1", "bus": "A429", "label": "L11", "name": "信号乙", "bit": "23",
                 "attr_value": "乙有效→bit23=1"},
            ]},
            {"attribute": "RefreshPeriod", "requirements": [
                {"req_id": "R1", "name": None, "bit": None, "attr_value": "1000ms"},
            ]},
        ],
    }, open(p, "w", encoding="utf-8"), ensure_ascii=False)

    m = load_hlr_clustered_attrs(p)
    # ① 多值被保留（不再覆盖）
    assert isinstance(m["R1"]["CodedSet"], list), type(m["R1"]["CodedSet"])
    assert len(m["R1"]["CodedSet"]) == 2, m["R1"]["CodedSet"]
    assert len(m["R1"]["RefreshPeriod"]) == 1

    # ② 按 name 选中 bit22 那条
    rec, how = _select_attr_record(m["R1"]["CodedSet"], {"name": "信号甲", "bit": "22"})
    assert rec["value"] == "甲有效→bit22=1", rec
    assert how == "name", how

    # ③ name 为空 → 按 bit 选
    rec, how = _select_attr_record(m["R1"]["CodedSet"], {"name": None, "bit": "23"})
    assert rec["value"] == "乙有效→bit23=1", rec
    assert how == "bit", how

    # ④ name 与 bit 都定位不到 → 退回首条（不丢属性）
    rec, how = _select_attr_record(m["R1"]["CodedSet"], {"name": "不存在", "bit": "99"})
    assert rec is not None and how == "fallback_first", (rec, how)

    # ⑤ 单记录 → single
    rec, how = _select_attr_record(m["R1"]["RefreshPeriod"], {"name": None, "bit": None})
    assert rec["value"] == "1000ms" and how == "single", (rec, how)

    # ⑥ 端到端：aggregate_hlr_by_req 用 name 选对值
    mh = [{"req_id": "R1", "hlr_identity": {"name": "信号甲", "bit": "22"},
           "attribute_classes": ["CodedSet"], "attr_value": "回填值"}]
    st = {}
    out = aggregate_hlr_by_req(mh, m, st)
    assert out["R1"]["attributes"]["CodedSet"] == "甲有效→bit22=1", out
    assert out["R1"]["attributes"]["RefreshPeriod"] == "1000ms", out
    assert st.get("name") == 1, st

    # ⑦ 命中方式统计里有 fallback_first 时可被观测
    mh2 = [{"req_id": "R1", "hlr_identity": {"name": "不存在", "bit": "99"},
            "attribute_classes": ["CodedSet"], "attr_value": "x"}]
    st2 = {}
    aggregate_hlr_by_req(mh2, m, st2)
    assert st2.get("fallback_first", 0) >= 1, st2

    # ── ⑧ 编码表述归一接入比对主循环（端到端）────────────────────
    # 场景：EoICD 枚举 "0=Not Failed;1=Failed" 与 HLR 规则表 "…→bit22=1" 语义同源，
    # default_compare 判不等 → 归一后判 True → 应升级为 matched。
    d2 = _tf.mkdtemp()
    p2 = _os.path.join(d2, "hlr_clustered.json")
    _json.dump({
        "attribute_classes": [
            {"attribute": "CodedSet", "requirements": [
                {"req_id": "R2", "name": "信号甲", "bit": "22",
                 "attr_value": "Failed→bit22=1"},
            ]},
        ],
    }, open(p2, "w", encoding="utf-8"), ensure_ascii=False)
    mr = {"matches": [{
        "eoicd_cluster_id": "C1",
        "eoicd_signal_name": "SIG.X",
        "eoicd_signal_short_name": "X",
        "eoicd_identity": {"bus": "A429", "label": "11", "bit_range": "22-22"},
        "eoicd_attributes": {"DP": {"CodedSet": "0=Not Failed;1=Failed"}},
        "matched_hlrs": [{"req_id": "R2", "hlr_identity": {"name": "信号甲", "bit": "22"},
                          "attribute_classes": ["CodedSet"], "attr_value": "回填值"}],
    }]}
    cfg = {"attribute_mapping": {"DP.CodedSet": "CodedSet"}, "paths": {"input_hlr": p2}}
    res = run_script_matching(mr, cfg)
    # 归一判 True → matched（原本 default_compare 会判不等）
    assert res["stats"]["matched"] == 1, res["stats"]
    assert res["stats"]["mismatch"] == 0, res["stats"]
    assert res["norm_stats"].get("normalized_matched") == 1, res["norm_stats"]

    # 归一判不了（语义无法确定）→ 保持 mismatch 交 AI
    d3 = _tf.mkdtemp()
    p3 = _os.path.join(d3, "hlr_clustered.json")
    _json.dump({"attribute_classes": [
        {"attribute": "CodedSet", "requirements": [
            {"req_id": "R2", "name": "信号甲", "bit": "22",
             "attr_value": "压调故障有效标志有效→bit22=1"}]},
    ]}, open(p3, "w", encoding="utf-8"), ensure_ascii=False)
    cfg3 = {"attribute_mapping": {"DP.CodedSet": "CodedSet"}, "paths": {"input_hlr": p3}}
    res2 = run_script_matching(mr, cfg3)
    assert res2["stats"]["mismatch"] == 1, res2["stats"]
    assert res2["stats"]["matched"] == 0, res2["stats"]
    assert res2["norm_stats"].get("needs_ai") == 1, res2["norm_stats"]

    print("attr_script_matcher 自测通过（多接口需求按 name/bit 选值/单记录不变/"
          "选不中退回首条不丢属性/命中方式统计/编码表述归一接入 均符合预期）")


if __name__ == "__main__":  # pragma: no cover
    _self_test()
