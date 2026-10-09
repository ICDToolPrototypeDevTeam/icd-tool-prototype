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
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

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
from multi_judge_arbitrator import run_arbitration  # noqa: E402  # 可选 Step5/5.5 LLM 仲裁层
from multi_judge_summary import build_summary, format_summary_text  # noqa: E402
from multi_judge_report import generate_report  # noqa: E402  # Word 共识报告（缺 python-docx 时优雅跳过）

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
# 阶段1：共享 EoICD 处理
# ---------------------------------------------------------------------------

def run_stage1_shared(cfg: dict, shared_ws: Path, env: dict) -> None:
    print(f"\n{'=' * 70}\n▶ [阶段1 共享] EoICD 数据处理 → {shared_ws}\n{'=' * 70}")
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
    # 阶段1 无 AI，沿用当前进程 env 即可
    subprocess.run(cmd, env=env, check=True)


# ---------------------------------------------------------------------------
# 单 judge 运行（子进程，独立 env + 工作区 + map 缓存）
# ---------------------------------------------------------------------------

def run_single_judge(judge: dict, idx: int, cfg: dict, shared_ws: Path,
                     judge_ws: Path, out_dir: Path, timeout: int,
                     procs: dict, procs_lock: threading.Lock) -> dict:
    """运行一个 judge（阶段2/3/4/5），返回该 judge 的分片或错误标记。

    返回 {name, ws, shard, coverage, error}
    coverage: "ok" 正常；"error" 运行失败/超时被杀；"gap" 部分信号缺失。
    """
    name = judge["name"]
    print(f"\n{'#' * 70}\n# ▶ judge[{idx}] {name}  ({judge.get('model')})\n{'#' * 70}")

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

    env = os.environ.copy()
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

    t0 = time.time()
    try:
        # 不靠 subprocess timeout 杀进程（超时任务继续跑，由迟到补收统一收集）；
        # 但把 Popen 句柄登记到 procs，供 orchestrate 的硬上限 watchdog 在极端卡死时 terminate()。
        proc = subprocess.Popen(cmd, env=env, cwd=str(BASE_DIR))
        with procs_lock:
            procs[idx] = proc
        try:
            rc = proc.wait()
        finally:
            with procs_lock:
                procs.pop(idx, None)
    except Exception as e:  # 极端情况
        return {"name": name, "ws": str(judge_ws), "shard": {}, "coverage": "error",
                "error": f"启动失败: {e}"}
    elapsed = time.time() - t0

    if rc != 0:
        print(f"  ⚠️  judge {name} 运行返回非0（{rc}），该 judge 标记为 error")
        return {"name": name, "ws": str(judge_ws), "shard": {}, "coverage": "error",
                "error": f"returncode={rc}", "elapsed": elapsed}

    # 收集 stage-5 report → 分片
    folder = _latest_summary_folder(judge_ws)
    if folder is None:
        return {"name": name, "ws": str(judge_ws), "shard": {}, "coverage": "error",
                "error": "未找到 stage-5 输出", "elapsed": elapsed}
    shard = build_shard_from_reports(folder)
    print(f"  ✅ judge {name} 完成（{elapsed:.1f}s），分片 {len(shard)} 个信号")
    return {"name": name, "ws": str(judge_ws), "shard": shard, "coverage": "ok",
            "elapsed": elapsed}


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

    # 阶段1 共享（沿用当前进程 env，无 AI）
    run_stage1_shared(cfg, shared_ws, dict(os.environ))

    # 每 judge 子进程并发；time 放在 submit 之后开始计时，避免排队吃掉预算
    results: dict[str, dict] = {}
    lock = threading.Lock()
    threads: list[threading.Thread] = []
    # 子进程句柄登记表（idx -> Popen），供硬上限 watchdog 终止卡死的 judge
    procs: dict[int, "subprocess.Popen"] = {}
    procs_lock = threading.Lock()

    def _worker(idx, judge):
        out_dir = root / f"judge_{idx}_{judge['name']}" / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        ws = root / f"judge_{idx}_{judge['name']}"
        res = run_single_judge(judge, idx, cfg, shared_ws, ws, out_dir, timeout,
                               procs, procs_lock)
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
            print(f"  ⏳ 仍有 judge 在迟到运行（超过预算），继续等待其补收结果（不中断）…")

    # 硬上限 watchdog（#2 修复）：软上限(deadline)后额外宽限 HARD_GRACE 秒做最后的迟到补收；
    # 宽限结束仍存活的子进程视为卡死（API 挂起/死循环），强制 terminate()，避免拖挂整个 runner
    # （原实现最后 `for t in threads: t.join()` 无 timeout，单 judge 卡死会永久阻塞）。
    if any(t.is_alive() for t in threads):
        print(f"\n  ⛔ 软上限已过仍有 judge 未结束，进入硬上限宽限（{HARD_GRACE}s）后强制终止挂起子进程…")
        time.sleep(HARD_GRACE)
        with procs_lock:
            stuck = list(procs.items())
        for idx, proc in stuck:
            if proc.poll() is None:
                print(f"  ⛔ 强制终止挂起的 judge[{idx}] 子进程 (pid={proc.pid})")
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
            print(f"  ⚠️ 某 judge 工作线程无法 join（可能 Python 级卡死），其分片将缺失")

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
        print("  [复核路由] ⚠️ {0}，{1} 条待复核信号未获第二裁决"
              "（已记入 review_stats.skipped）".format(reason, len(needed)))
        return stats
    if stats["arb_same_as_judge"]:
        print("  [复核路由] ⚠️ 仲裁视角与 judge 同源（{0}）：第二裁决只是同一次判定的复述，"
              "独立性受限，相关结论不宜当作交叉验证依据".format(model))

    budget = int(os.environ.get(REVIEW_BUDGET_ENV, "0") or 0)
    stats["budget"] = budget
    arb_cfg["_review_budget"] = {"left": budget} if budget > 0 else None

    print(f"\n{'=' * 70}\n▶ [复核路由] Step5 仲裁 + Step5.5 peer-aware 复查"
          f"（{len(needed)} 条需复核信号；串行执行，不占 judge 并发额度；预算={budget or '不限'}）\n{'=' * 70}")
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
        if trace.get("applied"):
            stats["executed"] += 1
            # #9 留痕：Step5.6 会刷新 trace，但复查是否真的发生要单独记，
            # 否则「复查因独立视角不足被跳过」会被算成「复核已生效」。
            pr = trace.get("peer_review") or {}
            if info.get("applied") is False and pr.get("reason"):
                gaps = stats.setdefault("review_gaps", {})
                gaps[pr["reason"]] = gaps.get(pr["reason"], 0) + 1
        else:
            reason = (trace.get("reason") or info.get("reason")
                      or "复核未生效（未写 review_trace）")
            _record_skip(stats, reason)
    return stats


