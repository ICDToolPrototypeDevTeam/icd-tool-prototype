#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EoICD → HLR 正向检查集成脚本
============================
将以下模块串联为一条流水线：
  1. EoICD 侧数据处理（定位 → 裁剪 → 追溯 → 去重）
  2. HLR 需求聚类
  3. EoICD-HLR 身份匹配（支持追溯）
  4. EoICD-HLR 属性匹配（支持追溯）
  5. 结果汇总

用法示例：
  # 完整运行（AMS 项目示例数据）
  python run_integration.py \
    --eoicd-pub "input/ams/AMS_EoICD_Publisher_Table.xlsx" \
    --eoicd-sub "input/ams/AMS_EoICD_Subscriber_Table.xlsx" \
    --hlr "input/ams/空气管理系统控制器控制通道控制软件高层需求规范.docx" \
    --project ams \
    --output-dir "./output"

  最终输出：output/ 下会按本次运行生成独立时间戳子文件夹
    <设备名>EoICD到软件高层需求的落实检查_<时间戳>/
    内含 report_*.json / summary_*.docx / summary_*.xlsx / integration.log，
  每次运行互不覆盖（设备名前缀默认自动识别，亦可用 --device 指定）。

  # 跳过 AI（仅用脚本规则跑通流程）
  python run_integration.py ... --skip-ai

  # 追溯过滤默认自动判断：检测到追溯表输入即自动启用过滤，无需手动设置；
  # 也可用 --use-trace 强制要求使用追溯版（追溯表不可用时告警回退）。
  # 追溯表可放在 EoICD 输入同级目录，或用 --trace-dir "<追溯表目录>" 指定。
  python run_integration.py ... --trace-dir "<追溯表目录>"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 模块级日志文件句柄，由 main() 设置
LOG_FILE: Path | None = None

# ============================ 路径常量 ============================

BASE_DIR = Path(__file__).parent.resolve()

EOICD_PROCESS_DIR = BASE_DIR / "EoICD侧数据处理"
HLR_CLUSTER_DIR = BASE_DIR / "HLR需求聚类" / "hlr_cluster_tool"
MATCH_DIR = BASE_DIR / "EoICD-HLR正向匹配与属性比对_支持追溯"
SUMMARY_DIR = BASE_DIR / "单judge结果汇总"

# 用于调用各子脚本的 Python 解释器。
# 默认使用「运行本脚本的解释器」(sys.executable)，保证子脚本与集成脚本使用同一套依赖；
# 可通过环境变量 PYTHON_BIN 覆盖（例如指向某个虚拟环境的可执行文件）。
# 早期版本曾硬编码 WorkBuddy 托管运行时路径（含本机用户名），已移除，避免换机器失效。
PYTHON = os.environ.get("PYTHON_BIN") or sys.executable

# 让 common/ 公共包可被导入：以脚本方式运行时 sys.path[0] 已是 BASE_DIR，
# 这里再做兜底，保证被当作模块导入时同样能找到 common。
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common import safe_load_json  # noqa: E402  统一 JSON 加载（含字符串安全的 // 注释剥离）
from common import get_project, project_ids, describe  # noqa: E402  统一系统配置（单一数据源）

# 系统 / 项目配置统一由 common.projects.PROJECTS 提供：
# 新增一个系统只需改 common/projects.py，无需再同步修改本脚本与 EoICD 数据处理模块。


# ============================ 工具函数 ============================

def run_cmd(cmd: list[str], desc: str, cwd: Path | str | None = None) -> float:
    """运行命令，失败时抛出 RuntimeError。返回耗时（秒）。
    若设置了全局 LOG_FILE，会同时将子进程输出写入日志文件。"""
    print(f"\n{'=' * 60}")
    print(f"▶ {desc}")
    print(f"  命令: {' '.join(str(c) for c in cmd)}")
    print(f"{'=' * 60}")

    start = time.time()

    if LOG_FILE is not None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as logf:
            process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if process.stdout is not None:
                for line in process.stdout:
                    sys.stdout.write(line)
                    logf.write(line)
            returncode = process.wait()
    else:
        result = subprocess.run(cmd, cwd=cwd, capture_output=False, text=True)
        returncode = result.returncode

    elapsed = time.time() - start

    status = "✅ 成功" if returncode == 0 else "❌ 失败"
    print(f"\n{status} | 耗时: {elapsed:.2f}s")

    if returncode != 0:
        raise RuntimeError(f"{desc} 失败（返回码 {returncode}）")
    return elapsed


def copy_or_skip(src: Path, dst: Path) -> None:
    """复制文件；源不存在时静默跳过（用于可选映射文件）。"""
    if src.exists():
        shutil.copyfile(src, dst)


