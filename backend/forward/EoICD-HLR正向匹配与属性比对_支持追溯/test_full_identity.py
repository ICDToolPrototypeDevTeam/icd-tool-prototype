#!/usr/bin/env python3
"""
完整身份匹配测试脚本
配置：并发=3, batch_size=200, api_key=读环境变量 DEEPSEEK_API_KEY
"""
import sys
import os
import shutil

# Add paths
sys.path.insert(0, '/root/.openclaw/workspace/projects/EoICD-HLR正向匹配/shared')
sys.path.insert(0, '/root/.openclaw/workspace/projects/EoICD-HLR正向匹配/identity_match/src')

from utils import load_config, load_json, save_json
from bus_label_matcher import run_bus_label_matching
from name_cropper import crop_name_input
from name_ai_matcher import batch_ai_match
from name_result_feeder import feed_ai_results
from bit_matcher import generate_match_report

# ==================== 配置 ====================
config_path = '/root/.openclaw/workspace/projects/EoICD-HLR正向匹配/identity_match/config/match_config.json'
config = load_config(config_path)

# 设置并行参数：3 workers，同一个 key 重复3次
config['ai_matching']['batch_size'] = 200
config['ai_matching']['parallel']['enabled'] = True
config['ai_matching']['parallel']['max_workers'] = 3
_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
if not _api_key:
    raise SystemExit("请先导出环境变量 DEEPSEEK_API_KEY")
config['ai_matching']['parallel']['api_keys'] = [_api_key] * 3

# 输出目录
output_dir = '/root/.openclaw/workspace/projects/EoICD-HLR正向匹配/data/output/identity_match'
intermediate_dir = os.path.join(output_dir, 'intermediate')
rejected_dir = os.path.join(output_dir, 'rejected')

# 清理之前的测试输出
print("=" * 60)
print("清理之前的测试输出...")
for d in [output_dir, intermediate_dir, rejected_dir]:
    if os.path.exists(d):
        shutil.rmtree(d)
        print(f"  已删除: {d}")

os.makedirs(output_dir, exist_ok=True)
os.makedirs(intermediate_dir, exist_ok=True)
os.makedirs(rejected_dir, exist_ok=True)
print("清理完成")

# ==================== 加载数据 ====================
print("\n" + "=" * 60)
print("加载数据...")
data_dir = '/root/.openclaw/workspace/projects/EoICD-HLR正向匹配/data/input'
eoicd_pub = load_json(os.path.join(data_dir, 'eoicd_pub_clustered.json'))
eoicd_sub = load_json(os.path.join(data_dir, 'eoicd_sub_clustered.json'))
hlr_data = load_json(os.path.join(data_dir, 'hlr_clustered.json'))
hlr = hlr_data.get('requirements', hlr_data)

print(f"  Pub clusters: {len(eoicd_pub)}")
print(f"  Sub clusters: {len(eoicd_sub)}")
print(f"  HLR reqs: {len(hlr)}")

# ==================== 处理 Pub 方向 ====================
print("\n" + "=" * 60)
print("【Pub 方向】身份匹配开始...")
import time
total_t0 = time.time()

# Step 1: Bus+Label+Bit
print("\nStep 1: Bus+Label+Bit 脚本匹配...")
t0 = time.time()
retained_pub, rejected_pub = run_bus_label_matching(eoicd_pub, hlr, config)
t1 = time.time()
print(f"  保留: {len(retained_pub)} 簇, 拒绝: {len(rejected_pub)} 对, 耗时: {t1-t0:.1f}s")

# Step 2: Name 裁剪
print("\nStep 2: Name 裁剪...")
cropped_pub, skip_ai_pub = crop_name_input(retained_pub)
print(f"  需 AI 处理: {len(cropped_pub)} 对, 跳过 AI: {len(skip_ai_pub)} 对")

# Step 3: AI Name 匹配
print("\nStep 3: AI Name 匹配...")
print(f"  配置: batch_size=200, max_workers=3")
t0 = time.time()
ai_results_pub = batch_ai_match(cropped_pub, config)
t1 = time.time()
print(f"  AI 处理完成: {len(ai_results_pub)} 对, 耗时: {t1-t0:.1f}s")

