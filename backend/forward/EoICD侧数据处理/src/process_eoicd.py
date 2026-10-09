# -*- coding: utf-8 -*-
"""
EoICD Pub/Sub 属性裁剪 + 身份信息提取工具
==================================
用途：读取 EoICD Publisher/Subscriber Excel，按信号聚合，
      裁剪保留标黄属性，输出统一 JSON。

复用反向匹配的解析逻辑：
  - _detect_side_boundaries: 检测 Pub/Sub 列边界
  - _detect_layers: 检测层级结构
  - _read_attr_names: 读取属性名
  - _read_layer_values: 读取属性值
  - _build_signal_name: 构建层级信号名
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
import yaml


# ============================ 数据模型 ============================

@dataclass
class LayerBlock:
    """一层数据的列范围"""
    layer_type: str
    start_col: int
    end_col: int = 0
    attr_names: list[str] = field(default_factory=list)


# ============================ 解析函数（复用反向匹配） ============================

def _detect_side_boundaries(row_cells: list[Any]) -> tuple[int | None, int | None]:
    """Row 1 中找 Publisher / Subscriber 的列索引（1-based）"""
    pub_col = None
    sub_col = None
    for i, val in enumerate(row_cells, start=1):
        if val is not None:
            s = str(val).strip()
            if s == "Publisher" and pub_col is None:
                pub_col = i
            elif s == "Subscriber" and sub_col is None:
                sub_col = i
    return pub_col, sub_col


def _detect_layers(row_cells: list[Any], col_start: int, col_end: int) -> list[LayerBlock]:
    """Row 2 扫描层级块"""
    layers: list[LayerBlock] = []
    current: LayerBlock | None = None
    for i in range(col_start, col_end):
        val = row_cells[i - 1] if i - 1 < len(row_cells) else None
        if val is not None:
            if current is not None:
                current.end_col = i
                layers.append(current)
            current = LayerBlock(layer_type=str(val).strip(), start_col=i)
    if current is not None:
        current.end_col = col_end
        layers.append(current)
    return layers


def _read_attr_names(row_cells: list[Any], layers: list[LayerBlock]) -> None:
    """Row 3 读取属性名填充到各 LayerBlock"""
    for layer in layers:
        for c in range(layer.start_col, layer.end_col):
            val = row_cells[c - 1] if c - 1 < len(row_cells) else None
            layer.attr_names.append(str(val).strip() if val is not None else "")


def _read_layer_values(row_cells: list[Any], layer: LayerBlock) -> dict[str, Any]:
    """从一行中读取某层的属性值"""
    values: dict[str, Any] = {}
    for offset, attr_name in enumerate(layer.attr_names):
        if not attr_name:
            continue
        c = layer.start_col + offset
        if c > len(row_cells):
            break
        val = row_cells[c - 1]
        if val is not None:
            values[attr_name] = val
    return values


def _to_int(value: Any) -> int | None:
    """把单元格值安全转为 int；空值/非法值返回 None（兼容 '16'、16.0、' 16 ' 等）"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def _build_signal_name(layer_names: list[str], include_leaf: bool = True) -> str:
    """构建层级信号名，相邻去重"""
    end = len(layer_names) if include_leaf else len(layer_names) - 1
    if end <= 0:
        return ""
    parts = []
    for name in layer_names[:end]:
        name = str(name).strip() if name else ""
        if name and (not parts or parts[-1] != name):
            parts.append(name)
    return ".".join(parts)


# ============================ 属性裁剪 + 身份信息提取核心逻辑 ============================

