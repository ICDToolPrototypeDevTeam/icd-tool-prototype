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
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

from app.job_log import (
    LOG_FILE_NAME,
    bind_job_log,
    config_lines,
    job_log_store,
    restore_job_log,
)
from app.job_manager import Job, JobCancelled, JobStatus
from app.v4.errors import classify_pipeline_error
from app.v4.llm.factory import use_mock_llm as mock_mode_enabled
from app.v4.pipeline import run_forward_pipeline, run_reverse_pipeline
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

# 参数快照中被视为「上传文件」的 key（用于任务列表展示 + 恢复前存在性校验）
_INPUT_FILE_PARAM_KEYS = (
    "hlr_path", "publisher_path", "subscriber_path",
    "device_icd_trace_file", "system_device_trace_file",
)


def _rel_path(job_dir: Path, p: Optional[Path]) -> Optional[str]:
    """绝对路径 → 相对 job_dir 的 POSIX 相对路径（输出目录整体搬移后仍可用）。"""
    if p is None:
        return None
    resolved = Path(p).resolve()
    try:
        return resolved.relative_to(Path(job_dir).resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _abs_path(job_dir: Path, rel: Optional[str]) -> Optional[Path]:
    if not rel:
        return None
    p = Path(rel)
    return p if p.is_absolute() else Path(job_dir) / p


def _require_file(job_dir: Path, rel: Optional[str]) -> Optional[Path]:
    """还原参数快照中的文件路径；记录在案但已不存在 → FileNotFoundError。"""
    p = _abs_path(job_dir, rel)
    if p is not None and not p.exists():
        raise FileNotFoundError(str(p))
    return p


def _begin_job_run(job: Job, job_dir: Path, requested_mock: Optional[bool]) -> Optional[tuple]:
    """进入管线线程的统一开场：绑定日志上下文 + 写日志头 + 记录模式与来源。

    必须在调用方设置 ``USE_MOCK_LLM`` **之后**调用，否则记录的模式不准。
    ``requested_mock`` 是前端传来的参数（``None`` 表示未传、沿用容器配置）；
    记下来源是为了让「容器配置」与「前端开关」谁生效在日志面板里一眼可辨 ——
    本次实测中曾因本地容器未关闭而误判 mock 状态，正是缺少这条信息。
    返回上一层线程绑定，供 finally 中 :func:`restore_job_log`；返回 ``None``
    表示「本次未绑定成功」，调用方据此跳过恢复。

    **本函数刻意不抛异常**：日志绑定与日志头只是可观测性，不得让一个正常任务
    失败（约束见 spec「日志/进度持久化失败不得影响主流程」）。调用方传入的对象
    未必是完整 Job（既有测试用只实现 ``update`` 的 stub 驱动本线程），因此注册
    或取字段失败时一律降级为「不进任务日志」。

    降级告警写进该任务**自己的** buffer，用户据此在日志面板与 ``job.log`` 里
    看到「日志为什么是空的」；只有连 buffer 都没拿到的残余情形才退回 stderr，
    那条通道对用户不可见（详见下方分支注释）。
    """
    prev: Optional[tuple] = None
    buf = None
    try:
        buf = job_log_store.register(job.job_id, job_dir / LOG_FILE_NAME)
        prev = bind_job_log(job.job_id, job)
        job.mock = mock_mode_enabled()
        source = '前端参数' if requested_mock is not None else '继承容器配置'
        buf.append(
            f'===== 任务开始 task={job.task_type} mock={job.mock} (来源: {source}) '
            f'resumed={job.resumed} job_id={job.job_id} =====',
            'info',
        )
        for line in config_lines():
            buf.append(line, 'info')
        return prev
    except Exception as e:  # noqa: BLE001 — 见 docstring：日志失败不得影响任务
        if prev is not None:
            restore_job_log(prev)
        warning = f'[job] 任务日志绑定失败，本次降级为不进任务日志: {type(e).__name__}: {e}'
        if buf is not None:
            # 已经拿到该任务的 buffer：告警写进它，用户就能在日志面板与 job.log
            # 里看到「日志为什么是空的」。这条写入同为 best-effort —— 告警自身
            # 失败不得影响任务，因此就地兜住，绝不外抛。
            try:
                buf.append(warning, 'error')
            except Exception:  # noqa: BLE001 — 告警写不进去就放弃，绝不外抛
                pass
        else:
            # 残余情形：连该任务的 buffer 都没拿到（register 本身失败，或调用方
            # 传入的不是完整 Job）。此时告警只能进 stderr；装了 Tee 后它落在
            # global_buffer，而该 buffer 没有任何接口暴露给前端、服务上也没有
            # SSH —— 这条告警**用户看不到**。不假装它可见：此处只保证不静默
            # （留在容器日志里），用户侧的可见性由后续任务承接。
            print(warning, file=sys.stderr)
        return None


def _fail_job(job: Job, exc: BaseException, label: str) -> None:
    """统一的失败收尾：分类 → 结构化错误 → 状态。

    ``JobCancelled`` 单独走 canceled 分支：它是用户主动终止，不是失败，
    也不应写 ``job.result``（半成品不得被当成结果展示）。
    """
    progress = _merged_progress(job)
    stage = progress.get('stage') or ''
    stage_index = progress.get('stage_index')
    error = classify_pipeline_error(exc, stage=stage, stage_index=stage_index)

    if isinstance(exc, JobCancelled):
        job.set_error(error)
        job.update(JobStatus.CANCELED, '任务已被用户终止')
        print(f'[job] cancelled by user at stage={stage or "(未知)"}', file=sys.stderr)
        return

    job.set_error(error)
    job.update(JobStatus.FAILED, f'{label} failed: {type(exc).__name__}: {exc}')
    traceback.print_exc()


def job_input_filenames(job: Job) -> list[str]:
    """任务列表展示用：从参数快照取出上传文件名（不含目录）。"""
    names = []
    for key in _INPUT_FILE_PARAM_KEYS:
        value = (job.params or {}).get(key)
        if value:
            names.append(Path(value).name)
    return names


def _parse_progress(message: Optional[str]) -> dict:
    """从 pipeline 写到 job.message 的字符串中解析 stage / case index。

    V4 pipeline.py 输出格式（反向 6 步 / 正向 8 步）：
      反向（correctness，6 步）：
      - "Step 1/6: Parsing input files"
      - "Step 2/6: HLR AI labeling"
      - "Step 3/6: Reverse matching ..."
      - "Step 4/6: Multi-agent judging ..."
      - "Step 5/6: Review agent consensus ..."
      - "Step 6/6: Generating report"
      正向（completeness，8 步）：
      - "Step 1/8: Parsing input files"
      - "Step 2/8: Forward scope"
      - "Step 3/8: Building forward ICD blocks"
      - "Step 4/8: Building HLR identity index"
      - "Step 5/8: Candidate recall"
      - "Step 6/8: Deterministic coverage judgment"
      - "Step 7/8: AI three-state review"
      - "Step 8/8: Consolidating coverage + generating reports"
    """
    out = {"stage": "", "stage_index": None, "stage_total": None, "case_index": None, "case_total": None}
    if not message:
        return out
    m = re.search(r"Step\s+([\d.]+)/(\d+)", message)
    if m:
        out["stage_index"] = int(float(m.group(1)))
        out["stage_total"] = int(m.group(2))
        si = out["stage_index"]
        if out["stage_total"] == 8:
            # 正向完整性分析（8 步）
            stage_names = {
                1: "parse", 2: "scope", 3: "blocks", 4: "identity_index",
                5: "candidate_recall", 6: "deterministic", 7: "ai_review", 8: "report",
            }
            out["stage"] = stage_names.get(si, "")
        else:
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


def _merged_progress(job: Job) -> dict:
    """合并 pipeline 上报的结构化进度与 message 正则兜底。

    I-1：错误分类（_fail_job）与状态查询（jobs.get_v4_job_status）必须用
    同一个 stage 来源，否则同一次失败的 error.stage 会为空、而顶层 stage 非空。
    """
    progress = dict(job.progress or {})
    for key, value in _parse_progress(job.message).items():
        progress.setdefault(key, value)
    return progress


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
    use_mock_llm: Optional[bool],
    controller_profile: str = "ams",
    no_refine: bool = False,
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
    prev_binding = None
    try:
        if judge_providers:
            os.environ["JUDGE_PROVIDERS"] = ",".join(judge_providers)
        # 注意：USE_MOCK_LLM 是进程级变量，本任务在 finally 之前一直「占有」它。
        # 由此本工具假定同一时刻只有一个分析任务在跑（单飞）：两个任务重叠时，
        # 后提交者会改写先提交者的 mock 模式，两边的日志与结果页警告都会失真（I-2）。
        if use_mock_llm is not None:
            os.environ["USE_MOCK_LLM"] = "1" if use_mock_llm else "0"

        # 必须在设置 env 之后调用：_begin_job_run 会读取生效后的 USE_MOCK_LLM
        prev_binding = _begin_job_run(job, job_dir, use_mock_llm)

        job.update(JobStatus.RUNNING, "Step 1/6: Parsing input files")

        # refine 仅 RPDU profile 启用，no_refine 可关闭（与 CLI --no-refine 一致做 A-B 对照）
        refine = (profile.profile_id == "rpdu") and (not no_refine)

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
            refine=refine,
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
    except JobCancelled as e:
        # 必须先于 Exception：JobCancelled 继承 BaseException，本不会被下面的
        # except Exception 捕获；显式列出是为了让取消走 canceled 而非 failed 分支。
        _fail_job(job, e, "V4 pipeline")
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
        _fail_job(job, e, "V4 pipeline")
    finally:
        # 恢复线程绑定，避免污染同线程的后续任务
        if prev_binding is not None:
            restore_job_log(prev_binding)
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
    use_mock_llm: Optional[bool],
    controller_profile: str = "ams",
    no_refine: bool = False,
) -> threading.Thread:
    """工厂：返回后台线程对象；前端已启动并发由 daemon 线程承载。"""
    # 落盘参数快照（供进程重启后按原参数继续执行）；必须在 start 之前完成，
    # 保证 POST 返回时 manifest 已存在。
    job.set_dir(job_dir, {
        "hlr_path": _rel_path(job_dir, hlr_path),
        "publisher_path": _rel_path(job_dir, publisher_path),
        "subscriber_path": _rel_path(job_dir, subscriber_path),
        "trace_dir": _rel_path(job_dir, trace_dir),
        "judge_providers": list(judge_providers),
        "use_mock_llm": use_mock_llm,
        "controller_profile": controller_profile,
        "no_refine": no_refine,
    })
    t = threading.Thread(
        target=run_v4_pipeline_thread,
        args=(job, job_dir, hlr_path, publisher_path, subscriber_path, trace_dir, judge_providers, use_mock_llm, controller_profile, no_refine),
        daemon=True,
    )
    t.start()
    return t


