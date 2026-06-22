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

## [Unreleased] - 2026-06-16

### Added

- 引入 CrewAI 框架（crewai>=1.0），实现基于 chunk 的多智能体条目化生成、评分择优与对比流程。
- 新增 `backend/app/llm/` 模块：`factory.py`（env 驱动 + mock fallback）、`prompt_loader.py`（Python 端上下文拼接，不修改 prompts/skills 文本）、`mock_llm.py`（继承 `crewai.BaseLLM` 的结构化 mock LLM）。
- 新增 `backend/app/crew/{agents,tasks,crews}.py`：5 个 Agent 工厂、5 个 Task 工厂、3 个 Crew 工厂。
- 新增 `backend/app/merge/` 模块：跨 chunk 合并 + 按模型维度合并。
- `backend/app/models.py` 扩增 `EoICDChunk / ChunkCandidate / ChunkAgentScoreResult / ChunkPythonScoreResult / BestChunkResult / ModelRequirementResult / MergedRequirementResult / ComparisonReportResult / GenerationOutput / ScoringOutput / ComparisonOutput`。
- `parsers/` 升级为 `List[EoICDChunk]`（默认 1 个 chunk-001，但 pipeline 已按 `for chunk in eoicd_chunks` 编写）。
- `scoring/` 升级为 chunk 内 Python 硬规则评分（4 维 25×4=100）+ agent 评分 × 0.6 + python × 0.4 融合。
- `docx/` 输出 4 份 Word：MiniMax条目化需求 / DeepSeek条目化需求 / 最优条目化需求（额外落 EoICD条目化需求.docx） / EoICD与软件高层需求差异报告。
- 新增 2 个下载接口：`/api/jobs/{job_id}/outputs/minimax-requirements`、`/api/jobs/{job_id}/outputs/deepseek-requirements`。
- `requirements.txt` 新增 `crewai>=1.0`、`pydantic>=2.11,<3`。
- 新增 `backend/.env.example`（仅占位，不含真实 Key；`.env` 已被 `.gitignore` 忽略）。
- `docker-compose.yml` 新增 22 个模型相关环境变量占位 + `env_file` 引用 `.env`。
- 新增环境变量：`USE_MOCK_LLM`、`CREWAI_VERBOSE`、`MINIMAX_*`（11 项）、`DEEPSEEK_*`（11 项），全部走 env 读取，**不在代码中写死**任何 API Key、Base URL、Model Name 或运行参数。
- 前端 `api/index.ts` 扩展 `JobResultResponse.outputs` 字段；`getDownloadUrl` 支持 `minimax-requirements` / `deepseek-requirements`。
- 前端 `JobStatus.tsx` 增 2 个下载链接（MiniMax / DeepSeek），保留原 2 个链接，分组展示。

### Changed

- `crew/{candidate_generator,candidate_reviewer,difference_analyzer}.py` 改为调用真实 CrewAI Crew。
- `UnifiedInputPackage.eoicd` 替换为 `eoicd_chunks: List[EoICDChunk]`。
- `JobOutputs.outputs` 扩展 `minimax_docx` / `deepseek_docx` 字段。
- 旧 `/api/jobs/{job_id}/outputs/requirements` 接口语义重映射为"最优条目化需求"（物理文件 `EoICD条目化需求.docx` 保留）。
- `prompts/__init__.py` 和 `skills/__init__.py` 的加载器加 `lru_cache` 缓存。

### Fixed

- 由于 crewai 拉入的 starlette 1.3.1 与 fastapi 0.109.2 冲突，requirements 中显式指定 `starlette<0.37,>=0.36.3` 兼容范围（实际安装验证后记录为 0.36.3）。
- `docker-compose.yml` `env_file: ./backend/.env` 改为 `env_file: { path: ./backend/.env, required: false }`，让 `.env` 可选（详见 `debug-log.md` BUG-20260617-001）。
- `requirements.txt` `uvicorn[standard]==0.27.1` 改为 `uvicorn[standard]>=0.31.1,<0.37`，解决 crewai 间接依赖 mcp>=1.16 要求 uvicorn>=0.31.1 导致的 `ResolutionImpossible`（详见 `debug-log.md` BUG-20260617-002）。
- `docker-compose.yml` volume 路径从 `./backend/app/output:/app/output` 改为 `./backend/app/output:/app/app/output`，对齐 `main.py` 实际写入路径（详见 `debug-log.md` BUG-20260617-003）。

## [Unreleased] - 2026-06-22

### Added

- 引入真实 LLM 后端（Issue #16）：MiniMax M2.7 和 DeepSeek 通过统一的 `provider=openai` 路径接入 CrewAI，`USE_MOCK_LLM=0` 时调用真实 API。
- `backend/app/llm/factory.py` 新增两个 monkey-patch，解决 CrewAI 与 MiniMax/DeepSeek 的结构化输出兼容：
  - `_patch_crewai_completion_for_unsupported_models()`：MiniMax `<think>` 清洗 + `response_format=json_object` 替代不兼容的 `json_schema`。
  - `_patch_crewai_instructor_for_unsupported_models()`：TOOLS mode 下对 MiniMax 间歇性 tool_calls 缺失做 content → tool_call fallback。
- `_provider_creds` 字典 + `_litellm_with_fallback` 按模型名动态注入 API Key/Base URL，避免多模型共用 `OPENAI_API_KEY` 环境变量冲突。
- `get_minimax_llm()` / `get_deepseek_llm()` 新增 `overrides` 参数，Agent 工厂可按角色注入 timeout / max_tokens。
- 5 个 Agent 按职责设定 timeout/max_tokens：generation 300s/16384、scoring 120s/4096、comparison 180s/8192。

### Changed

- `DEEPSEEK_PROVIDER` 统一为 `openai`，与 MiniMax 走相同的 `LLM` → `InternalInstructor` → TOOLS mode 结构化输出路径。
- `docker-compose.yml` 移除 22 个模型相关环境变量内联声明，全部通过 `env_file: ./backend/.env` 注入，简化维护。
- `backend/Dockerfile` 新增 litellm 安装步骤。

### Fixed

- `generation_prompt.md` 修正："生成 2 份候选结果" → "生成 1 份"，wrapper `candidates` 数组 → 单个 `ChunkCandidate` 对象，与 Pydantic schema 对齐，避免 MiniMax 输出多 JSON 拼接导致解析失败。
- `_litellm_with_fallback` 和 `_handle_completion` 的 JSON 解析改用 `JSONDecoder.raw_decode()` 防御多 JSON 拼接场景。

## [Unreleased] - 2026-06-12

### Changed

- 暂无。

### Fixed

- 暂无。

