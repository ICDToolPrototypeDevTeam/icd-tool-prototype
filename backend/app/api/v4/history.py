# -*- coding: utf-8 -*-
"""历史结果：列表 + 硬删除。

- GET  /api/v4/history         列出服务器上保留的历史结果（磁盘口径）
- POST /api/v4/history/delete  整目录硬删除（含该任务上传的输入文件）

**与 ``/jobs`` 的分工**：``/jobs`` 列的是**内存**里的任务（本进程新建 + 启动扫描
恢复的中断任务），跑完的任务在进程重启后就不在里面了；``/history`` 列的是**磁盘**
上的任务目录，所以重启后仍然看得到、也下得动。两者不互相替代：中断任务要在
``/jobs`` 里「继续 / 放弃」，历史结果要在 ``/history`` 里下载 / 清理。

删除是 ``shutil.rmtree``，不进回收站、没有撤销，因此接口要求显式
``confirm=true``，且批次里只要有一个任务还在跑就整批不删（部分成功的批次会让
用户分不清哪些删掉了、哪些没删）。
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.api.v4.runner import (
    FORWARD_OUTPUT_FILES,
    V4_OUTPUT_FILES,
    input_filenames_from_params,
)
from app.api.v4.schemas import (
    V4HistoryDeleteFailure,
    V4HistoryDeleteRequest,
    V4HistoryDeleteResponse,
    V4HistoryDeleteResult,
    V4HistoryItem,
    V4HistoryOutputs,
)
from app.job_log import job_log_store
from app.job_manager import MANIFEST_NAME, Job, JobStatus, job_manager
from app.v4.config import get_output_root


router = APIRouter()

# job_id 白名单：只接受 uuid4 的文本形式。删除动的是 ``shutil.rmtree``，路径必须
# 先被这道正则挡住（``..`` / 路径分隔符 / 通配符全都匹配不上），落到磁盘前再做一次
# 「解析后必须正好在 output/v4/ 下面」的归属校验，两道一起才动手。
_JOB_ID_RE = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)


def _v4_root() -> Path:
    return get_output_root() / 'v4'


def _dir_size(path: Path) -> int:
    """目录内普通文件的字节数合计；单个文件取不到大小就跳过（尽力而为）。"""
    total = 0
    for p in path.rglob('*'):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _output_flags(out_dir: Path) -> V4HistoryOutputs:
    """七类对外产物的存在性（反向 5 类 + 正向 2 类，文件名互不重名）。"""
    files = {**V4_OUTPUT_FILES, **FORWARD_OUTPUT_FILES}
    return V4HistoryOutputs(**{key: (out_dir / name).exists() for key, name in files.items()})


def _parse_iso(value: object) -> Optional[datetime]:
    """解析 manifest 里的 ISO 时间；缺字段 / 格式非法返回 None。

    无时区的时间按 UTC 处理：否则它与带时区的记录混在一起排序会抛
    ``TypeError: can't compare offset-naive and offset-aware datetimes``。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(timezone.utc)


def _history_item(job_dir: Path) -> tuple[V4HistoryItem, datetime]:
    """把一个任务目录组装成历史记录，并返回其排序用的时间。

    manifest 缺失 / 损坏时退化为「只剩目录」的记录：上传失败会留下这类残留目录，
    它们不出现在任何列表里，却照样占磁盘 —— 只有在这里能看到并清理掉。
    """
    outputs = _output_flags(job_dir / 'output')
    size = _dir_size(job_dir)
    fallback = _mtime(job_dir)

    data: Optional[dict] = None
    try:
        raw = json.loads((job_dir / MANIFEST_NAME).read_text(encoding='utf-8'))
        data = raw if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001 — 目录被删、JSON 损坏一律按「记录缺失」处理
        data = None

    if data is None:
        return (
            V4HistoryItem(
                job_id=job_dir.name,
                status='unknown',
                created_at=fallback.isoformat(),
                updated_at=fallback.isoformat(),
                outputs=outputs,
                size_bytes=size,
            ),
            fallback,
        )

    created = _parse_iso(data.get('created_at')) or fallback
    updated = _parse_iso(data.get('updated_at')) or created
    finished = _parse_iso(data.get('finished_at'))
    return (
        V4HistoryItem(
            job_id=str(data.get('job_id') or job_dir.name),
            task_type=str(data.get('task_type') or ''),
            status=str(data.get('status') or 'unknown'),
            message=data.get('message'),
            created_at=created.isoformat(),
            updated_at=updated.isoformat(),
            finished_at=finished.isoformat() if finished else None,
            input_files=input_filenames_from_params(data.get('params')),
            outputs=outputs,
            size_bytes=size,
            mock=bool(data.get('mock', False)),
        ),
        created,
    )


