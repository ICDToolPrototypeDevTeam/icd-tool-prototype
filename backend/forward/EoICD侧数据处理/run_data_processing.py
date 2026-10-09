#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EoICD 数据处理完整流程
=====================
一步执行：定位 → 裁剪 → 追溯（可选） → 去重

用法:
  python3 run_data_processing.py --config-dir config --output-dir data/processed

或指定单个步骤:
  python3 run_data_processing.py --step locate   # 只执行定位
  python3 run_data_processing.py --step crop     # 只执行裁剪
  python3 run_data_processing.py --step trace    # 只执行追溯链解析
  python3 run_data_processing.py --step dedup    # 只执行去重（自动读取追溯结果）

切换项目:
  python3 run_data_processing.py --project ams   # 空气管理系统（3 层链路）
  python3 run_data_processing.py --project eps   # 配电装置（4 层链路）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


# 让项目根的 common/ 公共包可被导入（本文件位于 EoICD侧数据处理/ 下）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from common.projects import get_project, project_ids  # noqa: E402  统一系统配置（单一数据源）


# ============================ 配置 ============================

# 以脚本所在目录为基准
BASE_DIR = Path(__file__).parent.resolve()

RAW_DIR = _PROJECT_ROOT / "input/ams"  # 仅作示例常量；实际输入目录以 common/projects.py 的 raw_dir 为准
PROCESSED_DIR = BASE_DIR / "data/processed"
CONFIG_DIR = BASE_DIR / "config"
SRC_DIR = BASE_DIR / "src"

CROP_CONFIG = CONFIG_DIR / "eoicd_crop.yaml"
TRACE_CONFIG = CONFIG_DIR / "traceability.yaml"

# ---------------- 项目配置 ----------------
# 不同项目的 EoICD 表名、输入目录、链路层数等都统一登记在 common/projects.py
# （单一数据源）。新增系统只需改那一个文件，无需改动本脚本。
# {side} 会被替换为 Publisher / Subscriber

LOCATE_TOOLS = {
    "publisher": SRC_DIR / "locate_eoicd.py",
    "subscriber": SRC_DIR / "locate_eoicd.py",
}

CROP_TOOLS = {
    "publisher": SRC_DIR / "process_eoicd.py",
    "subscriber": SRC_DIR / "process_eoicd.py",
}

TRACE_TOOL = SRC_DIR / "trace_eoicd.py"
DEDUP_TOOL = SRC_DIR / "dedup_eoicd.py"


# ============================ 工具函数 ============================

def run_cmd(cmd: list[str], desc: str) -> tuple[int, float]:
    """运行命令，返回 (返回码, 耗时秒)"""
    print(f"\n{'='*60}")
    print(f"▶ {desc}")
    print(f"  命令: {' '.join(cmd)}")
    print(f"{'='*60}")
    
    start = time.time()
    result = subprocess.run(cmd, capture_output=False, text=True)
    elapsed = time.time() - start
    
    status = "✅ 成功" if result.returncode == 0 else "❌ 失败"
    print(f"\n{status} | 耗时: {elapsed:.2f}s")
    
    return result.returncode, elapsed


def resolve_paths(proj: dict, raw_dir_arg: str | None) -> tuple[Path, Path, Path]:
    """返回 (raw_dir, pub_excel, sub_excel)"""
    raw_dir = Path(raw_dir_arg) if raw_dir_arg else (_PROJECT_ROOT / proj["raw_dir"])
    if not raw_dir.is_absolute():
        raw_dir = _PROJECT_ROOT / raw_dir
    sub_excel = raw_dir / proj["sub"] if proj.get("sub") else None
    # 仅 Subscriber 侧的项目（subscriber-only）无 pub 键；镜像 sub 处理，pub 缺失时置 None
    pub_excel = raw_dir / proj["pub"] if proj.get("pub") else None
    return raw_dir, pub_excel, sub_excel


