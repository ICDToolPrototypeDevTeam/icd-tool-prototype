# -*- coding: utf-8 -*-
"""
EoICD 去重工具
==============
用途：将 signal_name（FullName）完全相同的 EoICD 信号归为一簇，
      减少后续追溯过滤与身份比对的检查量。

去重逻辑：
  - 按 side + sheet + signal_name 分组
  - 组内属性完全一致：选一个代表，记录成员列表（标准去重）
  - 组内属性不同（同名异值）：拆分，每个成员单独成簇、不合并

追溯过滤（可选）：
  - 传入 -t trace_result.json 时，在归簇后按追溯链过滤，
    只保留与输入软件高层需求相关的 EoICD，输出 *_traced.json
  - 追溯表不存在或未启用时，保留全部簇，行为与原来完全一致

用法：
  python3 dedup_eoicd.py -i eoicd_pub.json -o eoicd_pub_clustered.json
  python3 dedup_eoicd.py -i eoicd_pub.json -o eoicd_pub_clustered.json -t trace_result.json
"""

import json
import argparse
from pathlib import Path
from typing import Any


def make_fingerprint(signal: dict) -> str:
    """
    生成属性指纹，用于判断两个信号的属性是否完全相同。
    按层级类型和属性名排序，确保顺序一致。
    """
    attrs = signal.get("attributes", {})
    parts = []
    # 按层级类型排序（固定顺序）
    for layer_type in sorted(attrs.keys()):
        layer_attrs = attrs[layer_type]
        # 按属性名排序
        for attr_name in sorted(layer_attrs.keys()):
            val = layer_attrs[attr_name]
            # 统一转为字符串，处理数值和None
            val_str = str(val) if val is not None else ""
            parts.append(f"{layer_type}.{attr_name}={val_str}")
    return "|".join(parts)


def _attach_identity_key(signal: dict) -> None:
    """根据 identity 五元组生成 identity_key，写入 signal。

    格式: bus--direction--label--name--bit_range
      direction : TX（本系统设备发送，取自 Publisher 表 DP 层）/
                  RX（本系统设备接收，取自 Subscriber 表 RP 层）
      bit_range : 位范围闭区间字符串，如 "16-31"
    """
    identity = signal.get("identity", {})
    bus = identity.get("bus")
    direction = identity.get("direction")
    label = identity.get("label")
    name = identity.get("name")
    bit_range = identity.get("bit_range")
    signal["identity_key"] = (
        f"{bus if bus is not None else 'None'}--"
        f"{direction if direction is not None else 'None'}--"
        f"{label if label is not None else 'None'}--"
        f"{name if name is not None else 'None'}--"
        f"{bit_range if bit_range is not None else 'None'}"
    )


def dedup_signals(signals: list[dict]) -> list[dict]:
    """
    去重：signal_name（完整路径/FullName）完全相同的信号归为一簇。

    分组键：(side, sheet, signal_name)

    行为：
      - 组内所有成员的属性指纹（make_fingerprint）完全一致 → 标准去重，
        只保留一个代表，并记录成员列表（无数据损失）。
      - 组内存在「同名异值」（属性指纹不同）→ 拆分，每个成员单独成簇、
        不合并，避免静默丢失属性。这些簇共享同一 cluster_id 并标记
        _cluster.split=True，便于下游识别。

    返回：去重/拆分后的信号列表，每个信号增加 _cluster 与 identity_key 字段。
    """
    # 按 (side, sheet, signal_name) 分组
    groups: dict[tuple[str, str, str], list[dict]] = {}

    for signal in signals:
        meta = signal.get("_meta", {})
        side = meta.get("side", "unknown")
        sheet = meta.get("sheet", "unknown")
        signal_name = signal.get("signal_name", "")

        key = (side, sheet, signal_name)
        groups.setdefault(key, []).append(signal)

    # 构建去重/拆分结果
    deduped = []
    cluster_idx = 0
    split_group_count = 0
    split_member_count = 0

    for (side, sheet, signal_name), members in groups.items():
        cluster_idx += 1
        cluster_id = f"{sheet}-{side[:3]}-{cluster_idx:04d}"
        member_names = [m.get("signal_name", "") for m in members]

        # 计算属性指纹，判断是否「同名异值」
        fingerprints = [make_fingerprint(m) for m in members]
        is_uniform = len(set(fingerprints)) == 1

        if is_uniform:
            # 标准去重：保留第一个成员作代表
            representative = members[0].copy()
            representative["_cluster"] = {
                "cluster_id": cluster_id,
                "duplicate_count": len(members),
                "members": member_names,
                "split": False,
            }
            _attach_identity_key(representative)
            deduped.append(representative)
        else:
            # 同名异值：拆分，每个成员单独成簇，不合并
            split_group_count += 1
            split_member_count += len(members)
            print(f"  ⚠️ 同名异值拆分: {sheet}/{side} {signal_name} "
                  f"({len(members)} 个成员属性不同，已拆分保留)")
            for i, member in enumerate(members):
                entry = member.copy()
                entry["_cluster"] = {
                    "cluster_id": cluster_id,
                    "duplicate_count": len(members),
                    "members": member_names,
                    "split": True,
                    "split_index": i,
                }
                _attach_identity_key(entry)
                deduped.append(entry)

    if split_group_count:
        print(f"  [去重] 共 {split_group_count} 组同名异值被拆分为 "
              f"{split_member_count} 个独立簇（未合并，无属性丢失）")

    return deduped