def relaunch_from_manifest(job: Job, job_dir: Path) -> threading.Thread:
    """按参数快照重启一个被中断的任务。

    参数完全取自 ``job.params``（不接受调用方覆盖），保证恢复后的 label 缓存
    等语义与首次运行一致。先校验输入文件，缺失则抛 FileNotFoundError 且任务
    状态保持不变；校验通过后才置 RUNNING 并启动线程。
    """
    params = job.params or {}
    hlr_path = _require_file(job_dir, params.get("hlr_path"))
    publisher_path = _require_file(job_dir, params.get("publisher_path"))
    subscriber_path = _require_file(job_dir, params.get("subscriber_path"))
    if hlr_path is None or (publisher_path is None and subscriber_path is None):
        raise FileNotFoundError("manifest missing required input paths")

    trace_dir = _abs_path(job_dir, params.get("trace_dir"))
    if trace_dir is not None and not trace_dir.is_dir():
        raise FileNotFoundError(str(trace_dir))

    # 恢复运行标记 + 计数重置（二次恢复不得累加上一轮的计数）
    job.resumed = True
    job.reuse = {"reused": 0, "rerun": 0}
    job.update(JobStatus.RUNNING, "任务继续执行中")

    if job.task_type == "completeness":
        device_icd = _require_file(job_dir, params.get("device_icd_trace_file"))
        system_device = _require_file(job_dir, params.get("system_device_trace_file"))
        return launch_forward_pipeline(
            job=job,
            job_dir=job_dir,
            hlr_path=hlr_path,
            publisher_path=publisher_path,
            subscriber_path=subscriber_path,
            analysis_mode=params.get("analysis_mode", "full"),
            device_icd_trace_file=device_icd,
            system_device_trace_file=system_device,
            use_mock_llm=params.get("use_mock_llm"),
        )

    return launch_v4_pipeline(
        job=job,
        job_dir=job_dir,
        hlr_path=hlr_path,
        publisher_path=publisher_path,
        subscriber_path=subscriber_path,
        trace_dir=trace_dir,
        judge_providers=list(params.get("judge_providers") or []),
        use_mock_llm=params.get("use_mock_llm"),
        controller_profile=params.get("controller_profile", "ams"),
        no_refine=bool(params.get("no_refine", False)),
    )


