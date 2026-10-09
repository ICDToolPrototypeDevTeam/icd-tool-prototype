"""
attr_result_integrator.py — 属性匹配：结果整合与输出

功能拆分：
  1. save_process_results() — 保存过程性结果（四类文件）
     在属性匹配阶段（Phase 1）调用
  2. generate_final_report() — 生成最终完整报告
     在流程最后（Phase 4）调用，可注入聚类/冲突处理信息
  3. integrate_results() — 兼容入口，同时执行 1+2
     保留供旧代码调用
"""

from __future__ import annotations

import json
import os
from typing import Any


# ============================ 内部工具 ============================

def _build_final_data(script_results: dict, ai_results: list[dict] | None) -> dict:
    """
    整合 script_results + ai_results，生成统一的 final_* 数据结构。

    返回：{
      "final_matched": [...],
      "final_mismatch": [...],
      "final_not_mentioned": [...],
      "final_null_value": [...],
      "not_mentioned_aggregated": [...],
      "summary": {...},
      "attr_mismatch_stats": {...},
    }
    """
    # 1. 分离最终结果
    final_matched = list(script_results["matched_pairs"])
    final_mismatch = []
    final_not_mentioned = list(script_results["not_mentioned_pairs"])
    final_null_value = list(script_results.get("null_value_pairs", []))

    # 处理 AI 复核结果
    if ai_results:
        for item in ai_results:
            if item.get("ai_result") == "一致":
                matched_item = {
                    "eoicd_cluster_id": item["eoicd_cluster_id"],
                    "eoicd_signal_name": item.get("eoicd_signal_name", ""),
                    "eoicd_signal_short_name": item.get("eoicd_signal_short_name", ""),
                    "req_id": item["req_id"],
                    "hlr_identity": item.get("hlr_identity", {}),
                    "attribute": item["attribute"],
                    "hlr_attr_name": item["hlr_attr_name"],
                    "eoicd_value": item["eoicd_value"],
                    "hlr_value": item["hlr_value"],
                    "rule": item.get("rule", "default"),
                    "result": "matched",
                    "upgraded_by_ai": True,
                }
                final_matched.append(matched_item)
            else:
                mismatch_item = {
                    "eoicd_cluster_id": item["eoicd_cluster_id"],
                    "eoicd_signal_name": item.get("eoicd_signal_name", ""),
                    "eoicd_signal_short_name": item.get("eoicd_signal_short_name", ""),
                    "req_id": item["req_id"],
                    "hlr_identity": item.get("hlr_identity", {}),
                    "attribute": item["attribute"],
                    "hlr_attr_name": item["hlr_attr_name"],
                    "eoicd_value": item["eoicd_value"],
                    "hlr_value": item["hlr_value"],
                    "rule": item.get("rule", "default"),
                    "result": "mismatch",
                    "ai_confirmed": True,
                }
                final_mismatch.append(mismatch_item)
    else:
        for item in script_results["mismatch_pairs"]:
            final_mismatch.append({
                "eoicd_cluster_id": item["eoicd_cluster_id"],
                "eoicd_signal_name": item.get("eoicd_signal_name", ""),
                "eoicd_signal_short_name": item.get("eoicd_signal_short_name", ""),
                "req_id": item["req_id"],
                "hlr_identity": item.get("hlr_identity", {}),
                "attribute": item["attribute"],
                "hlr_attr_name": item["hlr_attr_name"],
                "eoicd_value": item["eoicd_value"],
                "hlr_value": item["hlr_value"],
                "rule": item.get("rule", "default"),
                "result": "mismatch",
                "ai_reviewed": False,
            })

    # 2. 统计
    summary = {
        "matched": len(final_matched),
        "mismatch": len(final_mismatch),
        "not_mentioned": len(final_not_mentioned),
        "null_value": len(final_null_value),
        "total": len(final_matched) + len(final_mismatch) + len(final_not_mentioned) + len(final_null_value),
    }

    # 按属性统计 mismatch
    attr_mismatch_stats: dict[str, int] = {}
    for item in final_mismatch:
        attr = item.get("attribute", "unknown")
        attr_mismatch_stats[attr] = attr_mismatch_stats.get(attr, 0) + 1

    # 3. 聚合 not_mentioned：按 EoICD 信号分组
    not_mentioned_by_signal: dict[str, dict] = {}

    for item in final_not_mentioned:
        cluster_id = item["eoicd_cluster_id"]
        attr_path = item["attribute"]
        req_id = item["req_id"]

        if cluster_id not in not_mentioned_by_signal:
            not_mentioned_by_signal[cluster_id] = {
                "eoicd_cluster_id": cluster_id,
                "eoicd_signal_name": item.get("eoicd_signal_name", ""),
                "eoicd_signal_short_name": item.get("eoicd_signal_short_name", ""),
                "not_mentioned_attributes": {},
                "id_matched_hlrs": set(),
            }

        signal_entry = not_mentioned_by_signal[cluster_id]
        signal_entry["id_matched_hlrs"].add(req_id)

        if attr_path not in signal_entry["not_mentioned_attributes"]:
            signal_entry["not_mentioned_attributes"][attr_path] = {
                "attribute": attr_path,
                "eoicd_value": item["eoicd_value"],
                "missing_in_hlrs": [],
            }

        signal_entry["not_mentioned_attributes"][attr_path]["missing_in_hlrs"].append({
            "req_id": req_id,
            "hlr_identity": item.get("hlr_identity", {}),
        })

    not_mentioned_aggregated = []
    for cluster_id in sorted(not_mentioned_by_signal.keys()):
        entry = not_mentioned_by_signal[cluster_id]
        attr_list = []
        for attr_path in sorted(entry["not_mentioned_attributes"].keys()):
            attr_entry = entry["not_mentioned_attributes"][attr_path]
            attr_list.append({
                "attribute": attr_entry["attribute"],
                "eoicd_value": attr_entry["eoicd_value"],
                "missing_in_hlrs": attr_entry["missing_in_hlrs"],
            })

        not_mentioned_aggregated.append({
            "eoicd_cluster_id": entry["eoicd_cluster_id"],
            "eoicd_signal_name": entry["eoicd_signal_name"],
            "eoicd_signal_short_name": entry["eoicd_signal_short_name"],
            "id_matched_hlrs": sorted(list(entry["id_matched_hlrs"])),
            "not_mentioned_attributes": attr_list,
            "not_mentioned_count": len(attr_list),
        })

    summary["signals_with_not_mentioned"] = len(not_mentioned_aggregated)

    return {
        "final_matched": final_matched,
        "final_mismatch": final_mismatch,
        "final_not_mentioned": final_not_mentioned,
        "final_null_value": final_null_value,
        "not_mentioned_aggregated": not_mentioned_aggregated,
        "summary": summary,
        "attr_mismatch_stats": attr_mismatch_stats,
    }


