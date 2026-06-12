# 变更记录

本文档记录 ICD工具原型Ver2.0 的版本级变化。

## Unreleased

### Added

- 初始化工程目录和工程文档体系。
- 建立最小可运行前后端工程（React + TypeScript 前端，FastAPI 后端）。
- Docker Compose 本地启动方式。
- 前端文件上传页面和任务状态查询。
- 后端接收 EoICD Word、多个 Excel 附件和软件高层需求文件。
- 后端 job_id 创建与任务状态管理（内存）。
- 两个占位下载接口。
- pipeline.py 端到端流程骨架（mock 实现）。
- 预留 parsers、crew、prompts、skills、scoring、docx 等模块边界。

## [Unreleased] - 2026-06-12

### Added

- 端到端原型数据流：parsers/ → crew/生成候选 → crew/打分 → scoring/融合评分 → crew/差异比对 → docx/ 生成结构化文档。
- parsers/ 模块：结构化 EoICD 解析结果（UnifiedInputPackage），支持接口级条目。
- crew/ 模块三类智能体 stub：候选生成（固定两份）、候选打分（固定评分）、差异比对（固定5条差异项）。
- prompts/ 文本资产：generation_prompt.md、scoring_prompt.md、comparison_prompt.md。
- skills/ 文本资产：generation_skill.md、scoring_skill.md、comparison_skill.md。
- scoring/ 模块：融合 crew 评分（×0.6）和 Python 规则评分（×0.4），决策最佳候选。
- docx/ 模块：生成含结构化表格的 Word 文档（接口名称、信号名、数据类型等 ICD 场景字段）。
- pipeline.py 串联完整数据流，各 stub 模块数据在各阶段流转。

### Changed

- 暂无。

### Fixed

- 暂无。
