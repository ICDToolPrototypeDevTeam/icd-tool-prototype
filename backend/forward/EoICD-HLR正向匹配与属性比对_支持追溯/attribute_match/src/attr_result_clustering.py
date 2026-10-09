"""
attr_result_clustering.py — 属性匹配结果聚类

功能：
  1. 读取属性匹配阶段输出的四类结果文件（matched / mismatch / not_mentioned / null_value）
  2. 按 EoICD 信号分组，按属性再分组
  3. 标记冲突属性：同一属性被 ≥2 个 HLR 检查过（check_count >= 2）
  4. 标记结果不一致：同一属性下同时存在 matched 和 mismatch
  5. 输出结构化聚类视图，供后续冲突处理模块消费

输入：
  - attr_matched_{direction}.json
  - attr_mismatch_{direction}.json
  - attr_not_mentioned_{direction}.json（聚合格式，需展开）
  - attr_null_value_{direction}.json

输出：
  - attr_clustered_{direction}.json

设计约束：
  - 只读输入文件，不修改任何结果数据
  - 聚类是分析视图，不影响匹配逻辑
"""

from __future__ import annotations

import json
import os
from typing import Any


def load_result_files(output_dir: str, direction: str) -> dict:
    """
    加载属性匹配阶段的四类结果文件。

    返回：{
      "matched": [...],      # 来自 attr_matched_*.json（列表）
      "mismatch": [...],     # 来自 attr_mismatch_*.json（列表）
      "null_value": [...],   # 来自 attr_null_value_*.json（列表）
      "not_mentioned": [...], # 从 attr_not_mentioned_*.json 展开的单条记录
    }
    """
    results = {
        "matched": [],
        "mismatch": [],
        "null_value": [],
        "not_mentioned": [],
    }

    # matched
    matched_path = os.path.join(output_dir, f"attr_matched_{direction}.json")
    if os.path.exists(matched_path):
        with open(matched_path, "r", encoding="utf-8") as f:
            results["matched"] = json.load(f)

    # mismatch
    mismatch_path = os.path.join(output_dir, f"attr_mismatch_{direction}.json")
    if os.path.exists(mismatch_path):
        with open(mismatch_path, "r", encoding="utf-8") as f:
            results["mismatch"] = json.load(f)

    # null_value
    null_value_path = os.path.join(output_dir, f"attr_null_value_{direction}.json")
    if os.path.exists(null_value_path):
        with open(null_value_path, "r", encoding="utf-8") as f:
            results["null_value"] = json.load(f)

    # not_mentioned（聚合格式，需要展开为单条记录）
    not_mentioned_path = os.path.join(output_dir, f"attr_not_mentioned_{direction}.json")
    if os.path.exists(not_mentioned_path):
        with open(not_mentioned_path, "r", encoding="utf-8") as f:
            nm_data = json.load(f)

        # 格式: {"meta": {...}, "not_mentioned_by_signal": [...]}
        for signal_entry in nm_data.get("not_mentioned_by_signal", []):
            cluster_id = signal_entry.get("eoicd_cluster_id", "")
            signal_name = signal_entry.get("eoicd_signal_name", "")
            signal_short_name = signal_entry.get("eoicd_signal_short_name", "")
            id_matched_hlrs = signal_entry.get("id_matched_hlrs", [])

            for attr_entry in signal_entry.get("not_mentioned_attributes", []):
                attr_path = attr_entry.get("attribute", "")
                eoicd_value = attr_entry.get("eoicd_value")

                # 为每个 missing_in_hlrs 的 req_id 展开一条记录
                for missing in attr_entry.get("missing_in_hlrs", []):
                    results["not_mentioned"].append({
                        "eoicd_cluster_id": cluster_id,
                        "eoicd_signal_name": signal_name,
                        "eoicd_signal_short_name": signal_short_name,
                        "req_id": missing.get("req_id", ""),
                        "hlr_identity": missing.get("hlr_identity", {}),
                        "attribute": attr_path,
                        "hlr_attr_name": attr_path,  # 兼容字段
                        "eoicd_value": eoicd_value,
                        "hlr_value": None,
                        "result": "not_mentioned",
                    })

    return results


