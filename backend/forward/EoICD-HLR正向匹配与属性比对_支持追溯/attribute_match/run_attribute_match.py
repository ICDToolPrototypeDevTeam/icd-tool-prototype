#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EoICD → HLR 正向匹配：属性匹配阶段 — 一键运行入口
=========================================================

功能：
  1. 读取身份匹配报告（match_report_pub.json / match_report_sub.json）
  2. 预处理：聚合 HLR 属性、单位换算（预留）
  3. 脚本预匹配：默认字符串规则 + bit 特殊规则
  4. AI 复核 mismatch 数据（可选跳过）
  5. 结果整合输出：matched / mismatch / not_mentionned / full_report

用法:
  python3 run_attribute_match.py                    # 完整运行（Pub + Sub）
  python3 run_attribute_match.py --direction pub    # 只处理 Pub 方向
  python3 run_attribute_match.py --skip-ai          # 跳过 AI 复核（纯脚本模式）
  python3 run_attribute_match.py --config attribute_match/config/attribute_match_config.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# 将 src 加入路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

# 将 shared（身份匹配阶段通用工具，含流式防停滞读取 read_stream_with_stall_guard）
# 加入路径，使 attr_mismatch_ai_matcher / attr_conflict_ai_judge 能 import utils。
SHARED_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "shared"
)
_shared_abs = os.path.abspath(SHARED_DIR)
if os.path.isdir(_shared_abs) and _shared_abs not in sys.path:
    sys.path.insert(0, _shared_abs)

# --- 复用集成项目公共包 common/：自动向上定位到含 common/ 的根目录 ---
_p = Path(__file__).resolve()
while not (_p / "common").is_dir() and _p != _p.parent:
    _p = _p.parent
if str(_p) not in sys.path:
    sys.path.insert(0, str(_p))

from common import safe_load_json  # noqa: E402

from attr_script_matcher import run_script_matching
from attr_result_integrator import save_process_results, generate_final_report

try:
    from attr_mismatch_cropper import crop_mismatch_input
    from attr_mismatch_ai_matcher import batch_ai_review_mismatch
    from attr_mismatch_result_feeder import feed_ai_results
    from attr_result_clustering import run_clustering
    AI_MODULES_AVAILABLE = True
except ImportError:
    AI_MODULES_AVAILABLE = False

# 冲突处理模块（可选）
try:
    from attr_conflict_extractor import extract_conflict_items
    from attr_conflict_ai_judge import batch_ai_judge_conflicts
    from attr_conflict_resolver import resolve_conflicts
    CONFLICT_MODULES_AVAILABLE = True
except ImportError:
    CONFLICT_MODULES_AVAILABLE = False


# ============================ 配置加载 ============================

def load_config(config_path: str) -> dict:
    """加载 JSON 配置文件（支持 // 注释）。

    统一委托 common.safe_load_json：旧实现用 line.split("//")[0]，
    会把字符串值内的 //（如 URL）一并截断，导致配置被静默损坏。
    """
    # project_root 指向 attribute_match 的父目录（项目根目录）
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(project_root, config_path)

    return safe_load_json(full_path)


# ============================ 路径工具 ============================

def resolve_path(path: str, project_root: str) -> str:
    """解析相对/绝对路径"""
    return path if os.path.isabs(path) else os.path.join(project_root, path)


def ensure_dir(path: str) -> None:
    """确保目录存在"""
    os.makedirs(os.path.dirname(path), exist_ok=True)


# ============================ 核心流程 ============================