# ============================================================
# Forward completeness (EoICD → HLR)
# ============================================================

# 正向完整性分析对外输出文件（2 类；ADR-001 D7 不对外暴露中间 JSON）
FORWARD_OUTPUT_FILES = {
    "forward_xlsx": "EoICD至HLR正向完整性分析明细.xlsx",
    "forward_docx": "EoICD至HLR正向完整性分析报告.docx",
}

# 正向分析内部 JSON 中间产物（分阶段落盘；仅供反读派生，不对外下载）
FORWARD_INTERMEDIATE_JSON = {
    "forward_coverage": "forward_coverage.json",
    "forward_scope": "forward_scope.json",
    "forward_blocks": "forward_blocks.json",
    "hlr_identity_index": "hlr_identity_index.json",
    "forward_candidates": "forward_candidates.json",
    "forward_deterministic": "forward_deterministic.json",
    "forward_ai_review": "forward_ai_review.json",
}


def derive_forward_outputs(output_dir: Path) -> dict:
    """检查正向完整性分析两个对外文件是否存在。"""
    return {k: (output_dir / filename).exists() for k, filename in FORWARD_OUTPUT_FILES.items()}


def derive_forward_summary(output_dir: Path) -> dict:
    """反读 forward_coverage.json 提取覆盖分布 + AI 复核计数。"""
    out = {
        "analysis_mode": "",
        "total_blocks": 0,
        "covered_direct": 0,
        "covered_aggregate": 0,
        "parent_referenced": 0,
        "possible": 0,
        "uncovered": 0,
        "unsupported": 0,
        "input_error": 0,
        "ai_reviewed": 0,
    }
    coverage_p = output_dir / FORWARD_INTERMEDIATE_JSON["forward_coverage"]
    if coverage_p.exists():
        try:
            data = json.loads(coverage_p.read_text(encoding="utf-8"))
            out["analysis_mode"] = data.get("analysis_mode", "") or ""
            stats = data.get("stats", {}) or {}
            for key in (
                "covered_direct", "covered_aggregate", "parent_referenced",
                "possible", "uncovered", "unsupported", "input_error",
            ):
                try:
                    out[key] = int(stats.get(key, 0))
                except Exception:
                    pass
        except Exception:
            pass
    blocks_p = output_dir / FORWARD_INTERMEDIATE_JSON["forward_blocks"]
    if blocks_p.exists():
        try:
            data = json.loads(blocks_p.read_text(encoding="utf-8"))
            out["total_blocks"] = int(data.get("total_blocks", 0))
        except Exception:
            pass
    ai_p = output_dir / FORWARD_INTERMEDIATE_JSON["forward_ai_review"]
    if ai_p.exists():
        try:
            data = json.loads(ai_p.read_text(encoding="utf-8"))
            out["ai_reviewed"] = int(data.get("total_reviewed", 0))
        except Exception:
            pass
    return out


