# -*- coding: utf-8 -*-
"""V4.0 后台线程 runner。

ADR-001 Issue A 修正 #2（多输入批量并行 Step 1 收口）：
- ``USE_MOCK_LLM`` 不再写进程 env，改由 :mod:`app.runtime_context` 在本任务的
  线程内绑定 —— 原先「同一时刻只能有一个分析任务在跑（单飞）」的限制随之解除；
- ``JUDGE_PROVIDERS`` 的 env 保存/恢复保留原样：pipeline 读的是 import 期常量，
  该写入对本次运行无影响（单开 Issue 处理）。

多输入批量并行 Step 2：``launch_*_pipeline`` 不再直接起 daemon 线程，改为交给
:mod:`app.job_scheduler` 的进程内队列（并发上限 ``MAX_CONCURRENT_JOBS``，默认 2）。
"""
from __future__ import annotations

import functools
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Optional

from app import job_scheduler
from app.forward import artifacts as forward_artifacts
from app.forward.adapter import ForwardJobParams, run_forward_job
from app.job_log import (
    LOG_FILE_NAME,
    bind_job_log,
    config_lines,
    job_log_store,
    restore_job_log,
)
from app.job_manager import Job, JobCancelled, JobStatus, job_manager
from app.runtime_context import RuntimeContext, bind_runtime, restore_runtime
from app.v4.errors import classify_pipeline_error
from app.v4.llm.factory import use_mock_llm as mock_mode_enabled
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

# 参数快照中被视为「上传文件」的 key（用于任务列表展示 + 恢复前存在性校验）
_INPUT_FILE_PARAM_KEYS = (
    "hlr_path", "publisher_path", "subscriber_path",
)

# 追溯表的旧快照键：Issue #119 早期为两个固定单文件字段，现为 trace_files 列表。
# 兼容读取是为了历史任务列表展示与旧任务续跑（快照已落盘，改不掉）。
_LEGACY_TRACE_PARAM_KEYS = ("device_icd_trace_file", "system_device_trace_file")


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


def _trace_paths_from_params(job_dir: Path, params: dict) -> tuple[Path, ...]:
    """还原快照中的追溯表路径：新式 ``trace_files`` 列表；旧式两个单文件键兼容。

    与 :func:`input_filenames_from_params` 同一份兼容口径，仅多一层存在性校验
    （续跑时记录在案但已删除的文件按 FileNotFoundError 处理）。
    """
    trace = params.get("trace_files")
    rels = list(trace) if isinstance(trace, list) else [
        params.get(k) for k in _LEGACY_TRACE_PARAM_KEYS]
    return tuple(p for p in (_require_file(job_dir, r) for r in rels if r) if p is not None)


