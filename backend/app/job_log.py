# -*- coding: utf-8 -*-
"""进程级任务日志缓冲 + stdout/stderr Tee + 线程归属。

设计要点（见 docs/superpowers/specs/2026-09-22-服务器可观测与任务控制-design.md §5.1）：

- 用 Tee 采集日志，而不是把 150+ 处 ``print`` 改写成 ``logging``：既有日志
  文案在本项目被当契约看待（见 commit 17d336d「cache miss 打印歧义消除」），
  改写会改变文案。
- 归属用 thread-local：管线跑在独立线程，写入该 job 的 buffer；无归属时进
  全局 buffer。线程池的工作线程**不继承** thread-local，提交前需用
  :func:`bind_current_job` 包装。
- 每个 job 同时把日志追加到 ``{job_dir}/job.log``（封顶 5MB）。服务器上没有
  SSH，进程重启后内存 buffer 会空；从磁盘尾部恢复可让用户在前端继续看到
  重启前的日志。

本模块不得 import 任何 ``app.*``：它会被 ``app.job_manager`` 导入，引入依赖
即形成环。
"""
from __future__ import annotations

import sys
import threading
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

BUFFER_MAX_LINES = 2000
LOG_FILE_NAME = 'job.log'
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024

# 从 job.log 尾部恢复到内存时最多读取的字节数（避免大文件一次性读入）
_RESTORE_TAIL_BYTES = 256 * 1024

# print("a", "b") 会分多次 write；未成行的片段先攒着，超过此长度强制吐出
_MAX_PARTIAL_CHARS = 8192


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LogBuffer:
    """单个 job（或全局）的环形行缓冲，可选同步落盘。"""

    def __init__(self, max_lines: int = BUFFER_MAX_LINES, path: Optional[Path] = None):
        self._lines: deque[dict] = deque(maxlen=max_lines)
        self._seq = 0
        self._truncated = False
        self._path = path
        self._file_bytes = 0
        self._lock = threading.Lock()

    def append(self, text: str, level: str = 'info') -> None:
        if not text.strip():
            return
        with self._lock:
            if len(self._lines) == self._lines.maxlen:
                self._truncated = True
            self._seq += 1
            self._lines.append(
                {'seq': self._seq, 'ts': _now_iso(), 'level': level, 'text': text}
            )
            self._persist(text, level)

    def read(self, offset: int = 0, limit: int = 500) -> dict:
        """返回 ``seq > offset`` 的最多 limit 行。

        ``next_offset`` 取本批最后一行的 seq；没有新行时取当前最大 seq。这样
        当 offset 大于当前 seq（进程重启后 buffer 重建）时 next_offset 会**小于**
        offset，前端据此把偏移归零重新拉取，无需额外的版本号协商。
        """
        with self._lock:
            lines = list(self._lines)
            truncated = self._truncated
            seq = self._seq
        selected = [ln for ln in lines if ln['seq'] > offset][:limit]
        next_offset = selected[-1]['seq'] if selected else seq
        return {'lines': selected, 'next_offset': next_offset, 'truncated': truncated}

    def restore_from_file(self) -> None:
        """进程重启后从 job.log 尾部恢复内存缓冲。

        恢复的行用新的 seq 编号（原 seq 未落盘），ts 留空由前端只显示文本。
        """
        if self._path is None or not self._path.exists():
            return
        try:
            raw = self._path.read_bytes()
            self._file_bytes = len(raw)
            text = raw[-_RESTORE_TAIL_BYTES:].decode('utf-8', errors='replace')
            for line in text.splitlines()[-BUFFER_MAX_LINES:]:
                if not line.strip():
                    continue
                with self._lock:
                    self._seq += 1
                    self._lines.append(
                        {'seq': self._seq, 'ts': '', 'level': 'info', 'text': line}
                    )
        except Exception:  # noqa: BLE001 — 恢复失败不得影响服务启动
            pass

    def _persist(self, text: str, level: str) -> None:
        """同步追加到 job.log；任何 I/O 异常都不得影响任务本身。"""
        if self._path is None or self._file_bytes >= LOG_FILE_MAX_BYTES:
            return
        try:
            line = f'{_now_iso()} [{level}] {text}\n'
            with self._path.open('a', encoding='utf-8') as f:
                f.write(line)
            self._file_bytes += len(line.encode('utf-8'))
        except Exception:  # noqa: BLE001 — 磁盘满 / 权限不足时静默降级为「仅内存」
            pass