def _is_safe_to_clean(path: Path) -> bool:
    """只允许清理位于 BASE_DIR 下、名称符合预期的工作目录，避免误删。"""
    try:
        resolved = path.resolve()
        base = BASE_DIR.resolve()
        if not str(resolved).startswith(str(base) + os.sep):
            return False
        if not resolved.is_dir():
            return False
        return resolved.name.startswith(("integration_workspace", "workspace", "output", "output_"))
    except Exception:
        return False


def _copy_supporting_files(src_dirs: list[Path], dst_dir: Path, exclude_names: set[str]) -> None:
    """自动复制追溯表、需求矩阵等配套文件到 raw_dir。"""
    allowed_exts = {".xlsx", ".xlsm", ".xls", ".docx"}
    for src_dir in src_dirs:
        if not src_dir.exists():
            continue
        for item in src_dir.iterdir():
            if item.is_file():
                if item.name in exclude_names:
                    continue
                if item.suffix.lower() in allowed_exts:
                    shutil.copyfile(item, dst_dir / item.name)
            elif item.is_dir() and "追溯" in item.name:
                dst_sub = dst_dir / item.name
                dst_sub.mkdir(parents=True, exist_ok=True)
                for f in item.iterdir():
                    if f.is_file():
                        shutil.copyfile(f, dst_sub / f.name)


# ============================ 工作目录准备 ============================

def prepare_workspace(args: argparse.Namespace) -> Path:
    """创建并准备工作目录，复制输入文件。"""
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    raw_dir = workspace / "data" / "raw"
    eoicd_out = workspace / "eoicd_processed"
    hlr_out = workspace / "hlr_processed"
    match_input = workspace / "data" / "input"
    match_output = workspace / "data" / "output"
    summary_out = workspace / "summary" / "data"

    for d in (raw_dir, eoicd_out, hlr_out, match_input, match_output, summary_out):
        d.mkdir(parents=True, exist_ok=True)

    # 根据项目类型重命名 EoICD 输入文件，以符合 run_data_processing.py 的硬编码期望
    expected = get_project(args.project)
    pub_key = expected.get("pub")
    # 仅当项目登记了 pub 侧且用户提供了 pub 文件时才复制 Publisher 表
    # （subscriber-only 项目无 pub，跳过）
    if pub_key and args.eoicd_pub:
        pub_dst = raw_dir / pub_key
        shutil.copyfile(Path(args.eoicd_pub).resolve(), pub_dst)
    # 仅当项目登记了 sub 侧且用户提供了 sub 文件时才复制 Subscriber 表
    # （fgmc 等仅 Publisher 侧的项目无 sub，跳过）
    sub_key = expected.get("sub")
    if sub_key and args.eoicd_sub:
        sub_dst = raw_dir / sub_key
        shutil.copyfile(Path(args.eoicd_sub).resolve(), sub_dst)

    # HLR 需求文档同时放在 raw_dir（EoICD 追溯步骤会扫描）和 hlr_out
    hlr_src = Path(args.hlr).resolve()
    hlr_raw_dst = raw_dir / hlr_src.name
    shutil.copyfile(hlr_src, hlr_raw_dst)

    # 追溯表/需求矩阵等配套文件（自动从 EoICD 输入目录或 --trace-dir 复制）
    # 仅对应侧文件存在时才纳入 src_dirs / exclude_names（subscriber-only 无 pub 目录）
    src_dirs = []
    if args.eoicd_pub:
        src_dirs.append(Path(args.eoicd_pub).parent)
    if args.eoicd_sub:
        src_dirs.append(Path(args.eoicd_sub).parent)
    if args.trace_dir:
        src_dirs.append(Path(args.trace_dir).resolve())
    exclude_names = {hlr_src.name}
    if args.eoicd_pub:
        exclude_names.add(Path(args.eoicd_pub).name)
    if args.eoicd_sub:
        exclude_names.add(Path(args.eoicd_sub).name)
    _copy_supporting_files(src_dirs, raw_dir, exclude_names)

    return workspace


# ============================ 各阶段调用 ============================

