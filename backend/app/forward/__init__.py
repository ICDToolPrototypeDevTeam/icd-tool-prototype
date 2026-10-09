# -*- coding: utf-8 -*-
"""正向（完整性）集成适配层（Issue #119）。

正反向分离原则：本包只做「把 backend/forward 下的原样正向代码跑起来，并把结果
翻译成 V4 任务契约」，**不得 import `app.v4.*` 的任何业务模块**（仅可用
`app.job_manager` / `app.job_log` 等基础设施），也不被 `app.v4.*` 反向 import。
"""
