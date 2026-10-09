#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EoICD → HLR 正向匹配：身份匹配阶段 — 一键运行入口
=========================================================
一步执行：Bus+Label+Bit 匹配 → Name 裁剪 → AI Name 匹配 → 结果反哺 → 生成报告

用法:
  python3 run_identity_match.py                    # 完整运行（Pub + Sub）
  python3 run_identity_match.py --step bus_label_bit   # 只执行 Bus+Label+Bit 匹配
  python3 run_identity_match.py --step name        # 只执行 Name 相关流程
  python3 run_identity_match.py --direction pub    # 只处理 Pub 方向
  python3 run_identity_match.py --skip-ai          # 跳过 AI 调用（测试脚本逻辑）
  python3 run_identity_match.py --config config/my_config.json  # 指定配置文件
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 确保 src/ 和 shared/ 目录在 Python 路径中
# PROJECT_ROOT = 项目根目录（identity_match 的父目录）
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
SRC_DIR = PROJECT_ROOT / "identity_match" / "src"
SHARED_DIR = PROJECT_ROOT / "shared"
for d in [SRC_DIR, SHARED_DIR]:
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from utils import load_config, load_json, save_json, flatten_hlr_requirements, load_hlr_dedup
from bus_label_matcher import run_bus_label_matching
from direction_matcher import run_direction_matching
from name_cropper import crop_name_input
from name_ai_matcher import batch_ai_match
from name_pair_deduper import select_representatives, radiate_results
from name_result_feeder import feed_ai_results
from bit_matcher import generate_match_report


# ============================ 配置默认值 ============================

DEFAULT_CONFIG = PROJECT_ROOT / "identity_match" / "config" / "match_config.json"


# ============================ 工具函数 ============================

STEP_NAMES = {
    "bus_label_bit": "Bus + Label + Bit 脚本匹配",
    "direction": "Direction 方向匹配",
    "name":      "Name 匹配全流程（裁剪 → AI → 反哺）",
    "report":    "生成身份匹配报告",
}


def print_step_header(step_key: str, direction: str, extra: str = ""):
    """打印步骤分隔线。"""
    name = STEP_NAMES.get(step_key, step_key)
    print(f"\n{'='*60}")
    print(f"▶ {name} [{direction.upper()}]{f' — {extra}' if extra else ''}")
    print(f"{'='*60}")


def print_step_result(success: bool, elapsed: float, detail: str = ""):
    """打印步骤结果。"""
    status = "✅ 成功" if success else "❌ 失败"
    print(f"\n{status} | 耗时: {elapsed:.3f}s{f' | {detail}' if detail else ''}")


def resolve_eoicd_input_path(config_path: str, project_root: Path) -> str:
    """
    解析 EoICD 输入路径：若存在 _traced 版本则优先使用，否则回退到配置路径。
    例如：
      config_path = "data/input/eoicd_pub_clustered.json"
      优先检查 "data/input/eoicd_pub_clustered_traced.json"
    """
    if config_path.endswith(".json"):
        traced_path = config_path[:-5] + "_traced.json"
    else:
        traced_path = config_path + "_traced"

    traced_full = project_root / traced_path
    if traced_full.exists():
        return traced_path
    return config_path


def safe_save_json(data, rel_path: str, project_root: Path, indent: int = 2):
    """安全保存 JSON，自动创建父目录。"""
    full_path = project_root / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    return full_path


# ============================ 各步骤实现 ============================

def step_bus_label_bit(eoicd_clusters, hlr_units, config, direction: str, project_root: Path):
    """
    Step 1: Bus + Label + Bit 脚本匹配
    """
    print_step_header("bus_label_bit", direction)
    t0 = time.time()

    retained_bus_label_bit, rejected_bus_label_bit = run_bus_label_matching(
        eoicd_clusters, hlr_units, config
    )
    elapsed = time.time() - t0

    candidate_pairs = sum(len(item["candidate_interfaces"]) for item in retained_bus_label_bit)
    detail = f"保留 {len(retained_bus_label_bit)} 簇 ({candidate_pairs} 对), 拒绝 {len(rejected_bus_label_bit)} 对"
    print(f"  {detail}")
    print_step_result(True, elapsed, detail)

    # 保存
    output_cfg = config["output"]
    indent = output_cfg.get("indent_json", 2)
    paths = config["paths"]

    if output_cfg.get("save_rejected", True):
        safe_save_json(
            {
                "meta": {"direction": direction, "stage": "bus_label_bit", "total_rejected": len(rejected_bus_label_bit)},
                "rejections": rejected_bus_label_bit,
            },
            os.path.join(paths["rejected_dir"], f"rejected_bus_label_bit_{direction}.json"),
            project_root, indent,
        )

    safe_save_json(
        retained_bus_label_bit,
        os.path.join(paths["output_dir"], f"retained_bus_label_bit_{direction}.json"),
        project_root, indent,
    )

    return retained_bus_label_bit, rejected_bus_label_bit, elapsed


