# -*- coding: utf-8 -*-
"""正向任务编排：把「代码树副本 + 配置 + 子进程 + 产物」串成一次 V4 任务。

职责边界：本模块只做编排与翻译，不含任何正向业务算法（算法全在 backend/forward/）；
失败一律抛异常，由 runner 的管线线程统一转成任务失败（错误文案进任务日志）。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from app.forward import artifacts, config_builder, projects, root
from app.forward.config_builder import write_config
from app.forward.execution import run_multi_judge
from app.forward.mock_server import ensure_mock_server
from app.job_manager import Job, JobStatus
# 非规范 OOXML 包归一化：纯 I/O 工具（零业务算法，非 app.v4 业务模块）——
# 反向 BUG-20260923-001 同款组件，正向按 BUG-20261010-001 复用
from app.v4.parsers.zip_entry_normalize import ensure_standard_zip

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


def _normalize_upload_inputs(paths: Iterable[Optional[Path]]) -> list[str]:
    """把非规范 OOXML 上传包（zip 条目名含反斜杠，如第三方 Office 重存件）就地归一化。

    复用反向同款组件（app/v4/parsers/zip_entry_normalize，BUG-20260923-001）。该组件
    默认把规范副本写到 <目录>/_normalized/ 交由调用方改指；但正向 runner 按「输入所在
    目录」扫描追溯表等配套文件（backend/forward/run_integration.py 的 src_dirs =
    pub/sub 父目录），输入路径改指子目录会漏配配套文件——故这里把规范副本压回原位
    （同名同目录替换）。任务输入目录是每题一份的 scratch 副本，就地替换不影响用户
    本机原件；已规范的包原样直通，重复调用幂等。返回被归一化的文件名（空 = 未命中）。
    """
    fixed: list[str] = []
    for raw in paths:
        if not raw:
            continue
        path = Path(raw)
        norm_path, changed = ensure_standard_zip(path)
        if not changed:
            continue
        # 覆盖已存在的原文件：Windows 上目标刚被读过时，杀软/索引会短暂持有句柄，
        # replace 偶发 WinError 5（实测：读后立即 replace 必失败，隔 1s 即通过）；
        # Linux 是原子 rename 无此问题。小步重试覆盖该窗口。
        for attempt in range(5):
            try:
                os.replace(norm_path, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)
        try:
            norm_path.parent.rmdir()  # _normalized/ 已清空则顺手移除
        except OSError:
            pass
        fixed.append(path.name)
    return fixed


def run_forward_job(job: Job, job_dir: Path, params: ForwardJobParams, *,
                    forward_root: Optional[Path] = None) -> None:
    """跑一次正向多 judge 分析；成功写 job.result 并置 COMPLETED，失败抛异常。"""
    src_root = Path(forward_root) if forward_root is not None else root.FORWARD_ROOT

    # —— 上传包归一化：非规范 OOXML（zip 条目名含反斜杠，Linux 上读不出部件）就地
    #    修复后再进流程；不修的话各 judge 会在解析输入时全挂、最终报误导性错误 ——
    fixed_inputs = _normalize_upload_inputs(
        (params.hlr_path, params.publisher_path, params.subscriber_path, *params.trace_files))
    if fixed_inputs:
        print(f"  [fix] 非规范 OOXML 上传包已归一化（zip 条目名含反斜杠）："
              f"{'、'.join(fixed_inputs)}")

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
    # 续跑：把本任务已累计的 token 作为 base 传给 runner，报告里写「整个任务」口径
    base_tokens = max(0, int(getattr(job, "total_tokens", 0) or 0))
    run_multi_judge(
        job, forward_dir=fwd, config_path=config_path, output_dir=fwd / "output",
        judge_total=len(cfg.get("judges") or []),
        base_tokens=base_tokens,
        judge_timeout=_env_int(_JUDGE_TIMEOUT_ENV, 3600),
        timeout=_total_timeout(), env_extra=child_env,
    )
    # —— Excel 附件兜底（V3 起 runner 已自动生成；此处显式调用脚本 main()，同名覆盖写） ——
    artifacts.run_excel_report(fwd)

    # —— 产物收集 + 汇总映射 + 快照落盘（jobs.py 重启后按快照反读） ——
    outputs = artifacts.collect_deliverables(fwd, job_dir / "output")
    mapped = artifacts.map_summary(artifacts.read_forward_summary(fwd), use_trace=use_trace)
    mapped.update(outputs)
    mapped["errors"] = []

    # —— token 对账（必须在 cleanup_run_workspace 删掉 runs/ 之前） ——
    # 实时值来自父进程累加的 [tokens] delta；汇总值来自 runner 的 sidecar。
    # 两者任一路径可能缺失（输出被截断 / 进程被强杀没来得及写 sidecar），
    # 因此取「本轮增量」的较大值，再叠加续跑前的 base；用绝对赋值而非累加，
    # 避免把同一次消耗算两遍。
    summary_usage = mapped.get("token_usage") or {}
    summary_total = max(0, int(summary_usage.get("total_tokens", 0) or 0))
    live_total = max(0, int(getattr(job, "total_tokens", 0) or 0))
    live_delta = max(live_total - base_tokens, 0)
    summary_run = max(summary_total - base_tokens, 0)
    run_total = max(live_delta, summary_run)
    final_total = base_tokens + run_total
    mapped["token_usage"] = {"total_tokens": final_total}
    set_token_usage = getattr(job, "set_token_usage", None)
    if callable(set_token_usage):
        set_token_usage(final_total, force_flush=True)
    if live_delta and summary_run and abs(live_delta - summary_run) > max(
            100, int(0.1 * max(live_delta, summary_run))):
        print(f"  [tokens] 实时增量({live_delta})与汇总增量({summary_run})不一致，取较大值")

    artifacts.write_summary(job_dir / "output", mapped)
    job.result = mapped
    # —— 成功收尾：清 runs 工作区（交付物已复制、汇总已读；失败/中断任务不走这里，
    #    副本与中间产物保留供续跑与排查） ——
    artifacts.cleanup_run_workspace(fwd)
    job.update(JobStatus.COMPLETED, "V4 forward pipeline complete")