def run_forward_pipeline_thread(
    job: Job,
    job_dir: Path,
    hlr_path: Path,
    publisher_path: Optional[Path],
    subscriber_path: Optional[Path],
    analysis_mode: str,
    device_icd_trace_file: Optional[Path],
    system_device_trace_file: Optional[Path],
    use_mock_llm: Optional[bool],
) -> None:
    """在后台线程内跑 V4 正向完整性管线；带 env 保存/恢复；异常 → FAILED。"""
    output_dir = job_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    saved_use_mock_llm = os.environ.get("USE_MOCK_LLM")
    prev_binding = None
    try:
        # 注意：USE_MOCK_LLM 是进程级变量，本任务在 finally 之前一直「占有」它。
        # 由此本工具假定同一时刻只有一个分析任务在跑（单飞）：两个任务重叠时，
        # 后提交者会改写先提交者的 mock 模式，两边的日志与结果页警告都会失真（I-2）。
        if use_mock_llm is not None:
            os.environ["USE_MOCK_LLM"] = "1" if use_mock_llm else "0"

        # 必须在设置 env 之后调用：_begin_job_run 会读取生效后的 USE_MOCK_LLM
        prev_binding = _begin_job_run(job, job_dir, use_mock_llm)

        job.update(JobStatus.RUNNING, "Step 1/8: Parsing input files")

        result = run_forward_pipeline(
            hlr=hlr_path,
            eoicd_json=None,
            publisher=publisher_path,
            subscriber=subscriber_path,
            output_dir=output_dir,
            job=job,
            analysis_mode=analysis_mode,
            device_icd_trace_file=device_icd_trace_file,
            system_device_trace_file=system_device_trace_file,
        )

        outputs = derive_forward_outputs(output_dir)
        summary = derive_forward_summary(output_dir)
        counts = derive_eoicd_hlr_counts(output_dir)

        job.result = {
            **outputs,
            "analysis_mode": summary["analysis_mode"],
            "total_blocks": summary["total_blocks"],
            "covered_direct": summary["covered_direct"],
            "covered_aggregate": summary["covered_aggregate"],
            "parent_referenced": summary["parent_referenced"],
            "possible": summary["possible"],
            "uncovered": summary["uncovered"],
            "unsupported": summary["unsupported"],
            "input_error": summary["input_error"],
            "ai_reviewed": summary["ai_reviewed"],
            "eoicd_count": counts.get("eoicd_count", 0),
            "hlr_count": counts.get("hlr_count", 0),
            "errors": [],
        }
        job.update(JobStatus.COMPLETED, "V4 forward pipeline complete")
    except JobCancelled as e:
        # 必须先于 Exception：JobCancelled 继承 BaseException，本不会被下面的
        # except Exception 捕获；显式列出是为了让取消走 canceled 而非 failed 分支。
        _fail_job(job, e, "V4 forward pipeline")
    except Exception as e:
        job.result = {
            "forward_xlsx": (output_dir / FORWARD_OUTPUT_FILES["forward_xlsx"]).exists(),
            "forward_docx": (output_dir / FORWARD_OUTPUT_FILES["forward_docx"]).exists(),
            "analysis_mode": analysis_mode,
            "total_blocks": 0,
            "errors": [f"{type(e).__name__}: {e}"],
        }
        _fail_job(job, e, "V4 forward pipeline")
    finally:
        if prev_binding is not None:
            restore_job_log(prev_binding)
        if saved_use_mock_llm is None:
            os.environ.pop("USE_MOCK_LLM", None)
        else:
            os.environ["USE_MOCK_LLM"] = saved_use_mock_llm