def _self_test() -> None:
    """复核路由自测：不触发任何真实 LLM 调用（走「模型不可用」降级分支记账）。"""
    from multi_judge_aggregate import aggregate

    # 0 存活 → need_review=True（聚合层已强制标记）
    shards = {
        "judge_a": {"S1": {"matched_hlr": [], "verdict": "待确认", "name_match_status": False,
                           "hlr_identity": None, "coverage": "error", "attributes": []}},
        "judge_b": {"S1": {"matched_hlr": [], "verdict": "待确认", "name_match_status": False,
                           "hlr_identity": None, "coverage": "error", "attributes": []}},
    }
    meta = {j: {"model": "m", "base_url": "b", "key_hash": "k", "frozen": True} for j in shards}
    agg = aggregate(shards, meta, {}, n_configured=2)
    assert agg["S1"]["aggregation"]["need_review"] is True

    # 1) 未配 arbitrator 段 → 回落 judge[0]，但拿不到 key → 记账 skipped，不得静默
    cfg = {"judges": [{"name": "judge_a", "model": "deepseek", "base_url": "x",
                       "api_key_env": "_NO_SUCH_KEY_"}]}
    arb, source = _resolve_arbitrator_cfg(cfg)
    assert arb["model"] == "deepseek"
    assert "同源" in source, source
    st = run_review_route(agg, shards, meta, cfg)
    assert st["needed"] == 1 and st["executed"] == 0 and st["skipped"] == 1, st
    assert any("仲裁模型不可用" in k for k in st["reasons"]), st["reasons"]
    assert "回落" in st["arb_source"], st["arb_source"]

    # 2) 显式 enabled=false → 同样记账，来源说明点明是「降级且可见」
    cfg_off = {"judges": cfg["judges"], "arbitrator": {"enabled": False, "model": "m"}}
    arb_off, src_off = _resolve_arbitrator_cfg(cfg_off)
    assert "显式关闭" in arb_off.get("_block_reason", ""), arb_off
    assert "显式关闭" in src_off, src_off
    st2 = run_review_route(agg, shards, meta, cfg_off)
    assert st2["skipped"] == 1 and st2["executed"] == 0, st2
    assert "显式关闭" in st2["arb_source"], st2["arb_source"]

    # 2b) 同源识别：仲裁模型与 judge[0] 相同 → 需被显式点出，且默认允许但警示
    cfg_same = {"judges": cfg["judges"],
                "arbitrator": {"model": "deepseek", "base_url": "x",
                               "api_key_env": "_NO_SUCH_KEY_", "allow_same_as_judge": True}}
    arb_same, src_same = _resolve_arbitrator_cfg(cfg_same)
    assert arb_same.get("_same_as_judge") is True, arb_same
    assert "同源" in src_same and "独立性受限" in src_same, src_same
    st_same = run_review_route(agg, shards, meta, cfg_same)
    assert st_same["arb_same_as_judge"] is True, st_same
    assert st_same["arb_model"] == "deepseek", st_same

    # 2c) 同源 + 配置禁止 → 直接拒跑并记账，理由要写清是同源而非「模型不可用」
    cfg_nosame = dict(cfg_same, arbitrator=dict(
        cfg_same["arbitrator"], allow_same_as_judge=False))
    arb_nosame, src_nosame = _resolve_arbitrator_cfg(cfg_nosame)
    assert "禁止同源仲裁" in arb_nosame.get("_block_reason", ""), arb_nosame
    st_ns = run_review_route(agg, shards, meta, cfg_nosame)
    assert st_ns["skipped"] == 1 and st_ns["executed"] == 0, st_ns
    assert any("禁止同源仲裁" in k for k in st_ns["reasons"]), st_ns["reasons"]

    # 2d) 异模型仲裁 → 不得误判同源
    cfg_diff = {"judges": cfg["judges"],
                "arbitrator": {"model": "qwen-max", "base_url": "y",
                               "api_key_env": "_NO_SUCH_KEY_2_"}}
    arb_diff, _ = _resolve_arbitrator_cfg(cfg_diff)
    assert arb_diff.get("_same_as_judge") is False, arb_diff
    assert run_review_route(agg, shards, meta, cfg_diff)["arb_same_as_judge"] is False

    # 3) 无待复核信号时不产生任何记账噪音（全共识 → need_review=False）
    ok_entry = {"matched_hlr": ["R1"], "verdict": "已落实", "name_match_status": True,
                "hlr_identity": {"bus": "1", "label": "0x10", "bit": "8", "direction": "pub"},
                "coverage": "ok", "attributes": []}
    agg_ok = aggregate({"judge_a": {"S1": dict(ok_entry)}, "judge_b": {"S1": dict(ok_entry)}},
                       meta, {}, n_configured=2)
    assert agg_ok["S1"]["aggregation"]["need_review"] is False
    assert run_review_route(agg_ok, shards, meta, cfg)["needed"] == 0

    print("✅ multi_judge_runner 复核路由自测通过（回落/显式关闭/无 key 降级均按 skipped 记账；"
          "同源识别、同源禁止拒跑、异模型不误判均符合预期）")