def step_direction(retained_bus_label_bit, config, direction: str, project_root: Path):
    """
    Step 2: Direction 方向匹配
    """
    print_step_header("direction", direction)
    t0 = time.time()

    retained_direction, rejected_direction = run_direction_matching(
        retained_bus_label_bit, config
    )
    elapsed = time.time() - t0

    candidate_pairs = sum(len(item["candidate_interfaces"]) for item in retained_direction)
    rejected_count = len(rejected_direction)
    detail = f"保留 {len(retained_direction)} 簇 ({candidate_pairs} 对), 方向拒绝 {rejected_count} 对"
    print(f"  {detail}")
    print_step_result(True, elapsed, detail)

    # 保存
    output_cfg = config["output"]
    indent = output_cfg.get("indent_json", 2)
    paths = config["paths"]

    if output_cfg.get("save_rejected", True):
        safe_save_json(
            {
                "meta": {"direction": direction, "stage": "direction", "total_rejected": rejected_count},
                "rejections": rejected_direction,
            },
            os.path.join(paths["rejected_dir"], f"rejected_direction_{direction}.json"),
            project_root, indent,
        )

    safe_save_json(
        retained_direction,
        os.path.join(paths["output_dir"], f"retained_direction_{direction}.json"),
        project_root, indent,
    )

    return retained_direction, rejected_direction, elapsed


