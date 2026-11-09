# -*- coding: utf-8 -*-
"""HLR 表格样式模型：校验、内置模板派生与解析配置构建。

本模块只承担数据层职责：

- 校验用户提交的表格样式配置（``validate_schema_payload``）；
- 将样式序列化回配置字典（``schema_to_config``）；
- 从内置 profile 派生可参考的内置模板（``builtin_templates``）；
- 将样式叠加到既有 ``HLRParserConfig`` / ``ControllerProfile`` 上
  （``build_parser_config`` / ``build_profile_with_schema``）；
- 文档表格扫描与试解析辅助（``scan_docx_tables`` / ``preview_parse``）。

不包含存储或 API 逻辑——那些由后续任务实现。
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document  # type: ignore

from app.v4.parsers.hlr_word_parser import HLRWordParser, _cell_text
from app.v4.parsers.zip_entry_normalize import ensure_standard_zip
from app.v4.profiles import apply_hlr_preprocess_hook
from app.v4.profiles.base import (
    ControllerProfile,
    HLRParserConfig,
    RequirementScanConfig,
    load_profile_from_yaml,
)

# 用户自定义样式允许出现的标准字段名（顺序即报错时的提示顺序）。
ALLOWED_SCHEMA_FIELDS: tuple[str, ...] = (
    "id",
    "content",
    "rationale",
    "object_type",
    "is_derived",
    "is_safety_related",
    "verification_method",
    "implementation",
    "code",
    "source",
    "covered_ids",
    "notes",
    "input_data",
    "output_data",
    "is_requirement",
)

# 内置模板 ID → profile ID（顺序决定 ``builtin_templates()`` 的返回顺序）。
BUILTIN_TEMPLATE_PROFILE: dict[str, str] = {
    "builtin_ams": "ams",
    "builtin_fgmc": "fgmc",
    "builtin_hscu": "hscu",
    "builtin_rpdu": "rpdu",
}

MAX_LABEL_LENGTH = 100
MAX_ALIASES_PER_FIELD = 20
MAX_ID_PATTERN_LENGTH = 200

_PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"

_REQUIRED_FIELDS: tuple[str, ...] = ("id", "content")


class HLRSchemaValidationError(ValueError):
    """表格样式配置校验失败（``ValueError`` 子类）。"""


@dataclass(frozen=True)
class HLRSchema:
    """校验通过后的表格样式。

    ``fields`` 的键为 ``ALLOWED_SCHEMA_FIELDS`` 中的标准字段名，值为去重后
    保序的行标签别名元组。``id_pattern`` 作用于最终提取出的需求 ID。
    """

    fields: dict[str, tuple[str, ...]]
    id_pattern: str = ""
    version: int = 1
    mode: str = "docx_vertical"


@dataclass(frozen=True)
class BuiltinTemplate:
    """内置控制器模板（只读展示，供用户在此基础上修改）。"""

    template_id: str
    profile_id: str
    display_name: str
    config: dict
    usable_for_custom_style: bool


def _fail(message: str) -> None:
    raise HLRSchemaValidationError(message)


def _validate_label(field_name: str, position: int, raw_label: object) -> str:
    """校验并返回单个标签（trim 后）。"""
    if not isinstance(raw_label, str):
        _fail(
            f"fields.{field_name}[{position}] 必须为字符串，"
            f"当前为 {type(raw_label).__name__}"
        )
    label = raw_label.strip()
    if not label:
        _fail(
            f"fields.{field_name}[{position}] 不能为空字符串"
            "（去掉首尾空格后为空）"
        )
    if len(label) > MAX_LABEL_LENGTH:
        _fail(
            f"fields.{field_name} 的标签 {label!r} 超过 "
            f"{MAX_LABEL_LENGTH} 个字符（当前 {len(label)}）"
        )
    return label


def validate_schema_payload(payload: dict) -> HLRSchema:
    """校验用户提交的表格样式配置并返回 ``HLRSchema``。

    只读取 ``version`` / ``mode`` / ``fields`` / ``identify``；``name`` 及
    其他顶层元数据被忽略（名称由记录层承载）。校验失败抛出
    ``HLRSchemaValidationError``，消息包含具体字段或标签。
    """
    if not isinstance(payload, dict):
        _fail(
            "表格样式配置必须为对象（dict），"
            f"当前为 {type(payload).__name__}"
        )

    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        _fail(f"version 必须为 1，当前为 {version!r}")

    mode = payload.get("mode")
    if mode != "docx_vertical":
        _fail(f"mode 必须为 'docx_vertical'，当前为 {mode!r}")

    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, dict) or not raw_fields:
        _fail("fields 必须为非空对象（dict），且至少包含 id 与 content")

    fields: dict[str, tuple[str, ...]] = {}
    # 小写标签 → 已占用该标签的字段名（跨字段不区分大小写唯一）。
    label_owner: dict[str, str] = {}
    for field_name, raw_labels in raw_fields.items():
        if field_name not in ALLOWED_SCHEMA_FIELDS:
            _fail(
                f"fields 中存在不支持的字段 {field_name!r}；"
                f"允许的字段：{', '.join(ALLOWED_SCHEMA_FIELDS)}"
            )
        if not isinstance(raw_labels, (list, tuple)):
            _fail(
                f"fields.{field_name} 必须为字符串列表，"
                f"当前为 {type(raw_labels).__name__}"
            )
        labels: list[str] = []
        seen_in_field: set[str] = set()
        for position, raw_label in enumerate(raw_labels):
            label = _validate_label(field_name, position, raw_label)
            key = label.lower()
            if key in seen_in_field:
                continue  # 同字段内去重保序（忽略大小写）
            seen_in_field.add(key)
            labels.append(label)
        if len(labels) > MAX_ALIASES_PER_FIELD:
            _fail(
                f"fields.{field_name} 的标签超过 {MAX_ALIASES_PER_FIELD} 个"
                f"（当前 {len(labels)}）"
            )
        for label in labels:
            key = label.lower()
            owner = label_owner.get(key)
            if owner is not None:
                _fail(
                    f"标签 {label!r} 同时出现在 fields.{owner} 与 "
                    f"fields.{field_name}；同一标签（忽略大小写）只能归属一个字段"
                )
            label_owner[key] = field_name
        fields[field_name] = tuple(labels)

    for required in _REQUIRED_FIELDS:
        if not fields.get(required):
            _fail(
                f"fields 缺少必填字段 {required!r} 的标签；"
                f"{required!r} 必须存在且至少包含一个非空标签"
            )

    id_pattern = ""
    identify = payload.get("identify")
    if identify is not None:
        if not isinstance(identify, dict):
            _fail(
                "identify 必须为对象（dict），"
                f"当前为 {type(identify).__name__}"
            )
        raw_pattern = identify.get("id_pattern", "")
        if raw_pattern is None:
            raw_pattern = ""
        if not isinstance(raw_pattern, str):
            _fail(
                "identify.id_pattern 必须为字符串，"
                f"当前为 {type(raw_pattern).__name__}"
            )
        id_pattern = raw_pattern.strip()
        if len(id_pattern) > MAX_ID_PATTERN_LENGTH:
            _fail(
                "identify.id_pattern 超过 "
                f"{MAX_ID_PATTERN_LENGTH} 个字符（当前 {len(id_pattern)}）"
            )
        if id_pattern:
            try:
                re.compile(id_pattern)
            except re.error as e:
                _fail(
                    f"identify.id_pattern 不是合法正则：{id_pattern!r}（{e}）"
                )

    return HLRSchema(
        fields=fields,
        id_pattern=id_pattern,
        version=version,
        mode=mode,
    )


def schema_to_config(schema: HLRSchema) -> dict:
    """将 ``HLRSchema`` 序列化为可存储/回显的配置字典。"""
    return {
        "version": 1,
        "mode": "docx_vertical",
        "fields": {name: list(labels) for name, labels in schema.fields.items()},
        "identify": {"id_pattern": schema.id_pattern},
    }


def builtin_templates() -> list[BuiltinTemplate]:
    """从内置 profile 派生四个控制器模板（AMS/FGMC/HSCU/RPDU 顺序）。

    不缓存：仅供测试与界面低频调用，保持实现简单。
    """
    templates: list[BuiltinTemplate] = []
    for template_id, profile_id in BUILTIN_TEMPLATE_PROFILE.items():
        profile = load_profile_from_yaml(
            _PROFILES_DIR / profile_id / "config.yaml"
        )
        config = {
            "version": 1,
            "mode": "docx_vertical",
            "fields": {
                name: list(labels)
                for name, labels in profile.hlr_parser.field_map.items()
            },
            "identify": {
                "id_pattern": profile.hlr_parser.requirement_scan.id_pattern,
            },
        }
        templates.append(
            BuiltinTemplate(
                template_id=template_id,
                profile_id=profile.profile_id,
                display_name=profile.display_name,
                config=config,
                usable_for_custom_style=profile.profile_id != "rpdu",
            )
        )
    return templates


def get_builtin_template(template_id: str) -> BuiltinTemplate:
    """按 ID 取内置模板；未知 ID 抛 ``KeyError``。"""
    for template in builtin_templates():
        if template.template_id == template_id:
            return template
    raise KeyError(
        f"未知的内置表格模板：{template_id!r}；"
        f"可选：{', '.join(BUILTIN_TEMPLATE_PROFILE)}"
    )


def build_parser_config(
    base: HLRParserConfig, schema: HLRSchema
) -> HLRParserConfig:
    """在 ``base`` 基础上套用自定义样式，返回新的解析配置。

    样式决定行标签映射、需求表识别方式与非需求过滤；``base`` 中与样式
    无关的语义（术语表定位等）原样保留。``base.field_map`` 不会被修改。
    """
    field_map: dict[str, tuple[str, ...]] = {
        name: tuple(labels) for name, labels in schema.fields.items()
    }

    # 「是否为需求」由样式显式映射启用：只有用户把某行标签映射到
    # is_requirement 时才过滤非需求表；未映射则完全不过滤（即使基础
    # profile 声明了 filter_non_requirement，例如 FGMC 内置模板）。
    # 判定值沿用基础 profile 的 non_requirement_value（默认“否”）。
    requirement_labels = tuple(field_map.get("is_requirement", ()))
    filter_non_requirement = bool(requirement_labels)
    if not filter_non_requirement:
        field_map.pop("is_requirement", None)

    requirement_scan = RequirementScanConfig(
        mode="all_tables",
        id_pattern=schema.id_pattern,
        # 试解析需要看到 id_pattern_rejected 等警告；只写 parse_warnings，
        # 不改变提取结果。
        register_skipped=True,
        warn_min_rows=base.requirement_scan.warn_min_rows,
        required_fields=("id", "content"),
        require_content_value=True,
    )

    return dataclasses.replace(
        base,
        field_map=field_map,
        requirement_scan=requirement_scan,
        filter_non_requirement=filter_non_requirement,
    )


def build_profile_with_schema(
    base: ControllerProfile, schema: HLRSchema
) -> ControllerProfile:
    """返回套用自定义样式后的 profile（其余配置保持不变）。"""
    return dataclasses.replace(
        base,
        hlr_parser=build_parser_config(base.hlr_parser, schema),
    )


# ============================================================
# 文档扫描与试解析辅助（Task 4）
# ============================================================

# 试解析预览单字段截断长度（正文/基本原理）。
_PREVIEW_TEXT_LIMIT = 200
# 预览主字段之外、不计入 extra_fields 的字段。
_PREVIEW_EXCLUDED_FIELDS = frozenset(
    {"requirement_id", "content", "rationale", "source_file"}
)
# 样式配置里叫 implementation，需求模型里叫 implementation_method。
_EXTRA_FIELD_RENAMES = {"implementation_method": "implementation"}


def _builtin_id_aliases() -> set[str]:
    """全部内置模板中 id 字段的行标签别名（trim + casefold）。"""
    aliases: set[str] = set()
    for template in builtin_templates():
        raw = template.config.get("fields", {}).get("id", ())
        for alias in raw:
            if isinstance(alias, str) and alias.strip():
                aliases.add(alias.strip().casefold())
    return aliases


def scan_docx_tables(
    path: Path,
    max_tables: int = 30,
    max_rows: int = 30,
    max_text: int = 200,
) -> list[dict]:
    """扫描 .docx 中的表格结构，供用户配置表格样式时参考。

    返回每张表的索引、行数、列数、是否疑似需求表，以及前 ``max_rows`` 行
    的「第 0 列行标签 + 第 1 列值预览」（每格截断到 ``max_text`` 个字符）。

    ``looks_like_requirement`` 的判定：至少 2 列 2 行，且任一第 0 列标签
    命中内置模板的 id 别名，或包含「需求」。别名集合每次调用从
    ``builtin_templates()`` 现取，避免 profile 调整后与本函数失配。
    """
    path, _ = ensure_standard_zip(Path(path))
    document = Document(str(path))
    id_aliases = _builtin_id_aliases()

    tables: list[dict] = []
    for table_index, table in enumerate(document.tables[:max_tables]):
        row_count = len(table.rows)
        column_count = len(table.columns)
        labels = [_cell_text(table, r, 0) for r in range(row_count)]
        # python-docx 会把越界列「展平」到下一行：单列表中 cell(r, 1) 实际
        # 返回下一行第 0 列的值，必须显式判宽，避免把相邻行标签当成值预览。
        has_value_column = column_count >= 2
        looks_like_requirement = (
            column_count >= 2
            and row_count >= 2
            and any(
                label.strip().casefold() in id_aliases or "需求" in label
                for label in labels
            )
        )
        tables.append(
            {
                "table_index": table_index,
                "row_count": row_count,
                "column_count": column_count,
                "looks_like_requirement": looks_like_requirement,
                "rows": [
                    {
                        "label": labels[r][:max_text],
                        "value_preview": (
                            _cell_text(table, r, 1)[:max_text]
                            if has_value_column
                            else ""
                        ),
                    }
                    for r in range(min(row_count, max_rows))
                ],
            }
        )
    return tables


def _preview_extra_fields(requirement) -> dict:
    """预览主字段之外的非空字段（``implementation_method`` 改名为 ``implementation``）。"""
    extra_fields: dict = {}
    for name, value in requirement.model_dump().items():
        if name in _PREVIEW_EXCLUDED_FIELDS or not value:
            continue
        extra_fields[_EXTRA_FIELD_RENAMES.get(name, name)] = value
    return extra_fields


def _preview_warnings(requirements: list) -> list[dict]:
    """重复 ID 与空正文提示（仅针对解析后的全部需求）。"""
    warnings: list[dict] = []

    counts: dict[str, int] = {}
    for req in requirements:
        counts[req.requirement_id] = counts.get(req.requirement_id, 0) + 1
    duplicated_ids = [rid for rid, count in counts.items() if count > 1]
    if duplicated_ids:
        shown = duplicated_ids[:10]
        message = "存在重复的需求ID：" + "、".join(shown)
        if len(duplicated_ids) > 10:
            message += f"（共 {len(duplicated_ids)} 个，仅显示前 10 个）"
        warnings.append({"kind": "duplicate_id", "message": message})

    empty_count = sum(1 for req in requirements if not req.content.strip())
    if empty_count:
        warnings.append(
            {
                "kind": "empty_content",
                "message": f"有 {empty_count} 条需求的正文为空",
            }
        )
    return warnings


def preview_parse(
    path: Path,
    base_profile: ControllerProfile,
    schema: HLRSchema | None = None,
    limit: int = 5,
) -> dict:
    """用给定样式试解析 .docx，返回供界面预览的概要结果。

    ``schema`` 为 ``None`` 时按 ``base_profile`` 的原生规则解析；否则先
    用 ``build_profile_with_schema`` 套用自定义样式。流程与
    ``pipeline._parse_hlr`` 保持一致：先归一化非规范 zip 包，解析后临时
    暴露完整路径调用 HLR 预处理 hook，再恢复 ``source_file`` 的文件名，
    避免 hook 异常时泄漏完整路径。

    ``total_count`` 是解析出的全部需求数，``requirements`` 只截取前
    ``limit`` 条。``warnings`` 汇总重复 ID、空正文与 ``parse_warnings``
    中的登记项；``parse_warnings`` 保留解析器的原始登记。
    """
    path, _ = ensure_standard_zip(Path(path))
    profile = (
        build_profile_with_schema(base_profile, schema) if schema else base_profile
    )
    out = HLRWordParser(path, profile=profile).parse()

    saved_source_file = out.source_file
    hook_rewritten = 0
    try:
        out.source_file = str(path)
        hook_rewritten = apply_hlr_preprocess_hook(profile, out)
    finally:
        out.source_file = saved_source_file

    parse_warnings = list(out.parse_warnings)
    warnings = _preview_warnings(out.requirements)
    for warning in parse_warnings:
        warnings.append(
            {
                "kind": warning.get("kind", "unknown"),
                "message": warning.get("reason", ""),
            }
        )

    return {
        "total_count": out.total_count,
        "requirements": [
            {
                "requirement_id": req.requirement_id,
                "content_preview": req.content[:_PREVIEW_TEXT_LIMIT],
                "rationale_preview": req.rationale[:_PREVIEW_TEXT_LIMIT],
                "extra_fields": _preview_extra_fields(req),
            }
            for req in out.requirements[:limit]
        ],
        "warnings": warnings,
        "parse_warnings": parse_warnings,
        "hook_applied": hook_rewritten > 0,
    }