def main() -> int:
    ap = argparse.ArgumentParser(description="正向检查 · 多 judge 并发编排器")
    ap.add_argument("--config", required=True, help="multi_judge_config.json 路径")
    ap.add_argument("--judge-timeout", type=int, default=3600,
                    help="单个 judge 的运行预算（秒）；超时只告警不杀进程，迟到补收（默认3600）")
    ap.add_argument("--only-stage1", action="store_true",
                    help="仅运行共享阶段1（EoICD 处理），不跑各 judge")
    ap.add_argument("--output", default=None,
                    help="最终聚合报告输出目录（默认 <工程>/output）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    _normalize_ai_config(cfg)  # 让 model/base_url 支持 .env（单 judge 风格：省略或 ${AI_MODEL} 时从环境读取）
    if not cfg.get("judges"):
        print("❌ 配置中未找到 judges 列表")
        return 1

    if args.only_stage1:
        run_stage1_shared(cfg, BASE_DIR / "runs" / "integration_workspace" / "shared", dict(os.environ))
        print("✅ 仅阶段1共享完成")
        return 0

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
    out_summary = data_dir / "multi_judge_summary.json"
    out_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [汇总] 已写出：{out_summary}")
    # Word 报告：文件名带运行时间戳（保留 env MULTI_JUDGE_REPORT_NAME 覆盖能力）
    report_stem = Path(REPORT_FILE_NAME).stem
    report_path = out_dir / f"{report_stem}_{run_ts}.docx"
    generate_report(agg, summary, report_path)

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
            "excel_report": str(out_dir / f"{report_stem}_{run_ts}.xlsx"),
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
    print(f"\n{'=' * 70}")
    print(f"✅ 多 judge 聚合完成：{n_ok}/{n_total} judge 正常，{n_err} 失败")
    print(f"   信号总数：{len(agg)}；需复核信号：{n_review}")
    print(f"   总耗时：{time.time() - t0:.1f}s")
    print("  ── 汇总 ──")
    print("   " + format_summary_text(summary))
    print(f"   数据产物（runs 工作区）：{out_json}")
    print(f"   数据产物（runs 工作区）：{out_csv}")
    print(f"   汇总统计（runs 工作区）：{out_summary}")
    print(f"   交付报告（output/）：{report_path}")
    print(f"   运行清单（output/）：{manifest_path}")
    print(f"{'=' * 70}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