def step_name_pipeline(retained_direction, config, direction: str, project_root: Path, skip_ai: bool = False):
    """
    Step 3~5: Name 裁剪 → AI 匹配 → 结果反哺（合并为一个逻辑步骤）
    """
    print_step_header("name", direction, "跳过 AI" if skip_ai else "")
    t0_total = time.time()

    # ---- Step 2: 裁剪 ----
    t0 = time.time()
    cropped_pairs, skip_ai_pairs = crop_name_input(retained_direction)
    t_crop = time.time() - t0

    print(f"\n  [3.1] Name 信息裁剪")
    print(f"        {len(cropped_pairs)} 对进入 AI, {len(skip_ai_pairs)} 对 name=null 直接保留")
    print(f"        耗时: {t_crop:.3f}s")

    output_cfg = config["output"]
    indent = output_cfg.get("indent_json", 2)
    paths = config["paths"]

    # ---- Step 2.5: 含中文全 null unit 的 LLM 检索前置（防爆炸，以准确性为前提）----
    retrieval_rejected = []
    if not skip_ai and config["ai_matching"]["enabled"]:
        from identity_retrieval import retrieve_for_cn_fullnull
        t0_ret = time.time()
        cropped_pairs, retrieval_rejected = retrieve_for_cn_fullnull(cropped_pairs, config)
        print(f"\n  [3.1a] 含中文全 null 检索前置: 缩减后 {len(cropped_pairs)} 对, "
              f"检索丢弃 {len(retrieval_rejected)} 对 (耗时 {time.time()-t0_ret:.1f}s)")
        if output_cfg.get("save_rejected", True):
            safe_save_json(
                {
                    "meta": {"direction": direction, "stage": "null_identity_retrieval",
                             "total_rejected": len(retrieval_rejected)},
                    "rejections": retrieval_rejected,
                },
                os.path.join(paths["rejected_dir"], f"rejected_null_identity_retrieval_{direction}.json"),
                project_root, indent,
            )

    if output_cfg.get("save_intermediate", True):
        safe_save_json(
            {
                "meta": {"direction": direction, "total_pairs": len(cropped_pairs), "skip_ai": len(skip_ai_pairs)},
                "pairs": cropped_pairs,
            },
            os.path.join(paths["intermediate_dir"], f"cropped_name_input_{direction}.json"),
            project_root, indent,
        )

    # ---- 同名对去重（结构性去重，AI 输入完全相同的对只送 1 个代表）----
    rep_pairs, radiation_map = select_representatives(cropped_pairs)
    removed = len(cropped_pairs) - len(rep_pairs)
    if removed > 0:
        print(f"\n  [3.1b] 同名对去重辐射: {len(cropped_pairs)} 对 → {len(rep_pairs)} 代表"
              f"（去重 {removed} 对，结果判定后辐射回填）")

    # ---- Step 3: AI Name 匹配 ----
    ai_results = []
    t_ai = 0.0

    if skip_ai or not config["ai_matching"]["enabled"]:
        print(f"\n  [3.2] AI Name 匹配: 已跳过")
    elif not cropped_pairs:
        print(f"\n  [3.2] AI Name 匹配: 无待处理数据")
    else:
        print(f"\n  [3.2] AI Name 匹配")
        t0 = time.time()
        ai_results = batch_ai_match(rep_pairs, config)
        # 代表结果辐射回填给全组成员，恢复为完整结果列表（feeder 零感知）
        ai_results = radiate_results(ai_results, radiation_map)
        t_ai = time.time() - t0

        matched = sum(1 for r in ai_results if r["result"] == "匹配")
        unmatched = sum(1 for r in ai_results if r["result"] == "不匹配")
        print(f"        匹配: {matched} 对, 不匹配: {unmatched} 对, 耗时: {t_ai:.1f}s")

        if output_cfg.get("save_intermediate", True):
            safe_save_json(
                {
                    "meta": {"direction": direction, "total": len(ai_results), "matched": matched, "unmatched": unmatched},
                    "results": ai_results,
                },
                os.path.join(paths["intermediate_dir"], f"ai_name_results_{direction}.json"),
                project_root, indent,
            )

    # ---- Step 4: 结果反哺 ----
    print(f"\n  [3.3] 结果反哺")
    t0 = time.time()
    final_retained, rejected_name = feed_ai_results(retained_direction, ai_results, skip_ai_pairs)
    t_feed = time.time() - t0

    final_pairs = sum(len(item["candidate_interfaces"]) for item in final_retained)
    print(f"        最终保留 {len(final_retained)} 簇 ({final_pairs} 对), name 拒绝 {len(rejected_name)} 对")
    print(f"        耗时: {t_feed:.3f}s")

    if output_cfg.get("save_rejected", True):
        safe_save_json(
            {
                "meta": {"direction": direction, "stage": "name", "total_rejected": len(rejected_name)},
                "rejections": rejected_name,
            },
            os.path.join(paths["rejected_dir"], f"rejected_name_{direction}.json"),
            project_root, indent,
        )

    safe_save_json(
        final_retained,
        os.path.join(paths["output_dir"], f"retained_name_{direction}.json"),
        project_root, indent,
    )

    elapsed_total = time.time() - t0_total
    print_step_result(True, elapsed_total)

    return final_retained, rejected_name, t_crop, t_ai, t_feed


def step_report(final_retained, hlr_clustered, config, direction: str, project_root: Path):
    """
    Step 5: 生成身份匹配报告
    """
    print_step_header("report", direction)
    t0 = time.time()

    match_report = generate_match_report(final_retained, hlr_clustered, config)
    elapsed = time.time() - t0

    detail = f"完成 {len(match_report['matches'])} 个簇的身份匹配"
    print(f"  {detail}")
    print_step_result(True, elapsed, detail)

    indent = config["output"].get("indent_json", 2)
    paths = config["paths"]
    safe_save_json(
        match_report,
        os.path.join(paths["output_dir"], f"match_report_{direction}.json"),
        project_root, indent,
    )

    return match_report, elapsed


