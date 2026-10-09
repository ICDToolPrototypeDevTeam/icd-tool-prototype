# -*- coding: utf-8 -*-
"""正向工作区与产物：按任务隔离的代码树副本、产物定位、汇总映射。

并发隔离：正向 runner 把工作区硬编码在 ``<正向树根>/runs/integration_workspace``，
Word/Excel 落 ``<正向树根>/output``。两个并发任务共用一棵树必然互相覆盖，故每个
任务在 ``<job_dir>/forward/``（≈2MB，仅代码）下运行；续跑复用已有副本，保住 judge
已产出的中间结果（与旧正向的续跑语义一致）。任务成功后由
:func:`cleanup_run_workspace` 清副本内 ``runs/``（中间产物可达数百 MB）；失败/中断
任务不清理，供续跑复用与排查。

Excel：runner 自己不生成 xlsx（run_manifest 只记录期望路径），由
``multi_judge协同/gen_excel_report.py`` 读 runs 工作区的 aggregate/summary 生成。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from app.forward.execution import ForwardRunError
from app.forward.root import (
    DELIVERABLE_DOCX,
    DELIVERABLE_XLSX,
    EXCEL_SCRIPT,
    SUMMARY_JSON,
)

_MANIFEST_GLOB = "run_manifest_*.json"


def _ignore_runtime_dirs(_dir, names):
    """复制时排除运行时目录：input(样本 35M)、output/runs(历史产物)、
    EoICD侧数据处理/data(阶段1 中间数据 23M)、__pycache__、*.log。"""
    skipped = []
    for n in names:
        p = Path(_dir) / n
        if n in {"input", "output", "runs", "__pycache__", ".git"}:
            skipped.append(n)
        elif n == "data" and p.parent.name == "EoICD侧数据处理":
            skipped.append(n)
        elif n.endswith(".log"):
            skipped.append(n)
    return skipped


def prepare_job_tree(job_dir: Path, *, forward_root: Path, reuse: bool) -> Path:
    """把正向代码树复制到 <job_dir>/forward/；reuse=True 且已存在 → 直接复用。"""
    dst = job_dir / "forward"
    if dst.is_dir() and reuse:
        return dst
    if dst.exists():
        shutil.rmtree(dst)
    if not Path(forward_root).is_dir():
        raise ForwardRunError(f"正向代码树缺失：{forward_root}")
    shutil.copytree(forward_root, dst, ignore=_ignore_runtime_dirs)
    return dst


def run_excel_report(forward_dir: Path) -> None:
    """跑 gen_excel_report.py 生成 xlsx（读 runs 工作区，无参数）。"""
    script = forward_dir.joinpath(*EXCEL_SCRIPT)
    if not script.is_file():
        raise ForwardRunError(f"正向 Excel 脚本缺失：{script}")
    # 脚本末尾 print("✅ Excel 已写出…")：中文 Windows（GBK 管道）下缺该变量会
    # UnicodeEncodeError → exit=1（E2E 实测 trace 任务在此失败）
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, str(script)], cwd=str(forward_dir), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:]
        raise ForwardRunError(f"正向 Excel 报告生成失败（exit={proc.returncode}）：{tail}")


def find_run_outputs(forward_dir: Path) -> dict:
    """读 <forward_dir>/output 下最新的 run_manifest_*.json。"""
    out_dir = forward_dir / "output"
    manifests = [p for p in out_dir.glob(_MANIFEST_GLOB) if p.is_file()]
    if not manifests:
        raise ForwardRunError(f"未找到运行清单 run_manifest_*.json（{out_dir}）")
    latest = max(manifests, key=lambda p: p.stat().st_mtime)
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ForwardRunError(f"运行清单无法解析：{latest}（{e}）") from e


def read_forward_summary(forward_dir: Path) -> dict:
    """按 manifest 指向读 multi_judge_summary.json（缺 manifest 字段时按默认路径兜底）。"""
    manifest = find_run_outputs(forward_dir)
    raw = ((manifest.get("outputs") or {}).get("summary_json") or "").strip()
    path = Path(raw) if raw else (forward_dir / "runs" / "integration_workspace" / "multi_judge_summary.json")
    if not path.is_file():
        raise ForwardRunError(f"未找到正向汇总 multi_judge_summary.json：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ForwardRunError(f"正向汇总无法解析：{path}（{e}）") from e


def collect_deliverables(forward_dir: Path, output_dir: Path) -> dict:
    """把 Word/Excel 复制成本任务的固定交付名（前端下载接口按名取文件）。"""
    outputs = find_run_outputs(forward_dir).get("outputs") or {}
    pairs = (("word_report", DELIVERABLE_DOCX), ("excel_report", DELIVERABLE_XLSX))
    missing = [key for key, _ in pairs if not Path(str(outputs.get(key) or "")).is_file()]
    if missing:
        raise ForwardRunError(
            f"正向交付物缺失：{missing}；manifest={find_run_outputs(forward_dir)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for key, name in pairs:
        shutil.copy2(Path(str(outputs[key])), output_dir / name)
    return {"forward_xlsx": True, "forward_docx": True}


def map_summary(forward_summary: dict, *, use_trace: bool) -> dict:
    """multi_judge_summary.json → V4ForwardJobResultSummary 的 12 个字段。

    判定分布 → 前端 6 张统计卡（CompletenessResultView）：
      已落实→covered_direct、部分落实→covered_aggregate、未落实→uncovered；
      possible = 总数 − 上述三态（即 不一致/待确认/覆盖缺口 等需人工判断的余量）；
      新管线的判定世界里没有「父级引用 / 不支持 / 输入异常」三种旧口径 → 0。
    analysis_mode 保持 frontend types.ts 的 `'full' | 'trace'` 契约：按是否上传
    追溯表取 trace，否则 full（不再表示旧管线的内部分析模式）。
    """
    dist = forward_summary.get("status_distribution") or {}

    def _n(key: str) -> int:
        try:
            return int(dist.get(key, 0) or 0)
        except Exception:  # noqa: BLE001
            return 0

    covered_direct = _n("已落实")
    covered_aggregate = _n("部分落实")
    uncovered = _n("未落实")
    try:
        total = int(forward_summary.get("total", 0) or 0)
    except Exception:  # noqa: BLE001
        total = 0
    review = forward_summary.get("review_coverage") or {}
    try:
        ai_reviewed = int(review.get("reviewed", 0) or 0)
    except Exception:  # noqa: BLE001
        ai_reviewed = 0
    return {
        "analysis_mode": "trace" if use_trace else "full",
        "total_blocks": total,
        "covered_direct": covered_direct,
        "covered_aggregate": covered_aggregate,
        "parent_referenced": 0,
        "possible": max(total - covered_direct - covered_aggregate - uncovered, 0),
        "uncovered": uncovered,
        "unsupported": 0,
        "input_error": 0,
        "ai_reviewed": ai_reviewed,
        "eoicd_count": total,
        "hlr_count": 0,
    }


def cleanup_run_workspace(forward_dir: Path) -> None:
    """任务成功后清 <forward_dir>/runs 工作区（judge 中间产物，全量实测 574MB）。

    调用时机在交付物收集与汇总读取之后：下载/历史接口只读 <job>/output。
    失败/中断任务不调用本函数（副本与中间产物保留，供续跑复用与排查）；
    尽力而为——清理失败不得把已成功的任务转成失败。
    """
    shutil.rmtree(forward_dir / "runs", ignore_errors=True)


def write_summary(output_dir: Path, payload: dict) -> Path:
    """汇总快照落 <job>/output/forward_summary.json（供重启后反读，非对外下载）。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / SUMMARY_JSON
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_summary(output_dir: Path) -> Optional[dict]:
    """读汇总快照；缺失/损坏 → None（调用方按零值兜底）。"""
    path = Path(output_dir) / SUMMARY_JSON
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