def _load_cluster_count(path: Path) -> int:
    """读取归簇 JSON（list），返回簇数；读取失败/非空异常返回 0。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data) if isinstance(data, list) else 0
    except Exception:
        return 0


def _resolve_traced(traced_path: Path, full_path: Path, force: bool) -> tuple[Path, bool, int]:
    """
    追溯过滤自动决断（每侧独立）：

      返回 (实际使用路径, 是否使用追溯版, 簇数)

    规则：
      - traced 文件存在且簇数 > 0  → 用追溯过滤版（说明有对应追溯表输入且命中）
      - traced 缺失或为空          → 回退完整版（无对应追溯表输入 / 该侧未命中任何项，
                                      即「跳过对应处理」，不静默清零）
      - force=True（显式 --use-trace）但 traced 不可用 → 告警并回退完整版，绝不静默出错
    """
    traced_n = _load_cluster_count(traced_path)
    if traced_n > 0:
        return traced_path, True, traced_n
    if force:
        print(f"⚠️  强制 --use-trace，但 {traced_path.name} 缺失或为空，回退到完整簇")
    return full_path, False, _load_cluster_count(full_path)


def run_eoicd_processing(args: argparse.Namespace, workspace: Path) -> Path:
    """运行 EoICD 侧数据处理模块。"""
    raw_dir = workspace / "data" / "raw"
    output_dir = workspace / "eoicd_processed"
    config_dir = EOICD_PROCESS_DIR / "config"

    cmd = [
        PYTHON, str(EOICD_PROCESS_DIR / "run_data_processing.py"),
        "--project", args.project,
        "--raw-dir", str(raw_dir),
        "--output-dir", str(output_dir),
        "--config-dir", str(config_dir),
        "--step", "all",
    ]
    run_cmd(cmd, "EoICD 数据处理")
    return output_dir


def run_hlr_clustering(args: argparse.Namespace, workspace: Path) -> tuple[Path, Path]:
    """运行 HLR 需求聚类模块，返回 (聚类结果, 去重结果) 路径。"""
    hlr_src = Path(args.hlr).resolve()
    hlr_out = workspace / "hlr_processed"

    # 属性清单复用 EoICD 数据处理模块的裁剪配置
    yaml_path = EOICD_PROCESS_DIR / "config" / "eoicd_crop.yaml"

    cluster_out = hlr_out / "hlr_clustered.json"
    dedup_out = hlr_out / "hlr_dedup.json"

    cmd = [
        PYTHON, str(HLR_CLUSTER_DIR / "cluster_hlr.py"),
        "--docx", str(hlr_src),
        "--yaml", str(yaml_path),
        "--out", str(cluster_out),
        "--dedup-out", str(dedup_out),
    ]

    # 静态映射：命令行指定优先，否则使用模块自带文件。
    # 注意：只要显式传了 --name-map/--state-map/--signal-map 就转发给 cluster_hlr
    # （即使文件尚不存在也无妨——ai_maps 会以空映射加载并在本次运行后回写），
    # 以支持「多 judge 各自的映射缓存隔离」（每个 judge 指向自己工作区的 map 文件，
    # 避免多个 judge 共用模块目录下的 name/state/signal_map.json 造成交叉污染）。
    name_map = Path(args.name_map).resolve() if args.name_map else HLR_CLUSTER_DIR / "name_map.json"
    state_map = Path(args.state_map).resolve() if args.state_map else HLR_CLUSTER_DIR / "state_map.json"
    signal_map = Path(args.signal_map).resolve() if args.signal_map else HLR_CLUSTER_DIR / "signal_map.json"

    if args.name_map:
        cmd.extend(["--name-map", str(name_map)])
    if args.state_map:
        cmd.extend(["--state-map", str(state_map)])
    if args.signal_map:
        cmd.extend(["--signal-map", str(signal_map)])

    run_cmd(cmd, "HLR 需求聚类")
    return cluster_out, dedup_out


def prepare_match_input(
    args: argparse.Namespace,
    workspace: Path,
    eoicd_out: Path,
    cluster_out: Path,
    dedup_out: Path,
) -> None:
    """将 EoICD 处理结果与 HLR 聚类结果复制到匹配模块输入目录。

    追溯过滤「自动判断」（不再依赖人工 --use-trace）：
      - EoICD 数据处理阶段若检测到追溯表输入，会自动产出 *_clustered_traced.json；
      - 本函数据实际产物自动选用：某侧 traced 文件存在且非空 → 用追溯过滤版；
        否则（无对应追溯表输入 / 该侧未命中任何追溯项）→ 回退完整簇（即「跳过对应处理」）。
      - 显式 --use-trace 仍可强制要求使用追溯版（不可用则告警回退，不静默出错）。
    """
    match_input = workspace / "data" / "input"

    full_pub = eoicd_out / "eoicd_pub_clustered.json"
    full_sub = eoicd_out / "eoicd_sub_clustered.json"
    traced_pub = eoicd_out / "eoicd_pub_clustered_traced.json"
    traced_sub = eoicd_out / "eoicd_sub_clustered_traced.json"

    force = bool(args.use_trace)  # 默认 False = 自动检测；显式 --use-trace = 强制追溯版

    pub_src, pub_used, pub_n = _resolve_traced(traced_pub, full_pub, force)
    sub_src, sub_used, sub_n = _resolve_traced(traced_sub, full_sub, force)
    use_traced = pub_used or sub_used

    # 始终复制完整版（作为匹配模块的规范文件名回退），并仅在对应侧使用追溯版时
    # 额外复制 *_traced.json，供下游匹配模块自动检测优先使用。
    # 仅对应侧产物存在时才复制（subscriber-only 项目无 pub 产物，跳过；镜像 sub 守卫）
    if pub_src.exists():
        shutil.copyfile(pub_src, match_input / "eoicd_pub_clustered.json")
        if pub_used:
            shutil.copyfile(pub_src, match_input / "eoicd_pub_clustered_traced.json")
    if sub_src.exists():
        shutil.copyfile(sub_src, match_input / "eoicd_sub_clustered.json")
        if sub_used:
            shutil.copyfile(sub_src, match_input / "eoicd_sub_clustered_traced.json")

    shutil.copyfile(cluster_out, match_input / "hlr_clustered.json")
    shutil.copyfile(dedup_out, match_input / "hlr_dedup.json")

    print(f"\n[追溯过滤] 自动判定（{'强制' if force else '自动检测'}）:")
    if pub_src.exists():
        print(f"   publisher : {'追溯过滤版' if pub_used else '完整版（无对应追溯结果）'}  ({pub_n} 簇)")
    else:
        print(f"   publisher : 无此侧（跳过）")
    if sub_src.exists():
        print(f"   subscriber: {'追溯过滤版' if sub_used else '完整版（无对应追溯结果）'}  ({sub_n} 簇)")
    else:
        print(f"   subscriber: 无此侧（跳过）")


def _load_json_template(path: Path) -> dict:
    """加载 JSON 模板；支持 // 行注释（字符串安全）。

    实现统一收敛到 common.safe_load_json —— 避免在集成脚本与各子模块里
    出现多份「剥离 // 注释」的复制实现（改一处容易漏掉其它处）。
    """
    return safe_load_json(path)


def _strip_api_keys(config: dict) -> None:
    """移除配置中的明文 API Key，强制从环境变量读取，避免写入工作区。"""
    def clear(obj: dict) -> None:
        for key in list(obj.keys()):
            if key == "api_key" and isinstance(obj[key], str):
                obj[key] = ""
            if key == "api_keys" and isinstance(obj[key], list):
                obj[key] = []
            if isinstance(obj[key], dict):
                clear(obj[key])
            elif isinstance(obj[key], list):
                for item in obj[key]:
                    if isinstance(item, dict):
                        clear(item)
    clear(config)


def _inject_judge_parallel_keys(config: dict) -> None:
    """多 judge 编排支持：从环境变量 JUDGE_PARALLEL_KEYS 注入本 judge 的并行多 key。

    编排器(multi_judge_runner.py)在启动每个 judge 子进程时，可把该 judge 的并行 key
    列表以逗号分隔写入 JUDGE_PARALLEL_KEYS 环境变量；此处读取并填入所有含 parallel 段
    的配置节（ai_matching / ai_review），实现「judge 内部多 key 轮询吞吐」的 per-judge
    隔离。未设置该变量时保持空列表（沿用模板默认行为，回退主 key / 串行）。
    """
    raw = os.environ.get("JUDGE_PARALLEL_KEYS")
    if not raw:
        return
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        return
    for section in ("ai_matching", "ai_review"):
        if section in config and isinstance(config[section], dict):
            config[section].setdefault("parallel", {})["api_keys"] = keys


def generate_match_config(workspace: Path, skip_ai: bool, sides: list[str] | None = None) -> Path:
    """基于模板生成身份匹配配置文件（全部使用绝对路径）。

    sides: 该项目实际存在的信号侧（来自 common/projects.py）；默认 None 表示不感知、
           按双侧处理。仅当含 "subscriber" 时才写入 eoicd_sub 路径，否则置空，
           下游身份匹配模块按 --direction 跳过 sub。
    """
    template = MATCH_DIR / "identity_match" / "config" / "match_config.json"
    config = _load_json_template(template)

    # 仅当项目存在 publisher 侧时才配置 eoicd_pub 路径；
    # 仅 Subscriber 侧的项目无 publisher 输入，置空，下游模块按 --direction 跳过
    if sides is None or "publisher" in sides:
        config["paths"]["eoicd_pub"] = str(workspace / "data" / "input" / "eoicd_pub_clustered.json")
    else:
        config["paths"]["eoicd_pub"] = ""
    # 仅当项目存在 subscriber 侧时才配置 eoicd_sub 路径；
    # 仅 Publisher 侧的项目（如 fgmc）无 subscriber 输入，置空，下游模块按 --direction 跳过
    if sides is None or "subscriber" in sides:
        config["paths"]["eoicd_sub"] = str(workspace / "data" / "input" / "eoicd_sub_clustered.json")
    else:
        config["paths"]["eoicd_sub"] = ""
    config["paths"]["hlr_dedup"] = str(workspace / "data" / "input" / "hlr_dedup.json")
    config["paths"]["hlr"] = str(workspace / "data" / "input" / "hlr_clustered.json")
    config["paths"]["output_dir"] = str(workspace / "data" / "output" / "identity_match")
    config["paths"]["intermediate_dir"] = str(workspace / "data" / "output" / "identity_match" / "intermediate")
    config["paths"]["rejected_dir"] = str(workspace / "data" / "output" / "identity_match" / "rejected")

    if skip_ai:
        config["ai_matching"]["enabled"] = False

    _strip_api_keys(config)
    _inject_judge_parallel_keys(config)

    config_path = workspace / "match_config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return config_path


def generate_attr_config(workspace: Path, skip_ai: bool, sides: list[str] | None = None) -> Path:
    """基于模板生成属性匹配配置文件（全部使用绝对路径）。

    sides 语义同 generate_match_config：仅含 subscriber 侧时写 input_sub，否则置空。
    """
    template = MATCH_DIR / "attribute_match" / "config" / "attribute_match_config.json"
    config = _load_json_template(template)

    # 仅当项目存在 publisher 侧时才配置 input_pub；仅 Subscriber 侧项目置空
    if sides is None or "publisher" in sides:
        config["paths"]["input_pub"] = str(workspace / "data" / "output" / "identity_match" / "match_report_pub.json")
    else:
        config["paths"]["input_pub"] = ""
    # 仅当项目存在 subscriber 侧时才配置 input_sub；仅 Publisher 侧项目置空
    if sides is None or "subscriber" in sides:
        config["paths"]["input_sub"] = str(workspace / "data" / "output" / "identity_match" / "match_report_sub.json")
    else:
        config["paths"]["input_sub"] = ""
    config["paths"]["input_hlr"] = str(workspace / "data" / "input" / "hlr_clustered.json")
    config["paths"]["output_dir"] = str(workspace / "data" / "output" / "attribute_match")
    config["paths"]["intermediate_dir"] = str(workspace / "data" / "output" / "attribute_match" / "intermediate")

    if skip_ai:
        config["ai_review"]["enabled"] = False

    _strip_api_keys(config)
    _inject_judge_parallel_keys(config)

    config_path = workspace / "attribute_match_config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return config_path


def run_identity_match(args: argparse.Namespace, workspace: Path) -> None:
    """运行身份匹配模块。"""
    proj = get_project(args.project)
    sides = proj.get("sides", ["publisher", "subscriber"])
    config_path = generate_match_config(workspace, args.skip_ai, sides)

    cmd = [
        PYTHON, str(MATCH_DIR / "identity_match" / "run_identity_match.py"),
        "--config", str(config_path),
        # 方向按 sides 对称决定：仅 sub → sub；仅 pub → pub；pub+sub → both
        "--direction", ("sub" if "publisher" not in sides else ("pub" if "subscriber" not in sides else "both")),
    ]
    if args.skip_ai:
        cmd.append("--skip-ai")
    if args.match_step != "all":
        cmd.extend(["--step", args.match_step])

    run_cmd(cmd, "身份匹配")


def run_attribute_match(args: argparse.Namespace, workspace: Path) -> None:
    """运行属性匹配模块。"""
    proj = get_project(args.project)
    sides = proj.get("sides", ["publisher", "subscriber"])
    config_path = generate_attr_config(workspace, args.skip_ai, sides)

    cmd = [
        PYTHON, str(MATCH_DIR / "attribute_match" / "run_attribute_match.py"),
        "--config", str(config_path),
    ]
    # 方向按 sides 对称：仅 sub → sub；仅 pub → pub；pub+sub 不传（模块默认 both）
    if "publisher" not in sides:
        cmd.extend(["--direction", "sub"])
    elif "subscriber" not in sides:
        cmd.extend(["--direction", "pub"])
    if args.skip_ai:
        cmd.append("--skip-ai")

    run_cmd(cmd, "属性匹配")


def prepare_summary_input(args: argparse.Namespace, workspace: Path) -> None:
    """将属性匹配报告、身份识别 rejected 文件和 HLR 需求文档复制到汇总模块输入目录。"""
    summary_input = workspace / "summary" / "input"
    summary_input.mkdir(parents=True, exist_ok=True)

    attr_out = workspace / "data" / "output" / "attribute_match"
    rejected_dir = workspace / "data" / "output" / "identity_match" / "rejected"

    # 仅当 publisher 侧属性匹配报告存在时才复制（仅 Subscriber 侧项目无 pub 报告）
    pub_report = attr_out / "attribute_match_report_pub.json"
    if pub_report.exists():
        shutil.copyfile(pub_report, summary_input / "attribute_match_report_pub.json")
    else:
        print(f"  [跳过] 无 publisher 侧属性匹配报告（{pub_report.name}），仅汇总 subscriber 侧")
    # 仅当 subscriber 侧属性匹配报告存在时才复制（仅 Publisher 侧项目无 sub 报告）
    sub_report = attr_out / "attribute_match_report_sub.json"
    if sub_report.exists():
        shutil.copyfile(sub_report, summary_input / "attribute_match_report_sub.json")
    else:
        print(f"  [跳过] 无 subscriber 侧属性匹配报告（{sub_report.name}），仅汇总 publisher 侧")

    # 完整性兜底需要 rejected 文件
    rejected_out = summary_input / "rejected"
    rejected_out.mkdir(parents=True, exist_ok=True)
    if rejected_dir.exists():
        for f in rejected_dir.iterdir():
            if f.is_file() and f.name.startswith("rejected_"):
                shutil.copyfile(f, rejected_out / f.name)

    # 新版 summary.py 从 input/ 下识别『高层需求规范』.docx 自动提取设备名
    hlr_src = Path(args.hlr).resolve()
    shutil.copyfile(hlr_src, summary_input / hlr_src.name)


def run_summary(args: argparse.Namespace, workspace: Path) -> Path:
    """复制并运行结果汇总模块；返回本次运行生成的时间戳子目录。

    --device 用于指定输出文件夹的设备名前缀。若不指定，summary.py 会尝试从
    input 下名称含『高层需求规范』的 .docx 自动识别；像 EPS 的 HLR 文件名
    （RPDU_HLR未注入故障v1.docx）不含该关键字时，识别结果为空、文件夹会缺少
    设备名前缀，此时应显式传入 --device。
    """
    summary_dir = workspace / "summary"
    summary_input = summary_dir / "input"
    summary_out = summary_dir / "data"

    # 复制汇总脚本与配置到工作目录，使其以 workspace 为基准解析 input/rejected 路径
    shutil.copyfile(SUMMARY_DIR / "summary.py", summary_dir / "summary.py")
    shutil.copyfile(SUMMARY_DIR / "config_summary.json", summary_dir / "config_summary.json")

    cmd = [
        PYTHON, str(summary_dir / "summary.py"),
        "--config", str(summary_dir / "config_summary.json"),
        "--out", str(summary_out),
    ]
    # 仅传入实际存在的属性匹配报告；summary.py 自身也会跳过缺失文件，
    # 但显式按需传入可避免把不存在的 sub 报告路径塞进去
    pub_match = summary_input / "attribute_match_report_pub.json"
    sub_match = summary_input / "attribute_match_report_sub.json"
    if pub_match.exists():
        cmd.extend(["--match", str(pub_match)])
    if sub_match.exists():
        cmd.extend(["--match", str(sub_match)])
    if args.device:
        cmd.extend(["--device", args.device])

    run_cmd(cmd, "单judge结果汇总", cwd=str(summary_dir))

    # 新版 summary.py 在 --out 下生成 '<设备名>EoICD到软件高层需求的落实检查_<时间戳>' 子文件夹
    run_dirs = sorted(
        [d for d in summary_out.iterdir() if d.is_dir() and "EoICD到软件高层需求的落实检查_" in d.name],
        key=lambda d: d.stat().st_mtime,
    )
    if not run_dirs:
        raise RuntimeError("未找到结果汇总生成的输出子文件夹")
    return run_dirs[-1]


def copy_final_outputs(run_dir: Path, output_dir: str) -> list[str]:
    """将汇总结果从本次运行的时间戳子文件夹归档到最终输出目录下的同名时间戳子文件夹。

    输出目录结构示例：
      output/
        <设备名>EoICD到软件高层需求的落实检查_<时间戳>/
          report_pub.json   / report_sub.json
          summary_pub.docx  / summary_pub.xlsx
          summary_sub.docx  / summary_sub.xlsx

    汇总模块每次运行都会生成新的时间戳文件夹（run_dir.name），这里按同名归档，
    保证多次运行各自独立、互不覆盖；integration.log 由 main() 一并放入该子文件夹。
    """
    # 在 output/ 下按本次运行的时间戳子文件夹归档，而不是平铺到 output/ 根
    dst = Path(output_dir).resolve() / run_dir.name
    dst.mkdir(parents=True, exist_ok=True)

    candidates = [
        "report_pub.json", "report_sub.json",
        "summary_pub.docx", "summary_pub.xlsx",
        "summary_sub.docx", "summary_sub.xlsx",
    ]

    copied: list[str] = []
    for name in candidates:
        src = run_dir / name
        if src.exists():
            shutil.copyfile(src, dst / name)
            copied.append(name)

    return copied


# ============================ 主入口 ============================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="EoICD → HLR 正向检查集成脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run_integration.py \\
    --eoicd-pub "input/ams/AMS_EoICD_Publisher_Table.xlsx" \\
    --eoicd-sub "input/ams/AMS_EoICD_Subscriber_Table.xlsx" \\
    --hlr "input/ams/空气管理系统控制器控制通道控制软件高层需求规范.docx" \\
    --project ams \\
    --output-dir "./output"
""",
    )
    parser.add_argument("--eoicd-pub", required=False, default=None,
                        help="EoICD Publisher Excel 文件路径（仅 Publisher 侧项目可不传）")
    parser.add_argument("--eoicd-sub", required=False, default=None,
                        help="EoICD Subscriber Excel 文件路径（仅 Publisher 侧的项目如 fgmc 可不传）")
    parser.add_argument("--hlr", required=True, help="软件高层需求 .docx 文件路径")
    parser.add_argument("--project", choices=project_ids(), default="ams",
                        help="系统/项目标识（默认 ams）。可选：" + describe())
    parser.add_argument("--device", default=None,
                        help="设备名前缀（结果汇总输出文件夹的 XXX 部分）。默认由汇总模块从 "
                             "input 下含『高层需求规范』的 docx 自动识别；若 HLR 文件名不含该关键字"
                             "（如 EPS 的 RPDU_HLR未注入故障v1.docx），请显式指定，例：--device 配电装置RPDU")
    parser.add_argument("--output-dir", default=str(BASE_DIR / "output"), help="最终输出根目录（默认 ./output，与模块平级）。每次运行会在其下生成 "
                        "独立的 '<设备名>EoICD到软件高层需求的落实检查_<时间戳>' 子文件夹归档全部结果，多次运行互不覆盖。")
    parser.add_argument("--workspace", default=str(BASE_DIR / "runs" / "integration_workspace"), help="工作目录（默认 ./runs/integration_workspace）")
    parser.add_argument("--skip-ai", action="store_true", help="跳过 AI 调用（纯脚本模式）")
    parser.add_argument("--use-trace", action="store_true",
                        help="强制使用追溯过滤后的 EoICD 簇（*_clustered_traced.json）。"
                             "默认不传=自动判断：检测到追溯表输入且产出有效 traced 结果则自动启用，"
                             "否则回退完整簇（无需手动设置）")
    parser.add_argument("--trace-dir", help="追溯表所在目录（可选，其下文件会被复制到 raw_dir）")
    parser.add_argument("--name-map", help="HLR 静态信号名映射文件路径（可选，需预先用 AI 或人工生成）")
    parser.add_argument("--state-map", help="HLR 静态状态定义映射文件路径（可选，需预先用 AI 或人工生成）")
    parser.add_argument("--signal-map", help="HLR 静态信号级拆分映射文件路径（可选，需预先用 AI 或人工生成）")
    parser.add_argument("--match-step", choices=["all", "bus_label_bit", "direction", "name", "report"], default="all", help="身份匹配步骤（默认 all）")
    parser.add_argument("--step", choices=["all", "eoicd", "hlr", "identity", "attribute", "summary"], default="all", help="执行步骤（默认 all）")
    parser.add_argument("--steps", default=None,
                        help="逗号分隔的多个阶段，例如 'hlr,identity,attribute,summary'（用于多 judge 编排时跳过阶段1）。"
                             "与 --step 互斥，二选一；含 'all' 等同全部。")
    parser.add_argument("--clean", action="store_true", help="运行前清理工作目录（现已默认开启，保留以兼容旧命令）")
    parser.add_argument("--no-clean", action="store_true", help="运行前不清空工作目录（默认会先清空旧 workspace 再重写）")
    args = parser.parse_args()

    total_start = time.time()
    # run_dir 在「结果汇总」阶段生成；声明在此，便于成功/异常两条路径统一归档日志
    run_dir: Path | None = None

    try:
        if not args.no_clean:
            workspace_path = Path(args.workspace).resolve()
            if workspace_path.exists():
                if _is_safe_to_clean(workspace_path):
                    print(f"🧹 清理工作目录: {args.workspace}")
                    shutil.rmtree(workspace_path)
                else:
                    print(f"⚠️  拒绝清理非工作目录: {args.workspace}")
                    print("    --clean 只允许清理位于脚本目录下、名称以 integration_workspace/workspace/output 开头的目录。")
                    return 1

        global LOG_FILE
        LOG_FILE = Path(args.workspace).resolve() / "integration.log"
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
        )

        print("=" * 60)
        print("🚀 EoICD → HLR 正向检查集成流程")
        print("=" * 60)
        print(f"   Python: {PYTHON}")
        print(f"   项目:   {args.project}")
        print(f"   工作区: {args.workspace}")
        print(f"   输出:   {args.output_dir}")
        print(f"   AI:     {'禁用' if args.skip_ai else '启用'}")
        print(f"   追溯:   {'强制使用追溯版' if args.use_trace else '自动检测（有追溯表输入则启用，否则用完整簇）'}")

        workspace = prepare_workspace(args)

        # ---- 计算本次活动阶段集合（支持 --step 单值 或 --steps 组合）----
        # 多 judge 编排器(multi_judge_runner.py)会以 --steps hlr,identity,attribute,summary
        # 调用本脚本，复用阶段1的共享产物、只让本 judge 跑 2-5 阶段。
        if getattr(args, "steps", None):
            active = {s.strip() for s in args.steps.split(",") if s.strip()}
            if "all" in active:
                active = {"eoicd", "hlr", "identity", "attribute", "summary"}
        else:
            active = {"eoicd", "hlr", "identity", "attribute", "summary"} if args.step == "all" else {args.step}

        # ---- 阶段 1: EoICD 数据处理 ----
        if "eoicd" in active:
            eoicd_out = run_eoicd_processing(args, workspace)
        else:
            eoicd_out = workspace / "eoicd_processed"

        # ---- 阶段 2: HLR 需求聚类 ----
        if "hlr" in active:
            cluster_out, dedup_out = run_hlr_clustering(args, workspace)
        else:
            cluster_out = workspace / "hlr_processed" / "hlr_clustered.json"
            dedup_out = workspace / "hlr_processed" / "hlr_dedup.json"

        # ---- 阶段 3: 身份匹配 ----
        if "identity" in active:
            prepare_match_input(args, workspace, eoicd_out, cluster_out, dedup_out)
            run_identity_match(args, workspace)

        # ---- 阶段 4: 属性匹配 ----
        if "attribute" in active:
            run_attribute_match(args, workspace)

        # ---- 阶段 5: 结果汇总 ----
        if "summary" in active:
            prepare_summary_input(args, workspace)
            run_dir = run_summary(args, workspace)
            final_out_dir = Path(args.output_dir).resolve() / run_dir.name
            copied = copy_final_outputs(run_dir, args.output_dir)
            print(f"\n📁 最终输出已复制到: {final_out_dir}")
            for name in copied:
                print(f"  - {name}")

        total_time = time.time() - total_start
        print(f"\n{'=' * 60}")
        print(f"✅ 集成流程全部完成！总耗时: {total_time:.2f}s")
        print(f"{'=' * 60}")

        # 运行日志随本次结果一并归档到时间戳子文件夹（仅当本次生成了汇总子文件夹时）
        if LOG_FILE and LOG_FILE.exists():
            if run_dir is not None:
                log_dst = Path(args.output_dir).resolve() / run_dir.name / "integration.log"
            else:
                log_dst = Path(args.output_dir).resolve() / "integration.log"
            log_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(LOG_FILE, log_dst)
            print(f"📄 运行日志已保存: {log_dst}")

        return 0

    except Exception as e:
        print(f"\n{'=' * 60}")
        print(f"❌ 集成流程失败: {e}")
        print(f"{'=' * 60}")
        import traceback
        traceback.print_exc()
        logging.error("集成流程失败: %s", e, exc_info=True)
        if LOG_FILE and LOG_FILE.exists():
            # 若本次已生成汇总子文件夹，日志随结果归档到同名时间戳子文件夹
            if run_dir is not None:
                dst_log = Path(args.output_dir).resolve() / run_dir.name / "integration.log"
            else:
                dst_log = Path(args.output_dir).resolve() / "integration.log"
            dst_log.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(LOG_FILE, dst_log)
            print(f"📄 运行日志已保存: {dst_log}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