def cluster_attribute_results(output_dir: str, direction: str) -> dict:
    """
    执行属性匹配结果聚类。

    参数：
      - output_dir: 属性匹配结果文件所在目录
      - direction: "pub" 或 "sub"

    返回：结构化聚类数据
    """
    # 1. 加载四类结果
    raw_results = load_result_files(output_dir, direction)

    # 2. 收集所有检查记录
    all_records = (
        raw_results["matched"]
        + raw_results["mismatch"]
        + raw_results["null_value"]
        + raw_results["not_mentioned"]
    )

    # 3. 按信号分组 → 按属性分组
    # 结构: {cluster_id: {attr_path: [record, ...]}}
    signals_data: dict[str, dict] = {}

    for record in all_records:
        cluster_id = record.get("eoicd_cluster_id", "unknown")
        attr_path = record.get("attribute", "unknown")

        if cluster_id not in signals_data:
            signals_data[cluster_id] = {
                "eoicd_cluster_id": cluster_id,
                "eoicd_signal_name": record.get("eoicd_signal_name", ""),
                "eoicd_signal_short_name": record.get("eoicd_signal_short_name", ""),
                "results_by_attribute": {},
            }

        if attr_path not in signals_data[cluster_id]["results_by_attribute"]:
            signals_data[cluster_id]["results_by_attribute"][attr_path] = []

        signals_data[cluster_id]["results_by_attribute"][attr_path].append(record)

    # 4. 逐个信号/属性做统计与冲突判定
    signals_output = []
    conflict_summary = {
        "total_conflict_attributes": 0,
        "by_attribute": {},
        "by_signal": [],
    }

    total_results = 0
    signals_with_conflict_attrs = 0

    for cluster_id in sorted(signals_data.keys()):
        signal_info = signals_data[cluster_id]
        results_by_attr = signal_info["results_by_attribute"]

        signal_entry = {
            "eoicd_cluster_id": cluster_id,
            "eoicd_signal_name": signal_info["eoicd_signal_name"],
            "eoicd_signal_short_name": signal_info["eoicd_signal_short_name"],
            "total_results": 0,
            "conflict_attribute_count": 0,
            "conflict_attributes": [],
            "summary": {
                "matched": 0,
                "mismatch": 0,
                "not_mentioned": 0,
                "null_value": 0,
            },
            "results_by_attribute": {},
            "not_mentioned_summary": {
                "total_not_mentioned_items": 0,
                "attributes": [],
            },
        }

        has_conflict_attr = False

        for attr_path in sorted(results_by_attr.keys()):
            records = results_by_attr[attr_path]
            check_count = len(records)
            total_results += check_count

            # 按结果类型计数
            matched_count = sum(1 for r in records if r.get("result") == "matched")
            mismatch_count = sum(1 for r in records if r.get("result") == "mismatch")
            not_mentioned_count = sum(1 for r in records if r.get("result") == "not_mentioned")
            null_value_count = sum(1 for r in records if r.get("result") == "null_value")

            signal_entry["summary"]["matched"] += matched_count
            signal_entry["summary"]["mismatch"] += mismatch_count
            signal_entry["summary"]["not_mentioned"] += not_mentioned_count
            signal_entry["summary"]["null_value"] += null_value_count
            signal_entry["total_results"] += check_count

            # 收集该属性下所有结果类型
            all_types = set(r.get("result", "") for r in records)

            # 冲突判定：存在多种结果类型
            is_conflict = len(all_types) > 1

            # 不一致判定：同时存在 matched 和 mismatch
            has_inconsistent = (matched_count > 0 and mismatch_count > 0)

            # 构建 results 列表（精简字段）
            results_list = []
            for r in records:
                result_item = {
                    "req_id": r.get("req_id", ""),
                    "result": r.get("result", ""),
                    "eoicd_value": r.get("eoicd_value"),
                    "hlr_value": r.get("hlr_value"),
                }
                # 保留 AI 相关标记
                if r.get("upgraded_by_ai"):
                    result_item["upgraded_by_ai"] = True
                if r.get("ai_confirmed"):
                    result_item["ai_confirmed"] = True
                if r.get("ai_reviewed") is False:
                    result_item["ai_reviewed"] = False
                results_list.append(result_item)

            attr_entry = {
                "check_count": check_count,
                "is_conflict": is_conflict,
                "has_inconsistent_results": has_inconsistent,
                "results": results_list,
            }

            signal_entry["results_by_attribute"][attr_path] = attr_entry

            if is_conflict:
                has_conflict_attr = True
                signal_entry["conflict_attribute_count"] += 1
                signal_entry["conflict_attributes"].append(attr_path)

                # 全局冲突统计
                conflict_summary["total_conflict_attributes"] += 1
                if attr_path not in conflict_summary["by_attribute"]:
                    conflict_summary["by_attribute"][attr_path] = {
                        "signal_count": 0,
                        "signal_ids": [],
                    }
                conflict_summary["by_attribute"][attr_path]["signal_count"] += 1
                conflict_summary["by_attribute"][attr_path]["signal_ids"].append(cluster_id)

            # not_mentioned 单独汇总
            if not_mentioned_count > 0:
                signal_entry["not_mentioned_summary"]["total_not_mentioned_items"] += not_mentioned_count
                signal_entry["not_mentioned_summary"]["attributes"].append(attr_path)

        # 去重 not_mentioned attributes
        signal_entry["not_mentioned_summary"]["attributes"] = list(
            dict.fromkeys(signal_entry["not_mentioned_summary"]["attributes"])
        )

        if has_conflict_attr:
            signals_with_conflict_attrs += 1
            conflict_summary["by_signal"].append({
                "eoicd_cluster_id": cluster_id,
                "eoicd_signal_name": signal_info["eoicd_signal_name"],
                "conflict_attributes": signal_entry["conflict_attributes"],
            })

        signals_output.append(signal_entry)

    # 5. 构建最终输出
    result_type_distribution = {
        "matched": sum(s["summary"]["matched"] for s in signals_output),
        "mismatch": sum(s["summary"]["mismatch"] for s in signals_output),
        "not_mentioned": sum(s["summary"]["not_mentioned"] for s in signals_output),
        "null_value": sum(s["summary"]["null_value"] for s in signals_output),
    }

    output = {
        "meta": {
            "stage": "attribute_match_clustering",
            "direction": direction,
            "total_signals": len(signals_output),
            "signals_with_conflict_attrs": signals_with_conflict_attrs,
            "total_conflict_attributes": conflict_summary["total_conflict_attributes"],
            "total_check_records": total_results,
            "result_type_distribution": result_type_distribution,
        },
        "signals": signals_output,
        "conflict_summary": conflict_summary,
    }

    return output


def run_clustering(output_dir: str, direction: str) -> str:
    """
    执行聚类并保存结果文件。

    返回：输出文件路径
    """
    clustered = cluster_attribute_results(output_dir, direction)

    output_path = os.path.join(output_dir, f"attr_clustered_{direction}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(clustered, f, ensure_ascii=False, indent=2)

    return output_path
