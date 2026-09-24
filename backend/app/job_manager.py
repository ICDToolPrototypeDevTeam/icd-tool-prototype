# -*- coding: utf-8 -*-
"""V4.0 任务状态管理。

Job 状态默认只在内存；当调用方通过 ``Job.set_dir()`` 绑定了任务输出目录（API
路径），此后每次状态变更都会额外原子写一份 ``job.json`` manifest，使进程重启
后仍能重建任务并支持「继续 / 放弃」。CLI 等未绑定目录的调用方行为与之前完全
一致（不落盘）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from app.job_log import current_job


MANIFEST_NAME = 'job.json'
MANIFEST_SCHEMA_VERSION = 1

# set_progress 的落盘节流窗口：每个 case 都写一次磁盘会让 12 万条场景产生
# 大量原子写；阶段切换或超过该间隔才落盘（内存值始终即时更新）。
PROGRESS_PERSIST_INTERVAL_S = 5.0

# 终态：进入后写入 finished_at
_TERMINAL_STATUSES = frozenset({
    'completed', 'failed', 'canceled', 'abandoned',
})

# 保护 manifest 写盘（写盘来自管线线程，启动扫描来自主线程）
_LOCK = threading.Lock()


class JobStatus(str, Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    # 进程重启时由启动扫描写入：任务未跑完，进程就消失了
    INTERRUPTED = 'interrupted'
    # 用户主动放弃继续（不删除任何文件）
    ABANDONED = 'abandoned'
    # 用户在前端主动终止（不删除任何文件；与 ABANDONED 语义不同：
    # ABANDONED 指「中断任务被用户放弃」，本状态指「运行中的任务被终止」）
    CANCELED = 'canceled'


class JobCancelled(BaseException):
    """用户请求终止任务。

    刻意继承 ``BaseException`` 而非 ``Exception``：仓库内存在十余处
    ``except Exception`` 兜底（``comparison/semantic_judge.py``、
    ``comparison/re_review.py``、``matching/hlr_labeler.py`` 等）。若继承
    ``Exception``，取消会被这些兜底吞成「一条失败判定」，取消静默失效。
    与 ``KeyboardInterrupt`` / ``asyncio.CancelledError`` 的处理惯例一致。

    调用方必须**先**捕获 ``JobCancelled``，再捕获 ``Exception``。
    """


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


class Job:
    def __init__(self, task_type: str = "correctness"):
        self.job_id: str = str(uuid.uuid4())
        self.task_type: str = task_type          # "correctness" | "completeness"
        self.status: JobStatus = JobStatus.PENDING
        self.message: Optional[str] = None
        self.created_at: datetime = datetime.now(timezone.utc)
        self.updated_at: datetime = datetime.now(timezone.utc)
        self.result: Optional[dict] = None
        # 任务输出目录与参数快照；为 None 时不落盘（CLI 场景）
        self.job_dir: Optional[Path] = None
        self.params: dict = {}
        # 恢复运行标记与实时复用计数（API 路径使用；随 manifest 持久化）
        self.resumed: bool = False
        self.reuse: Optional[dict] = None
        # —— 可观测性与任务控制（Issue：服务器可观测与任务控制）——
        # 结构化进度：由 pipeline 每步 / 每 case 上报，避免从 message 正则解析
        self.progress: Optional[dict] = None
        # 结构化失败信息：{category, title, stage, ..., hint, at}
        self.error: Optional[dict] = None
        # 本次运行是否 MOCK 模式（结果页据此提示「模拟数据不可用于验收」）
        self.mock: bool = False
        self.finished_at: Optional[datetime] = None
        # 取消：Event 供管线检查点轮询，cancel_requested 供前端展示「正在终止…」
        self.cancel_event = threading.Event()
        self.cancel_requested: bool = False
        # 强制终止（不等待检查点）：hard_killed 后本任务的一切回写都被忽略；
        # thread_ident 是本任务管线线程的 ident，供注入 JobCancelled
        self.hard_killed: bool = False
        self.thread_ident: Optional[int] = None
        # 节流窗口的起点取「构造时刻」：若取 0.0，首条进度必然满足
        # ``now - 0.0 >= 5.0``（time.monotonic 是系统运行时长），
        # 于是每个任务的第一次上报都会写盘，节流对首个 case 形同虚设。
        self._last_progress_persist: float = time.monotonic()

    def set_dir(self, job_dir: Path, params: dict) -> None:
        """绑定输出目录与任务参数快照，并立即落盘 manifest。

        参数快照只存相对 job_dir 的路径 + 不可从产物反推的运行参数
        （judge_providers / use_mock_llm / controller_profile 等），
        供进程重启后按原参数重建任务。
        """
        self.job_dir = Path(job_dir)
        self.params = dict(params)
        self._persist()

    def _persist(self) -> None:
        """原子写 manifest；任何 I/O / 序列化异常都不得影响任务本身。

        ``set_progress`` 在管线热循环里被每个 case 调用一次，落盘失败（磁盘满、
        输出目录被删、Windows 文件锁、值不可序列化）若向上抛，会被 ``runner``
        的 ``except Exception`` 转成 FAILED —— 进度持久化失败就影响了主流程。
        因此与 :meth:`app.job_log.LogBuffer._persist` 一致，静默降级为「仅内存」。
        """
        if self.job_dir is None:
            return
        payload = {
            'schema_version': MANIFEST_SCHEMA_VERSION,
            'job_id': self.job_id,
            'task_type': self.task_type,
            'status': self.status.value,
            'message': self.message,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'params': self.params,
            'resumed': self.resumed,
            'reuse': self.reuse,
            'progress': self.progress,
            'error': self.error,
            'mock': self.mock,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'cancel_requested': self.cancel_requested,
        }
        path = self.job_dir / MANIFEST_NAME
        try:
            text = json.dumps(payload, indent=2, ensure_ascii=False)
            with _LOCK:
                _atomic_write(path, text)
        except Exception:  # noqa: BLE001 — 磁盘满 / 权限不足 / 目录被删时静默降级为「仅内存」
            pass

    def update(self, status: JobStatus, message: Optional[str] = None):
        if self.hard_killed:
            # 强制终止后：线程可能还卡在某个 C 调用里多跑一会儿，它的回写
            # 一律忽略——否则状态会被改回 running/completed，半成品会被当成结果
            return
        self.status = status
        if message is not None:
            self.message = message
        self.updated_at = datetime.now(timezone.utc)
        if status.value in _TERMINAL_STATUSES:
            self.finished_at = self.updated_at
        self._persist()

    def set_reuse_stats(self, reused: int, rerun: int) -> None:
        """更新恢复运行的实时复用计数并落盘（管线每完成一个 case 调用一次）。"""
        self.reuse = {'reused': int(reused), 'rerun': int(rerun)}
        self.updated_at = datetime.now(timezone.utc)
        self._persist()

    def set_progress(
        self,
        *,
        message: Optional[str] = None,
        stage: Optional[str] = None,
        stage_index: Optional[int] = None,
        stage_total: Optional[int] = None,
        case_index: Optional[int] = None,
        case_total: Optional[int] = None,
        force_flush: bool = False,
    ) -> None:
        """上报结构化进度。内存立即更新；job.json 按窗口节流落盘。

        ``message`` 与既有契约一致（``_parse_progress`` 仍在解析它），额外的
        stage_* / case_* 字段让前端不必再靠正则从 message 里猜。
        """
        if self.hard_killed:
            # 已强制终止：不让「正在生成报告」之类的进度文字盖在已终止的任务上
            return
        if message is not None:
            self.message = message
        progress = dict(self.progress or {})
        for key, value in (
            ('stage', stage),
            ('stage_index', stage_index),
            ('stage_total', stage_total),
            ('case_index', case_index),
            ('case_total', case_total),
        ):
            if value is not None:
                progress[key] = value
        progress['message'] = self.message
        self.progress = progress
        self.updated_at = datetime.now(timezone.utc)

        now = time.monotonic()
        if force_flush or (now - self._last_progress_persist) >= PROGRESS_PERSIST_INTERVAL_S:
            self._last_progress_persist = now
            self._persist()

    def set_error(self, error: dict) -> None:
        """记录结构化失败信息；``result`` 已存在时同步刷新其 ``errors`` 字段。

        刻意**不**为不存在的 ``result`` 建字典：取消的任务不得留下任何
        ``job.result``，否则半成品有被当作结果展示的风险（spec §6.C）。
        """
        if self.hard_killed:
            # 强制终止后的异常来自我们自己的注入，不是真实失败信息，不覆盖终止说明
            return
        self.error = dict(error)
        if self.result is not None:
            self.result['errors'] = [
                f"{error.get('error_type', 'Error')}: {error.get('message', '')}"
            ]
        self.updated_at = datetime.now(timezone.utc)
        self._persist()

    def request_cancel(self) -> None:
        """请求终止任务（协作式：管线在检查点抛出 JobCancelled）。

        **不覆盖** ``self.message``：前端仍靠它解析「当前在第几步」。
        """
        self.cancel_event.set()
        self.cancel_requested = True
        self.updated_at = datetime.now(timezone.utc)
        self._persist()

    def raise_if_cancelled(self) -> None:
        """取消检查点：被请求取消时抛出 JobCancelled。"""
        if self.cancel_event.is_set():
            raise JobCancelled('任务已被用户终止')

    def bind_thread(self) -> None:
        """记录本任务管线线程的 ident（供强制终止注入异常）。"""
        self.thread_ident = threading.get_ident()

    def unbind_thread(self) -> None:
        """线程退出前解除绑定。

        ident 在**线程完全退出后**可能被后续线程复用，不摘掉的话，一次迟到的
        强制终止会把异常打进另一个任务（很可能是另一次「重新执行」）的线程里。
        线程的 ``finally`` 一定早于线程退出，因此这里摘掉后就不存在这个窗口。
        """
        self.thread_ident = None

    def force_cancel(self) -> bool:
        """强制终止：立即置终态，并尽力中断本任务的管线线程。

        与 :meth:`request_cancel` 的区别是**不等检查点**：适用于管线卡在没有
        检查点的地方（例如报告生成里的长循环）。完成后本任务的一切回写（状态、
        进度、错误、结果）都被 ``hard_killed`` 挡掉，因此即使线程还在某个 C 调用
        里多跑几十秒，也不会把状态改回去、更不会写出半成品结果。

        只作用于**本任务自己的**线程（按 ident 注入），不重启进程、不改全局状态、
        不影响其他任务。

        返回是否真的注入成功（无绑定线程 / 线程已结束时为 ``False``，此时任务
        仍然已经被标记为终止）。
        """
        # 先落终态再置 hard_killed：update() 自己也被 hard_killed 挡着
        self.cancel_event.set()
        self.cancel_requested = True
        self.update(JobStatus.CANCELED, '已强制终止（不保留结果，不支持续跑）')
        self.hard_killed = True
        return self._inject_cancel()

    def _inject_cancel(self) -> bool:
        """向本任务线程注入 ``JobCancelled``（CPython 私有接口，尽力而为）。

        线程正在执行的 Python 字节码边界上会立刻抛出该异常，因此纯 Python 循环
        （解析逐行、报告逐行写入）是毫秒级退出；若线程正阻塞在 C 调用 / 网络
        读取里，异常要等那次调用返回才送达——这也是本方法只承诺「尽力而为」的
        原因，任务状态不依赖它（``force_cancel`` 已先落了终态）。
        """
        ident = self.thread_ident
        if not ident:
            return False
        import ctypes

        set_async_exc = ctypes.pythonapi.PyThreadState_SetAsyncExc
        set_async_exc.argtypes = [ctypes.c_ulong, ctypes.py_object]
        set_async_exc.restype = ctypes.c_int
        try:
            # 传类而不是实例：CPython 会用无参构造，JobCancelled() 合法
            count = set_async_exc(ctypes.c_ulong(ident), ctypes.py_object(JobCancelled))
        except Exception:  # noqa: BLE001 — 注入失败不影响「已终止」这个事实
            return False
        if count > 1:
            # 不该发生：一次只应命中一个线程。命中多个说明 ident 有歧义，立即撤销
            try:
                set_async_exc(ctypes.c_ulong(ident), None)
            except Exception:  # noqa: BLE001
                pass
            return False
        return count == 1

    def clear_cancel(self) -> None:
        """复位取消标志，使任务可以再次运行（续跑已终止的任务前调用）。

        取消分两步落位：``cancel_event`` 置位 + ``cancel_requested`` 落盘。
        进程重启重建 Job 时由 ``from_manifest`` 复位，但**同一进程内**续跑一个
        已终止的任务没有这道保障 —— 不复位则续跑后的第一个检查点会立刻再抛
        ``JobCancelled``，任务刚从 ``canceled`` 转 ``running`` 就又变回去。
        """
        self.cancel_event.clear()
        self.cancel_requested = False
        # 强制终止留下的 hard_killed 也必须复位：它挡着本任务的一切状态回写，
        # 不复位的话，任务一旦被「继续」就会永远停在 canceled（用户点了继续却毫无反应）
        self.hard_killed = False
        self._persist()

    @classmethod
    def from_manifest(cls, data: dict, job_dir: Path) -> 'Job':
        """按 manifest 重建 Job；字段缺失 / 格式非法时抛异常，由调用方跳过。"""
        job = cls(task_type=data.get('task_type', 'correctness'))
        job.job_id = data['job_id']
        job.status = JobStatus(data.get('status', 'pending'))
        job.message = data.get('message')
        job.created_at = datetime.fromisoformat(data['created_at'])
        job.updated_at = datetime.fromisoformat(data['updated_at'])
        job.job_dir = Path(job_dir)
        job.params = data.get('params') or {}
        job.resumed = bool(data.get('resumed', False))
        raw_reuse = data.get('reuse')
        job.reuse = raw_reuse if isinstance(raw_reuse, dict) else None
        raw_progress = data.get('progress')
        job.progress = raw_progress if isinstance(raw_progress, dict) else None
        raw_error = data.get('error')
        job.error = raw_error if isinstance(raw_error, dict) else None
        job.mock = bool(data.get('mock', False))
        raw_finished = data.get('finished_at')
        job.finished_at = datetime.fromisoformat(raw_finished) if raw_finished else None
        # 刻意不恢复「已请求取消」：重启后新进程应从干净状态开始
        job.cancel_requested = False
        return job


def _bound_job() -> Optional[Job]:
    """当前线程绑定的 Job；CLI / 单元测试未绑定时为 None。"""
    job = current_job()
    return job if isinstance(job, Job) else None


def raise_if_cancelled() -> None:
    """取消检查点（模块级）。

    供无法直接拿到 ``job`` 对象的深层层级（``_judge_with_degradation``、
    ``re_review``、``coverage_reviewer``）调用。无绑定任务时静默 no-op，
    因此 CLI 路径行为完全不变。
    """
    job = _bound_job()
    if job is not None:
        job.raise_if_cancelled()


def report_progress(**kwargs) -> None:
    """进度上报（模块级）；无绑定任务时静默 no-op。"""
    job = _bound_job()
    if job is not None:
        job.set_progress(**kwargs)


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create_job(self, task_type: str = "correctness") -> Job:
        """Create a new Job."""
        job = Job(task_type=task_type)
        self._jobs[job.job_id] = job
        return job

    def new_job(self, task_type: str = "correctness") -> Job:
        """构造一个**尚未登记**的任务（不进任务列表）。

        上传接口按此创建：请求还要经过「保存上传文件」与「自动识别系统类型」
        两步，任一步失败（文件过大 413、追溯表格式错 422、识别不出系统类型）请求
        就结束了，管线线程根本不会启动。若在创建时就登记，这条记录会永远停在
        ``pending``：前端显示「等待开始」，没有线程可终止，也不满足 abandon 的
        门槛 —— 用户只能看着它，没有任何操作能去掉它。登记因此推迟到真正要启动
        线程的 :meth:`register`（由 ``launch_*_pipeline`` 调用）。
        """
        return Job(task_type=task_type)

    def register(self, job: Job) -> Job:
        """把任务登记进任务列表（幂等）。"""
        self._jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def load_interrupted(self, v4_root: Path) -> int:
        """启动扫描：把磁盘上未跑完的任务标记为 interrupted 并载入内存。

        进程启动时调用一次。内存状态已随上一个进程消失，因此任何仍处于
        pending / running 的 manifest 都必然来自上次进程 → 判定为被中断。
        ``updated_at`` 保留为最后一次进度时间（不刷新为扫描时刻），前端据此展示
        「中断前的进度」。

        已标记为 interrupted 的 manifest 同样载入（不再重复写盘）：用户可能在
        未处理中断任务的情况下再次关闭程序，若此处跳过，任务会从列表中永久消失。

        只载入 interrupted 任务；completed / failed 不载入（本期不提供历史任务
        列表）。目录不存在或 manifest 损坏时跳过，绝不影响启动。
        """
        if not v4_root.is_dir():
            return 0

        count = 0
        for manifest in sorted(v4_root.glob(f'*/{MANIFEST_NAME}')):
            try:
                data = json.loads(manifest.read_text(encoding='utf-8'))
                if data.get('status') not in (
                    JobStatus.PENDING.value,
                    JobStatus.RUNNING.value,
                    JobStatus.INTERRUPTED.value,
                ):
                    continue
                job = Job.from_manifest(data, manifest.parent)
            except Exception as e:  # noqa: BLE001 — 单个坏 manifest 不得影响启动
                print(
                    f'[job] skip unreadable manifest {manifest}: {type(e).__name__}: {e}',
                    file=sys.stderr,
                )
                continue
            if job.status != JobStatus.INTERRUPTED:
                job.status = JobStatus.INTERRUPTED
                job._persist()
            self._jobs[job.job_id] = job
            count += 1

        if count:
            print(f'[job] {count} interrupted job(s) restored from {v4_root}')
        return count


job_manager = JobManager()
