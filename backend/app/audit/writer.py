# -*- coding: utf-8 -*-
"""审计日志异步批量写入器。

设计要点：

- **不阻塞业务**：登录 / 操作接口只把文档放进内存队列，立即返回；
- **fail-open**：Mongo 写入失败只告警并丢弃这一批，绝不让业务请求失败；
- **批量**：每 0.5 秒或攒够 100 条写一次，降低审计对 Mongo 的压力；
- 队列满时丢弃新日志并告警，不反压业务线程。

写入器运行在 FastAPI 主事件循环上的一个后台任务里；同步路由（线程池）通过
``loop.call_soon_threadsafe`` 入队，因此不需要额外线程或同步 Mongo 客户端。
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

from app.audit.models import AuditLog

_QUEUE_MAX = 20000
_BATCH_MAX = 100
_FLUSH_INTERVAL_S = 0.5
_STOP_TIMEOUT_S = 8.0


class AuditWriter:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._task: asyncio.Task | None = None
        self._closing = False
        self._dropped = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._closing = False
        self._task = self._loop.create_task(self._consume(), name="audit-writer")

    def submit(self, document: dict[str, Any]) -> None:
        """线程安全入队；写入器未启动或正在关闭时静默丢弃。"""
        loop = self._loop
        if loop is None or self._queue is None or self._closing:
            return
        try:
            loop.call_soon_threadsafe(self._enqueue, document)
        except RuntimeError:
            # 事件循环已关闭（进程退出阶段）
            pass

    def _enqueue(self, document: dict[str, Any]) -> None:
        queue = self._queue
        if queue is None or self._closing:
            return
        try:
            queue.put_nowait(document)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:
                print(
                    f"[audit] queue full; dropped {self._dropped} log(s)",
                    file=sys.stderr,
                )

    async def _consume(self) -> None:
        while True:
            try:
                first = await asyncio.wait_for(
                    self._queue.get(), timeout=_FLUSH_INTERVAL_S
                )
            except asyncio.TimeoutError:
                if self._closing and (self._queue is None or self._queue.empty()):
                    break
                continue
            batch = [first]
            while len(batch) < _BATCH_MAX:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await self._insert(batch)
            for _ in batch:
                self._queue.task_done()

    @staticmethod
    async def _insert(batch: list[dict[str, Any]]) -> None:
        try:
            collection = AuditLog.get_motor_collection()
        except Exception as exc:  # noqa: BLE001 — Beanie 未初始化（测试 / 启动早期）
            print(
                f"[audit] collection not ready: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return
        try:
            await collection.insert_many(batch, ordered=False)
        except Exception as exc:  # noqa: BLE001 — 审计写失败不能影响业务
            print(
                f"[audit] write failed ({len(batch)} log(s)): "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    async def stop(self) -> None:
        self._closing = True
        task = self._task
        if task is None:
            self._cleanup()
            return
        try:
            await asyncio.wait_for(task, timeout=_STOP_TIMEOUT_S)
        except asyncio.TimeoutError:
            task.cancel()
        except Exception:  # noqa: BLE001 — 关闭阶段不抛
            pass
        self._cleanup()

    def _cleanup(self) -> None:
        self._task = None
        self._queue = None
        self._loop = None


audit_writer = AuditWriter()


def start_audit_writer() -> None:
    audit_writer.start()


async def stop_audit_writer() -> None:
    await audit_writer.stop()
