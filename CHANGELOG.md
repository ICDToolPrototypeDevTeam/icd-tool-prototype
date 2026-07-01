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

## [Unreleased] - 2026-06-27

### Added

- **EoICD 真实文件输入支持**：新增 `eoicd_word_parser.py`（Word 文档解析）和 `eoicd_excel_parser.py`（PubSub Excel 表格解析），替代旧版 stub 解析器。
- **PubSub 嵌套数据预处理**：`parsers/__init__.py` 新增 `build_nested_sheets()`，将 PubSub Excel 的 Publisher/Subscriber 行数据转换为三层嵌套结构（Sheet → rows → hierarchy），供 LLM 端直接消费。
- **generation_skill.md 大幅扩展**：从 20 行 stub 扩展为 220+ 行完整生成规则，包含 8 条规则（层级信号名拼接、排除清单、属性中文名映射含英文原名、描述模板、单位自动追加、去重、空值跳过、叶节点属性参考），适配 PubSub 树状层级数据。
- **generation_prompt.md 重写**：明确 PubSub2IRD 处理路径（excel_data 优先），定义 IRD 格式 entry_id 和双模式字段规范（PubSub / 接口模式）。
- **scoring_skill.md 重写**：扩展 4 维评分细则（完整性/一致性/可追溯性/可读性），强制评分区分度和 `recommended_is_best` 唯一推荐。
- **scoring_prompt.md 重写**：移除 stub 描述，明确 chunk 级候选互评要求和横向对比规则，新增 scoring 输出关键提醒。
- **DeepSeek V4 Mode.MD_JSON 支持**：DeepSeek 路径切换为 `Mode.MD_JSON` + thinking 保留，`extract_json_from_codeblock()` 自动跳过 `<think>` 标签提取 JSON，恢复 scoring 等复杂推理任务质量。
- **generation_skill.md 规则 8·叶节点属性参考**：明确 DP/RP 叶节点常见属性列表，中间层级元数据（IDAL、XsdVersion、CANMessageProtocolType 等）不应生成需求条目，抑制模型输出非需求性属性。

### Changed

- DeepSeek 路径从 `Mode.TOOLS` + `thinking=disabled` 切换到 `Mode.MD_JSON` + thinking 保留。
- MiniMax TOOLS mode fallback：content → tool_call fallback 在 tool_calls 缺失时自动提取 JSON 包装。
- `_excel_to_chunk()` 聚合策略：所有 Excel Sheet → 单个 EoICDChunk（`excel-chunk-001`），`excel_data` 字段使用 `build_nested_sheets()` 的嵌套结构。
- `_build_real_llm()` 默认 `provider=openai`，MiniMax/DeepSeek 统一走 LLM → InternalInstructor 路径。
- LLM max_tokens 注入 instructor client，避免 instructor 使用内置默认 4096 截断长输出。

### Fixed

- **CrewAI 上下文污染修复**：所有 generation 和 scoring Task builder 设置 `context=None`，阻止 Process.sequential 自动将前序 Task 的 raw output 注入后续 Task 上下文，消除 MiniMax/DeepSeek scoring 输出完全一致的 bug。
- **多模型凭证冲突修复**：`_litellm_with_fallback` 按模型名动态匹配 `_provider_creds` 注入 api_key/api_base，避免多模型共用 `OPENAI_API_KEY`/`OPENAI_BASE_URL` 环境变量冲突。

### 2026-06-29 代码清理与数据流修复

#### Removed

- 删除 `_flatten_schema_defs()` 函数及调用点（MiniMax $defs 展平逻辑）。该函数基于错误的归因添加：空 tool_call arguments 实际是 DeepSeek TOOLS mode 的问题，已通过切换 MD_JSON 解决，与 MiniMax $defs 无关。
- 删除 MiniMax 分支中的死代码：`if "deepseek-v4" in mdl: thinking=disabled` — 该条件在 `is_minimax=True` 分支下永不为真，从未实际执行。

#### Fixed

- **Excel 数据流修复**：`EoICDChunk.excel_data` 类型从 `Optional[ParsedEoICDExcel]` 改为 `list[dict]`，直接存储 `build_nested_sheets()` 的嵌套结构；`tables` 字段回归只存 Word 内嵌表格。修复了 tasks.py 中 `excel_data=chunk.tables`（将 Word 表格误传为 Excel 数据）的 bug。
- **文档清理**：CHANGELOG、development-log、debug-log 中移除所有基于错误归因的 MiniMax $defs 展平/空 tool_call 相关描述。