def print_summary(signals: list[dict], deduped: list[dict]) -> None:
    """打印去重统计摘要。"""
    total_in = len(signals)
    total_out = len(deduped)
    duplicates = total_in - total_out
    ratio = (duplicates / total_in * 100) if total_in > 0 else 0

    print("\n===== EoICD 去重统计 =====")
    print(f"  输入信号数:   {total_in}")
    print(f"  去重后数量:   {total_out}")
    print(f"  去重数量:     {duplicates} ({ratio:.1f}%)")
    compression = f"{total_in / total_out:.1f}x" if total_out > 0 else "N/A"
    print(f"  压缩比:       {compression}")

    # 簇大小分布
    size_counts: dict[int, int] = {}
    for c in deduped:
        count = c.get("_cluster", {}).get("duplicate_count", 1)
        size_counts[count] = size_counts.get(count, 0) + 1

    print(f"\n  簇大小分布:")
    for size in sorted(size_counts.keys()):
        count = size_counts[size]
        print(f"    {size:3d} 个信号的簇: {count:4d} 个")

    # 最大的几个簇
    print(f"\n  最大的 5 个簇:")
    sorted_by_size = sorted(deduped, key=lambda x: x.get("_cluster", {}).get("duplicate_count", 1), reverse=True)
    for c in sorted_by_size[:5]:
        cluster = c.get("_cluster", {})
        print(f"    {cluster.get('cluster_id', '')}: {cluster.get('duplicate_count', 1)} 个成员")
        print(f"      代表: {c.get('signal_name', '')}")
        members = cluster.get("members", [])
        if len(members) > 1:
            print(f"      成员: {', '.join(members[:3])}{'...' if len(members) > 3 else ''}")


def filter_by_trace(deduped: list[dict], trace_file: Path) -> tuple[list[dict], dict]:
    """
    按追溯结果过滤簇：只保留 signal_name（FullName）命中追溯链的簇。

    返回 (过滤后的簇列表, 统计信息)
    """
    with open(trace_file, "r", encoding="utf-8") as f:
        trace = json.load(f)

    if not trace.get("enabled"):
        return deduped, {"enabled": False, "reason": trace.get("reason", "")}

    allowed = set(trace.get("eoicd_fullnames", []))
    kept, dropped = [], []
    for cluster in deduped:
        name = cluster.get("signal_name", "")
        if name in allowed:
            kept.append(cluster)
        else:
            dropped.append(name)

    stats = {
        "enabled": True,
        "chain": trace.get("chain"),
        "trace_fullnames": len(allowed),
        "clusters_before": len(deduped),
        "clusters_kept": len(kept),
        "clusters_dropped": len(dropped),
        "unmatched_trace_fullnames": len(allowed - {c.get("signal_name", "") for c in kept}),
        "dropped_samples": dropped[:5],
    }
    return kept, stats


def print_trace_summary(stats: dict) -> None:
    """打印追溯过滤统计"""
    print("\n===== 追溯过滤统计 =====")
    if not stats.get("enabled"):
        print(f"  未启用: {stats.get('reason', '无追溯表')}")
        print("  保留全部簇（按原方案继续）")
        return

    print(f"  追溯链路:         {stats['chain']}")
    print(f"  追溯到的 FullName: {stats['trace_fullnames']}")
    print(f"  过滤前簇数:       {stats['clusters_before']}")
    print(f"  保留簇数:         {stats['clusters_kept']}")
    print(f"  剔除簇数:         {stats['clusters_dropped']}")
    print(f"  追溯表中未命中的 FullName 数: {stats['unmatched_trace_fullnames']}")
    if stats.get("dropped_samples"):
        print("  剔除样例:")
        for name in stats["dropped_samples"]:
            print(f"    - {name}")


def main():
    parser = argparse.ArgumentParser(description="EoICD 去重聚类")
    parser.add_argument("-i", "--input", required=True, help="输入 EoICD JSON 文件路径")
    parser.add_argument("-o", "--output", required=True, help="输出聚类后 JSON 文件路径")
    parser.add_argument("-t", "--trace-file", default=None,
                        help="追溯结果 trace_result.json；提供则只保留追溯链命中的簇，"
                             "并额外输出 *_traced.json")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    # 读取输入
    with open(input_path, "r", encoding="utf-8") as f:
        signals = json.load(f)

    print(f"读取: {input_path} ({len(signals)} 条信号)")

    # 执行去重
    deduped = dedup_signals(signals)

    # 打印摘要
    print_summary(signals, deduped)

    # 保存输出（完整版）
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

    print(f"\n已保存: {output_path}")

    # 追溯过滤（可选）
    if args.trace_file:
        trace_path = Path(args.trace_file)
        if trace_path.exists():
            filtered, stats = filter_by_trace(deduped, trace_path)
            print_trace_summary(stats)

            if stats.get("enabled"):
                traced_path = output_path.with_name(output_path.stem + "_traced.json")
                with open(traced_path, "w", encoding="utf-8") as f:
                    json.dump(filtered, f, ensure_ascii=False, indent=2)
                print(f"已保存(追溯过滤后): {traced_path}")
        else:
            print(f"\n⚠️  追溯结果文件不存在，跳过过滤: {trace_path}")


if __name__ == "__main__":
    main()
