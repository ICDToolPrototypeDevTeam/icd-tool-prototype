# -*- coding: utf-8 -*-
"""任务执行层：inline（进程内队列）/ celery（独立 Worker 进程）。

本包刻意不在 ``__init__`` 导入任何子模块：``app.job_manager`` 会读
:mod:`app.execution.config` 判断执行模式，若这里顺手导入 celery / redis，
未安装执行依赖的测试环境会连带失败。
"""