def process_direction(
    eoicd_clusters,
    hlr_units,
    hlr_clustered,
    config,
    direction: str,
    project_root: Path,
    step: str = "all",
    skip_ai: bool = False,
):
    """
    处理一个方向（pub 或 sub）的完整身份匹配流程。
    支持分步骤执行。
    """
    timings = {}
    retained_bus_label_bit = None
    retained_direction = None
    final_retained = None
    match_report = None

    # Step 1: Bus + Label + Bit
    if step in ("all", "bus_label_bit"):
        retained_bus_label_bit, _, t = step_bus_label_bit(
            eoicd_clusters, hlr_units, config, direction, project_root
        )
        timings["bus_label_bit"] = t
    else:
        # 加载之前的结果
        path = config["paths"]["output_dir"] + f"/retained_bus_label_bit_{direction}.json"
        retained_bus_label_bit = load_json(path, project_root)
        print(f"  [加载] 已加载之前保留的 bus_label_bit 结果: {len(retained_bus_label_bit)} 簇")

    # Step 2: Direction 方向匹配
    if step in ("all", "direction"):
        retained_direction, _, t = step_direction(
            retained_bus_label_bit, config, direction, project_root
        )
        timings["direction"] = t
    else:
        # 优先加载 direction 结果，不存在则回退到 bus_label_bit（兼容旧数据）
        path = config["paths"]["output_dir"] + f"/retained_direction_{direction}.json"
        if os.path.exists(path):
            retained_direction = load_json(path, project_root)
            print(f"  [加载] 已加载之前保留的 direction 结果: {len(retained_direction)} 簇")
        else:
            retained_direction = retained_bus_label_bit
            print(f"  [加载] direction 结果不存在，使用 bus_label_bit 结果回退: {len(retained_direction)} 簇")

    # Step 3~5: Name 全流程
    if step in ("all", "name"):
        final_retained, _, t_crop, t_ai, t_feed = step_name_pipeline(
            retained_direction, config, direction, project_root, skip_ai
        )
        timings["name_crop"] = t_crop
        timings["ai_match"] = t_ai
        timings["name_feed"] = t_feed
    else:
        path = config["paths"]["output_dir"] + f"/retained_name_{direction}.json"
        final_retained = load_json(path, project_root)
        print(f"  [加载] 已加载之前保留的 name 结果: {len(final_retained)} 簇")

    # Step 6: 生成报告
    if step in ("all", "report"):
        match_report, t = step_report(final_retained, hlr_clustered, config, direction, project_root)
        timings["report"] = t

    return {
        "timings": timings,
        "retained_bus_label_bit": retained_bus_label_bit,
        "retained_direction": retained_direction,
        "final_retained": final_retained,
        "match_report": match_report,
    }


# ============================ 汇总输出 ============================

def print_summary(result_pub: dict, result_sub: dict, total_time: float, project_root: Path):
    """打印最终汇总报告。"""
    print(f"\n{'='*60}")
    print("📊 身份匹配阶段完成摘要")
    print(f"{'='*60}")

    def _get_counts(direction: str, result: dict):
        rbl = result.get("retained_bus_label_bit", [])
        fr = result.get("final_retained", [])
        mr = result.get("match_report")

        total_clusters = len(rbl)
        candidate_pairs = sum(len(item["candidate_interfaces"]) for item in rbl) if rbl else 0
        final_pairs = sum(len(item["candidate_interfaces"]) for item in fr) if fr else 0

        return {
            "total_clusters": total_clusters,
            "candidate_pairs": candidate_pairs,
            "final_pairs": final_pairs,
        }

    for direction, result, label in [("pub", result_pub, "PUB"), ("sub", result_sub, "SUB")]:
        if result is None:
            continue
        c = _get_counts(direction, result)
        t = result["timings"]

        print(f"\n【{label}】")
        print(f"  总簇数: {c['total_clusters']} → 最终保留: {c['final_pairs']} 对")
        print(f"  Bus+Label+Bit 候选: {c['candidate_pairs']} 对")
        if t:
            parts = [f"{k}={v:.2f}s" for k, v in sorted(t.items())]
            print(f"  耗时: {' | '.join(parts)}")

    print(f"\n⏱️ 总耗时: {total_time:.2f}s")

    # 输出文件列表
    output_dir = project_root / "data" / "output"
    if output_dir.exists():
        print(f"\n📁 输出文件:")
        for f in sorted(output_dir.rglob("*.json")):
            rel = f.relative_to(project_root)
            size_kb = f.stat().st_size / 1024
            print(f"  {str(rel):55s} {size_kb:7.1f} KB")

    print(f"\n✅ 身份匹配阶段全部完成！")
    print(f"{'='*60}")


# ============================ 主入口 ============================

