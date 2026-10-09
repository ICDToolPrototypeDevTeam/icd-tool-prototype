"""
match_engine.py — 身份匹配阶段主入口（已弃用 / 冗余）

⚠️ 注意：此文件当前未被任何代码引用。
实际入口为 run_identity_match.py，其内部直接调用各子模块：
  bus_label_matcher → name_cropper → name_ai_matcher → name_result_feeder → bit_matcher

保留原因：供历史参考。如需恢复使用，需将 run_identity_match.py 中的
process_direction() 逻辑迁移至此，并更新 import 关系。
"""


import time
import os

from utils import load_config, load_json, save_json, flatten_hlr_requirements
from bus_label_matcher import run_bus_label_matching
from name_cropper import crop_name_input
from name_ai_matcher import batch_ai_match
from name_result_feeder import feed_ai_results
from bit_matcher import run_bit_matching


def process_direction(eoicd_clusters, hlr_reqs, config, direction, project_root):
    """处理一个方向（pub 或 sub）的完整身份匹配流程。"""
    paths = config["paths"]
    output_cfg = config["output"]
    indent = output_cfg.get("indent_json", 2)
    ai_enabled = config["ai_matching"]["enabled"]

    print(f"\n{'='*60}")
    print(f"处理方向: {direction.upper()}")
    print(f"{'='*60}")

    # ------------------------------------------------------------------
    # Step 1: Bus + Label + Bit 脚本匹配
    # ------------------------------------------------------------------
    print(f"\n[Step 1] Bus + Label + Bit 脚本匹配...")
    t0 = time.time()
    retained_bus_label_bit, rejected_bus_label_bit = run_bus_label_matching(
        eoicd_clusters, hlr_reqs, config
    )
    t_bus = time.time() - t0

    candidate_pairs = sum(len(item["candidate_hlrs"]) for item in retained_bus_label_bit)
    print(f"  保留 {len(retained_bus_label_bit)} 个簇 ({candidate_pairs} 对), "
          f"拒绝 {len(rejected_bus_label_bit)} 对, 耗时 {t_bus:.2f}s")

    if output_cfg.get("save_rejected", True):
        save_json({
            "meta": {"direction": direction, "stage": "bus_label_bit", "total_rejected": len(rejected_bus_label_bit)},
            "rejections": rejected_bus_label_bit,
        }, os.path.join(paths["rejected_dir"], f"rejected_bus_label_bit_{direction}.json"), project_root, indent)
    save_json(retained_bus_label_bit,
              os.path.join(paths["output_dir"], f"retained_bus_label_bit_{direction}.json"), project_root, indent)

    # ------------------------------------------------------------------
    # Step 2: Name 信息裁剪
    # ------------------------------------------------------------------
    print(f"\n[Step 2] Name 信息裁剪...")
    t0 = time.time()
    cropped_pairs, skip_ai_pairs = crop_name_input(retained_bus_label_bit)
    t_crop = time.time() - t0

    print(f"  {len(cropped_pairs)} 对进入 AI, {len(skip_ai_pairs)} 对 name=null 直接保留, "
          f"耗时 {t_crop:.3f}s")

    if output_cfg.get("save_intermediate", True):
        save_json({
            "meta": {"direction": direction, "total_pairs": len(cropped_pairs), "skip_ai": len(skip_ai_pairs)},
            "pairs": cropped_pairs,
        }, os.path.join(paths["intermediate_dir"], f"cropped_name_input_{direction}.json"), project_root, indent)

    # ------------------------------------------------------------------
    # Step 3: AI Name 匹配（可开关）
    # ------------------------------------------------------------------
    ai_results = []
    t_ai = 0

    if ai_enabled and cropped_pairs:
        print(f"\n[Step 3] AI Name 匹配...")
        t0 = time.time()
        ai_results = batch_ai_match(cropped_pairs, config)
        t_ai = time.time() - t0

        matched = sum(1 for r in ai_results if r["result"] == "匹配")
        unmatched = sum(1 for r in ai_results if r["result"] == "不匹配")
        print(f"  AI 匹配完成: {matched} 对匹配, {unmatched} 对不匹配, 耗时 {t_ai:.1f}s")

        if output_cfg.get("save_intermediate", True):
            save_json({
                "meta": {"direction": direction, "total": len(ai_results), "matched": matched, "unmatched": unmatched},
                "results": ai_results,
            }, os.path.join(paths["intermediate_dir"], f"ai_name_results_{direction}.json"), project_root, indent)
    else:
        print(f"\n[Step 3] AI Name 匹配: 已跳过（ai_enabled={ai_enabled}, pairs={len(cropped_pairs)}）")

    # ------------------------------------------------------------------
    # Step 4: 结果反哺
    # ------------------------------------------------------------------
    print(f"\n[Step 4] 结果反哺...")
    t0 = time.time()
    final_retained, rejected_name = feed_ai_results(
        retained_bus_label_bit, ai_results, skip_ai_pairs
    )
    t_feed = time.time() - t0

    final_pairs = sum(len(item["candidate_hlrs"]) for item in final_retained)
    print(f"  最终保留 {len(final_retained)} 个簇 ({final_pairs} 对), "
          f"name 拒绝 {len(rejected_name)} 对, 耗时 {t_feed:.3f}s")

    if output_cfg.get("save_rejected", True):
        save_json({
            "meta": {"direction": direction, "stage": "name", "total_rejected": len(rejected_name)},
            "rejections": rejected_name,
        }, os.path.join(paths["rejected_dir"], f"rejected_name_{direction}.json"), project_root, indent)
    save_json(final_retained,
              os.path.join(paths["output_dir"], f"retained_name_{direction}.json"), project_root, indent)

    # ------------------------------------------------------------------
    # Step 5: 生成身份匹配报告
    # ------------------------------------------------------------------
    print(f"\n[Step 5] 生成身份匹配报告...")
    t0 = time.time()
    match_report = run_bit_matching(final_retained, config)
    t_bit = time.time() - t0

    print(f"  完成 {len(match_report['matches'])} 个簇的身份匹配")
    print(f"  耗时 {t_bit:.3f}s")

    save_json(match_report,
              os.path.join(paths["output_dir"], f"match_report_{direction}.json"), project_root, indent)

    return {
        "timings": {"bus_label_bit": t_bus, "crop": t_crop, "ai": t_ai, "feed": t_feed, "bit": t_bit},
        "counts": {
            "total_clusters": len(eoicd_clusters),
            "retained_clusters": len(retained_bus_label_bit),
            "candidate_pairs": candidate_pairs,
            "rejected_bus_label_bit": len(rejected_bus_label_bit),
            "need_ai_pairs": len(cropped_pairs),
            "skip_ai_pairs": len(skip_ai_pairs),
            "ai_matched": sum(1 for r in ai_results if r["result"] == "匹配") if ai_results else 0,
            "ai_unmatched": sum(1 for r in ai_results if r["result"] == "不匹配") if ai_results else 0,
            "final_clusters": len(final_retained),
            "final_pairs": final_pairs,
            "rejected_name": len(rejected_name),
        },
    }


