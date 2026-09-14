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
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


MANIFEST_NAME = 'job.json'
MANIFEST_SCHEMA_VERSION = 1

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
        }
        path = self.job_dir / MANIFEST_NAME
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        with _LOCK:
            _atomic_write(path, text)

    def update(self, status: JobStatus, message: Optional[str] = None):
        self.status = status
        if message is not None:
            self.message = message
        self.updated_at = datetime.now(timezone.utc)
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
        return job


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create_job(self, task_type: str = "correctness") -> Job:
        """Create a new Job."""
        job = Job(task_type=task_type)
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