def _begin_job_run(job: Job, job_dir: Path, requested_mock: Optional[bool]) -> Optional[tuple]:
    """进入管线线程的统一开场：绑定日志上下文 + 写日志头 + 记录模式与来源。

    必须在调用方 :func:`app.runtime_context.bind_runtime` **之后**调用，否则记录
    的模式不准（``mock_mode_enabled()`` 读的是线程内绑定值）。
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
    # 记录本任务线程 ident，供强制终止按 ident 注入 JobCancelled。放在 try 之外：
    # 这只是可观测/可控制性元数据，不得因它失败而丢掉日志绑定
    try:
        job.bind_thread()
    except Exception:  # noqa: BLE001 — stub job（既有测试）没有该方法时忽略
        pass
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


def input_filenames_from_params(params: Optional[dict]) -> list[str]:
    """从参数快照取出上传文件名（不含目录）。

    与 :func:`job_input_filenames` 同源，区别只在数据来源：历史结果列表读的是
    磁盘 manifest 里的 ``params``，那时内存中并没有对应的 ``Job`` 对象（进程
    重启后跑完的任务不会被启动扫描载入）。

    追溯表兼容两套快照形状：新式为 ``trace_files`` 列表，旧式（Issue #119 早期）
    为 device_icd_trace_file / system_device_trace_file 两个单文件键。
    """
    params = params or {}
    names = []
    for key in _INPUT_FILE_PARAM_KEYS:
        value = params.get(key)
        if value:
            names.append(Path(value).name)
    trace = params.get("trace_files")
    rels = list(trace) if isinstance(trace, list) else [
        params.get(k) for k in _LEGACY_TRACE_PARAM_KEYS]
    names.extend(Path(r).name for r in rels if r)
    return names


def job_input_filenames(job: Job) -> list[str]:
    """任务列表展示用：从参数快照取出上传文件名（不含目录）。"""
    return input_filenames_from_params(job.params)


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


def _reuse_parse_inputs(
    job: Job, output_dir: Path, hlr_path: Path
) -> tuple[Path, Optional[Path]]:
    """续跑时改传已落盘的解析产物，走管线自带的 ``[skip]`` 分支。

    Step 1 的解析（Excel/Word → JSON）不含 LLM 调用，产物落盘后即可复用：
    实测每次续跑省 ~26s（38s → 11s）。复用条件必须是**续跑**（job.resumed）——
    首跑时同名文件可能是上一轮的残留，当成解析结果用会让新上传的输入文件
    完全不生效。两份产物缺一不可：只复用其中一份会把两次不同的输入拼在一起。

    Returns:
        ``(hlr_arg, eoicd_json_arg)``。不满足复用条件时返回
        ``(hlr_path, None)``，与改动前的调用完全一致。
    """
    if not job.resumed:
        return hlr_path, None
    eoicd_json = output_dir / V4_INTERMEDIATE_JSON["eoicd_requirements"]
    hlr_json = output_dir / V4_INTERMEDIATE_JSON["hlr_requirements"]
    if eoicd_json.exists() and hlr_json.exists():
        return hlr_json, eoicd_json
    return hlr_path, None


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
    """在后台线程内跑 V4 反向管线；线程内绑定运行上下文；异常 → job.status=FAILED。"""
    output_dir = job_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # —— Issue #63 / Task 12: 加载 controller_profile（runner 在 backend/app/api/v4/runner.py，
    #     profiles 目录位于 backend/app/v4/profiles/；parents[2] 解析到 backend/app/）——
    reg = ProfileRegistry()
    reg.load_all(Path(__file__).resolve().parents[2] / "v4" / "profiles")
    profile = reg.get_or_raise(controller_profile)

    # 运行参数在本任务自己的线程内绑定（不再写进程 env，故任务可并发而不串台）；
    # 绑定是单次赋值，放在 try 之前，finally 中无条件恢复。
    prev_runtime = bind_runtime(RuntimeContext(use_mock_llm=use_mock_llm))
    saved_judge_providers = os.environ.get("JUDGE_PROVIDERS")
    prev_binding = None
    try:
        if judge_providers:
            os.environ["JUDGE_PROVIDERS"] = ",".join(judge_providers)

        # 必须在绑定运行上下文之后调用：_begin_job_run 读取生效后的 mock 模式
        prev_binding = _begin_job_run(job, job_dir, use_mock_llm)

        job.update(JobStatus.RUNNING, "Step 1/6: Parsing input files")

        # refine 仅 RPDU profile 启用，no_refine 可关闭（与 CLI --no-refine 一致做 A-B 对照）
        refine = (profile.profile_id == "rpdu") and (not no_refine)

        # 调 V4 in-process 流水线
        # 续跑时改传已落盘的解析产物（首跑仍传原始输入，eoicd_json=None）
        hlr_arg, eoicd_json_arg = _reuse_parse_inputs(job, output_dir, hlr_path)
        result = run_reverse_pipeline(
            hlr=hlr_arg,
            eoicd_json=eoicd_json_arg,
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
        # 两个计数由管线随 PipelineResult 回传，不再反读 eoicd_requirements.json：
        # 那份 JSON 有 87.9MB，反读一次峰值 +163~247MB（BUG-20260923-008）。
        # getattr：测试桩的 run_reverse_pipeline 可能返回 None（既有契约），
        # 真实管线正常返回时两个字段必有值。
        counts = {
            "eoicd_count": getattr(result, "eoicd_count", 0),
            "hlr_count": getattr(result, "hlr_count", 0),
        }

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
        # 摘掉线程 ident（ident 会被后续线程复用，见 Job.unbind_thread）
        try:
            job.unbind_thread()
        except Exception:  # noqa: BLE001 — stub job 没有该方法时忽略
            pass
        # 恢复线程绑定，避免污染同线程的后续任务
        if prev_binding is not None:
            restore_job_log(prev_binding)
        restore_runtime(prev_runtime)
        # —— ADR-001 Issue A 修正 #2：JUDGE_PROVIDERS env 恢复 ——
        if saved_judge_providers is None:
            os.environ.pop("JUDGE_PROVIDERS", None)
        else:
            os.environ["JUDGE_PROVIDERS"] = saved_judge_providers


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
) -> None:
    """工厂：落盘参数快照 + 登记，然后把任务交给进程内队列（job_scheduler）。

    并发上限由 ``MAX_CONCURRENT_JOBS`` 控制（默认 2）。此前是「登记后立即起
    daemon 线程」——单个任务没问题，批量提交会把部署机（2C2G）同时压上多个
    300~500MB 的管线。返回值恒为 None（三个调用方均不使用）。
    """
    # 落盘参数快照（供进程重启后按原参数继续执行）；必须在入队之前完成，
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
    # 登记即「已承诺运行」：在此之前失败的上传请求不会留下 pending 幽灵任务
    # （见 JobManager.new_job）。
    job_manager.register(job)
    job_scheduler.submit(job, functools.partial(
        run_v4_pipeline_thread,
        job, job_dir, hlr_path, publisher_path, subscriber_path, trace_dir,
        judge_providers, use_mock_llm, controller_profile, no_refine,
    ))


def relaunch_from_manifest(job: Job, job_dir: Path) -> None:
    """按参数快照重启一个被中断的任务。

    参数完全取自 ``job.params``（不接受调用方覆盖），保证恢复后的 label 缓存
    等语义与首次运行一致。先校验输入文件，缺失则抛 FileNotFoundError 且任务
    状态保持不变；校验通过后才入队（不预置 running —— 入队即落 pending +
    排队提示，任务真正开跑要等队列出空位，由管线线程入口落 running）。
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

    # 复位取消标志：同一进程内续跑一个已终止的任务时，Job 仍带着上次取消置位
    # 的 Event（``from_manifest`` 的复位只在进程重启重建 Job 时发生）。不复位
    # 则管线第一个检查点立刻再抛 JobCancelled。
    job.clear_cancel()
    # 恢复运行标记 + 计数重置（二次恢复不得累加上一轮的计数）
    job.resumed = True
    job.reuse = {"reused": 0, "rerun": 0}
    # 不在此处预置 RUNNING：入队由 job_scheduler.submit 统一落 pending + 排队提示，
    # 排队期间前端如实显示「等待开始 / 任务已排队…」；真正开跑由管线线程入口
    # 落 running + Step 1/N（否则排队等待时会被显示成「正在分析」）。

    if job.task_type == "completeness":
        return launch_forward_pipeline(
            job=job,
            job_dir=job_dir,
            hlr_path=hlr_path,
            publisher_path=publisher_path,
            subscriber_path=subscriber_path,
            analysis_mode=params.get("analysis_mode", "full"),
            trace_files=_trace_paths_from_params(job_dir, params),
            use_mock_llm=params.get("use_mock_llm"),
            controller_profile=params.get("controller_profile"),
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

def derive_forward_outputs(output_dir: Path) -> dict:
    """检查正向完整性分析两个对外文件是否存在。"""
    return {k: (output_dir / filename).exists() for k, filename in FORWARD_OUTPUT_FILES.items()}


# 正向汇总零值基线：新任务由 forward_summary.json 提供；Issue #119 之前的历史任务
# 只有旧口径的中间 JSON，数字无法映射到新口径，故不再反读（显示零值而非错误数字）。
_FORWARD_SUMMARY_ZERO = {
    "analysis_mode": "", "total_blocks": 0, "covered_direct": 0, "covered_aggregate": 0,
    "parent_referenced": 0, "possible": 0, "uncovered": 0, "unsupported": 0,
    "input_error": 0, "ai_reviewed": 0, "eoicd_count": 0, "hlr_count": 0,
}


def derive_forward_summary(output_dir: Path) -> dict:
    """读新正向管线落盘的 forward_summary.json（快照）；缺失 → 零值。"""
    out = dict(_FORWARD_SUMMARY_ZERO)
    data = forward_artifacts.read_summary(output_dir)
    if isinstance(data, dict):
        for key in out:
            if key in data and data[key] is not None:
                out[key] = data[key]
    return out


def run_forward_pipeline_thread(job: Job, job_dir: Path, params: ForwardJobParams) -> None:
    """在后台线程内跑正向多 judge 一致性分析；线程内绑定运行上下文；异常 → FAILED。"""
    prev_runtime = bind_runtime(RuntimeContext(use_mock_llm=params.use_mock_llm))
    prev_binding = None
    try:
        # 必须在绑定运行上下文之后调用：_begin_job_run 读取生效后的 mock 模式
        prev_binding = _begin_job_run(job, job_dir, params.use_mock_llm)
        run_forward_job(job, job_dir, params)
    except JobCancelled as e:
        # 必须先于 Exception：JobCancelled 继承 BaseException（同反向管线）
        _fail_job(job, e, "V4 forward pipeline")
    except Exception as e:
        output_dir = job_dir / "output"
        job.result = {
            "forward_xlsx": (output_dir / FORWARD_OUTPUT_FILES["forward_xlsx"]).exists(),
            "forward_docx": (output_dir / FORWARD_OUTPUT_FILES["forward_docx"]).exists(),
            "analysis_mode": params.analysis_mode,
            "total_blocks": 0,
            "errors": [f"{type(e).__name__}: {e}"],
        }
        _fail_job(job, e, "V4 forward pipeline")
    finally:
        # 摘掉线程 ident（ident 会被后续线程复用，见 Job.unbind_thread）
        try:
            job.unbind_thread()
        except Exception:  # noqa: BLE001 — stub job 没有该方法时忽略
            pass
        if prev_binding is not None:
            restore_job_log(prev_binding)
        restore_runtime(prev_runtime)


def launch_forward_pipeline(
    job: Job,
    job_dir: Path,
    hlr_path: Path,
    publisher_path: Optional[Path],
    subscriber_path: Optional[Path],
    analysis_mode: str,
    trace_files: tuple[Path, ...],
    use_mock_llm: Optional[bool],
    controller_profile: Optional[str] = None,
) -> None:
    """工厂：落盘参数快照 + 登记，然后交给进程内队列；语义同 launch_v4_pipeline。

    参数与落盘字段保持与旧正向一致（续跑/completeness 端点兼容），仅新增
    controller_profile（Issue #119 系统类型下拉）；追溯表为单字段多文件
    ``trace_files`` 列表（EPS 4 层链需 3 张表），旧快照双键由
    :func:`_trace_paths_from_params` 兼容读取。
    """
    params = ForwardJobParams(
        hlr_path=hlr_path,
        publisher_path=publisher_path,
        subscriber_path=subscriber_path,
        analysis_mode=analysis_mode,
        use_mock_llm=use_mock_llm,
        trace_files=tuple(trace_files),
        controller_profile=controller_profile,
    )
    job.set_dir(job_dir, {
        "hlr_path": _rel_path(job_dir, hlr_path),
        "publisher_path": _rel_path(job_dir, publisher_path),
        "subscriber_path": _rel_path(job_dir, subscriber_path),
        "analysis_mode": analysis_mode,
        "trace_files": [_rel_path(job_dir, p) for p in trace_files],
        "use_mock_llm": use_mock_llm,
        "controller_profile": controller_profile,
    })
    job_manager.register(job)
    job_scheduler.submit(job, functools.partial(
        run_forward_pipeline_thread, job, job_dir, params,
    ))