def _is_running(job: Optional[Job]) -> bool:
    """任务是否「还有可能写这个目录」。

    判据是**有没有线程在跑**，与 ``abandon`` 接口同一门槛（见
    ``jobs.abandon_v4_job``）：``pending`` 只有在 ``job_dir`` 非空（线程启动窗口
    内）时才算运行中；``thread_ident`` 则覆盖「已强制终止、但线程还在某个 C 调用
    里收尾」的窗口 —— 线程退出前会 ``unbind_thread``，所以它非空就说明目录还活着。
    """
    if job is None:
        return False
    if job.thread_ident is not None:
        return True
    if job.status == JobStatus.RUNNING:
        return True
    return job.status == JobStatus.PENDING and job.job_dir is not None


@router.get('/history', response_model=list[V4HistoryItem])
def list_history():
    """列出服务器上保留的全部历史结果，按创建时间倒序（新→旧）。

    扫的是 ``output/v4/*/`` 目录本身，不是内存登记表：跑完的任务在进程重启后
    不会被启动扫描载入，只有磁盘上的 ``job.json`` 知道它存在过。
    """
    root = _v4_root()
    if not root.is_dir():
        return []

    entries = [_history_item(child) for child in root.iterdir() if child.is_dir()]
    entries.sort(key=lambda pair: pair[1], reverse=True)
    return [item for item, _ in entries]


@router.post('/history/delete', response_model=V4HistoryDeleteResponse)
def delete_history(req: V4HistoryDeleteRequest):
    """硬删除历史结果：整目录 ``rmtree``，**含 ``input/`` 里用户上传的原文件**。

    不可恢复，因此按顺序设了三道关卡：显式 ``confirm`` → job_id 白名单 + 路径
    归属校验 → 批次内无运行中任务。单目录删除失败（权限、文件被占用）计入
    ``failed``，不影响同批其它任务；返回体里带上每个任务释放的字节数。
    """
    if not req.confirm:
        raise HTTPException(status_code=400, detail='confirm=true is required: deletion is irreversible')
    if not req.job_ids:
        raise HTTPException(status_code=400, detail='job_ids is empty')

    root = _v4_root().resolve()
    targets: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for job_id in req.job_ids:
        if not _JOB_ID_RE.match(job_id or ''):
            raise HTTPException(status_code=400, detail=f'invalid job_id: {job_id!r}')
        if job_id in seen:  # 同一批里重复传同一个 id：只删一次
            continue
        seen.add(job_id)
        job_dir = (root / job_id).resolve()
        # 解析后必须正好是 output/v4/ 的直接子目录：软链指向别处都会被这道挡住
        if job_dir.parent != root:
            raise HTTPException(status_code=400, detail=f'job_id escapes output root: {job_id}')
        targets.append((job_id, job_dir))

    blocked = [job_id for job_id, _ in targets if _is_running(job_manager.get_job(job_id))]
    if blocked:
        raise HTTPException(
            status_code=409,
            detail=f'job still running, nothing deleted: {", ".join(blocked)}',
        )

    deleted: list[V4HistoryDeleteResult] = []
    failed: list[V4HistoryDeleteFailure] = []
    total_freed = 0
    for job_id, job_dir in targets:
        if not job_dir.is_dir():
            failed.append(V4HistoryDeleteFailure(job_id=job_id, error='目录不存在（可能已被删除）'))
            continue
        try:
            freed = _dir_size(job_dir)
            shutil.rmtree(job_dir)
        except OSError as e:
            failed.append(V4HistoryDeleteFailure(job_id=job_id, error=f'{type(e).__name__}: {e}'))
            continue
        # 目录已经没了：内存登记表与日志 buffer 都要摘掉，否则任务列表里会留下一条
        # 指向已删目录的幽灵记录（点「继续」报文件缺失、点日志报目录不存在）。
        job_manager.forget(job_id)
        job_log_store.forget(job_id)
        total_freed += freed
        deleted.append(V4HistoryDeleteResult(job_id=job_id, freed_bytes=freed))
        print(f'[history] deleted job {job_id} dir={job_dir} freed={freed}B')

    return V4HistoryDeleteResponse(deleted=deleted, failed=failed, total_freed_bytes=total_freed)
