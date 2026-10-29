# -*- coding: utf-8 -*-
"""
multi_judge_runner.py — 正向检查 · 多 judge 并发编排器（嫁接反向检查多智能体内核）

把「反向检查 Step4 并发裁判组 + 结果层聚合」嫁接到正向检查集成工程：

  - judge = 一次完整正向检查运行（阶段1-5）。不同 judge 用不同 (model, base_url, api_key)，
    也可多个 judge 共用一套（一个模型跑多次，现有「多 key 轮询」内部吞吐机制保持不变）。
  - 阶段1 EoICD 数据处理（确定性、无 AI）：所有 judge 共享跑一次，产物 copy 给各 judge。
  - 阶段2/3/4（均调 AI）：每个 judge 在独立子进程 + 独立 env + 独立工作区 + 独立 map 缓存
    各自跑一遍（per-judge）。
  - 阶段5：每个 judge 产出 report_{pub,sub}.json；编排器按 signal_full_name 对齐聚合
    （多数投票 + 两维共识 + 分歧/覆盖缺口，见 multi_judge_aggregate.py）。

设计要点（详见 正向检查多judge嫁接方案.md）：
  - 子进程隔离：每 judge 一次 run_integration.py 子进程，AI_MODEL/BASE_URL/API_KEY 通过
    子进程 env 注入，自动穿透到阶段2(cluster_hlr 子进程)与阶段3/4（进程内调用）。
  - 超时/迟到补收：不 kill 超时 judge，由后台线程继续跑、统一补收（对应反向 Step4.5）。
  - 工作区/缓存隔离：每 judge 独立 --workspace / --output-dir / --name-map 等。
  - 退化兼容：judges 配 1 个 = 现有单 judge 行为。

用法：
  python multi_judge_runner.py --config multi_judge_config.json
  python multi_judge_runner.py --config multi_judge_config.json --judge-timeout 3600
  python multi_judge_runner.py --config multi_judge_config.json --only-stage1   # 仅跑共享阶段1

环境变量：配置里只存 env 变量名（api_key_env 等），真实密钥在运行环境解析，不入库。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# 强制 stdout/stderr 用 UTF-8（errors=replace 保证永不因编码崩溃），
# 兜底防止老旧子进程/外部脚本仍有遗留的彩色 emoji 或半角符号，runner 自身已统一 ASCII。
# 不依赖外部 PYTHONIOENCODING 是否生效。
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # 写进 os.environ：所有派生子进程（stage1 链 run_integration.py / judge 链）自动继承，
    # 其 stdout 走管道时也会用 UTF-8，不再因 emoji 崩溃。
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
except Exception:
    pass

# 复用工程内 common 包（safe_load_json 支持 // 注释）
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# 多 judge 子模块统一收拢到 multi_judge协同/ 目录；把该目录加入 sys.path，
# 使留在根目录的 multi_judge_runner.py 仍可用裸模块名 import（子模块内部互相 import 不改）。
MULTI_JUDGE_DIR = BASE_DIR / "multi_judge协同"
if str(MULTI_JUDGE_DIR) not in sys.path:
    sys.path.insert(0, str(MULTI_JUDGE_DIR))

from multi_judge_aggregate import build_shard_from_reports, aggregate, write_outputs  # noqa: E402
from multi_judge_arbitrator import (  # noqa: E402  # 可选 Step5/5.5 LLM 仲裁层
    run_arbitration,
    get_token_total,
    reset_token_usage,
)
from multi_judge_summary import build_summary, format_summary_text  # noqa: E402
from multi_judge_report import generate_report  # noqa: E402  # Word 共识报告（缺 python-docx 时优雅跳过）
from gen_excel_report import generate_excel_report  # noqa: E402  # Excel 共识报告（缺 openpyxl 时优雅跳过）

PYTHON = os.environ.get("PYTHON_BIN") or sys.executable

# 超时 / 迟到补收可调参数（可用环境变量临时压小，便于测试或紧急场景）
# SOFT_GRACE：每个 judge 软预算之外的宽限（秒），用于迟到补收。
SOFT_GRACE = int(os.environ.get("MULTI_JUDGE_SOFT_GRACE", "300"))
# HARD_GRACE：软上限之后再给的宽限（秒），宽限结束仍存活的子进程视为卡死并 terminate。
HARD_GRACE = int(os.environ.get("MULTI_JUDGE_HARD_GRACE", "120"))

# 阶段5 输出目录名提示（summary.py 产出的目录前缀）。改名后由「含 report 文件」兜底，
# 不依赖硬编码名称；也可经 env MULTI_JUDGE_SUMMARY_HINT 调整。
SUMMARY_FOLDER_HINT = os.environ.get("MULTI_JUDGE_SUMMARY_HINT", "EoICD到软件高层需求的落实检查_")

# Word 共识报告文件名（对齐反向 consensus_docx 的定位：产物固定名、可被平台按名校验）
REPORT_FILE_NAME = os.environ.get(
    "MULTI_JUDGE_REPORT_NAME", "EoICD至HLR正向一致性多模型共识分析报告.docx")


# ---------------------------------------------------------------------------
# Token 用量 sidecar（子进程契约，见 shared/utils.py 与 cluster_hlr.py）
# ---------------------------------------------------------------------------
# run_integration.generate_match_config / generate_attr_config 把两个阶段的
# output_dir 固定在 <judge_ws>/data/output/{identity_match,attribute_match}；
# 两个入口脚本在 atexit 时把本进程累计 token 写到该目录下的 token_usage.json。
# V3 起每个 judge 还会用 --ai-update 生成 name/state/signal 映射，cluster_hlr
# 把该进程的 token 写到 <judge_ws>/ai_update_token_usage.json。
# 只扫这些确定路径（不递归），避免把历史残留文件算进来。

_TOKEN_SIDECAR_PARTS = (
    ("data", "output", "identity_match", "token_usage.json"),
    ("data", "output", "attribute_match", "token_usage.json"),
    ("ai_update_token_usage.json",),
)


def _token_sidecar_paths(judge_ws: Path) -> list[Path]:
    return [Path(judge_ws).joinpath(*parts) for parts in _TOKEN_SIDECAR_PARTS]


def _read_token_sidecars(judge_ws: Path) -> int:
    """汇总一个 judge 各阶段的 token；缺失 / 损坏按 0。"""
    total = 0
    for path in _token_sidecar_paths(judge_ws):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            value = data.get("total_tokens") if isinstance(data, dict) else data
            if value is None or isinstance(value, bool):
                continue
            total += max(0, int(value))
        except Exception:  # noqa: BLE001 — 单个 sidecar 坏了不影响其它统计
            continue
    return total


def _clear_token_sidecars(judge_ws: Path) -> None:
    """启动 judge 前清掉旧 sidecar：复用工作区续跑时防止上一轮数值重复计数。"""
    for path in _token_sidecar_paths(judge_ws):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001 — 清理失败不影响主流程
            pass


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _hash_key(key: str) -> str:
    """把真实 api_key 哈希成短串用于元数据追溯（不落明文）。"""
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _resolve_ai_field(cfg_val, env_name):
    """单 judge 风格：config 字面量优先；缺省或 ${ENV} 形式时从环境读取。

    让多 judge 的 model/base_url 也能像单 judge 那样放在 .env（.env.multijudge）里，
    而 config 只需写 name + api_key_env（api_key_env 也可省略回落 API_KEY）。
    """
    if not cfg_val:
        return os.environ.get(env_name, "")
    if isinstance(cfg_val, str) and cfg_val.startswith("${") and cfg_val.endswith("}"):
        return os.environ.get(cfg_val[2:-1], "")
    return cfg_val


def _normalize_ai_config(cfg: dict) -> None:
    """原地解析各 judge 与 arbitrator 的 model/base_url（支持 ${ENV} 与缺省回落环境）。

    在 load_config 之后执行一次，保证 env 注入、同源判定(_view_identity)、
    仲裁回落(_resolve_arbitrator_cfg) 三处读到的都是解析后的真值，不会因 config 写
    ${AI_MODEL} 或留空而失真。现有带字面量的 config 行为完全不变（字面量原样返回）。
    """
    for j in cfg.get("judges") or []:
        j["model"] = _resolve_ai_field(j.get("model"), "AI_MODEL")
        j["base_url"] = _resolve_ai_field(j.get("base_url"), "BASE_URL")
    arb = cfg.get("arbitrator")
    if isinstance(arb, dict):
        # 仅当 arbitrator 显式给了 model/base_url 才解析；未给则交给 _resolve_arbitrator_cfg 回落 judge[0]
        if arb.get("model"):
            arb["model"] = _resolve_ai_field(arb.get("model"), "AI_MODEL")
        if arb.get("base_url"):
            arb["base_url"] = _resolve_ai_field(arb.get("base_url"), "BASE_URL")


def _resolve(p: str, base: Path) -> str:
    pp = Path(p)
    return str(pp if pp.is_absolute() else (base / pp))


def _latest_summary_folder(ws: Path) -> Path | None:
    """定位本 judge 的阶段5 输出目录。

    判定策略（稳健，不依赖硬编码目录名）：
      1. 优先选择 *包含 report_pub.json 或 report_sub.json* 的目录（与 summary.py 实际产出解耦）；
      2. 目录名含 SUMMARY_FOLDER_HINT 前缀作为次要偏好（仅当多个候选时区分同名输出）；
      3. 最终按 mtime 取最新，避免 summary.py 改名后静默空分片。
    返回 None 仅当 data/ 不存在或没有任何候选目录（调用方据此标 error 并告警）。
    """
    data = ws / "summary" / "data"
    if not data.exists():
        return None
    candidates: list[tuple[Path, bool, bool]] = []
    for p in data.iterdir():
        if not p.is_dir():
            continue
        has_report = (p / "report_pub.json").exists() or (p / "report_sub.json").exists()
        hinted = SUMMARY_FOLDER_HINT in p.name
        if has_report or hinted:
            candidates.append((p, has_report, hinted))
    if not candidates:
        return None
    # 排序：含报告文件 > 仅 hint 命中；同层级按 mtime 倒序
    candidates.sort(key=lambda x: (not x[1], not x[2], -x[0].stat().st_mtime))
    return candidates[0][0]


# ---------------------------------------------------------------------------
# 工作区清理：白名单式（保留 AI map 缓存，其余全部重置）
# ---------------------------------------------------------------------------
# 2026-10-16：run_integration 以 --no-clean 运行（为复用 AI map 缓存），而它的
# prepare_workspace() 只"建目录 + 复制"、从不清理 ⇒ 换分析对象后，上一轮的输入
# （EoICD 表 / 追溯表 / HLR 文档）与产物（eoicd_processed/*_Located.xlsx、
# output/<设备名…时间戳>/、summary/data/…）持续累积，并被本轮误用。已证后果：
#   ① 上一轮追溯表/HLR 被当作本轮输入喂入；
#   ② eoicd_processed/ 里残留上一轮 *_Located.xlsx；
#   ③ summary.py 的 _detect_device_name() 扫 raw 里的 *.docx 取设备名 ⇒ 本轮输出
#      目录被命名成上一轮的设备名（实测 eps 轮次出现上一轮 ams 的设备名）；
#   ④ 历史 output/summary/data 目录累积，异常时 _latest_summary_folder 可能回落取旧。
# 因此每个工作区在**开始前**重置为"只剩 map 缓存"。
# 安全边界：只遍历 ws 自身、不递归兄弟目录；且 ws 名必须是 shared 或 judge_*。
KEEP_IN_WORKSPACE = {"name_map.json", "state_map.json", "signal_map.json"}


def _clean_workspace(ws: Path, keep_names: set[str]) -> None:
    """把工作区重置为"只保留 keep_names 里的文件"，其余（子目录 + 文件）全删。

    keep_names 传空集即整目录清空（用于 shared/，阶段1 不产 map）。
    失败不中断（单文件删不掉仅告警，交由后续覆盖）。
    """
    if not ws.exists() or not ws.is_dir():
        return
    if ws.name != "shared" and not ws.name.startswith("judge_"):
        print(f"  [WARN] 拒绝清理非预期工作区目录：{ws}")
        return
    removed = 0
    for item in ws.iterdir():
        if item.is_symlink():
            try:
                item.unlink()
                removed += 1
            except OSError:
                pass
            continue
        if item.is_file() and item.name in keep_names:
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
            removed += 1
        except OSError as e:
            print(f"  [WARN] 清理失败（跳过）：{item.name}（{e}）")
    kept = sorted(keep_names) if keep_names else []
    print(f"  [CLEAN] 工作区重置：{ws.name}（移除 {removed} 项，保留 {kept or '无'}）")


def _reset_run_area(root: Path) -> None:
    """清理 runs/integration_workspace 下**顶层**的每轮重生成内容：logs/ 与聚合产物。

    judge_*/ 与 shared/ 不在此处理（它们要按白名单保留 AI map 缓存，见 _clean_workspace）。
    """
    logs = root / "logs"
    if logs.is_dir():
        shutil.rmtree(logs, ignore_errors=True)
        print("  [CLEAN] 已清空运行日志目录：logs/")
    for name in ("multi_judge_aggregate.json", "multi_judge_aggregate.csv",
                 "multi_judge_review_stats.json", "multi_judge_summary.json"):
        p = root / name
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# AI map 缓存健康检查（仅告警，不阻断；与 ai_maps.py 写入端的 generation 字段配套）
# ---------------------------------------------------------------------------
# 目的：每 judge 启动前自检本 judge 目录的 3 个 map（name / state / signal），
# 防止「judge_C signal_map.json 缺失」一类「半成品」被静默使用。
# 4 种状态分级（用户 2026-10-18 复盘，align 选项 A）：
#   [WARN] 缺失                文件不存在
#   [WARN] JSON 解析失败       文件存在但内容损坏
#   [WARN] 半成品              有 generation 但 name(entry<covered) — 真"半成品"
#   [WARN] 老格式且不齐全      无 generation 且 name(entry<covered) — 老 map + 数据也不全
#   [INFO] 老格式数据齐全      无 generation 但 name(entry≥covered) — P1.2 改动前的旧产物
#   [MAP]  健康                有 generation 且 name(entry≥covered)
# 注意：state/signal 是 partial 覆盖（AI 没识别到就不写），不参与 entry vs covered
# 的"半成品"判据；它们的 entry<covered 是设计预期，视为"齐全"。
# 不阻断：本函数是诊断性，不替换 load_all_maps 的兜底链（缺文件时仍可走正则）。
def _check_map_health(judge_ws: Path, label: str) -> None:
    """扫本 judge 目录的 3 个 map，按 4 种状态分级输出：

    - 缺失 / JSON 解析失败 / 半成品 / 老格式且不齐全 → [WARN]
    - 老格式但数据齐全（仅缺元数据）                  → [INFO]
    - 有 generation 且数据齐全                         → [MAP]

    不解析 HLR 文档，纯文件层；不阻断流程。
    """
    warns: list[str] = []
    infos: list[str] = []
    maps: list[str] = []
    for fname, kind in (("name_map.json", "name"),
                        ("state_map.json", "state"),
                        ("signal_map.json", "signal")):
        p = judge_ws / fname
        if not p.exists():
            warns.append(f"{kind}: 缺失")
            continue
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            warns.append(f"{kind}: JSON 解析失败（{e.__class__.__name__}）")
            continue
        meta = raw.get("__meta__") or {}
        gen = meta.get("generation")
        covered = meta.get("covered_reqs") or []
        n_entries = sum(1 for k in raw if not str(k).startswith("__"))
        cov_n = len(covered)
        gen_str = str(gen) if gen is not None else "?"
        is_legacy = gen is None or int(gen or 0) <= 0
        # 仅 name 是 full 覆盖，entry<covered 才算"数据不齐全"
        is_incomplete = (kind == "name") and n_entries < cov_n
        if is_legacy and is_incomplete:
            warns.append(
                f"{kind}: 老格式且不齐全 gen={gen_str} entry={n_entries} covered={cov_n}")
        elif is_incomplete:
            warns.append(
                f"{kind}: 半成品 gen={gen_str} entry={n_entries} covered={cov_n}")
        elif is_legacy:
            infos.append(
                f"{kind}: 老格式（无 generation 字段）entry={n_entries} covered={cov_n}")
        else:
            maps.append(
                f"{kind}: gen={gen_str} entry={n_entries} covered={cov_n}")
    if warns:
        print(f"  [WARN] {label} AI map 健康检查：{' | '.join(warns)}")
    if infos:
        print(f"  [INFO] {label} AI map 健康检查：{' | '.join(infos)}")
    if maps:
        print(f"  [MAP]  {label} AI map 健康检查：{' | '.join(maps)}")


# ---------------------------------------------------------------------------
# 子进程输出过滤式 tee（关键步骤回显 console，全量进日志）
# ---------------------------------------------------------------------------
# 2026-10-15：用户要查看每个 judge 的过程，但子模块逐信号 print 仍只进日志；
# 用正则匹配 6 类关键行（阶段开始 / 阶段成功失败 / 流程启动 / 流程完成 / 告警 / 清理），
# 写日志时同时 echo 到 console。子模块逐信号 print 仍只进日志，不刷屏。
# 注意：子进程模块的 emoji 已统一换成 ASCII 标记（> / [OK] / [FAIL] / [START] / [WARN] / [CLEAN]），
# 故此处正则同步改为匹配 ASCII 标记（否则关键行匹配不上、不再回显）。
_KEY_LINE_PATTERNS = [
    re.compile(r"^>\s"),                       # 子阶段开始（run_integration.run_cmd:84 "> {desc}"）
    re.compile(r"\|\s*耗时:"),                  # 子阶段完成/失败（"... | 耗时: Ns"）
    re.compile(r"^\[START\]"),                 # 集成流程启动横幅（run_integration:729）
    re.compile(r"\[OK\]\s*集成流程全部完成"),     # 集成完成（run_integration:784）
    re.compile(r"^\[WARN\]"),                  # 告警（强制/拒绝/回退/清理等）
    re.compile(r"^\[CLEAN\]"),                 # 清理提示（run_integration:712）
    re.compile(r"^\[tokens\]\s+delta="),       # token 增量（父进程只做加法）
]


def _is_key_line(line: str) -> bool:
    """判断一行子进程输出是否值得 echo 到 console。"""
    s = line.lstrip()
    return any(p.search(s) for p in _KEY_LINE_PATTERNS)


# 控制台 echo 用：防御性代码——所有子进程模块已统一 ASCII（> / [OK] / [FAIL] 等），
# 但历史上曾依赖符号区分关键行，万一以后子模块漏出彩色 emoji，
# 下面的 _CONSOLE_SYMBOLS / _EMOJI_STRIP 仍可在回显前降级，控制台永不乱码。
# 日志文件与控制台现在口径一致：均保留原文（已是 ASCII，无需再剥离）。
_CONSOLE_SYMBOLS = str.maketrans({"▶": ">", "─": "-", "═": "="})
_EMOJI_STRIP = re.compile(r"[✅❌⚠🚀🧹📊📁]\uFE0F?\s*")


def _ascii_for_console(s: str) -> str:
    """控制台回显前：符号转 ASCII、彩色 emoji 删除（防非 UTF-8 控制台乱码）。"""
    return _EMOJI_STRIP.sub("", s.translate(_CONSOLE_SYMBOLS))


def _drain_output(proc: subprocess.Popen, log_file, prefix: str) -> None:
    """后台线程：逐行读子进程 stdout，写日志 + 关键行 echo 到 console。

    proc.stdout 必须为 PIPE（text mode, encoding="utf-8", errors="replace"）。
    进程退出后 stdout EOF，循环自然结束。daemon=False 保证主线程 join 能等到收尾。
    日志写原文（UTF-8）；console echo 前做 ASCII 化（防非 UTF-8 控制台乱码）。

    实现说明（2026-10-19 修复回归）：
    曾尝试改用 `proc.stdout.read1(4096)` 按 chunk 读取（防 PIPE buffer 满阻塞），
    但 POC 测试发现 chunk 读取在某些场景下让 drain 0 写入——子进程 print 完后
    drain 卡在等"更多字节"而非返回 EOF（TextIOWrapper.read1 底层 BufferedReader.read1
    行为：读到 size 字节或 EOF 才返回，但 PIPE buffer 不满 size 时也会阻塞）。
    回滚到 `for line in proc.stdout:` 按 \\n 阻塞读——子进程每次 print 都有 \\n，
    能及时读出；D4 修复（run_integration 主流程 print 全加 flush=True）已让
    实际生产场景不撞 PIPE buffer 满，所以 chunk 读取是多余改进。
    """
    if proc.stdout is None:
        return
    for line in proc.stdout:
        log_file.write(line)
        log_file.flush()
        if _is_key_line(line):
            # 关键行回显：去尾换行 + ASCII 化 + 加 prefix（[阶段1] / [judge_X]）便于辨识
            stripped = _ascii_for_console(line.rstrip("\n"))
            print(f"  {prefix} {stripped}", flush=True)


# ---------------------------------------------------------------------------
# 阶段1：共享 EoICD 处理
# ---------------------------------------------------------------------------

def run_stage1_shared(cfg: dict, shared_ws: Path, env: dict) -> None:
    print(f"\n> [阶段1 共享] EoICD 数据处理 -> {shared_ws}")
    cmd = [
        PYTHON, str(BASE_DIR / "run_integration.py"),
        "--step", "eoicd",
        "--workspace", str(shared_ws),
        "--project", cfg["project"],
        "--no-clean",
    ]
    if cfg.get("eoicd_pub"):
        cmd += ["--eoicd-pub", cfg["eoicd_pub"]]
    if cfg.get("eoicd_sub"):
        cmd += ["--eoicd-sub", cfg["eoicd_sub"]]
    if cfg.get("hlr"):
        cmd += ["--hlr", cfg["hlr"]]
    if cfg.get("trace_dir"):
        cmd += ["--trace-dir", cfg["trace_dir"]]
    if cfg.get("use_trace"):
        cmd += ["--use-trace"]
    # 阶段1 无 AI，沿用当前进程 env 即可。
    # 子进程 stdout/stderr 重定向到日志文件，关键步骤（> 阶段X / [OK]/[FAIL] 成功失败 / [START] / [WARN] / [CLEAN]）
    # 同步 echo 到 console（过滤式 tee），子模块逐信号 print 仍只进日志。
    log_dir = BASE_DIR / "runs" / "integration_workspace" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "stage1.log"
    with open(log_path, "w", encoding="utf-8") as _lf:
        try:
            proc = subprocess.Popen(cmd, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            drainer = threading.Thread(
                target=_drain_output, args=(proc, _lf, "[阶段1]"), daemon=False)
            drainer.start()
            try:
                rc = proc.wait()
            finally:
                drainer.join(timeout=10)
            if rc != 0:
                raise subprocess.CalledProcessError(rc, cmd)
        except subprocess.CalledProcessError as _e:
            print(f"  阶段1 运行失败（返回码 {_e.returncode}），详细日志见 {log_path}")
            raise
    print(f"  [阶段1] 运行日志已写入：{log_path}")


# ---------------------------------------------------------------------------
# 单 judge 运行（子进程，独立 env + 工作区 + map 缓存）
# ---------------------------------------------------------------------------

@dataclass
class JudgeRunContext:
    """run_single_judge 的入参容器。

    改造动机：原签名 9 个位置参数（`judge, idx, cfg, shared_ws, judge_ws,
    out_dir, timeout, procs, procs_lock`），调用点要靠位置记忆，且
    `shared_ws` / `judge_ws` 同名前缀易混、IDE 与类型检查帮不上忙。

    改后：单参数 `ctx: JudgeRunContext`，构造 + 属性访问都自解释。
    字段顺序对齐原 9 参数，方便从 `_worker` 处逐个填入。

    注：`timeout` 在本函数体不直接使用（orchestrate 持有 timeout 算 deadline，
    run_single_judge 通过 ctx.procs 登记表配合 watchdog）；保留字段以备 per-judge
    子预算扩展。`procs_lock` 函数体**直接**用 `ctx.procs_lock`（不解包成局部变量，
    避免遗留 NameError；P3.2 改 dataclass 时曾因 `_ = ctx.procs_lock` 占位
    导致 Popen 启动后立即 NameError，被 except Exception 静默吃掉）。
    """
    judge: dict                       # 配置段里的单 judge 项
    idx: int                          # 在 frozen 列表里的下标
    cfg: dict                         # 整体配置（input / aggregation / project 等）
    shared_ws: Path                   # 共享阶段1工作区
    judge_ws: Path                    # 本 judge 工作区
    out_dir: Path                     # 本 judge 阶段5输出目录
    timeout: int                      # per-judge 预算（秒）
    procs: dict                       # 全局 Popen 登记表（idx -> Popen）
    procs_lock: threading.Lock        # 保护 procs 的写锁
    # 被硬上限 watchdog 强制终止的 judge 下标集合（2026-10-29）。
    # 用途：把「超时被杀」与「自身异常退出」区分开——Windows 上 terminate() 的退出码
    # 就是 1，不记这一笔的话，报告里只剩「returncode=1」，读者无法判断发生了什么
    # （2026-10-29 实测：judge_C 失效原因不明，note 只写「覆盖缺口：judge_C(returncode=1)」）。
    killed_by_watchdog: set | None = None


def run_single_judge(ctx: JudgeRunContext) -> dict:
    """运行一个 judge（阶段2/3/4/5），返回该 judge 的分片或错误标记。

    返回 {name, ws, shard, coverage, error}
    coverage: "ok" 正常；"error" 运行失败/超时被杀；"gap" 部分信号缺失。
    """
    # 把 ctx 字段解开为本函数常用的局部变量（保持与原 9 参数版本一致的命名风格，减少函数体内的改动）。
    # 注意：procs_lock / procs **不**解出——函数体下方 L533/L544 直接 `with ctx.procs_lock:` /
    # `ctx.procs[idx] = proc` / `ctx.procs.pop(idx, None)`，避免遗留 NameError（P3.2 改 dataclass
    # 时曾因 `_ = ctx.procs_lock` 占位导致 NameError，Popen 抛异常被静默吃，production 0/3 失败）。
    judge = ctx.judge
    idx = ctx.idx
    cfg = ctx.cfg
    shared_ws = ctx.shared_ws
    judge_ws = ctx.judge_ws
    out_dir = ctx.out_dir
    name = judge["name"]
    print(f"\n> judge[{idx}] {name}  ({judge.get('model')})")

    # 复制共享阶段1产物（raw 输入 + eoicd_processed）到本 judge 工作区
    ws_raw = judge_ws / "data" / "raw"
    ws_eoicd = judge_ws / "eoicd_processed"
    src_raw = shared_ws / "data" / "raw"
    src_eoicd = shared_ws / "eoicd_processed"
    if src_raw.exists():
        shutil.copytree(src_raw, ws_raw, dirs_exist_ok=True)
    if src_eoicd.exists():
        shutil.copytree(src_eoicd, ws_eoicd, dirs_exist_ok=True)

    # 每 judge 独立 map 缓存（阶段2 映射隔离，防交叉污染）
    nm = judge_ws / "name_map.json"
    sm = judge_ws / "state_map.json"
    gm = judge_ws / "signal_map.json"
    for p in (nm, sm, gm):
        p.parent.mkdir(parents=True, exist_ok=True)

    # AI map 健康检查（诊断性，缺文件/老格式/半成品仅 WARN，不阻断本 judge 运行）
    _check_map_health(judge_ws, f"judge[{idx} {name}]")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")  # 子进程(run_integration.py 等)打印 emoji 时也不崩
    # judge["model"]/["base_url"] 已在 _normalize_ai_config 中解析（config 字面量 或 .env 的 ${AI_MODEL}）
    env["AI_MODEL"] = judge["model"]
    env["BASE_URL"] = judge["base_url"]
    env["API_KEY"] = os.environ.get(judge.get("api_key_env", "API_KEY"), "")
    if judge.get("parallel_api_keys"):
        env["JUDGE_PARALLEL_KEYS"] = ",".join(
            os.environ.get(k, "") for k in judge["parallel_api_keys"]
        )

    cmd = [
        PYTHON, str(BASE_DIR / "run_integration.py"),
        "--steps", "hlr,identity,attribute,summary",
        "--no-clean",
        "--workspace", str(judge_ws),
        "--output-dir", str(out_dir),
        "--project", cfg["project"],
    ]
    if cfg.get("eoicd_pub"):
        cmd += ["--eoicd-pub", cfg["eoicd_pub"]]
    if cfg.get("eoicd_sub"):
        cmd += ["--eoicd-sub", cfg["eoicd_sub"]]
    if cfg.get("hlr"):
        cmd += ["--hlr", cfg["hlr"]]
    if cfg.get("device"):
        cmd += ["--device", cfg["device"]]
    if cfg.get("trace_dir"):
        cmd += ["--trace-dir", cfg["trace_dir"]]
    if cfg.get("use_trace"):
        cmd += ["--use-trace"]
    cmd += ["--name-map", str(nm), "--state-map", str(sm), "--signal-map", str(gm)]
    # 每 judge 各自生成自己的 name/state/signal 映射（方案 A：per-judge 隔离）。
    # 默认开启；API 配置未就绪（如 400）时可将配置里 ai_update_maps 置 false 跳过，避免重复失败。
    if cfg.get("ai_update_maps", True):
        cmd.append("--ai-update")

    # 复用工作区续跑时，上一轮的 sidecar 会留在这里；先清掉再启动，避免重复计数
    _clear_token_sidecars(judge_ws)

    t0 = time.time()
    # 子进程 stdout/stderr 重定向到按 judge 命名的日志文件，关键步骤（> 阶段X / [OK]/[FAIL] 成功失败 /
    # [START] / [WARN] / [CLEAN]）同步 echo 到 console（过滤式 tee）；失败/异常时打印指向日志的告警，排错入口不丢。
    log_dir = BASE_DIR / "runs" / "integration_workspace" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    log_path = log_dir / f"judge_{idx}_{_safe}.log"
    try:
        with open(log_path, "w", encoding="utf-8") as _lf:
            # 不靠 subprocess timeout 杀进程（超时任务继续跑，由迟到补收统一收集）；
            # 但把 Popen 句柄登记到 procs，供 orchestrate 的硬上限 watchdog 在极端卡死时 terminate()。
            proc = subprocess.Popen(cmd, env=env, cwd=str(BASE_DIR),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            with ctx.procs_lock:
                ctx.procs[idx] = proc
            # 后台线程：写日志 + 关键行 echo console
            drainer = threading.Thread(
                target=_drain_output, args=(proc, _lf, f"[judge_{idx} {name}]"),
                daemon=False)
            drainer.start()
            try:
                rc = proc.wait()
            finally:
                with ctx.procs_lock:
                    ctx.procs.pop(idx, None)
                drainer.join(timeout=10)
    except Exception as e:  # 极端情况（启动失败等）
        # 必须 print 异常根因——之前 P3.2 的 procs_lock NameError 在这里被静默吃掉，
        # 导致 production 跑出 0/3 judge 失败 + 0 信号却没有任何错误信息。
        import traceback as _tb
        _tb.print_exc()
        print(f"[Popen 启动失败] judge[{idx} {name}] {type(e).__name__}: {e}",
              flush=True)
        return {"name": name, "ws": str(judge_ws), "shard": {}, "coverage": "error",
                "error": f"启动失败: {e}", "token_usage": _read_token_sidecars(judge_ws)}
    elapsed = time.time() - t0

    if rc != 0:
        print(f"  judge {name} 运行返回非0（{rc}），详细日志见 {log_path}，该 judge 标记为 error")
        # 区分「被看门狗强杀（运行超时）」与「自身异常退出」——Windows 上 terminate()
        # 的退出码就是 1，不区分的话报告里只剩 returncode=1，读者无法判断发生了什么。
        _killed = bool(ctx.killed_by_watchdog is not None and idx in ctx.killed_by_watchdog)
        _err = ("被看门狗强制终止（运行超时，rc={0}）".format(rc) if _killed
                else "returncode={0}".format(rc))
        return {"name": name, "ws": str(judge_ws), "shard": {}, "coverage": "error",
                "error": _err, "elapsed": elapsed,
                "token_usage": _read_token_sidecars(judge_ws)}

    # 收集 stage-5 report → 分片
    folder = _latest_summary_folder(judge_ws)
    if folder is None:
        return {"name": name, "ws": str(judge_ws), "shard": {}, "coverage": "error",
                "error": "未找到 stage-5 输出", "elapsed": elapsed,
                "token_usage": _read_token_sidecars(judge_ws)}
    shard = build_shard_from_reports(folder)
    print(f"  judge {name} 完成（{elapsed:.1f}s），分片 {len(shard)} 个信号")
    return {"name": name, "ws": str(judge_ws), "shard": shard, "coverage": "ok",
            "elapsed": elapsed, "token_usage": _read_token_sidecars(judge_ws)}


# ---------------------------------------------------------------------------
# 并发编排（超时/迟到补收：不 kill，后台线程统一收）
# ---------------------------------------------------------------------------

def orchestrate(cfg: dict, timeout: int) -> dict[str, dict]:
    judges = cfg["judges"]
    # 冻结 judges 列表（对应反向 frozen providers）
    frozen = list(judges)

    root = BASE_DIR / "runs" / "integration_workspace"
    # 清孤儿：删掉本次配置里已不存在的 judge_* 工作区（当前配置的 judge 文件夹保留，
    # 以复用其 AI map 缓存；shared/ 与数据文件不动）。
    _expected_judge_dirs = {root / f"judge_{i}_{j['name']}" for i, j in enumerate(frozen)}
    if root.exists():
        for _child in root.iterdir():
            if (_child.is_dir() and _child.name.startswith("judge_")
                    and _child not in _expected_judge_dirs):
                shutil.rmtree(_child)
                print(f"  [清理] 删除孤儿工作区：{_child.name}")
    shared_ws = root / "shared"
    shared_ws.mkdir(parents=True, exist_ok=True)
    # 本轮开始前重置运行区：顶层日志/聚合产物每次重生成，先清避免陈旧数据误导；
    # shared/ 无 map，整目录重置（清掉上一轮残留的输入与 *_Located.xlsx 等）。
    _reset_run_area(root)
    _clean_workspace(shared_ws, set())

    # 阶段1 共享（沿用当前进程 env，无 AI）
    run_stage1_shared(cfg, shared_ws, dict(os.environ))

    # 每 judge 子进程并发；time 放在 submit 之后开始计时，避免排队吃掉预算
    results: dict[str, dict] = {}
    lock = threading.Lock()
    threads: list[threading.Thread] = []
    # 子进程句柄登记表（idx -> Popen），供硬上限 watchdog 终止卡死的 judge
    procs: dict[int, "subprocess.Popen"] = {}
    procs_lock = threading.Lock()
    # 被硬上限 watchdog 强制终止的 judge 下标（2026-10-29）：用于在 judge 的失败原因里
    # 明确写「超时被杀」而不是笼统的 returncode=1。
    killed_by_watchdog: set[int] = set()

    def _worker(idx, judge):
        ws = root / f"judge_{idx}_{judge['name']}"
        # 先重置本 judge 工作区（只留 3 个 AI map 缓存），再重建 output 目录；
        # 必须在 run_single_judge 复制"共享阶段1产物"之前，否则会连复制进来的
        # eoicd_processed/ 一起清掉（judge 只跑 hlr/identity/attribute/summary，
        # 阶段1 产物依赖这里复制进来）。
        _clean_workspace(ws, KEEP_IN_WORKSPACE)
        out_dir = ws / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        ctx = JudgeRunContext(
            judge=judge, idx=idx, cfg=cfg, shared_ws=shared_ws, judge_ws=ws,
            out_dir=out_dir, timeout=timeout, procs=procs, procs_lock=procs_lock,
            killed_by_watchdog=killed_by_watchdog,
        )
        res = run_single_judge(ctx)
        with lock:
            results[judge["name"]] = res

    for idx, judge in enumerate(frozen):
        t = threading.Thread(target=_worker, args=(idx, judge), daemon=True)
        threads.append(t)
        t.start()

    # 主线程等待：超过 per-judge 预算只告警「迟到」，不杀进程（迟到补收）
    deadline = time.time() + timeout * len(frozen) + SOFT_GRACE
    for t in threads:
        remaining = max(0.1, deadline - time.time())
        t.join(timeout=remaining)
        if t.is_alive():
            print(f"  [LATE] 仍有 judge 在迟到运行（超过预算），继续等待其补收结果（不中断）…")

    # 硬上限 watchdog（#2 修复）：软上限(deadline)后额外宽限 HARD_GRACE 秒做最后的迟到补收；
    # 宽限结束仍存活的子进程视为卡死（API 挂起/死循环），强制 terminate()，避免拖挂整个 runner
    # （原实现最后 `for t in threads: t.join()` 无 timeout，单 judge 卡死会永久阻塞）。
    if any(t.is_alive() for t in threads):
        print(f"\n  [HARD-LIMIT] 软上限已过仍有 judge 未结束，进入硬上限宽限（{HARD_GRACE}s）后强制终止挂起子进程…")
        time.sleep(HARD_GRACE)
        with procs_lock:
            stuck = list(procs.items())
        for idx, proc in stuck:
            if proc.poll() is None:
                print(f"  [HARD-LIMIT] 强制终止挂起的 judge[{idx}] 子进程 (pid={proc.pid})")
                # 记账：这个 judge 是被看门狗强杀的（运行超时），不是自身异常退出。
                # 报告的「运行有效性」小节要能说清原因（2026-10-29：judge_C 只报
                # returncode=1，读者无法判断是超时还是崩溃）。
                killed_by_watchdog.add(idx)
                try:
                    proc.terminate()
                except Exception:
                    pass
        # 给终止进程一点退出时间（SIGTERM 后 worker 的 proc.wait() 返回非0 → 标记 coverage=error）
        time.sleep(5)
        with procs_lock:
            for idx, proc in list(procs.items()):
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    # 最终收集：被终止的 judge 子进程 rc≠0 → coverage=error；线程若仍卡死则补标记
    for t in threads:
        t.join(timeout=120)
        if t.is_alive():
            print(f"  某 judge 工作线程无法 join（可能 Python 级卡死），其分片将缺失")

    # 对未返回结果的 judge 补 coverage=error（硬超时终止等极端场景）
    for judge in frozen:
        if judge["name"] not in results:
            results[judge["name"]] = {
                "name": judge["name"], "ws": "", "shard": {},
                "coverage": "error", "error": "线程未返回（硬超时终止）",
            }
    return results


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    base = Path(path).resolve().parent
    # input 子对象里的路径字段提升到顶层（相对本文件目录解析）
    inp = cfg.get("input", {}) or {}
    for k in ("eoicd_pub", "eoicd_sub", "hlr", "trace_dir"):
        if inp.get(k):
            cfg[k] = _resolve(inp[k], base)
    cfg.setdefault("project", "ams")
    cfg.setdefault("aggregation", {})
    return cfg


# ---------------------------------------------------------------------------
# 复核路由（#8 路线一 + 三）：need_review 是路由标记，必须有必然执行的消费者
# ---------------------------------------------------------------------------

# 成本护栏：本轮复核的 LLM 调用次数上限（0 = 不限）
REVIEW_BUDGET_ENV = "MULTI_JUDGE_REVIEW_BUDGET"


def _record_skip(stats: dict, reason: str, n: int = 1) -> None:
    stats["skipped"] += n
    stats["reasons"][reason] = stats["reasons"].get(reason, 0) + n


def _judge_view_key(judge: dict) -> tuple[str, str]:
    """judge 的「视角身份」= (model, base_url)（同源判定用）。"""
    return ((judge.get("model") or "").strip().lower().rstrip("/"),
            (judge.get("base_url") or "").strip().lower().rstrip("/"))


def _is_same_as_judge(cfg: dict, arb_cfg: dict) -> bool:
    """仲裁视角是否与某个 judge 同源（model+base_url 相同即同一视角）。

    反向复核的对象是确定性层，不存在「自己复核自己」；正向仲裁者面对的是 judge 的
    结论，仲裁模型与 judge 同模型时这道第二裁决只是同一次判定的复述，独立性不成立。
    """
    keys = {_judge_view_key(j) for j in (cfg.get("judges") or [])}
    if not keys or any(not k[0] for k in keys):
        return False
    return (arb_cfg.get("model") or "", arb_cfg.get("base_url") or "") in keys


def _resolve_arbitrator_cfg(cfg: dict) -> tuple[dict, str]:
    """解析仲裁配置，返回 (arb_cfg, 来源说明)。

    反向 needs_ai 是「打了标就必然进 C7」，正向缺的正是这一层。此处把「要不要跑」
    从配置挪回代码，配置的权限收敛为「用哪个模型」，而不是「跑不跑」：
      · 显式 enabled=false → 返回不可用配置 + _block_reason，记账 skipped（降级且可见）；
      · 未配 arbitrator 段 → 回落到 judge[0] 的 model/base_url，并如实标注同源；
      · 已配 → 用配置段（可覆盖 model/base_url/api_key_env）。
    同源处置：默认允许但显式警示（allow_same_as_judge=false 则拒绝执行）。
    """
    judges = cfg.get("judges") or []
    arb_cfg = cfg.get("arbitrator")
    if isinstance(arb_cfg, dict) and arb_cfg.get("enabled") is False:
        return ({"_block_reason": "仲裁配置显式关闭（arbitrator.enabled=false）"},
                "配置显式关闭（arbitrator.enabled=false）——已降级，未按路由消费")
    if not arb_cfg:
        if not judges:
            return {"_block_reason": "无 judge 可回落"}, "无 judge 可回落"
        j0 = judges[0]
        arb_cfg = {
            "model": j0.get("model"),
            "base_url": j0.get("base_url"),
            "api_key_env": j0.get("api_key_env", ""),
        }
        source = "回落：沿用 judge[0]({0}) 的 model/base_url".format(j0.get("name"))
    else:
        arb_cfg = dict(arb_cfg)
        source = "配置：arbitrator 段"

    same = _is_same_as_judge(cfg, arb_cfg)
    arb_cfg["_same_as_judge"] = same
    if same and arb_cfg.get("allow_same_as_judge") is False:
        # 保留 _same_as_judge=True：本批信号确实是被「同源」这一条拒绝的，
        # 记账必须能反映这一点，不能因为配置被替换就把它说成「模型不可用」。
        arb_cfg = {"_same_as_judge": True,
                   "_block_reason": "仲裁视角与 judge 同源且禁止同源仲裁（allow_same_as_judge=false）"}
        return arb_cfg, source + "；仲裁视角与 judge 同源，且配置禁止同源仲裁，已拒绝执行"
    if same:
        source += "；仲裁视角与 judge 同源，第二裁决独立性受限"
    return arb_cfg, source


def run_review_route(agg: dict, shards: dict, judge_meta: dict, cfg: dict) -> dict:
    """把 need_review=True 的信号强制送进 Step5/Step5.5，并如实记账。

    与反向 C7 同构的三点：触发不靠开关、跑不了要记账、次数可审计。
    """
    stats = {
        "needed": 0, "executed": 0, "skipped": 0, "reasons": {},
        "arb_source": "", "arb_model": "", "arb_same_as_judge": False,
        "concurrency": 1, "budget": 0,
        # 2026-10-29：失败记账。此前失败被计入 executed，报告宣称「获第二裁决」
        # 而实际存在调用失败（2026-10-29 实测：38 条全部计入 executed，
        # 而 logs/arb_fail.jsonl 里有 11 次调用失败）。
        "failed": 0, "partially_failed": 0, "failed_stages": {},
    }
    needed = [f for f, it in agg.items() if it["aggregation"].get("need_review")]
    stats["needed"] = len(needed)
    if not needed:
        return stats

    arb_cfg, source = _resolve_arbitrator_cfg(cfg)
    stats["arb_source"] = source
    stats["arb_same_as_judge"] = bool(arb_cfg.get("_same_as_judge"))
    # 即使本轮仲裁跑不起来，也要留下「本应用哪个模型」的痕迹，便于溯源
    stats["arb_model"] = arb_cfg.get("model") or ""

    model = arb_cfg.get("model")
    base_url = arb_cfg.get("base_url")
    api_key = os.environ.get(arb_cfg.get("api_key_env", "API_KEY"), "")
    if not (model and base_url and api_key):
        # 与反向 `except Exception → stats={"skipped": 1}` 同构：跑不了就记账，不假装复核过
        block = arb_cfg.get("_block_reason")
        reason = block or "仲裁模型不可用（model/base_url/api_key 缺失）"
        _record_skip(stats, reason, len(needed))
        print("  [复核路由] {0}，{1} 条待复核信号未获第二裁决"
              "（已记入 review_stats.skipped）".format(reason, len(needed)))
        return stats
    if stats["arb_same_as_judge"]:
        print("  [复核路由] 仲裁视角与 judge 同源（{0}）：第二裁决只是同一次判定的复述，"
              "独立性受限，相关结论不宜当作交叉验证依据".format(model))

    budget = int(os.environ.get(REVIEW_BUDGET_ENV, "0") or 0)
    stats["budget"] = budget
    arb_cfg["_review_budget"] = {"left": budget} if budget > 0 else None

    # 注意：默认值须与 multi_judge_arbitrator.run_arbitration 保持一致（同一 env，
    # 否则此处展示的并发度与实际生效值不符）。2026-10-15 由 6 改回 8。
    conc = int(os.environ.get("MULTI_JUDGE_REVIEW_CONCURRENCY", "8"))
    conc = max(1, min(conc, 16))
    stats["concurrency"] = conc
    # 打印生效的超时/重试/熔断参数：它们决定「一条信号最坏要等多久」与「什么情况下
    # 整批止损」。2026-10-29 排查欠费问题时，这三个生效值无法从产物反推，只能回头翻
    # 源码与 .env——故在此显式打印。
    _rt = int(os.environ.get("MULTI_JUDGE_REVIEW_TIMEOUT", "60") or 60)
    _rr = int(os.environ.get("MULTI_JUDGE_REVIEW_RETRIES", "2") or 2)
    _fs = int(os.environ.get("MULTI_JUDGE_REVIEW_FAILSTOP", "15") or 15)
    _worst = _rt * (_rr + 1) + _rr * (_rr + 1) // 2   # 每次等满 + 退避合计
    print(f"\n> [复核路由] Step5 仲裁 + Step5.5 peer-aware 复查"
          f"（{len(needed)} 条需复核信号；并发度={conc}，不占 judge 并发额度；预算={budget or '不限'}）")
    print(f"  [复核参数] 单次超时={_rt}s；每条最多尝试 {_rr + 1} 次（单条最坏约 {_worst}s）；"
          f"熔断阈值={_fs} 次；欠费/鉴权/模型不存在等永久错误会立即止损")
    arb_cfg = dict(arb_cfg)
    arb_cfg["re_review"] = cfg.get("re_review") or {}
    agg = run_arbitration(agg, shards, judge_meta, arb_cfg)

    for full in needed:
        item = agg.get(full)
        if item is None:
            _record_skip(stats, "复核后信号从结果中消失")
            continue
        a = item["aggregation"]
        trace = a.get("review_trace") or {}
        info = a.get("peer_review_info") or {}
        applied_stages = a.get("review_applied_stages")
        failures = a.get("review_failures") or []
        # 「是否获得第二裁决」的判据：优先用 review_applied_stages（真正产出过结论的阶段）；
        # 老数据没有该字段时才回退 review_trace.applied。
        # **不能只看 trace.applied**：复核是多阶段的（step5 → step5.5 → step5.6），
        # 后一阶段成功会整体覆盖 trace，把前一阶段的失败抹掉——统计上就出现
        # 「executed == needed」而实际有调用失败（2026-10-29 实测）。
        got_second_opinion = (bool(applied_stages) if applied_stages is not None
                              else bool(trace.get("applied")))
        if failures:
            stats["failed"] += 1
            for f in failures:
                _st = f.get("stage") or "unknown"
                stats["failed_stages"][_st] = stats["failed_stages"].get(_st, 0) + 1
        if got_second_opinion:
            stats["executed"] += 1
            if failures:
                # 部分阶段失败但结论已拿到：计入 executed，同时单列，避免与"全程成功"混为一谈
                stats["partially_failed"] += 1
            # #9 留痕：Step5.6 会刷新 trace，但复查是否真的发生要单独记，
            # 否则「复查因独立视角不足被跳过」会被算成「复核已生效」。
            pr = trace.get("peer_review") or {}
            if info.get("applied") is False and pr.get("reason"):
                gaps = stats.setdefault("review_gaps", {})
                gaps[pr["reason"]] = gaps.get(pr["reason"], 0) + 1
        else:
            reason = (trace.get("reason") or info.get("reason")
                      or "复核未生效（未写 review_trace）")
            if failures:
                reason = "复核未获第二裁决（调用失败）：" + "；".join(
                    "{0} {1}".format(f.get("stage"), f.get("reason")) for f in failures)
            _record_skip(stats, reason)
    return stats




def main() -> int:
    ap = argparse.ArgumentParser(description="正向检查 · 多 judge 并发编排器")
    ap.add_argument("--config", default=None,
                    help="multi_judge_config.json 路径（--selftest 自测时不需要）")
    ap.add_argument("--judge-timeout", type=int, default=3600,
                    help="单个 judge 的运行预算（秒）；超时只告警不杀进程，迟到补收（默认3600）")
    ap.add_argument("--only-stage1", action="store_true",
                    help="仅运行共享阶段1（EoICD 处理），不跑各 judge")
    ap.add_argument("--output", default=None,
                    help="最终聚合报告输出目录（默认 <工程>/output）")
    ap.add_argument("--base-tokens", type=int, default=0,
                    help="本次运行前该任务已累计的 token（续跑时由父进程传入；默认 0）")
    ap.add_argument("--selftest", action="store_true",
                    help="运行内置自测（不触发任何真实 LLM 调用），验证聚合层强制 need_review 路由逻辑")
    args = ap.parse_args()

    if args.selftest:
        # 自测已迁出到 tests/test_runner_review_route.py（生产文件瘦身）
        from tests.test_runner_review_route import run_all as _run_self_test
        _run_self_test()
        return 0

    if not args.config:
        print("未指定 --config（运行完整多 judge 流程所需）")
        return 1

    cfg = load_config(args.config)
    _normalize_ai_config(cfg)  # 让 model/base_url 支持 .env（单 judge 风格：省略或 ${AI_MODEL} 时从环境读取）
    if not cfg.get("judges"):
        print("配置中未找到 judges 列表")
        return 1

    if args.only_stage1:
        run_stage1_shared(cfg, BASE_DIR / "runs" / "integration_workspace" / "shared", dict(os.environ))
        print("仅阶段1共享完成")
        return 0

    # 每轮从 0 累积仲裁 token；续跑前总量由 --base-tokens 单独传入
    reset_token_usage()

    t0 = time.time()
    # 统一运行时间戳：同一次 run 的 Word 与 xlsx 共用后缀，便于配对与跨 run 对比
    run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    results = orchestrate(cfg, args.judge_timeout)

    # 构建 judge_meta
    judge_meta = {}
    for j in cfg["judges"]:
        judge_meta[j["name"]] = {
            "model": j.get("model"),
            "base_url": j.get("base_url"),
            "key_hash": _hash_key(os.environ.get(j.get("api_key_env", ""), "")),
            "frozen": True,
        }

    # 汇总分片
    shards = {r["name"]: r["shard"] for r in results.values()}
    # #8①：judge 失败原因下传——覆盖缺口的 note 会带上「为什么这个 judge 没结论」，
    # 让下游复核者能判断该补跑还是该跳过（硬超时 / 子进程异常 / 未产出，处置各不相同）。
    judge_errors = {r["name"]: r["error"] for r in results.values()
                    if r.get("coverage") != "ok" and r.get("error")}
    agg = aggregate(shards, judge_meta, cfg.get("aggregation"),
                    n_configured=len(cfg["judges"]), judge_errors=judge_errors)

    # 复核路由：need_review=True 的信号强制进入 Step5/5.5（不再依赖 config 手动开）
    review_stats = run_review_route(agg, shards, judge_meta, cfg)

    # token 汇总（报告用）：各 judge 的 sidecar + 本轮仲裁调用；续跑前的总量
    # 由父进程经 --base-tokens 传入，因此报告数字是「整个任务累计」口径。
    judge_tokens = sum(max(0, int(r.get("token_usage") or 0)) for r in results.values())
    arb_tokens = get_token_total()
    base_tokens = max(0, int(args.base_tokens or 0))
    total_tokens = base_tokens + judge_tokens + arb_tokens

    # 输出目录：
    #  - out_dir：用户向交付物（Word 报告）落点，默认 <工程>/output
    #  - data_dir：数据类中间产物（聚合/CSV/复核记账/汇总）下沉到 runs 工作区，不进 output/
    out_dir = Path(args.output) if args.output else (BASE_DIR / "output")
    data_dir = BASE_DIR / "runs" / "integration_workspace"
    data_dir.mkdir(parents=True, exist_ok=True)

    out_json = data_dir / "multi_judge_aggregate.json"
    out_csv = data_dir / "multi_judge_aggregate.csv"
    write_outputs(agg, out_json, out_csv)

    # 复核记账（与反向 `coverage.ai_review_calls` 同构）：单独落文件，不并入 agg，
    # 避免 review_stats 被下游 build_summary / write_outputs 当成信号遍历到。
    review_stats_path = data_dir / "multi_judge_review_stats.json"
    review_stats_path.write_text(
        json.dumps(review_stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [复核路由] 需复核 {review_stats['needed']} 条 → 生效 {review_stats['executed']} 条、"
          f"未生效 {review_stats['skipped']} 条；明细：{review_stats_path}")
    for reason, cnt in (review_stats.get("reasons") or {}).items():
        print(f"      · 未生效原因：{reason} × {cnt}")
    for reason, cnt in (review_stats.get("review_gaps") or {}).items():
        print(f"      · 复查未生效（Step5 已生效，不占 skipped）：{reason} × {cnt}")

    # 汇总统计（对齐反向 review_agent._build_summary 口径，并带 AI 复核覆盖）
    # + Word 共识报告（复核覆盖必须进报告，否则低星结论无法自证是否过过第二裁决）
    summary = build_summary(agg, review_stats)
    summary["run_timestamp"] = run_ts
    summary["token_usage"] = {"total_tokens": total_tokens}
    out_summary = data_dir / "multi_judge_summary.json"
    out_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [汇总] 已写出：{out_summary}")
    # Word / Excel 报告共用运行时间戳与文件名词干（report_stem），保证两者配对；
    # Excel 文件名需在生成 Word 前确定，以便把实际文件名传进 Word 正文（详见《xxx.xlsx》）。
    report_stem = Path(REPORT_FILE_NAME).stem
    report_path = out_dir / f"{report_stem}_{run_ts}.docx"
    excel_path = out_dir / f"{report_stem}_{run_ts}.xlsx"
    # 明细展示模式（默认 exceptions_only 只列异常信号；大数据量下显著降低生成耗时）
    report_cfg = cfg.get("report", {}) or {}
    word_detail_mode = report_cfg.get("word_detail_mode", "exceptions_only")
    generate_report(agg, summary, report_path, word_detail_mode=word_detail_mode,
                    excel_filename=excel_path.name)

    # Excel 报告（与 Word 同源，自动生成；缺 openpyxl 时优雅跳过）
    generate_excel_report(agg, summary, excel_path)

    # 运行清单：每次 run 一份，记录时间戳/配置/judge/产物路径，便于跨 run 对比与溯源
    manifest = {
        "run_timestamp": run_ts,
        "config": str(Path(args.config).resolve()),
        "judges": [
            {"name": j["name"], "model": j.get("model"), "base_url": j.get("base_url")}
            for j in cfg["judges"]
        ],
        "outputs": {
            "word_report": str(report_path),
            "excel_report": str(excel_path),
            "aggregate_json": str(out_json),
            "aggregate_csv": str(out_csv),
            "summary_json": str(out_summary),
            "review_stats_json": str(review_stats_path),
            "data_dir": str(data_dir),
        },
    }
    manifest_path = out_dir / f"run_manifest_{run_ts}.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # 运行小结
    n_ok = sum(1 for r in results.values() if r["coverage"] == "ok")
    n_err = sum(1 for r in results.values() if r["coverage"] == "error")
    n_total = len(results)
    n_review = sum(1 for it in agg.values() if it["aggregation"]["need_review"])
    print(f"\n> 多 judge 聚合完成：{n_ok}/{n_total} judge 正常，{n_err} 失败")
    print(f"   信号总数：{len(agg)}；需复核信号：{n_review}")
    print(f"   Token 用量：本轮 {judge_tokens + arb_tokens:,}"
          f"（judge {judge_tokens:,} + 仲裁 {arb_tokens:,}），累计 {total_tokens:,}")
    print(f"   总耗时：{time.time() - t0:.1f}s")
    print("  -- 汇总 --")
    print("   " + format_summary_text(summary))
    print(f"   数据产物（runs 工作区）：{out_json}")
    print(f"   数据产物（runs 工作区）：{out_csv}")
    print(f"   汇总统计（runs 工作区）：{out_summary}")
    print(f"   交付报告（output/）：{report_path}")
    if excel_path.exists():
        print(f"   交付报告（output/）：{excel_path}")
    print(f"   运行清单（output/）：{manifest_path}")
    print(f"   运行日志（runs 工作区/logs/，含阶段1 与每 judge 详细输出）："
          f"{BASE_DIR / 'runs' / 'integration_workspace' / 'logs'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
