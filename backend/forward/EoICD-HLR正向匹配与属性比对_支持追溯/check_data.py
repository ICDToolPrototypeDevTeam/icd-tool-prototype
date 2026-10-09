#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据完整性检查脚本 (v2)
"""
import json
import os
from pathlib import Path

PROJECT = Path("/root/.openclaw/workspace/projects/EoICD-HLR正向匹配")
ERRORS = []
WARNINGS = []

def load_json_with_comments(path):
    """加载带注释的 JSON 文件"""
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        if "//" in line:
            line = line.split("//")[0] + "\n"
        clean_lines.append(line)
    return json.loads("".join(clean_lines))

def check_json(path, desc):
    """检查 JSON 文件是否可解析"""
    if not path.exists():
        ERRORS.append(f"❌ 文件不存在: {desc} ({path})")
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        ERRORS.append(f"❌ JSON 解析失败: {desc} ({e})")
        return None

print("="*60)
print("🔍 数据完整性检查 v2")
print("="*60)

# ============================================
# 1. 身份匹配阶段检查
# ============================================
print("\n📋 身份匹配阶段")
print("-"*40)

# 1.1 拒绝文件检查
for stage in ["bus_label_bit", "direction", "name"]:
    for direction in ["pub", "sub"]:
        path = PROJECT / f"data/output/identity_match/rejected/rejected_{stage}_{direction}.json"
        data = check_json(path, f"rejected_{stage}_{direction}")
        if data and "rejections" in data:
            rej = data["rejections"]
            if rej:
                first = rej[0]
                if "eoicd_signal_name" not in first:
                    ERRORS.append(f"❌ rejected_{stage}_{direction} 缺少 eoicd_signal_name 字段")
                else:
                    print(f"  ✓ rejected_{stage}_{direction}: {len(rej)} 条, 含 eoicd_signal_name")
            else:
                print(f"  ✓ rejected_{stage}_{direction}: 0 条")

# 1.2 match_report 检查
for direction in ["pub", "sub"]:
    path = PROJECT / f"data/output/identity_match/match_report_{direction}.json"
    data = check_json(path, f"match_report_{direction}")
    if data:
        matches = data.get("matches", [])
        print(f"  ✓ match_report_{direction}: {len(matches)} 个簇")
        if matches:
            first = matches[0]
            required = ["eoicd_cluster_id", "eoicd_signal_name", "eoicd_identity", "matched_hlrs"]
            missing = [f for f in required if f not in first]
            if missing:
                ERRORS.append(f"❌ match_report_{direction} 缺少字段: {missing}")
            else:
                print(f"    ✓ 字段完整 (eoicd_cluster_id, eoicd_signal_name, eoicd_identity, matched_hlrs)")

# ============================================
# 2. 属性匹配阶段检查
# ============================================
print("\n📋 属性匹配阶段")
print("-"*40)

for direction in ["pub", "sub"]:
    # 2.1 四分文件
    for result_type in ["matched", "mismatch", "null_value"]:
        path = PROJECT / f"data/output/attribute_match/attr_{result_type}_{direction}.json"
        data = check_json(path, f"attr_{result_type}_{direction}")
        if data is not None:
            if isinstance(data, list):
                count = len(data)
                print(f"  ✓ attr_{result_type}_{direction}: {count} 条 (list)")
                if count > 0:
                    first = data[0]
                    required = ["eoicd_cluster_id", "eoicd_signal_name", "attribute", "eoicd_value", "hlr_value"]
                    missing = [f for f in required if f not in first]
                    if missing:
                        ERRORS.append(f"❌ attr_{result_type}_{direction} 缺少字段: {missing}")
            else:
                ERRORS.append(f"❌ attr_{result_type}_{direction} 应该是 list，实际是 {type(data).__name__}")

    # not_mentioned 是特殊结构
    path = PROJECT / f"data/output/attribute_match/attr_not_mentioned_{direction}.json"
    data = check_json(path, f"attr_not_mentioned_{direction}")
    if data and isinstance(data, dict):
        by_signal = data.get("not_mentioned_by_signal", [])
        print(f"  ✓ attr_not_mentioned_{direction}: {len(by_signal)} 信号 (dict)")

    # 2.2 聚类文件
    path = PROJECT / f"data/output/attribute_match/attr_clustered_{direction}.json"
    data = check_json(path, f"attr_clustered_{direction}")
    if data and isinstance(data, dict) and "signals" in data:
        signals = data["signals"]
        conflict_signals = [s for s in signals if s.get("has_conflict", False)]
        print(f"  ✓ attr_clustered_{direction}: {len(signals)} 信号, 冲突信号 {len(conflict_signals)}")

    # 2.3 最终报告
    path = PROJECT / f"data/output/attribute_match/attribute_match_report_{direction}.json"
    data = check_json(path, f"attribute_match_report_{direction}")
    if data and isinstance(data, dict):
        matched = len(data.get("matched", [])) if isinstance(data.get("matched"), list) else 0
        mismatch = len(data.get("mismatch", [])) if isinstance(data.get("mismatch"), list) else 0
        not_mentioned = data.get("not_mentioned", {})
        not_mentioned_count = len(not_mentioned.get("not_mentioned_by_signal", [])) if isinstance(not_mentioned, dict) else 0
        null_value = len(data.get("null_value", [])) if isinstance(data.get("null_value"), list) else 0
        print(f"  ✓ attribute_match_report_{direction}: matched={matched}, mismatch={mismatch}, not_mentioned_signals={not_mentioned_count}, null_value={null_value}")

# ============================================
# 3. 数据一致性检查
# ============================================
print("\n📋 数据一致性")
print("-"*40)

# 3.1 身份匹配: match_report 与 retained_name 数据量一致
for direction in ["pub", "sub"]:
    report = check_json(
        PROJECT / f"data/output/identity_match/match_report_{direction}.json",
        f"match_report_{direction}"
    )
    retained = check_json(
        PROJECT / f"data/output/identity_match/retained_name_{direction}.json",
        f"retained_name_{direction}"
    )
    if report and retained:
        report_count = len(report.get("matches", []))
        retained_count = len(retained)
        if report_count != retained_count:
            WARNINGS.append(f"⚠️ match_report_{direction} ({report_count}) ≠ retained_name_{direction} ({retained_count})")
        else:
            print(f"  ✓ match_report_{direction} == retained_name_{direction}: {report_count}")

# 3.2 属性匹配: 四分文件总和 == 聚类信号属性总和
for direction in ["pub", "sub"]:
    total = 0
    for result_type in ["matched", "mismatch", "null_value"]:
        data = check_json(
            PROJECT / f"data/output/attribute_match/attr_{result_type}_{direction}.json",
            f"attr_{result_type}_{direction}"
        )
        if isinstance(data, list):
            total += len(data)
    
    # not_mentioned 需要展开计算 missing_in_hlrs 总数
    nm_data = check_json(
        PROJECT / f"data/output/attribute_match/attr_not_mentioned_{direction}.json",
        f"attr_not_mentioned_{direction}"
    )
    nm_count = 0
    if nm_data and isinstance(nm_data, dict):
        for s in nm_data.get("not_mentioned_by_signal", []):
            for attr in s.get("not_mentioned_attributes", []):
                nm_count += len(attr.get("missing_in_hlrs", []))
    total += nm_count
    
    clustered = check_json(
        PROJECT / f"data/output/attribute_match/attr_clustered_{direction}.json",
        f"attr_clustered_{direction}"
    )
    if clustered and isinstance(clustered, dict) and "signals" in clustered:
        cluster_total = 0
        for s in clustered["signals"]:
            for attr_data in s.get("results_by_attribute", {}).values():
                if isinstance(attr_data, dict) and "results" in attr_data:
                    cluster_total += len(attr_data["results"])
                elif isinstance(attr_data, list):
                    cluster_total += len(attr_data)
        # not_mentioned_summary 是汇总信息，results_by_attribute 已包含 not_mentioned 记录
        # 不再重复累加 not_mentioned_summary
        if total != cluster_total:
            WARNINGS.append(f"⚠️ 属性匹配四分总和 ({total}) ≠ 聚类属性总和 ({cluster_total}) [{direction}]")
        else:
            print(f"  ✓ 四分总和 == 聚类总和: {total} [{direction}]")

# ============================================
# 4. 冲突处理模块检查
# ============================================
print("\n📋 冲突处理模块")
print("-"*40)

code_files = [
    "attribute_match/src/attr_conflict_extractor.py",
    "attribute_match/src/attr_conflict_ai_judge.py",
    "attribute_match/src/attr_conflict_resolver.py",
]
for f in code_files:
    path = PROJECT / f
    if path.exists():
        print(f"  ✓ {f} 存在")
    else:
        ERRORS.append(f"❌ {f} 不存在")

# ============================================
# 5. 配置文件检查
# ============================================
print("\n📋 配置文件")
print("-"*40)

try:
    im_config = load_json_with_comments(PROJECT / "identity_match/config/match_config.json")
    print(f"  ✓ identity_match/config/match_config.json 可解析")
except Exception as e:
    ERRORS.append(f"❌ identity_match_config 解析失败: {e}")

try:
    am_config = load_json_with_comments(PROJECT / "attribute_match/config/attribute_match_config.json")
    print(f"  ✓ attribute_match/config/attribute_match_config.json 可解析")
    if "input_hlr" not in am_config.get("paths", {}):
        WARNINGS.append("⚠️ attribute_match_config 缺少 input_hlr 路径（用于冲突处理）")
    else:
        print(f"  ✓ attribute_match_config 含 input_hlr 配置")
except Exception as e:
    ERRORS.append(f"❌ attribute_match_config 解析失败: {e}")

# ============================================
# 汇总
# ============================================
print("\n" + "="*60)
print("📊 检查结果汇总")
print("="*60)

if ERRORS:
    print(f"\n❌ 错误 ({len(ERRORS)}):")
    for e in ERRORS:
        print(f"  {e}")
else:
    print("\n✅ 无错误")

if WARNINGS:
    print(f"\n⚠️ 警告 ({len(WARNINGS)}):")
    for w in WARNINGS:
        print(f"  {w}")
else:
    print("\n✅ 无警告")

print("\n" + "="*60)
