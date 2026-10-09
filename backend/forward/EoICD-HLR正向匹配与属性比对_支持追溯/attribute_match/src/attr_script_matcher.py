"""
attr_script_matcher.py — 属性匹配：脚本预匹配

功能：
  1. HLR 属性按 req_id 聚合
  2. 遍历 attribute_mapping，逐一比对 EoICD 与 HLR 属性值
  3. 统一规则：字符串比较（不区分大小写，去空格）
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
      ⚠️ 带字母边界检查，避免误匹配 MACHINE / EXCHANGE 中的 CH

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

def default_compare(eoicd_value: Any, hlr_value: Any) -> bool:
    """
    默认比较规则：字符串相等（不区分大小写，去空格）。
    如果任一方为 None → 不比较（由调用方处理为 not_mentioned）
    """
    if eoicd_value is None or hlr_value is None:
        return False  # 不触发 matched，由上层判定为 not_mentioned

    eoicd_str = str(eoicd_value).strip().lower()
    hlr_str = str(hlr_value).strip().lower()

    # 空字符串视为无值
    if not eoicd_str or not hlr_str:
        return False

    return eoicd_str == hlr_str


# ============================ HLR 属性聚合 ============================

def load_hlr_clustered_attrs(hlr_clustered_path: str) -> dict[str, dict]:
    """
    从 hlr_clustered.json 的 attribute_classes 数组中加载每个 req_id 的完整属性。

    返回: {req_id: {attr_class: attr_value, ...}, ...}
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
            req_attrs[req_id][cls_name] = req.get("attr_value")

    return req_attrs


def aggregate_hlr_by_req(matched_hlrs: list[dict], hlr_attr_map: dict[str, dict]) -> dict[str, dict]:
    """
    将 matched_hlrs 按 req_id 聚合，属性值从 hlr_clustered.json 的完整属性中读取。

    输入:
      - matched_hlrs: 身份匹配输出的 matched_hlrs 列表
      - hlr_attr_map: load_hlr_clustered_attrs() 加载的 {req_id: {attr_class: attr_value}}

    输出: {
      "req_id": {
        "req_id": "...",
        "hlr_identity": {...},
        "attributes": {"attr_class": "value", ...}
      },
      ...
    }
    """
    hlr_by_req: dict[str, dict] = {}

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

        # 从 hlr_clustered.json 的完整属性中读取（优先于 matched_hlr 中的回填属性）
        if req_id in hlr_attr_map:
            hlr_by_req[req_id]["attributes"] = hlr_attr_map[req_id].copy()
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

    matched_pairs = []
    not_mentioned_pairs = []
    null_value_pairs = []   # 新增：无具体值
    mismatch_pairs = []

    stats = {"matched": 0, "not_mentioned": 0, "null_value": 0, "mismatch": 0, "total_checks": 0}

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
        hlr_by_req = aggregate_hlr_by_req(matched_hlrs, hlr_attr_map)

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
                }

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
    }