def located_path(processed_dir: Path, proj: dict, side: str) -> Path:
    return processed_dir / proj["located"].format(side=side.title())


def step_locate(processed_dir: Path, config_dir: Path, proj: dict,
                pub_excel: Path, sub_excel: Path) -> dict[str, float]:
    """第一步：定向定位"""
    timings = {}

    if proj.get("skip_locate"):
        print("\n⏭  该项目未配置定位条件，跳过定位步骤（裁剪将直接读取原始表）")
        return timings

    locate_config = config_dir / proj["locate_config"]

    for side in proj.get("sides", ["publisher", "subscriber"]):
        input_file = pub_excel if side == "publisher" else sub_excel
        output_file = located_path(processed_dir, proj, side)

        cmd = [
            sys.executable, str(LOCATE_TOOLS[side]),
            "-i", str(input_file),
            "-c", str(locate_config),
            "-s", side,
            "-o", str(output_file),
        ]

        rc, elapsed = run_cmd(cmd, f"第一步: 定向定位 [{side}]")
        timings[f"locate_{side}"] = elapsed

        if rc != 0:
            print(f"❌ 定位失败 [{side}]，终止流程")
            sys.exit(1)

    return timings


def step_crop(processed_dir: Path, config_dir: Path, proj: dict,
              pub_excel: Path, sub_excel: Path) -> dict[str, float]:
    """第二步：属性裁剪"""
    timings = {}

    crop_config = config_dir / "eoicd_crop.yaml"

    for side in proj.get("sides", ["publisher", "subscriber"]):
        raw_file = pub_excel if side == "publisher" else sub_excel
        located = located_path(processed_dir, proj, side)
        # 跳过定位时直接读原始表；否则读定位后的文件
        input_file = raw_file if (proj.get("skip_locate") or not located.exists()) else located
        output_file = processed_dir / f"eoicd_{side[:3]}.json"

        cmd = [
            sys.executable, str(CROP_TOOLS[side]),
            "-i", str(input_file),
            "-c", str(crop_config),
            "-s", side,
            "-o", str(output_file),
        ]

        rc, elapsed = run_cmd(cmd, f"第二步: 属性裁剪 [{side}]")
        timings[f"crop_{side}"] = elapsed

        if rc != 0:
            print(f"❌ 裁剪失败 [{side}]，终止流程")
            sys.exit(1)

    return timings


def step_trace(processed_dir: Path, config_dir: Path, raw_dir: Path) -> dict[str, float]:
    """第 2.5 步：追溯链解析（可选，无追溯表时自动跳过）"""
    timings: dict[str, float] = {}

    output_file = processed_dir / "trace_result.json"

    cmd = [
        sys.executable, str(TRACE_TOOL),
        "-c", str(config_dir / "traceability.yaml"),
        "-r", str(raw_dir),
        "-o", str(output_file),
    ]

    rc, elapsed = run_cmd(cmd, "第 2.5 步: 追溯链解析（检测追溯表）")
    timings["trace"] = elapsed

    if rc != 0:
        print("⚠️  追溯解析失败，按原方案继续（全量保留）")

    return timings


def step_dedup(processed_dir: Path, proj: dict) -> dict[str, float]:
    """第三步：去重归簇（+ 追溯过滤）"""
    timings = {}

    trace_file = processed_dir / "trace_result.json"
    trace_enabled = False
    if trace_file.exists():
        try:
            with open(trace_file, "r", encoding="utf-8") as f:
                trace_enabled = bool(json.load(f).get("enabled"))
        except Exception:  # noqa: BLE001
            trace_enabled = False

    for side in proj.get("sides", ["publisher", "subscriber"]):
        input_file = processed_dir / f"eoicd_{side[:3]}.json"
        output_file = processed_dir / f"eoicd_{side[:3]}_clustered.json"

        cmd = [
            sys.executable, str(DEDUP_TOOL),
            "-i", str(input_file),
            "-o", str(output_file),
        ]
        if trace_enabled:
            cmd += ["-t", str(trace_file)]

        desc = f"第三步: 去重归簇 [{side}]"
        if trace_enabled:
            desc += " + 追溯过滤"

        rc, elapsed = run_cmd(cmd, desc)
        timings[f"dedup_{side}"] = elapsed

        if rc != 0:
            print(f"❌ 去重失败 [{side}]，终止流程")
            sys.exit(1)

    return timings


