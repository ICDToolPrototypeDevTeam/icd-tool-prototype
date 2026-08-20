# Task 4 Report: Runner 层改造 - 传递 system_type 到 pipeline

## Status
DONE

## Summary
按 Task 4 brief 完成了 `backend/app/api/v4/runner.py` 的改造：

1. `run_v4_pipeline_thread` 新增 `system_type: str = "hvac"` 参数，并将其作为关键字参数传给 `run_reverse_pipeline`。
2. `launch_v4_pipeline` 新增同名同默认值参数，并在 `args` 元组中传递给后台线程。

未触及任何其它代码或文档（最小改动原则）。

## Modified Files
- `backend/app/api/v4/runner.py`

## Changes Detail

### run_v4_pipeline_thread (runner.py:176)
签名尾追加：
```python
system_type: str = "hvac",
```
`run_reverse_pipeline(...)` 调用尾追加：
```python
system_type=system_type,
```

### launch_v4_pipeline (runner.py:269)
签名尾追加：
```python
system_type: str = "hvac",
```
`args=(...)` 元组尾追加：
```python
system_type
```

## Verification

### Command
```
cd backend && python -c "from app.api.v4.runner import launch_v4_pipeline; print('OK')"
```

### Output
```
OK
```

模块可以正常导入；新参数作为默认参数加入，未改变既有调用方（向后兼容）。

## Concerns
无。

补充说明（与本任务范围相关、不需要修复但需要记录）：
- `run_forward_pipeline` 尚未接受 `system_type`，与 Task 3 备注一致；按 plan 不在本任务范围。
- `system_type` 当前仅在 runner 层透传，路由层（`v4/routes.py`）仍固定使用 `"hvac"` 默认值；后续 Task 会在路由层根据前端请求 / 系统类型动态注入。