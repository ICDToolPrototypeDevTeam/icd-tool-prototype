#!/usr/bin/env python3
"""
完整匹配阶段测试脚本（身份匹配 + 属性匹配）
配置：并发=3, batch_size=200, 3个不同key
"""
import sys
import os
import shutil
import time
import json

# 添加路径
sys.path.insert(0, '/root/.openclaw/workspace/projects/EoICD-HLR正向匹配/shared')
sys.path.insert(0, '/root/.openclaw/workspace/projects/EoICD-HLR正向匹配/identity_match/src')
sys.path.insert(0, '/root/.openclaw/workspace/projects/EoICD-HLR正向匹配/attribute_match/src')

from utils import load_config, load_json, save_json
from bus_label_matcher import run_bus_label_matching
from name_cropper import crop_name_input
from name_ai_matcher import batch_ai_match
from name_result_feeder import feed_ai_results
from bit_matcher import generate_match_report

# 属性匹配导入
from attr_script_matcher import run_script_matching
from attr_result_integrator import integrate_results
from attr_mismatch_cropper import crop_mismatch_input
from attr_mismatch_ai_matcher import batch_ai_review_mismatch
from attr_mismatch_result_feeder import feed_ai_results as feed_attr_ai_results

# ==================== 配置 ====================
project_root = '/root/.openclaw/workspace/projects/EoICD-HLR正向匹配'
identity_config_path = os.path.join(project_root, 'identity_match/config/match_config.json')
attr_config_path = os.path.join(project_root, 'attribute_match/config/attribute_match_config.json')

identity_config = load_config(identity_config_path)

# 设置身份匹配并行参数：3 workers，同一把 key 重复 3 次（读环境变量 DEEPSEEK_API_KEY）
identity_config['ai_matching']['batch_size'] = 200
identity_config['ai_matching']['parallel']['enabled'] = True
identity_config['ai_matching']['parallel']['max_workers'] = 3
_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
if not _api_key:
    raise SystemExit("请先导出环境变量 DEEPSEEK_API_KEY")
identity_config['ai_matching']['parallel']['api_keys'] = [_api_key] * 3

# 输出目录
identity_output_dir = os.path.join(project_root, 'data/output/identity_match')
attr_output_dir = os.path.join(project_root, 'data/output/attribute_match')

# 清理之前的测试输出
print("=" * 70)
print("清理之前的测试输出...")
for d in [identity_output_dir, attr_output_dir]:
    if os.path.exists(d):
        shutil.rmtree(d)
        print(f"  已删除: {d}")

os.makedirs(identity_output_dir, exist_ok=True)
os.makedirs(os.path.join(identity_output_dir, 'intermediate'), exist_ok=True)
os.makedirs(os.path.join(identity_output_dir, 'rejected'), exist_ok=True)
os.makedirs(attr_output_dir, exist_ok=True)
os.makedirs(os.path.join(attr_output_dir, 'intermediate'), exist_ok=True)
print("清理完成")

# ==================== 加载数据 ====================
print("\n" + "=" * 70)
print("加载数据...")
data_dir = os.path.join(project_root, 'data/input')
eoicd_pub = load_json(os.path.join(data_dir, 'eoicd_pub_clustered.json'))
eoicd_sub = load_json(os.path.join(data_dir, 'eoicd_sub_clustered.json'))
hlr_data = load_json(os.path.join(data_dir, 'hlr_clustered.json'))
hlr = hlr_data.get('requirements', hlr_data)

print(f"  Pub clusters: {len(eoicd_pub)}")
print(f"  Sub clusters: {len(eoicd_sub)}")
print(f"  HLR reqs: {len(hlr)}")

# ==================== 身份匹配阶段 ====================
print("\n" + "=" * 70)
print("【身份匹配阶段】开始")
identity_t0 = time.time()

for direction, eoicd_data in [("pub", eoicd_pub), ("sub", eoicd_sub)]:
    print(f"\n{'='*60}")
    print(f"【{direction.upper()} 方向】")
    dir_t0 = time.time()
    
    # Step 1: Bus+Label+Bit
    print("\nStep 1: Bus+Label+Bit 脚本匹配...")
    t0 = time.time()
    retained, rejected = run_bus_label_matching(eoicd_data, hlr, identity_config)
    t1 = time.time()
    print(f"  保留: {len(retained)} 簇, 拒绝: {len(rejected)} 对, 耗时: {t1-t0:.1f}s")
    
    # Step 2: Name 裁剪
    print("\nStep 2: Name 裁剪...")
    cropped, skip_ai = crop_name_input(retained)
    print(f"  需 AI: {len(cropped)} 对, 跳过: {len(skip_ai)} 对")
    
    # Step 3: AI Name 匹配
    print("\nStep 3: AI Name 匹配...")
    print(f"  配置: batch_size=200, max_workers=3, 3个不同key")
    t0 = time.time()
    ai_results = batch_ai_match(cropped, identity_config)
    t1 = time.time()
    print(f"  AI 完成: {len(ai_results)} 对, 耗时: {t1-t0:.1f}s")
    
    # Step 4: 结果反哺
    print("\nStep 4: 结果反哺...")
    final_retained, rejected_name = feed_ai_results(retained, ai_results, skip_ai)
    print(f"  最终保留: {len(final_retained)} 簇, Name拒绝: {len(rejected_name)} 对")
    
    # Step 5: 生成报告
    print("\nStep 5: 生成报告...")
    report = generate_match_report(final_retained, identity_config)
    
    # 保存
    save_json(report, os.path.join(identity_output_dir, f'match_report_{direction}.json'))
    save_json(retained, os.path.join(identity_output_dir, 'intermediate', f'retained_bus_label_bit_{direction}.json'))
    save_json(cropped, os.path.join(identity_output_dir, 'intermediate', f'cropped_{direction}.json'))
    save_json(rejected, os.path.join(identity_output_dir, 'rejected', f'rejected_bus_label_bit_{direction}.json'))
    if rejected_name:
        save_json(rejected_name, os.path.join(identity_output_dir, 'rejected', f'rejected_name_{direction}.json'))
    
    dir_time = time.time() - dir_t0
    print(f"\n【{direction.upper()} 完成】耗时: {dir_time:.1f}s")
    print(f"  匹配簇数: {report['meta']['total_clusters']}")
    print(f"  匹配详情: {len(report.get('matches', []))} 条")

