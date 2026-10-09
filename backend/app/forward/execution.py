# -*- coding: utf-8 -*-
"""正向多 judge 子进程执行层：启动 / 进度解析 / 取消 / 杀进程树 / 失败兜底。

进度口径（4 步，前端 ProcessingView.STAGE_LABELS 已有 parse/multi_judge/review/report）：
  1 parse       阶段1 共享（EoICD 数据处理）
  2 multi_judge 各 judge 并行判定（case_index / case_total = 已完成 judge 数 / judge 总数）
  3 review      Step5 仲裁 + Step5.5 peer-aware 复查
  4 report      聚合产物 + Word/Excel 报告

取消与超时：轮询 loop 内主动 `job.raise_if_cancelled()`；无论取消、超时、异常还是
正常结束，finally 都 `_kill_tree` 兜底——正向 runner 自己还会 fork 阶段2 子进程，
只杀直接子进程会留下孤儿继续跑模型。
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Mapping, Optional

from app.job_log import job_log_context
from app.job_manager import Job, JobCancelled


class ForwardRunError(RuntimeError):
    """正向子进程非 0 退出 / 超时 / 未产出预期文件。"""


# runner 的进度标记（文案是 backend/forward/multi_judge_runner.py 的 print 契约）
_RE_STAGE1 = re.compile(r"▶ \[阶段1 共享\]")
_RE_JUDGE_START = re.compile(r"# ▶ judge\[(\d+)\]\s*(\S+)")
_RE_JUDGE_DONE = re.compile(r"✅ judge (\S+) 完成")
_RE_REVIEW = re.compile(r"\[复核路由\]")
_RE_AGG_DONE = re.compile(r"✅ 多 judge 聚合完成")
# 报告阶段起点：复核记账 + 汇总落盘后即进入最耗时的 Word 报告生成（generate_report）
_RE_REPORT_START = re.compile(r"\[汇总\] 已写出")


def _kill_tree(proc: subprocess.Popen) -> None:
    """终止子进程及其全部后代（尽力而为 + 二次兜底）。"""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, check=False)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:  # noqa: BLE001 — 进程可能已退出
        pass
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


def _spawn_kwargs() -> dict:
    """Windows 用新进程组（配合 taskkill /T）；POSIX 用新会话（配合 killpg）。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def run_multi_judge(
    job: Job,
    *,
    forward_dir: Path,
    config_path: Path,
    output_dir: Path,
    judge_total: int,
    judge_timeout: int = 3600,
    timeout: Optional[float] = None,
    env_extra: Optional[Mapping[str, str]] = None,
    on_started: Optional[Callable[[subprocess.Popen], None]] = None,
) -> None:
    """在 forward_dir 下跑 multi_judge_runner.py，实时把进度/日志转给 job。

    judge_timeout 透传 --judge-timeout（单 judge 软预算，runner 自己处理迟到补收）；
    timeout 是**整轮**墙钟上限（None = 不限，作为安全网）；env_extra 注入明文密钥。
    on_started 仅测试用（捕获子进程句柄以断言终止结果）。
    """
    cmd = [sys.executable, str(forward_dir / "multi_judge_runner.py"),
           "--config", str(config_path), "--output", str(output_dir),
           "--judge-timeout", str(int(judge_timeout))]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # 必须无缓冲：进度标记都是 runner 自身 print 的，块缓冲会把标记行压到进程退出才
    # 到达父进程（E2E 实测整轮停在「Step 1/4」）；judge/阶段子进程继承同 env，一并生效。
    env.setdefault("PYTHONUNBUFFERED", "1")
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})

    job.set_progress(stage="parse", stage_index=1, stage_total=4,
                     message="Step 1/4: 解析输入文件（阶段1 共享）", force_flush=True)
    proc = subprocess.Popen(
        cmd, cwd=str(forward_dir), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, **_spawn_kwargs(),
    )
    if on_started is not None:
        on_started(proc)

    completed = 0

    def _pump() -> None:
        """把子进程输出转进任务日志（Tee→日志面板）并解析进度标记。"""
        nonlocal completed
        try:
            with job_log_context(job.job_id, job):
                for line in iter(proc.stdout.readline, ""):
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    print(line)
                    if _RE_STAGE1.search(line):
                        job.set_progress(stage="parse", stage_index=1, stage_total=4,
                                         message="Step 1/4: 解析输入文件（阶段1 共享）")
                        continue
                    m = _RE_JUDGE_START.search(line)
                    if m:
                        job.set_progress(stage="multi_judge", stage_index=2, stage_total=4,
                                         case_total=judge_total, case_index=completed,
                                         message=f"Step 2/4: 多模型裁判判定（{m.group(2)}）")
                        continue
                    if _RE_JUDGE_DONE.search(line):
                        completed += 1
                        job.set_progress(
                            stage="multi_judge", stage_index=2, stage_total=4,
                            case_total=judge_total, case_index=completed,
                            message=f"Step 2/4: 多模型裁判判定（已完成 {completed}/{judge_total}）",
                            force_flush=True)
                        continue
                    if _RE_REVIEW.search(line):
                        job.set_progress(stage="review", stage_index=3, stage_total=4,
                                         message="Step 3/4: 共识复核（Step5 仲裁 + Step5.5 复查）")
                        continue
                    if _RE_REPORT_START.search(line):
                        job.set_progress(stage="report", stage_index=4, stage_total=4,
                                         message="Step 4/4: 生成报告")
                        continue
                    if _RE_AGG_DONE.search(line):
                        job.set_progress(stage="report", stage_index=4, stage_total=4,
                                         message="Step 4/4: 生成报告")
        except Exception:  # noqa: BLE001 — 读流失败不得打断主循环
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass

    reader = threading.Thread(target=_pump, name="forward-runner-log", daemon=True)
    reader.start()
    started = time.monotonic()
    rc: Optional[int] = None
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            job.raise_if_cancelled()
            if timeout and (time.monotonic() - started) > timeout:
                raise ForwardRunError(f"正向多 judge 运行超时（>{int(timeout)}s）")
            time.sleep(0.5)
    finally:
        _kill_tree(proc)          # 正常结束时 poll() 已非 None → 立即返回
        reader.join(timeout=10)
    if rc != 0:
        raise ForwardRunError(f"正向多 judge 运行失败（exit={rc}）；详见任务日志")