def process_direction(direction: str, config: dict, skip_ai: bool, project_root: str) -> dict:
    """处理单个方向（pub / sub）的属性匹配，保存过程性结果，返回中间数据供最终报告使用。"""

    print(f"\n{'='*60}")
    print(f"  方向: {direction.upper()}")
    print(f"{'='*60}")

    # 1. 读取身份匹配报告
    input_path = resolve_path(config["paths"][f"input_{direction}"], project_root)
    print(f"\n[1/4] 读取身份匹配报告: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        match_report = json.load(f)

    total_clusters = match_report["meta"]["total_clusters"]
    print(f"      共 {total_clusters} 个信号簇待属性匹配")

    # 2. 脚本预匹配
    special_rules = config.get("special_rules", {})
    special_desc = "、".join(special_rules.keys()) if special_rules else "无"
    print(f"\n[2/4] 脚本预匹配（默认规则: 字符串不区分大小写 | 特殊规则: {special_desc}）")
    script_results = run_script_matching(match_report, config)

    stats = script_results["stats"]
    print(f"      matched:      {stats['matched']:4d}")
    print(f"      not_mentioned: {stats['not_mentioned']:4d}")
    print(f"      null_value:   {stats.get('null_value', 0):4d}")
    print(f"      mismatch:     {stats['mismatch']:4d}")
    print(f"      总计检查项:   {stats['total_checks']:4d}")

    # 3. AI 复核 mismatch（如果启用）
    ai_results = None
    if not skip_ai and stats["mismatch"] > 0:
        if not AI_MODULES_AVAILABLE:
            print(f"\n[!] AI 模块不可用，跳过 AI 复核")
        elif not config.get("ai_review", {}).get("enabled", True):
            print(f"\n[!] AI 复核已禁用（config.ai_review.enabled=false）")
        else:
            print(f"\n[3/4] AI 复核 mismatch 数据（共 {stats['mismatch']} 项）")

            # 3.1 裁剪 mismatch 输入
            mismatch_pairs = script_results["mismatch_pairs"]
            cropped = crop_mismatch_input(mismatch_pairs, direction)

            # 保存中间文件
            if config.get("output", {}).get("save_intermediate", True):
                crop_path = resolve_path(
                    f"{config['paths']['intermediate_dir']}/cropped_attr_mismatch_input_{direction}.json",
                    project_root
                )
                ensure_dir(crop_path)
                with open(crop_path, "w", encoding="utf-8") as f:
                    json.dump(cropped, f, ensure_ascii=False, indent=2)
                print(f"      已保存裁剪输入: {crop_path}")

            # 3.2 分批 AI 复核
            ai_cfg = config.get("ai_review", {})
            ai_raw_results = batch_ai_review_mismatch(cropped, ai_cfg)

            # 保存 AI 结果
            if config.get("output", {}).get("save_intermediate", True):
                ai_path = resolve_path(
                    f"{config['paths']['intermediate_dir']}/ai_attr_results_{direction}.json",
                    project_root
                )
                ensure_dir(ai_path)
                with open(ai_path, "w", encoding="utf-8") as f:
                    json.dump(ai_raw_results, f, ensure_ascii=False, indent=2)
                print(f"      已保存 AI 结果: {ai_path}")

            # 3.3 结果反哺
            ai_results = feed_ai_results(mismatch_pairs, ai_raw_results)

            ai_matched = sum(1 for r in ai_results if r.get("ai_result") == "一致")
            ai_mismatch = sum(1 for r in ai_results if r.get("ai_result") == "不一致")
            print(f"      AI 复核: {ai_matched} 项升级到 matched, {ai_mismatch} 项确认 mismatch")
    else:
        if skip_ai:
            print(f"\n[3/4] 跳过 AI 复核（--skip-ai）")
        elif stats["mismatch"] == 0:
            print(f"\n[3/4] 无 mismatch 数据，跳过 AI 复核")

    # 4. 保存过程性结果（四类文件）
    print(f"\n[4/4] 保存过程性结果")
    summary = save_process_results(script_results, ai_results, direction, config, project_root)

    print(f"\n      过程性结果:")
    print(f"      - matched:       {summary['matched']}")
    print(f"      - mismatch:      {summary['mismatch']}")
    print(f"      - null_value:    {summary.get('null_value', 0)}")
    print(f"      - not_mentioned: {summary['not_mentioned']}")

    # 返回中间数据，供 Phase 4 最终报告使用
    return {
        "summary": summary,
        "script_results": script_results,
        "ai_results": ai_results,
    }


def _run_attribute_match(args, config, project_root):
    """运行属性匹配阶段（Phase 1→2→3）"""
    results = {}
    directions = [args.direction] if args.direction else ["pub", "sub"]

    for direction in directions:
        try:
            results[direction] = process_direction(direction, config, args.skip_ai, project_root)
        except Exception as e:
            print(f"\n[错误] {direction.upper()} 方向处理失败: {e}")
            import traceback
            traceback.print_exc()
            results[direction] = {"error": str(e)}

    # ========== Phase 2: 结果聚类 ==========
    enable_clustering = True
    clustering_data = {}

    if enable_clustering:
        print(f"\n{'='*60}")
        print("  Phase 2: 属性匹配结果聚类")
        print(f"{'='*60}")

        for direction in directions:
            if direction not in results or results[direction].get("error"):
                continue

            try:
                output_dir = resolve_path(config["paths"]["output_dir"], project_root)
                cluster_path = run_clustering(output_dir, direction)

                with open(cluster_path, "r", encoding="utf-8") as f:
                    clustered = json.load(f)
                clustering_data[direction] = clustered

                meta = clustered["meta"]
                print(f"\n  [{direction.upper()}]")
                print(f"    总信号数: {meta['total_signals']}")
                print(f"    含冲突属性信号数: {meta['signals_with_conflict_attrs']}")
                print(f"    冲突属性总数: {meta['total_conflict_attributes']}")
                print(f"    聚类文件: {cluster_path}")

            except Exception as e:
                print(f"\n[错误] {direction.upper()} 方向聚类失败: {e}")
                import traceback
                traceback.print_exc()

    # ========== Phase 3: 最终整合输出 ==========
    print(f"\n{'='*60}")
    print("  Phase 3: 最终整合输出")
    print(f"{'='*60}")

    for direction in directions:
        if direction not in results or results[direction].get("error"):
            continue

        try:
            direction_data = results[direction]
            summary = generate_final_report(
                direction_data["script_results"],
                direction_data["ai_results"],
                direction,
                config,
                project_root,
                clustering_info=clustering_data.get(direction),
            )

            print(f"\n  [{direction.upper()}] 最终报告:")
            print(f"    - matched:       {summary['matched']}")
            print(f"    - mismatch:      {summary['mismatch']}")
            print(f"    - null_value:    {summary.get('null_value', 0)}")
            print(f"    - not_mentioned: {summary['not_mentioned']}")

        except Exception as e:
            print(f"\n[错误] {direction.upper()} 方向最终报告生成失败: {e}")
            import traceback
            traceback.print_exc()

    return results


def _run_conflict_resolution(args, config, project_root):
    """运行冲突处理阶段（Step 1→2→3）"""
    if not CONFLICT_MODULES_AVAILABLE:
        print("\n[!] 冲突处理模块不可用，跳过")
        return

    print(f"\n{'='*60}")
    print("  冲突处理阶段")
    print(f"{'='*60}")

    directions = [args.direction] if args.direction else ["pub", "sub"]
    output_dir = resolve_path(config["paths"]["output_dir"], project_root)
    hlr_clustered_path = resolve_path(config["paths"]["input_hlr"], project_root)

    for direction in directions:
        print(f"\n  [{direction.upper()}]")

        clustered_path = os.path.join(output_dir, f"attr_clustered_{direction}.json")
        if not os.path.exists(clustered_path):
            print(f"    [!] 聚类结果不存在: {clustered_path}，跳过")
            continue

        with open(clustered_path, "r", encoding="utf-8") as f:
            clustered = json.load(f)
        
        if clustered["meta"]["total_conflict_attributes"] == 0:
            print(f"    ✓ 无冲突属性，跳过冲突处理")
            continue

        # Step 1: 提取冲突项
        print(f"    [Step 1/3] 提取冲突项...")
        conflict_items_path = os.path.join(output_dir, "conflict", f"conflict_items_{direction}.json")
        matched_path = os.path.join(output_dir, f"attr_matched_{direction}.json")
        mismatch_path = os.path.join(output_dir, f"attr_mismatch_{direction}.json")
        null_value_path = os.path.join(output_dir, f"attr_null_value_{direction}.json")
        
        count = extract_conflict_items(
            clustered_path=clustered_path,
            matched_path=matched_path,
            mismatch_path=mismatch_path,
            null_value_path=null_value_path,
            hlr_clustered_path=hlr_clustered_path,
            output_path=conflict_items_path
        )
        print(f"    ✓ 提取冲突项: {count} 个")

        # Step 2: AI 裁决
        print(f"    [Step 2/3] AI 裁决冲突项...")
        ai_raw_dir = os.path.join(output_dir, "conflict")
        ai_cfg = config.get("ai_review", {})
        resolutions = batch_ai_judge_conflicts(
            input_path=conflict_items_path,
            output_dir=ai_raw_dir,
            direction=direction,
            ai_cfg=ai_cfg
        )
        keep_count = sum(1 for r in resolutions if r.get("resolution") == "keep")
        manual_count = sum(1 for r in resolutions if r.get("resolution") == "manual_review")
        print(f"    ✓ AI 裁决: {keep_count} 个保留, {manual_count} 个人工审核")

        # Step 3: 解析并应用
        print(f"    [Step 3/3] 应用裁决结果...")
        result = resolve_conflicts(
            ai_raw_dir=ai_raw_dir,
            conflict_items_path=conflict_items_path,
            attr_matched_path=matched_path,
            attr_mismatch_path=mismatch_path,
            output_dir=output_dir,
            direction=direction,
            save_judgement=True
        )
        print(f"    ✓ 冲突处理完成:")
        print(f"      - resolved_matched: {result['resolved_matched_path']}")
        print(f"      - resolved_mismatch: {result['resolved_mismatch_path']}")
        if result['manual_review_count'] > 0:
            print(f"      - manual_review ({result['manual_review_count']} 项): {result['manual_review_path']}")


# ============================ 主入口 ============================

def main():
    parser = argparse.ArgumentParser(description="EoICD → HLR 属性匹配阶段")
    parser.add_argument("--config", "-c", default="attribute_match/config/attribute_match_config.json",
                        help="配置文件路径（相对项目根目录）")
    parser.add_argument("--step", "-s", choices=["all", "attribute_match", "conflict_resolution"],
                        default="all",
                        help="执行步骤: all=完整运行, attribute_match=仅属性匹配, conflict_resolution=仅冲突处理")
    parser.add_argument("--direction", "-d", choices=["pub", "sub"],
                        help="只处理指定方向（pub 或 sub）")
    parser.add_argument("--skip-ai", action="store_true",
                        help="跳过 AI 复核（纯脚本模式）")
    args = parser.parse_args()

    # project_root 指向 attribute_match 的父目录（项目根目录）
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 加载配置
    print(f"加载配置: {args.config}")
    config = load_config(args.config)
    print(f"  AI 复核: {'启用' if config.get('ai_review', {}).get('enabled', True) else '禁用'}")
    print(f"  单位换算: {'启用' if config.get('unit_conversion', {}).get('enabled', False) else '禁用（预留）'}")

    t0 = time.time()

    # 根据 step 执行不同逻辑
    if args.step in ("all", "attribute_match"):
        _run_attribute_match(args, config, project_root)

    if args.step in ("all", "conflict_resolution"):
        _run_conflict_resolution(args, config, project_root)

    # ========== 汇总 ==========
    print(f"\n{'='*60}")
    print(f"  属性匹配阶段完成")
    print(f"  总耗时: {time.time() - t0:.1f}s")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
