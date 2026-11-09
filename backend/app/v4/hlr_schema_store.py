# -*- coding: utf-8 -*-
"""自定义 HLR 表格样式的持久化、CRUD 与引用解析。

存储文件为 ``get_output_root() / hlr_schemas.json``，结构：

.. code-block:: json

    {"version": 1, "schemas": [
        {"id": "sch_xxxxxxxx", "name": "样式名",
         "created_at": "...", "updated_at": "...",
         "config": {"version": 1, "mode": "docx_vertical",
                    "fields": {...}, "identify": {"id_pattern": "..."}}}
    ]}

其中 ``config`` 即 ``schema_to_config()`` 的结果。写入采用临时文件 +
``os.replace`` 的原子替换；读取遇到损坏文件或单条无效记录时只告警
（stderr）并降级，不向调用方抛异常。

本模块只承担存储与引用解析职责，不包含试解析（scan/preview）、API 或
解析器编排——那些由后续任务实现。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.v4.config import get_output_root
from app.v4.parsers.hlr_schema import (
    BUILTIN_TEMPLATE_PROFILE,
    HLRSchema,
    HLRSchemaValidationError,
    builtin_templates,
    get_builtin_template,
    schema_to_config,
    validate_schema_payload,
)

SCHEMAS_FILE_NAME = "hlr_schemas.json"

_STORE_VERSION = 1
_MAX_NAME_LENGTH = 50
_RAW_SNIPPET_LENGTH = 120
_CN_TZ = timezone(timedelta(hours=8))

# 所有读写都在同一把锁内完成，避免「读-改-写」交错导致记录丢失。
_lock = threading.Lock()


class SchemaValidationError(ValueError):
    """表格样式引用或存储数据不合法。"""


class SchemaNotFoundError(LookupError):
    """引用的自定义表格样式不存在。"""


class SchemaConflictError(ValueError):
    """样式名称与已有自定义样式或内置模板重名。"""


class SchemaReadOnlyError(PermissionError):
    """试图修改或删除只读的内置模板。"""


@dataclass(frozen=True)
class StoredSchema:
    """一条已持久化的自定义样式记录。"""

    id: str
    name: str
    schema: HLRSchema
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ResolvedSchema:
    """样式引用的解析结果：内置模板（native）或自定义/内联样式（custom）。"""

    mode: str
    schema: HLRSchema | None = None
    template_id: str | None = None
    profile_id: str | None = None

    def to_snapshot(self) -> dict:
        """转为可写入任务输入包/日志的字典（只含必要字段）。"""
        if self.mode == "native":
            return {
                "mode": "native",
                "template_id": self.template_id,
                "profile_id": self.profile_id,
            }
        if self.mode == "custom" and self.schema is not None:
            return {"mode": "custom", "schema": schema_to_config(self.schema)}
        raise SchemaValidationError(
            f"无法序列化表格样式引用：mode={self.mode!r}，"
            f"schema 为 {'缺失' if self.schema is None else '存在'}"
        )


def _warn(message: str) -> None:
    print(f"[hlr_schema_store] {message}", file=sys.stderr)


def _schemas_path():
    """存储文件路径；通过模块级 ``get_output_root`` 解析（便于测试替换）。"""
    return get_output_root() / SCHEMAS_FILE_NAME


def _now() -> str:
    return datetime.now(_CN_TZ).isoformat(timespec="seconds")


def _record_to_payload(record: StoredSchema) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "config": schema_to_config(record.schema),
    }


def _record_from_payload(entry: object) -> StoredSchema:
    """将单条磁盘记录还原为 ``StoredSchema``；无效时抛 ``ValueError``。"""
    if not isinstance(entry, dict):
        raise ValueError(f"记录必须为对象，当前为 {type(entry).__name__}")
    record_id = entry.get("id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise ValueError("记录缺少有效的 id")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("记录缺少有效的 name")
    created_at = entry.get("created_at")
    updated_at = entry.get("updated_at")
    if not isinstance(created_at, str) or not isinstance(updated_at, str):
        raise ValueError("记录缺少 created_at / updated_at")
    config = entry.get("config")
    if not isinstance(config, dict):
        raise ValueError("记录缺少 config 对象（应为 schema_to_config 的结果）")
    schema = validate_schema_payload(config)
    return StoredSchema(
        id=record_id,
        name=name,
        schema=schema,
        created_at=created_at,
        updated_at=updated_at,
    )


def _load_records() -> list[StoredSchema]:
    """读取全部自定义样式。文件损坏/结构非法返回空列表，不抛异常。"""
    path = _schemas_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        _warn(f"读取样式文件失败，按空列表处理：{path}（{exc}）")
        return []

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _warn(f"样式文件不是合法 JSON，按空列表处理：{path}（{exc}）")
        return []

    if not isinstance(data, dict) or not isinstance(data.get("schemas"), list):
        _warn(
            '样式文件结构非法（应为 {"version": 1, "schemas": [...]}），'
            f"按空列表处理：{path}"
        )
        return []

    records: list[StoredSchema] = []
    for index, entry in enumerate(data["schemas"]):
        try:
            records.append(_record_from_payload(entry))
        except (ValueError, TypeError) as exc:
            _warn(
                f"样式文件第 {index + 1} 条记录无效，已跳过：{path}（{exc}）"
            )
    return records


def _save_records(records: list[StoredSchema]) -> None:
    """原子写入全部记录（临时文件 + ``os.replace``）。"""
    path = _schemas_path()
    payload = {
        "version": _STORE_VERSION,
        "schemas": [_record_to_payload(record) for record in records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    except UnicodeEncodeError as exc:
        # 名称校验已拒绝孤立代理项，这里兜底其它不可编码数据：
        # 转为存储层校验错误（API 映射 422），并按下方模式清理临时文件。
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise SchemaValidationError(
            f"样式数据包含无法以 UTF-8 编码的字符：{exc}"
        ) from exc
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _normalize_name(name: object) -> str:
    """trim 后校验样式名称：非空、1–50 字符、无控制/不可见字符。"""
    if not isinstance(name, str):
        raise SchemaValidationError(
            f"样式名称必须为字符串，当前为 {type(name).__name__}"
        )
    normalized = name.strip()
    if not normalized:
        raise SchemaValidationError("样式名称不能为空")
    if len(normalized) > _MAX_NAME_LENGTH:
        raise SchemaValidationError(
            f"样式名称不能超过 {_MAX_NAME_LENGTH} 个字符"
            f"（当前 {len(normalized)}）"
        )
    if any(
        unicodedata.category(ch) in {"Cc", "Cf", "Cs"} for ch in normalized
    ):
        # Cf（格式字符，如零宽字符）不可见；Cs（孤立代理项）无法以 UTF-8
        # 编码，若放行会在写盘时抛 UnicodeEncodeError。
        raise SchemaValidationError("样式名称不能包含控制字符或不可见字符")
    return normalized


def _name_key(name: str) -> str:
    return name.strip().casefold()


def _ensure_unique_name(
    name: str, records: list[StoredSchema], *, exclude_id: str | None = None
) -> None:
    """名称忽略大小写唯一：不与已有自定义样式或内置模板重名。"""
    key = _name_key(name)
    for record in records:
        if exclude_id is not None and record.id == exclude_id:
            continue
        if _name_key(record.name) == key:
            raise SchemaConflictError(f"样式名称已存在：{name}")
    for template in builtin_templates():
        if _name_key(template.display_name) == key:
            raise SchemaConflictError(f"样式名称与内置模板重名：{name}")


def _ensure_schema(schema: object) -> None:
    if not isinstance(schema, HLRSchema):
        raise SchemaValidationError(
            f"表格样式必须为 HLRSchema 对象，当前为 {type(schema).__name__}"
        )


def _ensure_not_builtin(schema_id: object) -> None:
    if isinstance(schema_id, str) and schema_id in BUILTIN_TEMPLATE_PROFILE:
        raise SchemaReadOnlyError(
            f"内置模板为只读，不能修改或删除：{schema_id}"
        )


def _new_id(existing_ids: set[str]) -> str:
    while True:
        candidate = "sch_" + uuid.uuid4().hex[:8]
        if candidate not in existing_ids:
            return candidate


def _find_index(records: list[StoredSchema], schema_id: object) -> int | None:
    for index, record in enumerate(records):
        if record.id == schema_id:
            return index
    return None


def list_custom_schemas() -> list[StoredSchema]:
    """按落盘顺序返回全部自定义样式。"""
    with _lock:
        return _load_records()


def get_custom_schema(schema_id: str) -> StoredSchema:
    """按 ID 取自定义样式；不存在抛 ``SchemaNotFoundError``。"""
    with _lock:
        records = _load_records()
    index = _find_index(records, schema_id)
    if index is None:
        raise SchemaNotFoundError(f"自定义表格样式不存在：{schema_id}")
    return records[index]


def create_custom_schema(name: str, schema: HLRSchema) -> StoredSchema:
    """新建自定义样式；名称冲突抛 ``SchemaConflictError``。"""
    normalized = _normalize_name(name)
    _ensure_schema(schema)
    with _lock:
        records = _load_records()
        _ensure_unique_name(normalized, records)
        timestamp = _now()
        record = StoredSchema(
            id=_new_id({item.id for item in records}),
            name=normalized,
            schema=schema,
            created_at=timestamp,
            updated_at=timestamp,
        )
        records.append(record)
        _save_records(records)
    return record


def update_custom_schema(
    schema_id: str, name: str, schema: HLRSchema
) -> StoredSchema:
    """更新自定义样式；内置 ID 只读，未知 ID 不存在。"""
    _ensure_not_builtin(schema_id)
    _ensure_schema(schema)
    normalized = _normalize_name(name)
    with _lock:
        records = _load_records()
        index = _find_index(records, schema_id)
        if index is None:
            raise SchemaNotFoundError(f"自定义表格样式不存在：{schema_id}")
        _ensure_unique_name(normalized, records, exclude_id=schema_id)
        previous = records[index]
        updated = StoredSchema(
            id=previous.id,
            name=normalized,
            schema=schema,
            created_at=previous.created_at,
            updated_at=_now(),
        )
        records[index] = updated
        _save_records(records)
    return updated


def delete_custom_schema(schema_id: str) -> None:
    """删除自定义样式；内置 ID 只读，未知 ID 不存在。"""
    _ensure_not_builtin(schema_id)
    with _lock:
        records = _load_records()
        index = _find_index(records, schema_id)
        if index is None:
            raise SchemaNotFoundError(f"自定义表格样式不存在：{schema_id}")
        del records[index]
        _save_records(records)


def _raw_snippet(raw: str) -> str:
    return raw[:_RAW_SNIPPET_LENGTH]


def _resolve_template(template_id: object) -> ResolvedSchema:
    if template_id == "builtin_rpdu":
        raise SchemaValidationError("RPDU 使用内置 Excel 解析，不支持自定义样式")
    try:
        template = get_builtin_template(template_id)  # type: ignore[arg-type]
    except KeyError:
        raise SchemaValidationError(
            f"未知内置模板：{template_id}；"
            f"可选：{', '.join(BUILTIN_TEMPLATE_PROFILE)}"
        ) from None
    if not template.usable_for_custom_style:
        raise SchemaValidationError(
            f"内置模板 {template.template_id} 不支持自定义样式"
        )
    return ResolvedSchema(
        mode="native",
        template_id=template.template_id,
        profile_id=template.profile_id,
    )


def _resolve_custom(schema_id: object) -> ResolvedSchema:
    with _lock:
        records = _load_records()
    for record in records:
        if record.id == schema_id:
            return ResolvedSchema(mode="custom", schema=record.schema)
    raise SchemaNotFoundError(f"自定义表格样式不存在：{schema_id}")


def resolve_schema_reference(raw: str) -> ResolvedSchema:
    """解析样式引用 JSON。

    优先级：``template_id``（内置模板）> ``schema_id``（自定义样式）> 内联
    样式配置（整个 dict 交给 ``validate_schema_payload``）。
    """
    if not isinstance(raw, str):
        raise SchemaValidationError(
            f"表格样式引用必须为 JSON 字符串，当前为 {type(raw).__name__}"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(
            f"表格样式引用不是合法 JSON：{_raw_snippet(raw)}（{exc}）"
        ) from exc
    if not isinstance(payload, dict):
        raise SchemaValidationError(
            "表格样式引用必须为 JSON 对象（dict），"
            f"当前为 {type(payload).__name__}：{_raw_snippet(raw)}"
        )

    if "template_id" in payload:
        return _resolve_template(payload["template_id"])
    if "schema_id" in payload:
        return _resolve_custom(payload["schema_id"])

    try:
        schema = validate_schema_payload(payload)
    except HLRSchemaValidationError as exc:
        # 统一为存储层错误类型，便于后续任务只捕获一种异常；
        # 原始校验消息保留全部可定位信息。
        raise SchemaValidationError(str(exc)) from exc
    return ResolvedSchema(mode="custom", schema=schema)