# Step 4: 结果反哺
print("\nStep 4: 结果反哺...")
final_retained_pub, rejected_name_pub = feed_ai_results(retained_pub, ai_results_pub, skip_ai_pub)
print(f"  最终保留: {len(final_retained_pub)} 簇, Name 拒绝: {len(rejected_name_pub)} 对")

# Step 5: 生成报告
print("\nStep 5: 生成报告...")
report_pub = generate_match_report(final_retained_pub, config)

# 保存 Pub 输出
save_json(report_pub, os.path.join(output_dir, 'match_report_pub.json'))
save_json(retained_pub, os.path.join(intermediate_dir, 'retained_bus_label_bit_pub.json'))
save_json(cropped_pub, os.path.join(intermediate_dir, 'cropped_pub.json'))
save_json(rejected_pub, os.path.join(rejected_dir, 'rejected_bus_label_bit_pub.json'))
if rejected_name_pub:
    save_json(rejected_name_pub, os.path.join(rejected_dir, 'rejected_name_pub.json'))

pub_time = time.time() - total_t0
print(f"\n【Pub 方向完成】总耗时: {pub_time:.1f}s")
print(f"  输出: {output_dir}/match_report_pub.json")

# ==================== 处理 Sub 方向 ====================
print("\n" + "=" * 60)
print("【Sub 方向】身份匹配开始...")
total_t0 = time.time()

# Step 1: Bus+Label+Bit
print("\nStep 1: Bus+Label+Bit 脚本匹配...")
t0 = time.time()
retained_sub, rejected_sub = run_bus_label_matching(eoicd_sub, hlr, config)
t1 = time.time()
print(f"  保留: {len(retained_sub)} 簇, 拒绝: {len(rejected_sub)} 对, 耗时: {t1-t0:.1f}s")

# Step 2: Name 裁剪
print("\nStep 2: Name 裁剪...")
cropped_sub, skip_ai_sub = crop_name_input(retained_sub)
print(f"  需 AI 处理: {len(cropped_sub)} 对, 跳过 AI: {len(skip_ai_sub)} 对")

# Step 3: AI Name 匹配
print("\nStep 3: AI Name 匹配...")
t0 = time.time()
ai_results_sub = batch_ai_match(cropped_sub, config)
t1 = time.time()
print(f"  AI 处理完成: {len(ai_results_sub)} 对, 耗时: {t1-t0:.1f}s")

# Step 4: 结果反哺
print("\nStep 4: 结果反哺...")
final_retained_sub, rejected_name_sub = feed_ai_results(retained_sub, ai_results_sub, skip_ai_sub)
print(f"  最终保留: {len(final_retained_sub)} 簇, Name 拒绝: {len(rejected_name_sub)} 对")

# Step 5: 生成报告
print("\nStep 5: 生成报告...")
report_sub = generate_match_report(final_retained_sub, config)

# 保存 Sub 输出
save_json(report_sub, os.path.join(output_dir, 'match_report_sub.json'))
save_json(retained_sub, os.path.join(intermediate_dir, 'retained_bus_label_bit_sub.json'))
save_json(cropped_sub, os.path.join(intermediate_dir, 'cropped_sub.json'))
save_json(rejected_sub, os.path.join(rejected_dir, 'rejected_bus_label_bit_sub.json'))
if rejected_name_sub:
    save_json(rejected_name_sub, os.path.join(rejected_dir, 'rejected_name_sub.json'))

sub_time = time.time() - total_t0
print(f"\n【Sub 方向完成】总耗时: {sub_time:.1f}s")
print(f"  输出: {output_dir}/match_report_sub.json")

# ==================== 汇总 ====================
print("\n" + "=" * 60)
print("【身份匹配测试完成】")
print(f"Pub 方向总耗时: {pub_time:.1f}s")
print(f"  - 匹配簇数: {report_pub['meta']['total_clusters']}")
print(f"  - 匹配详情: {len(report_pub.get('matches', []))} 条")
print(f"Sub 方向总耗时: {sub_time:.1f}s")
print(f"  - 匹配簇数: {report_sub['meta']['total_clusters']}")
print(f"  - 匹配详情: {len(report_sub.get('matches', []))} 条")
print(f"\n输出目录: {output_dir}")
print("=" * 60)
