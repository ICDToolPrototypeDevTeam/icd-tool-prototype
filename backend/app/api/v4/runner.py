# -*- coding: utf-8 -*-
"""V4.0 后台线程 runner。

ADR-001 Issue A 修正 #2：
- 进入线程前先保存 JUDGE_PROVIDERS / USE_MOCK_LLM 等环境变量旧值；
- try ... finally 中按"原值是否为 None"分别 pop 或赋值恢复；
- 仅做最小保护；并发彻底隔离（thread-local env）由后续 Issue 处理。
"""
from __future__ import annotations

import json
import os
import re
import threading
import traceback
from pathlib import Path
from typing import Optional

from app.job_manager import Job, JobStatus
from app.v4.pipeline import run_reverse_pipeline
from app.v4.profiles import ProfileRegistry


# V4 输出文件路径常量（与 V4 pipeline.py 输出一致；ADR-001 §6）
V4_OUTPUT_FILES = {
    "eoicd_xlsx": "EoICD条目化清单.xlsx",
    "consistency_deepseek_docx": "EoICD与SWHLR单模型差异分析报告_DeepSeek.docx",
    "consistency_minimax_docx": "EoICD与SWHLR单模型差异分析报告_MiniMax.docx",
    "consistency_qwen_docx": "EoICD与SWHLR单模型差异分析报告_Qwen.docx",
    "consensus_docx": "EoICD与SWHLR多模型差异分析报告.docx",
}

# V4 内部使用的 JSON 中间产物；D7 不作为下载 API 暴露
V4_INTERMEDIATE_JSON = {
    "multi_judge": "multi_judge_results.json",
    "consensus": "consensus_results.json",
    "reverse_matches": "reverse_matches.json",
    "reverse_report": "reverse_report.json",
    "eoicd_requirements": "eoicd_requirements.json",
    "hlr_requirements": "hlr_requirements.json",
    "hlr_labels": "hlr_labels.json",
}

MOCK_ONLY_PROVIDERS = {"minimax", "qwen"}


def _parse_progress(message: Optional[str]) -> dict:
    """从 pipeline 写到 job.message 的字符串中解析 stage / case index。

    V4 pipeline.py 输出格式（统一 6 步）：
      - "Step 1/6: Parsing input files"
      - "Step 2/6: HLR AI labeling"
      - "Step 3/6: Reverse matching ..."
      - "Step 4/6: Multi-agent judging ..."
      - "Step 5/6: Review agent consensus ..."
      - "Step 6/6: Generating report"
    """
    out = {"stage": "", "stage_index": None, "stage_total": None, "case_index": None, "case_total": None}
    if not message:
        return out
    m = re.search(r"Step\s+([\d.]+)/(\d+)", message)
    if m:
        out["stage_index"] = int(float(m.group(1)))
        out["stage_total"] = int(m.group(2))
        si = out["stage_index"]
        if si == 1:
            out["stage"] = "parse"
        elif si == 2:
            out["stage"] = "label"
        elif si == 3:
            out["stage"] = "match"
        elif si == 4:
            out["stage"] = "multi_judge"
        elif si == 5:
            out["stage"] = "review"
        elif si == 6:
            out["stage"] = "report"
    m2 = re.search(r"\((\d+)\s*cases", message)
    if m2:
        out["case_total"] = int(m2.group(1))
    return out


def derive_outputs(output_dir: Path) -> dict:
    """扫 output_dir 检查各 V4 输出文件是否存在，返回 V4JobOutputs 字段对应 dict。"""
    return {k: (output_dir / filename).exists() for k, filename in V4_OUTPUT_FILES.items()}


def derive_mock_models(output_dir: Path) -> list[str]:
    """按 ADR-001 D5 规则从 multi_judge_results.json.providers ∩ {"minimax","qwen"} 取 mock_models。"""
    path = output_dir / V4_INTERMEDIATE_JSON["multi_judge"]
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        sources = data.get("providers", []) or []
        return [p for p in sources if p in MOCK_ONLY_PROVIDERS]
    except Exception:
        return []


