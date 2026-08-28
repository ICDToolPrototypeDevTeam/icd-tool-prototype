# Task 5 Report: API Schema 层 - 新增 SystemType 枚举

## Status
DONE

## Summary
在 `backend/app/api/v4/schemas.py` 顶部新增 `SystemType(str, Enum)` 枚举，包含 `HVAC = "hvac"` 和 `FUEL = "fuel"` 两个值。

## Modified Files
- `backend/app/api/v4/schemas.py`
  - 新增 `from enum import Enum` import
  - 新增 `SystemType` 枚举类（位于 imports 之后，schema 类之前）

## Verification Command
```bash
cd backend && python -c "from app.api.v4.schemas import SystemType; print(SystemType.HVAC, SystemType.FUEL)"
```

## Verification Output
```
SystemType.HVAC SystemType.FUEL
```

## Diff
```python
 from __future__ import annotations

+from enum import Enum
 from typing import Optional

 from pydantic import BaseModel

 from app.models import JobStatus

+
+class SystemType(str, Enum):
+    HVAC = "hvac"
+    FUEL = "fuel"
+
```

## Notes
- `SystemType` 继承 `str, Enum`，方便在 Pydantic 模型中作为字段类型直接使用。
- 后续 Task 6 可在 `coverage.py` 中以 `system_type: SystemType` 形式引用本枚举。