## [Unreleased] - 2026-06-29

### Changed

- **前端 UI 完整重设计**：参照 icd_demo v1.0 视觉风格，统一蓝色主题（#0066cc），实现 3 步工作流步骤条（上传文件 → 智能处理 → 查看结果），卡片式布局，Header/Footer 完整框架。
- 文件上传页改为三区布局（EoICD Word / Excel 附件 / 软件高层需求），支持文件列表管理与移除。
- 新增 Word（mammoth）和 Excel（xlsx）客户端实时文件预览。
- 处理中页面新增 3 阶段进度同步：后端 pipeline 实时推送进度文字（解析输入 → 生成评分择优 → 检查需求一致性），前端轮询显示。
- 结果页新增最优条目化需求和差异分析报告双卡 DOCX 预览，保留全部 4 个输出文档下载入口。
- 新增全局 CSS 设计令牌系统（CSS Variables），响应式适配（900px 断点）。

### Added

- 前端新增依赖：`xlsx`（Excel 预览）、`mammoth`（Word 预览）。
- 前端新增组件：`FilePreview.tsx`、`ProcessingView.tsx`、`ResultView.tsx`。
- 前端新增 `index.css` 全局样式表、`types.ts` UI 类型定义。
- 后端 `pipeline.py` 新增 3 阶段 `job.update` 进度消息。

## [Unreleased] - 2026-07-01

### Added

- **真实 SRS Parser 实现**：`backend/app/parsers/software_req_parser.py` 从硬编码 stub 改为基于 python-docx 的真实解析。从"软件需求"章节下的 8 行 × 2 列需求表格中提取字段，按文档约定映射（对象类型 `需求 → requirement`、`注释 → comment`；实现方法 `手工编码 → manual_coding`、`基于模型 → model_based`），并跳过 Table[0] 缩略语表（3×3）。处理空单元格和 "NA"/"N/A" 标记，`requirement_id` 或 `requirement_text` 为空时跳过该条 + warn log。
- **`ParsedSoftwareRequirement` 数据模型扩展**：从 3 字段扩展为 8 字段，新增 `object_type`（"requirement" / "comment"）、`is_derived`（bool）、`rationale`、`verification_method`、`implementation_method`（"manual_coding" / "model_based"）、`source_file`。
- **差异比对输出结构升级**：`DifferenceEntry` 和 `DifferenceItem` 拆 `difference_id` 为两个关联 ID（`difference_requirement_id` 关联 SRS 端 `requirement_id`、`difference_eoicd_entry_id` 关联 EoICD 端 `entry_id`），并把 `requirement_text` 改名为 `eoicd_requirement_text` 以消除歧义。
- **结构化 description 格式**：每条差异的 `description` 字段约定为多属性对比的结构化文本，每行一条 `属性 <名>: SWHLR=<值> IRD=<值> <判定> - <分析>`，末尾追加 `整体判定 / 整体分析 / 整体建议` 三行。判定值 5 种：`一致` / `不一致` / `仅IRD定义` / `仅SWHLR描述` / `待确认`，由 `difference_type` 取值映射。
- **差异报告 docx 渲染升级**：汇总表从 4 列扩展为 5 列（差异编号 / 关联定位 / 差异类型 / 差异描述 / 建议处理方式），详情区新增"关联定位"block（分行列出 SRS ID 和 EoICD 条目 ID）。新增 `_render_description()` 函数按 `\n` 拆行渲染 description，并对"属性 XX:" / "整体XX:" 前缀加粗。
- **comparison_prompt.md 与 comparison_skill.md 同步更新**：清除原"stub"提示，明确两边均为真实解析后的结构化数据；新增 description 结构化格式章节与判定值映射表。

### Changed

- `crew/difference_analyzer.py` 字段搬运更新，对齐新 schema。
- `crew/tasks.py` `expected_output` 字段名同步（`requirement_text` → `eoicd_requirement_text`，新增两个关联 ID 字段名）。
- `llm/mock_llm.py` `_comparison_mock_data()` 5 条 mock diff 改写为新 schema 字段 + 结构化 description 文本（供 mock 模式演示）。

### Known Issues

- 真实 LLM 模式下 `deepseek_comparison` agent 在 description 结构化后输出变长，存在偶发 `max_tokens` 上限触发 `IncompleteOutputException` 的情况，导致任务失败。当前未修改 `agents.py` 的 `max_tokens=8192` 配置，建议后续根据实际输出长度评估调整，或在 prompt 中限制 description 行数上限。