identity_time = time.time() - identity_t0
print(f"\n{'='*70}")
print(f"【身份匹配阶段完成】总耗时: {identity_time:.1f}s")
print(f"{'='*70}")

# ==================== 属性匹配阶段 ====================
print("\n" + "=" * 70)
print("【属性匹配阶段】开始")
attr_t0 = time.time()

# 加载属性匹配配置
attr_config = load_config(attr_config_path)

for direction in ["pub", "sub"]:
    print(f"\n{'='*60}")
    print(f"【{direction.upper()} 方向】属性匹配")
    dir_t0 = time.time()
    
    # 1. 读取身份匹配报告
    input_path = os.path.join(identity_output_dir, f'match_report_{direction}.json')
    print(f"\n[1/4] 读取身份匹配报告: {input_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        match_report = json.load(f)
    total_clusters = match_report["meta"]["total_clusters"]
    print(f"      共 {total_clusters} 个信号簇待属性匹配")
    
    # 2. 脚本预匹配
    special_rules = attr_config.get("special_rules", {})
    special_desc = "、".join(special_rules.keys()) if special_rules else "无"
    print(f"\n[2/4] 脚本预匹配（默认规则: 字符串不区分大小写 | 特殊规则: {special_desc}）")
    script_results = run_script_matching(match_report, attr_config)
    stats = script_results["stats"]
    print(f"      matched:      {stats['matched']:4d}")
    print(f"      not_mentioned: {stats['not_mentioned']:4d}")
    print(f"      null_value:   {stats.get('null_value', 0):4d}")
    print(f"      mismatch:     {stats['mismatch']:4d}")
    print(f"      总计检查项:   {stats['total_checks']:4d}")
    
    # 3. AI 复核 mismatch
    ai_results = None
    if stats["mismatch"] > 0 and attr_config.get("ai_review", {}).get("enabled", True):
        print(f"\n[3/4] AI 复核 mismatch 数据（共 {stats['mismatch']} 项）")
        
        # 3.1 裁剪
        mismatch_pairs = script_results["mismatch_pairs"]
        cropped = crop_mismatch_input(mismatch_pairs, direction)
        print(f"      裁剪后: {len(cropped)} 对需 AI 复核")
        
        # 3.2 AI 复核
        ai_cfg = attr_config.get("ai_review", {})
        ai_raw_results = batch_ai_review_mismatch(cropped, ai_cfg)
        
        # 3.3 结果反哺
        ai_results = feed_attr_ai_results(mismatch_pairs, ai_raw_results)
        
        ai_matched = sum(1 for r in ai_results if r.get("ai_result") == "一致")
        ai_mismatch = sum(1 for r in ai_results if r.get("ai_result") == "不一致")
        print(f"      AI 复核: {ai_matched} 项→matched, {ai_mismatch} 项→mismatch")
    else:
        print(f"\n[3/4] 跳过 AI 复核（mismatch={stats['mismatch']}, enabled={attr_config.get('ai_review', {}).get('enabled', True)}）")
    
    # 4. 结果整合
    print(f"\n[4/4] 结果整合输出")
    final_results = integrate_results(script_results, ai_results, direction, attr_config, project_root)
    
    print(f"\n      最终输出:")
    print(f"      - matched:       {final_results['matched']}")
    print(f"      - mismatch:      {final_results['mismatch']}")
    print(f"      - not_mentioned: {final_results['not_mentioned']}")
    
    dir_time = time.time() - dir_t0
    print(f"\n【{direction.upper()} 属性匹配完成】耗时: {dir_time:.1f}s")

attr_time = time.time() - attr_t0
print(f"\n{'='*70}")
print(f"【属性匹配阶段完成】总耗时: {attr_time:.1f}s")
print(f"{'='*70}")

# ==================== 总汇总 ====================
total_time = identity_time + attr_time
print(f"\n{'='*70}")
print(f"【完整匹配阶段测试完成】")
print(f"身份匹配耗时: {identity_time:.1f}s")
print(f"属性匹配耗时: {attr_time:.1f}s")
print(f"总耗时: {total_time:.1f}s")
print(f"\n输出目录:")
print(f"  身份匹配: {identity_output_dir}")
print(f"  属性匹配: {attr_output_dir}")
print(f"{'='*70}")
