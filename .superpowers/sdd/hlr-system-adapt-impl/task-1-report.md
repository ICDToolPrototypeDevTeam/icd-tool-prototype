# Task 1 Report: 配置层 - 新增 HLR_SYSTEMS 配置

## Status

DONE

## Completed Content

在 `backend/app/v4/config.py` 末尾新增 `HLR_SYSTEMS` 配置字典和 `get_hlr_system_config()` 函数。

新增内容包括：

1. `HLR_SYSTEMS: dict[str, dict]` 字典，包含两个系统类型配置：
   - `hvac`（环控系统）：术语表索引 0、需求表 8 行、字段行索引按 brief 定义
   - `fuel`（燃油系统）：术语表索引 1、需求表 13 行、字段行索引按 brief 定义，`implementation_method` 为 `None`，`is_requirement_is_boolean` 为 `True`
2. `get_hlr_system_config(system_type: str)` 函数：根据系统类型返回对应配置，未知类型抛出 `ValueError`

## Modified Files

- `backend/app/v4/config.py`（在 `load_synonyms` 函数后追加配置块）

## Added Files

- 无

## Deleted Files

- 无

## Updated Documents

- 无（按要求，本任务为机械性配置新增，不涉及文档更新）

## Verification Command

```bash
cd "backend" && python -c "from app.v4.config import HLR_SYSTEMS, get_hlr_system_config; print('hvac' in HLR_SYSTEMS, 'fuel' in HLR_SYSTEMS)"
```

## Verification Result

```
True True
```

输出与预期完全一致。

## Concerns / Notes

- 验证过程中发现环境缺少 `python-dotenv` 模块，已通过 `pip install python-dotenv` 安装（v1.2.3）后验证通过。该依赖已在 `backend/requirements.txt` 中声明，属环境初始化问题，不影响本任务实现。
- `fuel` 配置的 `implementation_method` 值为 `None`，符合 brief 语义（燃油表无对应列）。
- 两个系统的 `is_requirement` 解析方式不同：`hvac` 通过 `is_requirement_value` 字符串比对，`fuel` 通过 `is_requirement_is_boolean` 直接读取布尔值。后续使用方需根据这一差异编写解析分支。

## Next Step Suggestions

- Task 2 可基于 `HLR_SYSTEMS` 配置读取对应字段，开始术语表与需求表解析逻辑的适配。