def print_summary(timings: dict[str, float], processed_dir: Path) -> None:
    """打印最终摘要"""
    print(f"\n{'='*60}")
    print("📊 数据处理完成摘要")
    print(f"{'='*60}")
    
    total_time = sum(timings.values())
    
    print(f"\n⏱️ 各步骤耗时:")
    for name, t in sorted(timings.items()):
        print(f"  {name:20s}: {t:6.2f}s")
    print(f"  {'总计':20s}: {total_time:6.2f}s")
    
    print(f"\n📁 输出文件:")
    for f in sorted(processed_dir.glob("*")):
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"  {f.name:40s} {size_mb:6.1f} MB")
    
    # 去重统计
    for side_short in ["pub", "sub"]:
        clustered_file = processed_dir / f"eoicd_{side_short}_clustered.json"
        if clustered_file.exists():
            try:
                with open(clustered_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cluster_count = len(data)
                print(f"\n  eoicd_{side_short}_clustered.json: {cluster_count} 个簇")
            except Exception:
                pass
    
    print(f"\n✅ 全部完成！总耗时: {total_time:.2f}s")


# ============================ 主入口 ============================

def main():
    parser = argparse.ArgumentParser(description="EoICD 数据处理完整流程")
    parser.add_argument("--step", choices=["locate", "crop", "trace", "dedup", "all"], default="all",
                        help="执行单个步骤或全部 (默认: all)")
    parser.add_argument("--project", choices=project_ids(), default="ams",
                        help="项目标识，决定输入文件、输出目录与链路层数 (默认: ams)")
    parser.add_argument("--output-dir", default=None,
                        help="输出目录 (默认取项目配置中的 output_dir)")
    parser.add_argument("--raw-dir", default=None,
                        help="输入目录 (默认取项目配置中的 raw_dir)")
    parser.add_argument("--config-dir", default=str(CONFIG_DIR),
                        help=f"配置目录 (默认: {CONFIG_DIR})")
    args = parser.parse_args()

    proj = get_project(args.project)
    config_dir = Path(args.config_dir)
    raw_dir, pub_excel, sub_excel = resolve_paths(proj, args.raw_dir)
    processed_dir = Path(args.output_dir) if args.output_dir else (BASE_DIR / proj["output_dir"])
    processed_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}

    print(f"🚀 EoICD 数据处理开始")
    print(f"   项目:     {args.project}")
    print(f"   输入目录: {raw_dir}")
    print(f"   输出目录: {processed_dir}")
    print(f"   执行步骤: {args.step}")

    for f in (pub_excel, sub_excel):
        if f is None:
            continue
        if not f.exists():
            print(f"❌ 输入文件不存在: {f}")
            sys.exit(1)

    if args.step in ("locate", "all"):
        timings.update(step_locate(processed_dir, config_dir, proj, pub_excel, sub_excel))

    if args.step in ("crop", "all"):
        timings.update(step_crop(processed_dir, config_dir, proj, pub_excel, sub_excel))

    if args.step in ("trace", "all"):
        if (config_dir / "traceability.yaml").exists():
            timings.update(step_trace(processed_dir, config_dir, raw_dir))
        else:
            print("\n⚠️  未找到 config/traceability.yaml，跳过追溯步骤")

    if args.step in ("dedup", "all"):
        timings.update(step_dedup(processed_dir, proj))

    print_summary(timings, processed_dir)


if __name__ == "__main__":
    main()
