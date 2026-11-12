# -*- coding: utf-8 -*-
"""HLR 表格样式管理 API：自定义样式 CRUD、文档扫描与试解析。

- GET    /api/v4/hlr-schemas                内置模板 + 自定义样式列表
- POST   /api/v4/hlr-schemas                新建自定义样式（201）
- PUT    /api/v4/hlr-schemas/{schema_id}    更新自定义样式
- DELETE /api/v4/hlr-schemas/{schema_id}    删除自定义样式
- POST   /api/v4/hlr-schemas/scan           扫描 .docx 表格结构（不调用 LLM）
- POST   /api/v4/hlr-schemas/preview        用指定样式试解析 .docx（不调用 LLM）

内置模板只读（``deletable=False``）；扫描 / 试解析的上传文件写入临时目录，
请求结束自动清理，不落到 OUTPUT_DIR。``scan`` / ``preview`` 必须定义在
``/{schema_id}`` 路由之前。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.v4.coverage import ALLOWED_CONTROLLER_PROFILES
from app.api.v4.schemas import (
    V4HLRSchemaCreate,
    V4HLRSchemaItem,
    V4HLRSchemaListResponse,
    V4HLRSchemaPreviewResponse,
    V4HLRSchemaScanResponse,
    V4HLRSchemaUpdate,
)
from app.auth.deps import current_admin, current_user
from app.auth.models import User
from app.v4 import hlr_schema_store as store
from app.v4.parsers.hlr_schema import (
    BuiltinTemplate,
    HLRSchemaValidationError,
    builtin_templates,
    preview_parse,
    scan_docx_tables,
    schema_to_config,
    validate_schema_payload,
)
from app.v4.profiles import ProfileLoadError, ProfileRegistry


router = APIRouter()

PROFILES_DIR = Path(__file__).resolve().parents[2] / "v4" / "profiles"

# 上传文件大小上限：分块读取，避免超大文件一次性读入内存拖垮服务。
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_UPLOAD_CHUNK_SIZE = 1024 * 1024


def _load_profile(profile_id: str):
    """加载指定控制器 profile（``load_all`` 返回 None，不能链式调用）。"""
    reg = ProfileRegistry()
    reg.load_all(PROFILES_DIR)
    return reg.get_or_raise(profile_id)


def _item_from_builtin(tpl: BuiltinTemplate) -> V4HLRSchemaItem:
    """内置模板 → 列表项（只读、不可删除）。"""
    return V4HLRSchemaItem(
        id=tpl.template_id,
        name=tpl.display_name,
        source="builtin",
        deletable=False,
        usable_for_custom_style=tpl.usable_for_custom_style,
        config=tpl.config,
        created_at=None,
        updated_at=None,
    )


def _item_from_stored(stored: store.StoredSchema) -> V4HLRSchemaItem:
    """已持久化的自定义样式 → 列表项 / 响应。"""
    return V4HLRSchemaItem(
        id=stored.id,
        name=stored.name,
        source="custom",
        deletable=True,
        usable_for_custom_style=True,
        config=schema_to_config(stored.schema),
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )


def _require_docx(filename: str | None) -> None:
    """校验上传文件名：缺失或非 .docx 一律 422（后缀不区分大小写）。"""
    if not filename:
        raise HTTPException(
            status_code=422,
            detail="上传文件缺少文件名，无法识别格式；请选择 .docx 格式的 Word 文档",
        )
    if not filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"仅支持 .docx 文件（当前文件：{filename}）；"
                "请另存为 .docx 后重试"
            ),
        )


async def _write_upload(file: UploadFile, path: Path) -> None:
    """分块写入上传文件；超过 ``_MAX_UPLOAD_BYTES`` 立即 422。

    分块读取避免把整个文件一次读进内存；超限时不继续写入剩余内容。
    """
    total = 0
    with path.open("wb") as fh:
        while True:
            chunk = await file.read(_UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail="文件过大，请上传不超过 50 MB 的 Word 文档",
                )
            fh.write(chunk)


@router.get('/hlr-schemas', response_model=V4HLRSchemaListResponse)
def list_hlr_schemas(
    _user: User = Depends(current_user),
) -> V4HLRSchemaListResponse:
    """内置模板在前（AMS/FGMC/HSCU/RPDU），自定义样式按创建时间在后。

    样式为全局共享资源：查看 / 扫描 / 试解析对全部登录用户开放，只有新建 /
    更新 / 删除限管理员。
    """
    try:
        schemas = [_item_from_builtin(tpl) for tpl in builtin_templates()]
    except ProfileLoadError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"内置控制器配置加载失败：{exc}；"
                f"请检查部署包中的 profile 目录（{PROFILES_DIR}）"
            ),
        ) from exc
    schemas.extend(
        _item_from_stored(stored) for stored in store.list_custom_schemas()
    )
    return V4HLRSchemaListResponse(schemas=schemas)


@router.post(
    '/hlr-schemas', response_model=V4HLRSchemaItem, status_code=201
)
def create_hlr_schema(
    payload: V4HLRSchemaCreate, _user: User = Depends(current_admin)
) -> V4HLRSchemaItem:
    """新建自定义样式（仅管理员）；样式配置非法 422，名称冲突 409。"""
    try:
        schema = validate_schema_payload(payload.config)
        stored = store.create_custom_schema(payload.name, schema)
    except (HLRSchemaValidationError, store.SchemaValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except store.SchemaConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _item_from_stored(stored)


# scan / preview 必须定义在 /{schema_id} 之前（路由按声明顺序匹配）。
@router.post('/hlr-schemas/scan', response_model=V4HLRSchemaScanResponse)
async def scan_hlr_schema(
    file: UploadFile = File(...),
    _user: User = Depends(current_user),
) -> V4HLRSchemaScanResponse:
    """扫描上传 .docx 的表格结构；只做本地解析，不调用 LLM。"""
    _require_docx(file.filename)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "scan.docx"
        await _write_upload(file, path)
        try:
            tables = scan_docx_tables(path)
        except Exception as exc:  # noqa: BLE001 — 解析器异常统一映射为 422
            raise HTTPException(
                status_code=422,
                detail="无法解析该 Word 文档，请确认文件为有效的 .docx",
            ) from exc
    return V4HLRSchemaScanResponse(tables=tables)


@router.post('/hlr-schemas/preview', response_model=V4HLRSchemaPreviewResponse)
async def preview_hlr_schema(
    file: UploadFile = File(...),
    schema: str = Form(...),
    controller_profile: str = Form(...),
    _user: User = Depends(current_user),
) -> V4HLRSchemaPreviewResponse:
    """用指定样式试解析上传 .docx；只做本地解析，不调用 LLM。"""
    _require_docx(file.filename)
    if controller_profile not in ALLOWED_CONTROLLER_PROFILES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"不支持的控制器类型：{controller_profile!r}；"
                f"可选值：{', '.join(sorted(ALLOWED_CONTROLLER_PROFILES))}"
            ),
        )

    try:
        profile = _load_profile(controller_profile)
    except ProfileLoadError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"控制器配置加载失败（{controller_profile}）：{exc}；"
                f"可用的控制器：{', '.join(sorted(ALLOWED_CONTROLLER_PROFILES))}"
            ),
        ) from exc

    try:
        resolved = store.resolve_schema_reference(schema)
    except (HLRSchemaValidationError, store.SchemaValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except store.SchemaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if resolved.mode == "native" and resolved.profile_id != controller_profile:
        raise HTTPException(
            status_code=422,
            detail=(
                f"内置模板 {resolved.template_id} 属于控制器 "
                f"{resolved.profile_id}，与所选控制器 {controller_profile} 不一致；"
                "请选择匹配的控制器，或改用自定义样式"
            ),
        )

    schema_obj = None if resolved.mode == "native" else resolved.schema
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "preview.docx"
        await _write_upload(file, path)
        try:
            result = preview_parse(path, profile, schema_obj)
        except Exception as exc:  # noqa: BLE001 — 解析器异常统一映射为 422
            raise HTTPException(
                status_code=422,
                detail="无法解析该 Word 文档，请确认文件为有效的 .docx",
            ) from exc
    return V4HLRSchemaPreviewResponse(**result)


@router.put('/hlr-schemas/{schema_id}', response_model=V4HLRSchemaItem)
def update_hlr_schema(
    schema_id: str,
    payload: V4HLRSchemaUpdate,
    _user: User = Depends(current_admin),
) -> V4HLRSchemaItem:
    """更新自定义样式（仅管理员）；内置模板只读（403），未知 ID 404。"""
    try:
        schema = validate_schema_payload(payload.config)
        stored = store.update_custom_schema(schema_id, payload.name, schema)
    except (HLRSchemaValidationError, store.SchemaValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except store.SchemaConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except store.SchemaReadOnlyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except store.SchemaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _item_from_stored(stored)


@router.delete('/hlr-schemas/{schema_id}')
def delete_hlr_schema(
    schema_id: str, _user: User = Depends(current_admin)
) -> dict:
    """删除自定义样式（仅管理员）；内置模板只读（403），未知 ID 404。"""
    try:
        store.delete_custom_schema(schema_id)
    except store.SchemaReadOnlyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except store.SchemaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}