def derive_consensus_summary(output_dir: Path) -> dict:
    """反读 consensus_results.json 提取 agreement / star / status 分布。"""
    out = {"agreement_distribution": {}, "star_distribution": {}, "status_distribution": {}, "average_star_rating": 0.0, "judged_count": 0, "degradation": {}}
    path = output_dir / V4_INTERMEDIATE_JSON["consensus"]
    if not path.exists():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        summary = data.get("summary", {}) or {}
        out["agreement_distribution"] = summary.get("agreement_distribution", {}) or {}
        out["star_distribution"] = summary.get("star_distribution", {}) or {}
        out["status_distribution"] = summary.get("status_distribution", {}) or {}
        out["average_star_rating"] = float(summary.get("average_star_rating", 0.0) or 0.0)
        out["judged_count"] = int(summary.get("total", 0) or 0)
        out["degradation"] = data.get("degradation", {}) or {}
    except Exception:
        pass
    return out


def derive_match_summary(output_dir: Path) -> dict:
    """反读 reverse_matches.json 提取 eoicd_blocks / matched/pending/unmatched 等计数。"""
    out = {
        "eoicd_blocks_total": 0,
        "eoicd_blocks_matched": 0,
        "matched_count": 0,
        "pending_count": 0,
        "unmatched_count": 0,
    }
    path = output_dir / V4_INTERMEDIATE_JSON["reverse_matches"]
    if not path.exists():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stats = data.get("stats", {}) or {}
        # V4 stats 键是中文："hlr_total", "hlr_已匹配", "hlr_待确定", "hlr_无匹配", "eoicd_blocks_total", "eoicd_blocks_matched"
        for k_zh, k_en in [("hlr_已匹配", "matched_count"), ("hlr_待确定", "pending_count"), ("hlr_无匹配", "unmatched_count")]:
            v = stats.get(k_zh, 0)
            try:
                out[k_en] = int(v)
            except Exception:
                out[k_en] = 0
        try:
            out["eoicd_blocks_total"] = int(stats.get("eoicd_blocks_total", 0))
        except Exception:
            pass
        try:
            out["eoicd_blocks_matched"] = int(stats.get("eoicd_blocks_matched", 0))
        except Exception:
            pass
    except Exception:
        pass
    return out


def derive_eoicd_hlr_counts(output_dir: Path) -> dict:
    """反读 eoicd_requirements.json 与 hlr_requirements.json 计数。"""
    out = {"eoicd_count": 0, "hlr_count": 0}
    eoicd_p = output_dir / V4_INTERMEDIATE_JSON["eoicd_requirements"]
    if eoicd_p.exists():
        try:
            data = json.loads(eoicd_p.read_text(encoding="utf-8"))
            out["eoicd_count"] = int(data.get("total_after_dedup", 0))
        except Exception:
            pass
    hlr_p = output_dir / V4_INTERMEDIATE_JSON["hlr_requirements"]
    if hlr_p.exists():
        try:
            data = json.loads(hlr_p.read_text(encoding="utf-8"))
            out["hlr_count"] = int(data.get("total_count", 0))
        except Exception:
            pass
    return out