class JobLogStore:
    """job_id → LogBuffer 的注册表；另有一个不归属任何 job 的全局 buffer。"""

    def __init__(self) -> None:
        self._buffers: dict[str, LogBuffer] = {}
        self._global = LogBuffer()
        self._lock = threading.Lock()

    @property
    def global_buffer(self) -> LogBuffer:
        return self._global

    def register(self, job_id: str, log_path: Optional[Path] = None) -> LogBuffer:
        """绑定（或复用）job 的 buffer；首次创建且有 log_path 时从磁盘尾部恢复。"""
        with self._lock:
            buf = self._buffers.get(job_id)
            if buf is not None:
                return buf
            buf = LogBuffer(path=Path(log_path) if log_path is not None else None)
            self._buffers[job_id] = buf
        buf.restore_from_file()
        return buf

    def read(self, job_id: str, offset: int = 0, limit: int = 500) -> dict:
        with self._lock:
            buf = self._buffers.get(job_id)
        if buf is None:
            return {'lines': [], 'next_offset': offset, 'truncated': False}
        return buf.read(offset=offset, limit=limit)


job_log_store = JobLogStore()


# ---------------------------------------------------------------- 线程归属

_local = threading.local()


def current_job_id() -> Optional[str]:
    return getattr(_local, 'job_id', None)


def current_job():
    """当前线程绑定的 Job（供进度上报与取消检查点使用；未绑定时为 None）。"""
    return getattr(_local, 'job', None)


def bind_job_log(job_id: Optional[str], job=None) -> tuple:
    """把当前线程的输出归属到 job_id；返回上一层归属，供 :func:`restore_job_log`。"""
    prev = (getattr(_local, 'job_id', None), getattr(_local, 'job', None))
    _local.job_id = job_id
    _local.job = job
    return prev


def restore_job_log(prev: tuple) -> None:
    _local.job_id, _local.job = prev


@contextmanager
def job_log_context(job_id: Optional[str], job=None) -> Iterator[None]:
    prev = bind_job_log(job_id, job)
    try:
        yield
    finally:
        restore_job_log(prev)


def bind_current_job(fn: Callable) -> Callable:
    """把 ``fn`` 包成「在提交方 job 上下文内执行」，供线程池提交使用。

    线程池工作线程不继承 thread-local，池内 ``print`` 会落进全局 buffer、
    进度上报与取消检查点也会失效。提交前包装一层即可让它们归属同一 job。
    """
    job_id = current_job_id()
    job = current_job()
    if job_id is None and job is None:
        return fn

    def _wrapped(*args, **kwargs):
        prev = bind_job_log(job_id, job)
        try:
            return fn(*args, **kwargs)
        finally:
            restore_job_log(prev)

    return _wrapped


# ---------------------------------------------------------------- 启动配置自检

_config_lines: list[str] = []


def set_config_lines(lines: list[str]) -> None:
    """记录启动自检结果：写入全局 buffer，并在每个任务日志开头重复一遍。"""
    global _config_lines
    _config_lines = list(lines)
    for line in _config_lines:
        job_log_store.global_buffer.append(line, 'info')


def config_lines() -> list[str]:
    return list(_config_lines)


# ---------------------------------------------------------------- stdout Tee


class _Tee:
    """把写向 stdout/stderr 的内容同时转给原流与任务日志缓冲。

    刻意不继承 ``io.TextIOBase``：容器里 ``sys.stdout`` 是普通文本流，继承会
    引入值为 None 的 ``encoding`` 等属性，反而与真实行为不符。``print`` 只需要
    ``write``；``fileno`` / ``isatty`` 透传原流。
    """

    def __init__(self, original, level: str):
        self._original = original
        self._level = level
        # 未成行的片段按线程各自持有：``print`` 把一行拆成两次 ``write``（文本 +
        # 换行），若片段是实例级共享状态，另一线程的文本会插进「本线程文本」与
        # 「本线程换行」之间，拼成一行并按写换行的线程归属，原线程的半行随之丢失
        # （跨 job 就会串行）。thread-local 让半行等在写它的那个线程里。
        self._partial = threading.local()
        # 发射（含落盘）仍串行：一次 ``write`` 里的多行不被其它线程插队。
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        try:
            self._original.write(text)
        except Exception:  # noqa: BLE001 — 原流已关闭时不得打断任务
            pass
        partial = getattr(self._partial, 'text', '') + text
        with self._lock:
            while '\n' in partial:
                line, partial = partial.split('\n', 1)
                _append_current(line, self._level)
            if len(partial) > _MAX_PARTIAL_CHARS:
                _append_current(partial, self._level)
                partial = ''
            self._partial.text = partial
        return len(text)

    def flush(self) -> None:
        try:
            self._original.flush()
        except Exception:  # noqa: BLE001
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._original.isatty())
        except Exception:  # noqa: BLE001
            return False

    def fileno(self) -> int:
        return self._original.fileno()


def _append_current(line: str, level: str) -> None:
    job_id = current_job_id()
    if job_id is not None:
        job_log_store.register(job_id).append(line, level)
    else:
        job_log_store.global_buffer.append(line, level)


_installed = False


def install_log_tee() -> None:
    """把 sys.stdout / sys.stderr 换成 Tee。幂等，重复调用无副作用。"""
    global _installed
    if _installed:
        return
    _installed = True
    sys.stdout = _Tee(sys.stdout, 'info')     # type: ignore[assignment]
    sys.stderr = _Tee(sys.stderr, 'error')    # type: ignore[assignment]