def main():
    total_start = time.time()

    print("=" * 60)
    print("EoICD → HLR 正向匹配：身份匹配阶段")
    print("=" * 60)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = load_config("config/match_config.json")

    paths = config["paths"]
    output_cfg = config["output"]
    indent = output_cfg.get("indent_json", 2)

    # 加载数据
    print("\n[加载数据]")
    t0 = time.time()
    eoicd_pub = load_json(paths["eoicd_pub"], project_root)
    eoicd_sub = load_json(paths["eoicd_sub"], project_root)
    hlr_data = load_json(paths["hlr"], project_root)
    hlr_reqs = flatten_hlr_requirements(hlr_data)
    t_load = time.time() - t0

    print(f"  EoICD Pub: {len(eoicd_pub)} clusters")
    print(f"  EoICD Sub: {len(eoicd_sub)} clusters")
    print(f"  HLR reqs:  {len(hlr_reqs)} requirements")
    print(f"  加载耗时: {t_load:.2f}s")

    # 处理 Pub
    result_pub = process_direction(eoicd_pub, hlr_reqs, config, "pub", project_root)

    # 处理 Sub
    result_sub = process_direction(eoicd_sub, hlr_reqs, config, "sub", project_root)

    # 汇总
    total_time = time.time() - total_start
    print("\n" + "=" * 60)
    print("最终汇总")
    print("=" * 60)

    for direction, result in [("PUB", result_pub), ("SUB", result_sub)]:
        c = result["counts"]
        print(f"\n【{direction}】")
        print(f"  总簇数: {c['total_clusters']} → 保留: {c['final_clusters']}")
        print(f"  Bus+Label+Bit: 候选 {c['candidate_pairs']} 对, 拒绝 {c['rejected_bus_label_bit']} 对")
        print(f"  Name: 需 AI {c['need_ai_pairs']} 对, name=null 保留 {c['skip_ai_pairs']} 对")
        if c["need_ai_pairs"] > 0:
            print(f"  AI 结果: 匹配 {c['ai_matched']} 对, 不匹配 {c['ai_unmatched']} 对")
        print(f"  Name 后保留: {c['final_pairs']} 对, name 拒绝: {c['rejected_name']} 对")
        t = result["timings"]
        print(f"  耗时: bus_label_bit={t['bus_label_bit']:.2f}s + crop={t['crop']:.3f}s + "
              f"ai={t['ai']:.1f}s + feed={t['feed']:.3f}s + report={t['bit']:.3f}s")

    print(f"\n总耗时: {total_time:.2f}s")

    # 保存汇总
    summary = {
        "meta": {"stage": "identity_match_complete", "total_time_seconds": round(total_time, 2)},
        "pub": result_pub["counts"],
        "sub": result_sub["counts"],
    }
    save_json(summary, os.path.join(paths["output_dir"], "summary_identity_match.json"), project_root, indent)

    print(f"\n输出目录: {os.path.join(project_root, paths['output_dir'])}")
    print("=" * 60)


if __name__ == "__main__":
    main()