def _save_file(path: str, data: Any, indent: int = 2):
    """保存单个 JSON 文件"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    print(f"      已保存: {path}")


# ============================ Phase 1: 保存过程性结果 ============================

def save_process_results(
    script_results: dict,
    ai_results: list[dict] | None,
    direction: str,
    config: dict,
    project_root: str,
) -> dict:
    """
    保存属性匹配的过程性结果（四类文件）。

    在 Phase 1（属性匹配阶段）调用，保留四类结果供聚类分析使用。

    返回：summary 统计
    """
    output_cfg = config.get("output", {})
    paths_cfg = config.get("paths", {})
    indent = output_cfg.get("indent_json", 2)
    output_dir = paths_cfg.get("output_dir", "data/output")

    # 整合数据
    data = _build_final_data(script_results, ai_results)
    final_matched = data["final_matched"]
    final_mismatch = data["final_mismatch"]
    final_not_mentioned = data["final_not_mentioned"]
    final_null_value = data["final_null_value"]
    not_mentioned_aggregated = data["not_mentioned_aggregated"]
    summary = data["summary"]

    # 按类别分别保存（过程性结果）
    if output_cfg.get("save_per_category", True):
        _save_file(
            os.path.join(project_root, output_dir, f"attr_matched_{direction}.json"),
            final_matched, indent
        )
        _save_file(
            os.path.join(project_root, output_dir, f"attr_mismatch_{direction}.json"),
            final_mismatch, indent
        )
        _save_file(
            os.path.join(project_root, output_dir, f"attr_null_value_{direction}.json"),
            final_null_value, indent
        )
        _save_file(
            os.path.join(project_root, output_dir, f"attr_not_mentioned_{direction}.json"),
            {
                "meta": {
                    "description": "未提及属性按 EoICD 信号聚合",
                    "total_signals": len(not_mentioned_aggregated),
                    "total_not_mentioned_items": summary["not_mentioned"],
                },
                "not_mentioned_by_signal": not_mentioned_aggregated,
            },
            indent
        )

    return summary


# ============================ Phase 4: 生成最终报告 ============================

def generate_final_report(
    script_results: dict,
    ai_results: list[dict] | None,
    direction: str,
    config: dict,
    project_root: str,
    clustering_info: dict | None = None,
) -> dict:
    """
    生成最终完整报告。

    在流程最后（Phase 4）调用，此时聚类/冲突处理已完成。
    可注入聚类信息，生成包含冲突标记的最终报告。

    参数：
      - clustering_info: Phase 2 聚类结果（可选），用于在报告中附加冲突标记

    返回：summary 统计
    """
    output_cfg = config.get("output", {})
    paths_cfg = config.get("paths", {})
    indent = output_cfg.get("indent_json", 2)
    output_dir = paths_cfg.get("output_dir", "data/output")

    # 整合数据
    data = _build_final_data(script_results, ai_results)
    final_matched = data["final_matched"]
    final_mismatch = data["final_mismatch"]
    final_not_mentioned = data["final_not_mentioned"]
    final_null_value = data["final_null_value"]
    not_mentioned_aggregated = data["not_mentioned_aggregated"]
    summary = data["summary"]
    attr_mismatch_stats = data["attr_mismatch_stats"]

    # 构建最终报告
    full_report = {
        "meta": {
            "stage": "attribute_match_final",
            "direction": direction,
            "summary": summary,
            "ai_reviewed": ai_results is not None,
            "has_clustering": clustering_info is not None,
        },
        "mismatch_by_attribute": attr_mismatch_stats,
        "matched": final_matched,
        "mismatch": final_mismatch,
        "null_value": final_null_value,
        "not_mentioned": {
            "not_mentioned_by_signal": not_mentioned_aggregated,
        },
    }

    # 注入聚类信息（如有）
    if clustering_info:
        full_report["clustering"] = {
            "total_signals": clustering_info.get("meta", {}).get("total_signals", 0),
            "signals_with_conflict_attrs": clustering_info.get("meta", {}).get("signals_with_conflict_attrs", 0),
            "total_conflict_attributes": clustering_info.get("meta", {}).get("total_conflict_attributes", 0),
            "conflict_summary": clustering_info.get("conflict_summary", {}),
        }

    # 保存最终报告
    report_path = os.path.join(project_root, output_dir, f"attribute_match_report_{direction}.json")
    _save_file(report_path, full_report, indent)

    return summary


# ============================ 兼容入口 ============================

def integrate_results(
    script_results: dict,
    ai_results: list[dict] | None,
    direction: str,
    config: dict,
    project_root: str,
) -> dict:
    """
    【兼容入口】同时执行保存过程结果 + 生成最终报告。

    保留供旧代码直接调用，行为与改造前一致。
    """
    # 先保存过程性结果
    summary = save_process_results(script_results, ai_results, direction, config, project_root)
    # 再生成最终报告（无聚类信息）
    generate_final_report(script_results, ai_results, direction, config, project_root, clustering_info=None)
    return summary