def run_v4_pipeline_thread(
    job: Job,
    job_dir: Path,
    hlr_path: Path,
    publisher_path: Optional[Path],
    subscriber_path: Optional[Path],
    trace_dir: Optional[Path],
    judge_providers: list[str],
    use_mock_llm: bool,
    controller_profile: str = "ams",
) -> None:
    """在后台线程内跑 V4 反向管线；带 env 保存/恢复；异常 → job.status=FAILED。"""
    output_dir = job_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # —— Issue #63 / Task 12: 加载 controller_profile（runner 在 backend/app/api/v4/runner.py，
    #     profiles 目录位于 backend/app/v4/profiles/；parents[2] 解析到 backend/app/）——
    reg = ProfileRegistry()
    reg.load_all(Path(__file__).resolve().parents[2] / "v4" / "profiles")
    profile = reg.get_or_raise(controller_profile)

    # —— ADR-001 Issue A 修正 #2：进入线程前保存旧 env；finally 中按 None/赋值恢复 ——
    saved_judge_providers = os.environ.get("JUDGE_PROVIDERS")
    saved_use_mock_llm = os.environ.get("USE_MOCK_LLM")
    try:
        if judge_providers:
            os.environ["JUDGE_PROVIDERS"] = ",".join(judge_providers)
        if use_mock_llm is not None:
            os.environ["USE_MOCK_LLM"] = "1" if use_mock_llm else "0"

        job.update(JobStatus.RUNNING, "Step 1/6: Parsing input files")

        # 调 V4 in-process 流水线
        result = run_reverse_pipeline(
            hlr=hlr_path,
            eoicd_json=None,  # 缓存路径不在 API 暴露，避免触发 _v4_backend_raw 已澄清的设计假设分歧
            publisher=publisher_path,
            subscriber=subscriber_path,
            output_dir=output_dir,
            job=job,
            trace_dir=trace_dir,
            profile=profile,
        )

        # —— 反读落盘 JSON 派生结构化字段（避免在 runner 中实现 V4 Pydantic 序列化） ——
        outputs = derive_outputs(output_dir)
        mock_models = derive_mock_models(output_dir)
        consensus = derive_consensus_summary(output_dir)
        match_stats = derive_match_summary(output_dir)
        counts = derive_eoicd_hlr_counts(output_dir)

        # 拼装 job.result
        job.result = {
            # V3 兼容字段
            "requirement_count": counts.get("hlr_count", 0),
            "difference_count": 0,
            # V4 输出（5 类对外）
            **outputs,
            "eoicd_count": counts.get("eoicd_count", 0),
            "eoicd_blocks_total": match_stats["eoicd_blocks_total"],
            "eoicd_blocks_matched": match_stats["eoicd_blocks_matched"],
            "hlr_count": counts.get("hlr_count", 0),
            "matched_count": match_stats["matched_count"],
            "pending_count": match_stats["pending_count"],
            "unmatched_count": match_stats["unmatched_count"],
            "judged_count": consensus["judged_count"],
            "star_distribution": consensus["star_distribution"],
            "status_distribution": {**consensus["status_distribution"], "无匹配": match_stats["unmatched_count"]},
            "average_star_rating": consensus["average_star_rating"],
            "mock_models": mock_models,
            "degradation": consensus.get("degradation", {}),
            "errors": [],
        }
        job.update(JobStatus.COMPLETED, "V4 reverse pipeline complete")
    except Exception as e:
        job.result = {
            "requirement_count": 0,
            "difference_count": 0,
            "eoicd_xlsx": (output_dir / V4_OUTPUT_FILES["eoicd_xlsx"]).exists(),
            "consistency_deepseek_docx": (output_dir / V4_OUTPUT_FILES["consistency_deepseek_docx"]).exists(),
            "consistency_minimax_docx": (output_dir / V4_OUTPUT_FILES["consistency_minimax_docx"]).exists(),
            "consistency_qwen_docx": (output_dir / V4_OUTPUT_FILES["consistency_qwen_docx"]).exists(),
            "consensus_docx": (output_dir / V4_OUTPUT_FILES["consensus_docx"]).exists(),
            "mock_models": [],
            "degradation": {},
            "errors": [f"{type(e).__name__}: {e}"],
        }
        job.update(JobStatus.FAILED, f"V4 pipeline failed: {type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        # —— ADR-001 Issue A 修正 #2：env 恢复 ——
        if saved_judge_providers is None:
            os.environ.pop("JUDGE_PROVIDERS", None)
        else:
            os.environ["JUDGE_PROVIDERS"] = saved_judge_providers
        if saved_use_mock_llm is None:
            os.environ.pop("USE_MOCK_LLM", None)
        else:
            os.environ["USE_MOCK_LLM"] = saved_use_mock_llm


def launch_v4_pipeline(
    job: Job,
    job_dir: Path,
    hlr_path: Path,
    publisher_path: Optional[Path],
    subscriber_path: Optional[Path],
    trace_dir: Optional[Path],
    judge_providers: list[str],
    use_mock_llm: bool,
    controller_profile: str = "ams",
) -> threading.Thread:
    """工厂：返回后台线程对象；前端已启动并发由 daemon 线程承载。"""
    t = threading.Thread(
        target=run_v4_pipeline_thread,
        args=(job, job_dir, hlr_path, publisher_path, subscriber_path, trace_dir, judge_providers, use_mock_llm, controller_profile),
        daemon=True,
    )
    t.start()
    return t