def launch_forward_pipeline(
    job: Job,
    job_dir: Path,
    hlr_path: Path,
    publisher_path: Optional[Path],
    subscriber_path: Optional[Path],
    analysis_mode: str,
    device_icd_trace_file: Optional[Path],
    system_device_trace_file: Optional[Path],
    use_mock_llm: Optional[bool],
) -> threading.Thread:
    """工厂：返回正向后台线程对象。"""
    # 落盘参数快照，语义同反向管线（见 launch_v4_pipeline）。
    job.set_dir(job_dir, {
        "hlr_path": _rel_path(job_dir, hlr_path),
        "publisher_path": _rel_path(job_dir, publisher_path),
        "subscriber_path": _rel_path(job_dir, subscriber_path),
        "analysis_mode": analysis_mode,
        "device_icd_trace_file": _rel_path(job_dir, device_icd_trace_file),
        "system_device_trace_file": _rel_path(job_dir, system_device_trace_file),
        "use_mock_llm": use_mock_llm,
    })
    t = threading.Thread(
        target=run_forward_pipeline_thread,
        args=(
            job, job_dir, hlr_path, publisher_path, subscriber_path,
            analysis_mode, device_icd_trace_file, system_device_trace_file, use_mock_llm,
        ),
        daemon=True,
    )
    t.start()
    return t