def process_eoicd(excel_path: Path, config: dict, side: str) -> list[dict[str, Any]]:
    """
    处理 EoICD Excel → 按信号聚合的 JSON

    Args:
        excel_path: Excel 文件路径
        config: 裁剪配置 (publisher/subscriber 下的 sheet 配置)
        side: "publisher" 或 "subscriber"
    """
    # 预编译正则
    label_pattern = re.compile(r'^L(\d+)')
    bus_patterns = [
        (re.compile(r'ANALOG|ANLG', re.I), 'Analog'),
        (re.compile(r'DISC', re.I), 'Discrete'),
        (re.compile(r'429', re.I), 'A429'),
        (re.compile(r'825', re.I), 'A825'),
        (re.compile(r'664', re.I), 'A664'),
    ]

    wb = openpyxl.load_workbook(excel_path, data_only=True, read_only=True)
    signals: list[dict[str, Any]] = []  # 不再聚合，输出所有原始行

    for sheet_name in wb.sheetnames:
        if sheet_name not in config:
            continue

        sheet_cfg = config[sheet_name]
        ws = wb[sheet_name]
        rows = [list(row) for row in ws.iter_rows(values_only=True)]

        if len(rows) < 4:
            continue

        max_col = max(len(r) for r in rows) if rows else 0
        total_cols = max_col + 1
        row1 = rows[0] if len(rows) > 0 else []
        row2 = rows[1] if len(rows) > 1 else []
        row3 = rows[2] if len(rows) > 2 else []

        # 检测 Pub/Sub 边界
        pub_col, sub_col = _detect_side_boundaries(row1)

        # 确定目标列范围
        if side == "publisher":
            target_col = pub_col
            target_end = sub_col if sub_col else total_cols
            leaf_type = "DP"
        else:
            target_col = sub_col
            target_end = total_cols
            leaf_type = "RP"

        if not target_col:
            continue

        # 检测层级
        layers = _detect_layers(row2, target_col, target_end)
        _read_attr_names(row3, layers)

        # 裁剪字段集合（用于快速查找）
        crop_fields_by_layer: dict[str, set[str]] = {}
        for layer_type, fields in sheet_cfg.get("layers", {}).items():
            crop_fields_by_layer[layer_type] = set(fields)

        # 逐行解析
        for r in range(3, len(rows)):
            row_cells = rows[r]

            # 读取各层数据
            layer_names: list[str] = []
            layer_values: list[dict[str, Any]] = []
            leaf_idx = -1

            for idx, layer in enumerate(layers):
                vals = _read_layer_values(row_cells, layer)
                layer_values.append(vals)
                name = vals.get("Name", "")
                layer_names.append(str(name).strip() if name else "")

                if layer.layer_type == leaf_type:
                    leaf_idx = idx

            if leaf_idx < 0:
                continue

            # 构建信号名
            signal_name = _build_signal_name(layer_names[:leaf_idx + 1], include_leaf=True)
            if not signal_name:
                continue

            # 检查叶子层 Name 是否为空（空行跳过）
            leaf_name = layer_names[leaf_idx] if leaf_idx < len(layer_names) else ""
            if not leaf_name:
                continue

            # ========== 提取身份信息（在裁剪之前）==========
            # direction: Publisher 表只取 DP 层 → TX（本系统设备发送的信号）
            #            Subscriber 表只取 RP 层 → RX（本系统设备接收的信号）
            identity: dict[str, Any] = {
                "bus": None,
                "direction": "TX" if side == "publisher" else "RX",
                "label": None,
                "name": leaf_name,
                "bit_range": None,
            }

            # --- bus 提取 ---
            if side == "publisher":
                bus_match = re.match(r'^(.+)-RP$', sheet_name)
                identity["bus"] = bus_match.group(1) if bus_match else None
            else:
                # Subscriber: 从第二个 LogicalPort.Physical 提取
                # layer_values 中可能存在多个 LogicalPort，需要找到 Subscriber 侧的
                physical = None
                for idx2, layer in enumerate(layers):
                    if layer.layer_type == "LogicalPort":
                        # 取该层的 Physical 值（Subscriber 侧在目标列范围内）
                        vals = layer_values[idx2]
                        if "Physical" in vals:
                            physical = vals["Physical"]
                            break
                if physical:
                    physical_str = str(physical).strip()
                    for pattern, bus_name in bus_patterns:
                        if pattern.search(physical_str):
                            identity["bus"] = bus_name
                            break

            # --- label 提取 ---
            if side == "publisher":
                # 从 A429Word.Name 提取 Label
                for idx2, layer in enumerate(layers):
                    if layer.layer_type == "A429Word":
                        vals = layer_values[idx2]
                        a429word_name = vals.get("Name")
                        if a429word_name:
                            match = label_pattern.match(str(a429word_name))
                            if match:
                                identity["label"] = match.group(1)
                        break
            else:
                # Subscriber: 从 RP.Label 读取
                label_raw = layer_values[leaf_idx].get("Label")
                if label_raw is not None:
                    label_str = str(label_raw).strip()
                    if label_str:
                        identity["label"] = label_str

            # --- bit_range 提取（位范围，闭区间 [start, end]）---
            # Publisher  → BitOffsetWithinDS
            # Subscriber → BitOffsetWithinMsg
            # end = start + ParameterSize - 1
            # 兜底：offset 缺失但 ParameterSize 存在 → 视为从 0 开始，即 [0, size-1]
            #       offset 与 size 均缺失 → bit_range = None
            bit_attr = "BitOffsetWithinDS" if side == "publisher" else "BitOffsetWithinMsg"
            bit_offset = _to_int(layer_values[leaf_idx].get(bit_attr))
            bit_size = _to_int(layer_values[leaf_idx].get("ParameterSize"))

            # offset 为负数（如 -1）在 ICD 中表示「未定义/不适用」，等同缺失处理，
            # 否则会生成 "-1--1" 这类有歧义的范围字符串
            if bit_offset is not None and bit_offset < 0:
                bit_offset = None

            if bit_size is not None and bit_size > 0:
                start = bit_offset if bit_offset is not None else 0
                identity["bit_range"] = f"{start}-{start + bit_size - 1}"

            # 裁剪保留标黄属性（按层级组织）
            attrs: dict[str, dict[str, Any]] = {}
            for idx, layer in enumerate(layers):
                layer_type = layer.layer_type
                if layer_type not in crop_fields_by_layer:
                    continue

                crop_fields = crop_fields_by_layer[layer_type]
                vals = layer_values[idx]

                layer_attrs: dict[str, Any] = {}
                for attr_name, attr_value in vals.items():
                    if attr_name in crop_fields:
                        # 属性值不存在时赋 None，保持 JSON 格式统一
                        layer_attrs[attr_name] = attr_value if attr_value is not None else None

                if layer_attrs:
                    attrs[layer_type] = layer_attrs

            # 每个原始行作为独立信号输出（不去重，聚类工具负责）
            signals.append({
                "_meta": {
                    "sheet": sheet_name,
                    "side": side,
                    "source_file": excel_path.name,
                },
                "signal_name": signal_name,
                "signal_short_name": leaf_name,
                "identity": identity,        # 【新增】身份信息
                "attributes": attrs,
            })

    return signals


# ============================ 主入口 ============================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="EoICD 属性裁剪与身份信息提取")
    parser.add_argument("--input", "-i", required=True, help="输入 Excel 文件路径")
    parser.add_argument("--config", "-c", required=True, help="裁剪配置文件路径 (YAML)")
    parser.add_argument("--side", "-s", choices=["publisher", "subscriber"], required=True,
                        help="处理 Publisher 还是 Subscriber 侧")
    parser.add_argument("--output", "-o", required=True, help="输出 JSON 文件路径")
    args = parser.parse_args()

    # 读取配置
    with open(args.config, "r", encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    config = full_config.get(args.side, {})
    if not config:
        print(f"错误: 配置文件中未找到 '{args.side}' 配置", file=sys.stderr)
        sys.exit(1)

    # 处理
    result = process_eoicd(Path(args.input), config, args.side)

    # 输出 JSON
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"处理完成: {len(result)} 条信号 → {output_path}")


if __name__ == "__main__":
    main()
