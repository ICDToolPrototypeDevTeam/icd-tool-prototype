# -*- coding: utf-8 -*-
"""正向任务编排：把「代码树副本 + 配置 + 子进程 + 产物」串成一次 V4 任务。

职责边界：本模块只做编排与翻译，不含任何正向业务算法（算法全在 backend/forward/）；
失败一律抛异常，由 runner 的管线线程统一转成任务失败（错误文案进任务日志）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.forward import artifacts, config_builder, projects, root
from app.forward.config_builder import write_config
from app.forward.execution import run_multi_judge
from app.forward.mock_server import ensure_mock_server
from app.job_manager import Job, JobStatus

# 单 judge 软预算（秒；透传 --judge-timeout）
_JUDGE_TIMEOUT_ENV = "FORWARD_JUDGE_TIMEOUT"
# 整轮墙钟安全网（秒；0/缺省 = 不限，避免误杀长任务）
_TOTAL_TIMEOUT_ENV = "FORWARD_TOTAL_TIMEOUT"


@dataclass(frozen=True)
class ForwardJobParams:
    """一次正向任务的输入参数（由 runner 从请求参数构造）。"""

    hlr_path: Path
    publisher_path: Optional[Path]
    subscriber_path: Optional[Path]
    analysis_mode: str
    use_mock_llm: Optional[bool]
    trace_files: tuple[Path, ...] = ()
    controller_profile: Optional[str] = None


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def _total_timeout() -> Optional[float]:
    raw = (os.environ.get(_TOTAL_TIMEOUT_ENV) or "0").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    return value if value > 0 else None


def run_forward_job(job: Job, job_dir: Path, params: ForwardJobParams, *,
                    forward_root: Optional[Path] = None) -> None:
    """跑一次正向多 judge 分析；成功写 job.result 并置 COMPLETED，失败抛异常。"""
    src_root = Path(forward_root) if forward_root is not None else root.FORWARD_ROOT

    # —— 系统类型：显式选择优先，否则按上传文件名自动识别（失败即任务失败） ——
    filenames = [p.name for p in (params.hlr_path, params.publisher_path,
                                  params.subscriber_path, *params.trace_files) if p]
    if (params.controller_profile or "").strip():
        project = projects.resolve_project(params.controller_profile)
    else:
        project = projects.detect_project(filenames)
    job.update(JobStatus.RUNNING, f"Step 1/4: 解析输入文件（系统类型：{project}）")

    # —— 每任务代码树副本（并发隔离；续跑复用保住中间结果） ——
    fwd = artifacts.prepare_job_tree(job_dir, forward_root=src_root,
                                     reuse=bool(getattr(job, "resumed", False)))
    use_trace = params.analysis_mode == "trace" and bool(params.trace_files)

    # —— 配置生成：mock 走本地 mock 服务；真实走 .env 三家密钥（缺 key 直接失败） ——
    if bool(getattr(job, "mock", True)):
        ensure_mock_server()
        cfg, child_env = config_builder.build_mock_config(
            project=project, eoicd_pub=params.publisher_path, eoicd_sub=params.subscriber_path,
            hlr=params.hlr_path, use_trace=use_trace)
    else:
        cfg, child_env = config_builder.build_real_config(
            project=project, eoicd_pub=params.publisher_path, eoicd_sub=params.subscriber_path,
            hlr=params.hlr_path, use_trace=use_trace)
    config_path = write_config(cfg, job_dir / "forward_config.json")

    # —— 子进程跑多 judge（共享阶段1 + N judge 并行 + 聚合/仲裁/复查 + Word） ——
    run_multi_judge(
        job, forward_dir=fwd, config_path=config_path, output_dir=fwd / "output",
        judge_total=len(cfg.get("judges") or []),
        judge_timeout=_env_int(_JUDGE_TIMEOUT_ENV, 3600),
        timeout=_total_timeout(), env_extra=child_env,
    )
    # —— Excel 附件（runner 不生成；由正向自带脚本读 runs 工作区产出） ——
    artifacts.run_excel_report(fwd)

    # —— 产物收集 + 汇总映射 + 快照落盘（jobs.py 重启后按快照反读） ——
    outputs = artifacts.collect_deliverables(fwd, job_dir / "output")
    mapped = artifacts.map_summary(artifacts.read_forward_summary(fwd), use_trace=use_trace)
    mapped.update(outputs)
    mapped["errors"] = []
    artifacts.write_summary(job_dir / "output", mapped)
    job.result = mapped
    # —— 成功收尾：清 runs 工作区（交付物已复制、汇总已读；失败/中断任务不走这里，
    #    副本与中间产物保留供续跑与排查） ——
    artifacts.cleanup_run_workspace(fwd)
    job.update(JobStatus.COMPLETED, "V4 forward pipeline complete")
