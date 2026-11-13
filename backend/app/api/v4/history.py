# -*- coding: utf-8 -*-
"""历史结果：列表 + 硬删除 + 运行日志下载 + 匹配中间产物查看。

- GET  /api/v4/history                 列出服务器上保留的历史结果（磁盘口径）
- POST /api/v4/history/delete          整目录硬删除（含该任务上传的输入文件）
- GET  /api/v4/jobs/{job_id}/logs/download  下载该次运行的 job.log
- GET  /api/v4/jobs/{job_id}/intermediates/reverse-matches  浏览器内查看反向匹配
  中间产物（ADR-001 D7 预留的「新开子路由」方式，只开这一个中间产物）

**与 ``/jobs`` 的分工**：``/jobs`` 列的是**内存**里的任务（本进程新建 + 启动扫描
恢复的中断任务），跑完的任务在进程重启后就不在里面了；``/history`` 列的是**磁盘**
上的任务目录，所以重启后仍然看得到、也下得动。两者不互相替代：中断任务要在
``/jobs`` 里「继续 / 放弃」，历史结果要在 ``/history`` 里下载 / 清理。

日志下载同样走**磁盘口径**，与 ``/jobs/{job_id}/logs``（增量拉取，只认内存里的
任务）互补：任务失败 / 中断后用户最需要的就是日志，而那时任务往往已经不在内存
里（进程重启）或本就只留在磁盘上，因此这里不查内存登记表，直接读任务目录下的
``job.log``。

删除是 ``shutil.rmtree``，不进回收站、没有撤销，因此接口要求显式
``confirm=true``，且批次里只要有一个任务还在跑就整批不删（部分成功的批次会让
用户分不清哪些删掉了、哪些没删）。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.v4.runner import (
    FORWARD_OUTPUT_FILES,
    V4_INTERMEDIATE_JSON,
    V4_OUTPUT_FILES,
    input_filenames_from_params,
)
from app.api.v4.schemas import (
    V4HistoryAttachment,
    V4HistoryDeleteFailure,
    V4HistoryDeleteRequest,
    V4HistoryDeleteResponse,
    V4HistoryDeleteResult,
    V4HistoryItem,
    V4HistoryOutputs,
    V4TokenUsage,
)
from app.auth.access import is_valid_job_id, require_job_access
from app.auth.deps import current_admin, current_user
from app.auth.models import User
from app.job_log import LOG_FILE_NAME, job_log_store
from app.job_manager import MANIFEST_NAME, Job, JobStatus, job_manager
from app.v4.config import get_output_root


router = APIRouter()

# 容器时区常为 UTC；固定东八区偏移，与 outputs.py 的产物命名规则一致
_CN_TZ = timezone(timedelta(hours=8))

# job_id 白名单复用 ``access.is_valid_job_id``（与归属校验同一份规则）。删除动的
# 是 ``shutil.rmtree``，路径必须先被这道白名单挡住（``..`` / 路径分隔符 / 通配符
# 全都匹配不上），落到磁盘前再做一次「解析后必须正好在 output/v4/ 下面」的归属
# 校验，两道一起才动手。

# 反向匹配中间产物（Step 3 输出，文件名 SSoT 在 runner.V4_INTERMEDIATE_JSON）。
# ADR-001 D7 原本不暴露 JSON 中间产物，并预留了「新开子路由」的开放方式；本模块
# 按该方式只开 reverse_matches 一个查看口，其余 6 类中间产物仍不暴露。
_REVERSE_MATCHES_FILE = V4_INTERMEDIATE_JSON['reverse_matches']


def _v4_root() -> Path:
    return get_output_root() / 'v4'


def _resolve_job_dir(job_id: str) -> Path:
    """job_id → 任务目录；两道门槛：uuid 白名单 + 解析后必须是 ``output/v4/`` 的直接子目录。

    删除动的是 ``shutil.rmtree``，下载读的是任意 job_id 拼出来的路径，两者都必须
    先被这两道挡住（``..`` / 路径分隔符 / 通配符匹配不上正则；软链指向别处被
    「解析后父目录必须正好是 output/v4/」挡住）。
    """
    if not is_valid_job_id(job_id):
        raise HTTPException(status_code=400, detail=f'invalid job_id: {job_id!r}')
    root = _v4_root().resolve()
    job_dir = (root / job_id).resolve()
    if job_dir.parent != root:
        raise HTTPException(status_code=400, detail=f'job_id escapes output root: {job_id}')
    return job_dir


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


def _parse_token_usage(value: object) -> Optional[V4TokenUsage]:
    """解析 manifest 的 ``token_usage``。

    兼容 ``{"total_tokens": N}`` 与裸数字；缺失 / 非法 → None（旧任务不显示），
    ``0`` 是有效值（MOCK 任务）。负数按 0 处理。
    """
    if isinstance(value, dict):
        value = value.get('total_tokens')
    elif isinstance(value, bool):
        return None
    if value is None:
        return None
    try:
        return V4TokenUsage(total_tokens=max(0, int(value)))
    except (TypeError, ValueError):
        return None


def _mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(timezone.utc)


def _read_manifest(job_dir: Path) -> Optional[dict]:
    """读取任务 manifest（``job.json``）；缺失 / 损坏一律返回 None。"""
    try:
        raw = json.loads((job_dir / MANIFEST_NAME).read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001 — 目录被删、JSON 损坏一律按「记录缺失」处理
        return None
    return raw if isinstance(raw, dict) else None


def _attachment_items(params: object) -> list[V4HistoryAttachment]:
    """从 manifest 参数快照取出附件列表（仅管理员可见，调用方负责过滤）。

    ``index`` 保留原始列表下标：即使个别记录损坏被跳过，管理员下载接口仍按
    manifest 中的同一下标定位，不会与展示顺序错位。
    """
    raw = params.get('attachment_files') if isinstance(params, dict) else None
    if not isinstance(raw, list):
        return []
    items: list[V4HistoryAttachment] = []
    for index, record in enumerate(raw):
        if not isinstance(record, dict):
            continue
        name = record.get('name')
        if not isinstance(name, str) or not name:
            continue
        try:
            size = max(0, int(record.get('size', 0)))
        except (TypeError, ValueError):
            size = 0
        items.append(V4HistoryAttachment(index=index, name=name, size=size))
    return items


def _history_item(
    job_dir: Path, include_attachments: bool = True
) -> tuple[V4HistoryItem, datetime, str | None]:
    """把一个任务目录组装成历史记录，返回「记录 / 排序时间 / 归属 owner_id」。

    第三项供 ``list_history`` 做归属过滤：只认 ``owner_id``（``str(User.id)``），
    ``owner_username`` 仅用于展示。manifest 缺失 / 损坏时 owner_id 为 ``None``
    （未归属，仅管理员可见）。

    ``include_attachments=False`` 时 ``attachments`` 置空：附件仅管理员可见，
    普通用户即使拥有该任务也拿不到文件名（列表接口层面的信息隔离）。

    manifest 缺失 / 损坏时退化为「只剩目录」的记录：上传失败会留下这类残留目录，
    它们不出现在任何列表里，却照样占磁盘 —— 只有在这里能看到并清理掉。
    """
    out_dir = job_dir / 'output'
    outputs = _output_flags(out_dir)
    # 失败 / 中断的任务正是最需要日志的场景，判据只认磁盘上的 job.log 本身：
    # 进程重启后内存 buffer 已经没了，但任务跑过就必然留下了它
    has_log = (job_dir / LOG_FILE_NAME).is_file()
    # 反向匹配中间产物（Step 3 输出）：只有反向任务会写它，正向任务 / 残留目录自然
    # 为 False。与 has_log 同理，不并入 outputs —— 那是「七类对外产物」的词汇表
    has_reverse_matches = (out_dir / _REVERSE_MATCHES_FILE).is_file()
    size = _dir_size(job_dir)
    fallback = _mtime(job_dir)

    data = _read_manifest(job_dir)

    if data is None:
        return (
            V4HistoryItem(
                job_id=job_dir.name,
                status='unknown',
                created_at=fallback.isoformat(),
                updated_at=fallback.isoformat(),
                outputs=outputs,
                size_bytes=size,
                has_log=has_log,
                has_reverse_matches=has_reverse_matches,
            ),
            fallback,
            None,
        )

    owner_id = data.get('owner_id')
    owner_username = data.get('owner_username')
    created = _parse_iso(data.get('created_at')) or fallback
    updated = _parse_iso(data.get('updated_at')) or created
    finished = _parse_iso(data.get('finished_at'))
    return (
        V4HistoryItem(
            job_id=str(data.get('job_id') or job_dir.name),
            owner_username=owner_username if isinstance(owner_username, str) else None,
            task_type=str(data.get('task_type') or ''),
            status=str(data.get('status') or 'unknown'),
            message=data.get('message'),
            created_at=created.isoformat(),
            updated_at=updated.isoformat(),
            finished_at=finished.isoformat() if finished else None,
            input_files=input_filenames_from_params(data.get('params')),
            outputs=outputs,
            size_bytes=size,
            has_log=has_log,
            has_reverse_matches=has_reverse_matches,
            mock=bool(data.get('mock', False)),
            token_usage=_parse_token_usage(data.get('token_usage')),
            attachments=(
                _attachment_items(data.get('params'))
                if include_attachments
                else []
            ),
        ),
        created,
        owner_id if isinstance(owner_id, str) else None,
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
def list_history(user: User = Depends(current_user)):
    """列出当前账号可见的历史结果，按创建时间倒序（新→旧）。

    管理员返回全部；普通用户只返回 ``owner_id == str(user.id)`` 的目录，
    未归属目录（旧版本遗留 / manifest 损坏）仅管理员可见。扫的是
    ``output/v4/*/`` 目录本身，不是内存登记表：跑完的任务在进程重启后不会被
    启动扫描载入，只有磁盘上的 ``job.json`` 知道它存在过。
    """
    root = _v4_root()
    if not root.is_dir():
        return []

    entries = [
        _history_item(child, include_attachments=user.is_superuser)
        for child in root.iterdir()
        if child.is_dir()
    ]
    if not user.is_superuser:
        user_id = str(user.id)
        entries = [entry for entry in entries if entry[2] == user_id]
    entries.sort(key=lambda entry: entry[1], reverse=True)
    return [item for item, _, _ in entries]


def _log_download_name(job_id: str, path: Path) -> str:
    """下载保存名 = 任务号前 8 位 + 日志最后写入时刻（东八区 YYYYMMDD_HHMM）。

    取 mtime 而非下载时刻（与 ``outputs.py`` 的产物命名同规则）：同一份日志重复
    下载文件名稳定不变，且任务号前缀让同一批任务的日志在下载目录里区分得开。
    """
    stamp = datetime.fromtimestamp(path.stat().st_mtime, _CN_TZ).strftime('%Y%m%d_%H%M')
    return f'job_{job_id[:8]}_{stamp}.log'


@router.get('/jobs/{job_id}/logs/download')
def download_job_log(job_id: str, user: User = Depends(current_user)):
    """下载该次运行的 ``job.log``（stdout/stderr 全集，含失败堆栈与执行过程）。

    **磁盘口径**：不查内存登记表，因此进程重启后、任务失败 / 中断 / 被终止之后
    照样下得动 —— 这些正是最需要日志的场景（``/jobs/{job_id}/logs`` 增量拉取只认
    内存里的任务，重启后对这类任务直接 404）。日志由 Tee 逐行同步落盘，任务中途
    被 OOM 杀掉也已写到最后一行为止。

    归属校验先于路径解析与文件存在性判断：非本人 / 未归属任务统一 404。
    日志文件不存在（任务从未真正开始、目录已被删）→ 404；job_id 非法 → 400。
    """
    require_job_access(job_id, user)
    path = _resolve_job_dir(job_id) / LOG_FILE_NAME
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail='job.log not found (job never started, or its dir was deleted)',
        )
    return FileResponse(
        path=path,
        filename=_log_download_name(job_id, path),
        media_type='text/plain',
    )


@router.get('/jobs/{job_id}/intermediates/reverse-matches')
def view_reverse_matches(job_id: str, user: User = Depends(current_user)):
    """浏览器内查看反向匹配中间产物 ``output/reverse_matches.json``（Step 3 匹配明细）。

    ADR-001 D7 原本不暴露 JSON 中间产物，并预留了「新开子路由」的开放方式；本路由
    即按该方式为用户开的口子，**只开 ``reverse_matches`` 一个**，其余 6 类中间产物
    仍不暴露。不传 ``filename``，因此不发 ``Content-Disposition``：浏览器在新标签页
    里直接按 JSON 渲染，需要留档时页内另存即可。

    归属校验先于路径解析与文件存在性判断：非本人 / 未归属任务统一 404。
    文件不存在（正向任务、Step 3 未跑完、目录已被删）→ 404；job_id 非法 → 400
    （两道校验同日志下载，走同一个 ``_resolve_job_dir``）。
    """
    require_job_access(job_id, user)
    path = _resolve_job_dir(job_id) / 'output' / _REVERSE_MATCHES_FILE
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail='reverse_matches.json not found (forward job, or pipeline stopped before Step 3)',
        )
    return FileResponse(path=path, media_type='application/json')


@router.get('/jobs/{job_id}/attachments/{index}')
def download_attachment(
    job_id: str, index: int, user: User = Depends(current_admin)
):
    """下载任务附件（上传时只存储、不参与分析），**仅管理员**。

    普通用户即使是任务所有者也不可见：历史列表接口对他们返回空
    ``attachments``，本接口由 ``current_admin`` 直接 403，两道一起做信息隔离。

    文件按 manifest ``params.attachment_files[index]`` 定位，不拼接用户传来的
    文件名；resolved 后仍校验必须落在任务目录内，防止 manifest 被篡改后越界。
    下载响应固定 ``application/octet-stream`` + ``Content-Disposition:
    attachment``，不在浏览器内联渲染 HTML / SVG 等类型。
    """
    job_dir = _resolve_job_dir(job_id)
    data = _read_manifest(job_dir)
    if data is None:
        raise HTTPException(status_code=404, detail='job manifest not found')

    params = data.get('params')
    raw = params.get('attachment_files') if isinstance(params, dict) else None
    if not isinstance(raw, list) or index < 0 or index >= len(raw):
        raise HTTPException(status_code=404, detail='attachment not found')

    record = raw[index]
    rel = record.get('path') if isinstance(record, dict) else None
    if not isinstance(rel, str) or not rel:
        raise HTTPException(status_code=404, detail='attachment not found')

    root = job_dir.resolve()
    path = (job_dir / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail='attachment not found')

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail='attachment file missing (job dir or file may have been deleted)',
        )

    name = record.get('name')
    filename = name if isinstance(name, str) and name else path.name
    return FileResponse(
        path=path,
        filename=filename,
        media_type='application/octet-stream',
    )


@router.post('/history/delete', response_model=V4HistoryDeleteResponse)
def delete_history(
    req: V4HistoryDeleteRequest, user: User = Depends(current_admin)
):
    """硬删除历史结果：整目录 ``rmtree``，**含 ``input/`` 里用户上传的原文件**。

    仅管理员可调用（普通用户 403）：删除是全局清理动作，且要能清掉未归属的
    遗留任务。不可恢复，因此按顺序设了三道关卡：显式 ``confirm`` → job_id
    白名单 + 路径归属校验 → 批次内无运行中任务。单目录删除失败（权限、文件被
    占用）计入 ``failed``，不影响同批其它任务；返回体里带上每个任务释放的字节数。
    """
    if not req.confirm:
        raise HTTPException(status_code=400, detail='confirm=true is required: deletion is irreversible')
    if not req.job_ids:
        raise HTTPException(status_code=400, detail='job_ids is empty')

    targets: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for job_id in req.job_ids:
        if job_id in seen:  # 同一批里重复传同一个 id：只删一次
            continue
        seen.add(job_id)
        targets.append((job_id, _resolve_job_dir(job_id)))

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