def main():
    parser = argparse.ArgumentParser(
        description="EoICD → HLR 正向匹配：身份匹配阶段 — 一键运行入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 run_identity_match.py                          # 完整运行
  python3 run_identity_match.py --step bus_label_bit         # 只执行 Bus+Label+Bit 匹配
  python3 run_identity_match.py --step direction             # 只执行 Direction 方向匹配
  python3 run_identity_match.py --step name                  # 只执行 Name 匹配全流程
  python3 run_identity_match.py --step report                # 只生成身份匹配报告（需先完成 name 步骤）
  python3 run_identity_match.py --skip-ai                # 跳过 AI 调用（测试用）
        """,
    )
    parser.add_argument(
        "--step",
        choices=["all", "bus_label_bit", "direction", "name", "report"],
        default="all",
        help="执行单个步骤或全部 (默认: all)",
    )
    parser.add_argument(
        "--direction",
        choices=["both", "pub", "sub"],
        default="both",
        help="处理方向 (默认: both)",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"配置文件路径 (默认: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--skip-ai",
        action="store_true",
        help="跳过 AI Name 匹配（仅测试脚本逻辑时使用）",
    )
    args = parser.parse_args()

    total_start = time.time()

    # 打印启动信息
    print("=" * 60)
    print("🚀 EoICD → HLR 正向匹配：身份匹配阶段")
    print("=" * 60)
    print(f"   配置文件: {args.config}")
    print(f"   执行步骤: {args.step}")
    print(f"   处理方向: {args.direction}")
    if args.skip_ai:
        print(f"   ⚠️ 跳过 AI 调用（测试模式）")

    # 加载配置
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path

    if not config_path.exists():
        print(f"\n❌ 配置文件不存在: {config_path}")
        sys.exit(1)

    # 统一使用公共包 common.safe_load_json 加载配置（支持 // 注释且字符串安全）
    import sys as _sys
    from pathlib import Path as _Path

    _p = _Path(__file__).resolve()
    while not (_p / "common").is_dir() and _p != _p.parent:
        _p = _p.parent
    if str(_p) not in _sys.path:
        _sys.path.insert(0, str(_p))
    from common import safe_load_json

    config = safe_load_json(config_path)

    # 检查 API Key（如果启用 AI）
    ai_enabled = config["ai_matching"]["enabled"]
    ai_cfg = config["ai_matching"]

    # 优先从配置文件读取 api_key，其次按优先级从环境变量读取
    api_key = ai_cfg.get("api_key", "")
    if not api_key:
        # 环境变量优先级：配置指定的 api_key_env > API_KEY > DEEPSEEK_API_KEY > HLR_AI_API_KEY > OPENAI_API_KEY
        env_names = [ai_cfg.get("api_key_env", "API_KEY")] + ["API_KEY", "DEEPSEEK_API_KEY", "HLR_AI_API_KEY", "OPENAI_API_KEY"]
        seen = set()
        for name in env_names:
            if name and name not in seen:
                seen.add(name)
                api_key = os.environ.get(name, "")
                if api_key:
                    break

    if ai_enabled and not args.skip_ai and not api_key:
        api_key_env = ai_cfg.get("api_key_env", "API_KEY")
        print(f"\n{'!'*60}")
        print(f"⚠️  API Key 未设置")
        print(f"{'!'*60}")
        print(f"\n当前配置启用了 AI 匹配，但配置文件中未找到 api_key:")
        print(f"\n解决方案:")
        print(f"  在配置文件 match_config.json 的 ai_matching 里添加:")
        print(f'       "api_key": "sk-xxx"')
        print(f"\n  或者设置环境变量:")
        print(f"       Windows PowerShell:  $env:{api_key_env}=\"sk-xxx\"")
        print(f"       Windows CMD:         set {api_key_env}=sk-xxx")
        print(f"\n  也可以跳过 AI 调用（纯脚本测试）:")
        print(f"       python3 run_identity_match.py --skip-ai")
        print(f"\n{'!'*60}")
        sys.exit(1)

    # 确保输出目录存在
    for d in ["output_dir", "intermediate_dir", "rejected_dir"]:
        (PROJECT_ROOT / config["paths"][d]).mkdir(parents=True, exist_ok=True)

    # 加载数据
    print(f"\n[加载数据]")
    t0 = time.time()

    paths = config["paths"]

    # Publisher 侧：仅当处理方向包含 pub 且输入文件存在时才加载；
    # 仅 Subscriber 侧的项目无 publisher 输入，跳过 pub 方向（不崩，镜像 sub 处理）。
    eoicd_pub = None
    eoicd_pub_path = "(未指定)"
    if args.direction in ("both", "pub"):
        pub_rel = paths.get("eoicd_pub", "")
        if pub_rel:
            eoicd_pub_path = resolve_eoicd_input_path(pub_rel, PROJECT_ROOT)
            if (PROJECT_ROOT / eoicd_pub_path).exists():
                eoicd_pub = load_json(eoicd_pub_path, PROJECT_ROOT)
            else:
                print(f"\n⚠️  Publisher 输入不存在，跳过 pub 方向: {eoicd_pub_path}")
                eoicd_pub = []
        else:
            print(f"\n⚠️  配置未指定 eoicd_pub（仅 Subscriber 侧项目），跳过 pub 方向")
            eoicd_pub = []

    # Subscriber 侧：仅当处理方向包含 sub 且输入文件存在时才加载；
    # 仅 Publisher 侧的项目（如 fgmc）无 subscriber 输入，跳过 sub 方向（不崩）。
    eoicd_sub = None
    eoicd_sub_path = "(未指定)"
    if args.direction in ("both", "sub"):
        sub_rel = paths.get("eoicd_sub", "")
        if sub_rel:
            eoicd_sub_path = resolve_eoicd_input_path(sub_rel, PROJECT_ROOT)
            if (PROJECT_ROOT / eoicd_sub_path).exists():
                eoicd_sub = load_json(eoicd_sub_path, PROJECT_ROOT)
            else:
                print(f"\n⚠️  Subscriber 输入不存在，跳过 sub 方向: {eoicd_sub_path}")
                eoicd_sub = []
        else:
            print(f"\n⚠️  配置未指定 eoicd_sub（仅 Publisher 侧项目），跳过 sub 方向")
            eoicd_sub = []

    # HLR 数据加载策略：优先使用去重结果，否则回退到聚类结果
    hlr_dedup_path = paths.get("hlr_dedup")
    hlr_clustered_path = paths.get("hlr")

    if hlr_dedup_path:
        # 新版：使用去重结果（interfaces 展开）
        hlr_units = load_hlr_dedup(hlr_dedup_path, PROJECT_ROOT)
        hlr_clustered = load_json(hlr_clustered_path, PROJECT_ROOT)
        print(f"  使用 HLR 去重结果: {len(hlr_units)} 个匹配单元")
    else:
        # 旧版兼容：使用聚类结果扁平化
        hlr_data = load_json(hlr_clustered_path, PROJECT_ROOT)
        hlr_units = flatten_hlr_requirements(hlr_data)
        hlr_clustered = hlr_data
        print(f"  使用 HLR 聚类结果: {len(hlr_units)} 条需求")

    t_load = time.time() - t0
    if eoicd_pub is not None:
        print(f"  EoICD Pub: {len(eoicd_pub)} clusters (路径: {eoicd_pub_path})")
    if eoicd_sub is not None:
        print(f"  EoICD Sub: {len(eoicd_sub)} clusters (路径: {eoicd_sub_path})")
    print(f"  加载耗时: {t_load:.2f}s")

    # 处理 Pub
    result_pub = None
    if args.direction in ("both", "pub"):
        result_pub = process_direction(
            eoicd_pub, hlr_units, hlr_clustered, config, "pub", PROJECT_ROOT,
            step=args.step, skip_ai=args.skip_ai,
        )

    # 处理 Sub
    result_sub = None
    if args.direction in ("both", "sub"):
        result_sub = process_direction(
            eoicd_sub, hlr_units, hlr_clustered, config, "sub", PROJECT_ROOT,
            step=args.step, skip_ai=args.skip_ai,
        )

    # 汇总
    total_time = time.time() - total_start
    print_summary(result_pub, result_sub, total_time, PROJECT_ROOT)

    # 保存汇总 JSON（合并已有数据，支持分步运行累积）
    summary_path = os.path.join(paths["output_dir"], "summary_identity_match.json")
    existing_summary = {}
    if os.path.exists(summary_path):
        try:
            existing_summary = load_json(summary_path, PROJECT_ROOT)
        except Exception:
            existing_summary = {}

    summary = existing_summary
    summary.setdefault("meta", {})
    summary["meta"].update({
        "stage": "identity_match_complete",
        "total_time_seconds": round(total_time, 2),
        "step": args.step,
        "direction": args.direction,
        "skip_ai": args.skip_ai,
    })

    if result_pub:
        summary["pub"] = {
            "timings": result_pub["timings"],
            "final_clusters": len(result_pub.get("final_retained", [])),
        }
    if result_sub:
        summary["sub"] = {
            "timings": result_sub["timings"],
            "final_clusters": len(result_sub.get("final_retained", [])),
        }

    safe_save_json(
        summary, summary_path,
        PROJECT_ROOT, config["output"].get("indent_json", 2),
    )


if __name__ == "__main__":
    main()
