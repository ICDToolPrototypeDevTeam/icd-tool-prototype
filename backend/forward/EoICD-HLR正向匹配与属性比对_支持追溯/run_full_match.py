#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
完整运行匹配阶段（身份匹配 + 属性匹配）
"""
import subprocess
import sys

PROJECT = "/root/.openclaw/workspace/projects/EoICD-HLR正向匹配"

print("="*60)
print("🚀 完整运行匹配阶段")
print("="*60)

# Step 1: 身份匹配
print("\n▶ 执行身份匹配...")
r1 = subprocess.run(
    [sys.executable, f"{PROJECT}/identity_match/run_identity_match.py"],
    cwd=PROJECT,
    capture_output=False,
)
if r1.returncode != 0:
    print("❌ 身份匹配失败")
    sys.exit(1)

# Step 2: 属性匹配（含聚合、冲突处理）
print("\n▶ 执行属性匹配（含AI复核、聚合、冲突处理）...")
r2 = subprocess.run(
    [sys.executable, f"{PROJECT}/attribute_match/run_attribute_match.py", "--step", "all"],
    cwd=PROJECT,
    capture_output=False,
)
if r2.returncode != 0:
    print("❌ 属性匹配失败")
    sys.exit(1)

print("\n✅ 全部完成")
