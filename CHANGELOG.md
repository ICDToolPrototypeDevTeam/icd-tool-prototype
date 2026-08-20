# 变更记录

本文档记录 ICD工具原型Ver2.0 的版本级变化。

## [Unreleased] - 2026-08-12

### Changed

- **LLM Client 截断自适应重试下沉**：`finish_reason=length` 截断重试从业务层 (`_chat_with_truncation_retry`) 下沉到三个 LLM client（DeepSeek / MiniMax / Qwen）的 `chat()` 方法内部，截断时自动翻倍 `max_tokens` 重试（4096→8192→16384，上限 16384），覆盖所有 LLM 调用方（judge / review / labeler）。`ChatResponse.truncated` 字段随之移除。

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

## [Unreleased] - 2026-07-28：V4.0 后端工程化集成（Issue A 落地）

### Added

- **V4.0 后端工程化集成**：把 `_v4_backend_raw/backend/app/` 整体迁入 `backend/app/v4/`，新增 `/api/v4` FastAPI 路由命名空间，V3 与 V4 双版本共存。
- 新增 5 个 V4 FastAPI 端点（`/api/v4/*` 前缀）：
  - `POST /api/v4/coverage-analysis`：multipart 接收 `hlr_word_file` + `eoicd_publisher_file` / `eoicd_subscriber_file` + 可选 `traceability_files` + `use_mock_llm` / `judge_providers` / `enable_traceability_prefilter`，同步返回 V4JobId。
  - `GET /api/v4/jobs/{job_id}`：返回 `V4JobStatusResponse`（含 `stage` / `stage_index` / `case_index` / `mock_models`）。
  - `GET /api/v4/jobs/{job_id}/result`：返回 `V4JobResultResponse`（含 `summary` / `outputs` / `mock_models` / `errors`）。
  - `GET /api/v4/jobs/{job_id}/outputs/{eoicd-xlsx|consensus-docx|consistency/{model}}`：3 类对外下载。
  - `GET /api/v4/health`：V4 健康检查。
- 新增 V4 Pydantic schemas：`V4AnalyzeResponse` / `V4JobStatusResponse` / `V4JobOutputs` / `V4JobResultSummary` / `V4JobResultResponse`（位于 `backend/app/api/v4/schemas.py`），与 V3 响应 schema 互不污染。
- 新增 V4 runner 工具（`backend/app/api/v4/runner.py`）：`run_v4_pipeline_thread()` 后台线程包装，env 保存/恢复（修正 #2：进入线程前 `os.environ.get` 备份，退出时 `try/finally` 恢复），落盘后反读 `multi_judge_results.json` 派生 `mock_models`（D5 规则：`mock_models = [p for p in actual_providers if p in {"minimax","qwen"}]`）。
- 新增 ADR-001：`docs/decisions/ADR-001-V4后端接入策略.md`（D1-V4 作为后续主线；D2-V3 旧 API 暂留；D3-`/api/v4` 独立命名空间；D4-V4 业务逻辑保护；D5-mock_models 显式标识；D6-`consistency/{model}` 扩展点；D7-JSON 不暴露）。
- 新增追溯表预筛选能力（V4 `enable_traceability_prefilter=true`）：`backend/app/v4/traceability/trace_parser.py` 在主名匹配失败时 `glob(*.xlsx)` 排序兜底（解决 MSYS bash 中文文件名编码降级场景）。

### Changed

- **V4 路径布局（按 Issue A 决定）**：
  - V3 任务目录：`backend/output/v3/{job_id}/`（平铺，input + output 不分）。
  - V4 任务目录：`backend/output/v4/{job_id}/input/` + `backend/output/v4/{job_id}/output/`（分两层）。
  - 此前 V4 临时目录 `backend/app/output/` 已删除，所有输出迁到 `backend/output/` 根下。
- **V3 与 V4 共享 JobManager**：`backend/app/job_manager.py` 给 `Job` 加 `kind: Literal["v3","v4"]` 字段（默认 "v3"），V4 路由显式传 `kind="v4"`；V3/V4 路由跨版本查询返回 404 + 友好提示。
- **V3 旧 V3 router（机械拆分）**：原 `backend/app/main.py` 拆分到 `backend/app/api/v3/router.py`（173 行），其中 `/api/jobs/{job_id}` 与 `/api/jobs/{job_id}/result` 加 `job.kind != "v3"` 跨版本 404 检查；其他路由 URL、字段、后台线程逻辑、文件保存、下载 helper 全部保持原状。`backend/app/main.py` 改为 33 行的 thin shell（CORS + V3 router 装载，预留 V4 router 装载位）。
- **V4 router 路径表达式统一 4 层**：`coverage.py:job_dir` / `outputs.py:root` / `jobs.py:base_outputs_dir` 都从 `parent.parent.parent` 升级为 `parent.parent.parent.parent`，与 V3 共享 `backend/output/` 根目录对齐。
- **V4 路由层 V4 路径计算都用 4 层**（不再走 3 层）；3 处都用 `Path(__file__).resolve().parent.parent.parent.parent / 'output' / 'v4' / {job_id} [/output]` 模式。
- **`backend/.env.example` 收尾**：删去之前为 V4 加的 `_V4` 后缀占位段（`DEEPSEEK_API_KEY_V4` / `DEEPSEEK_BASE_URL_V4` 等），恢复 39 行 V3-only 模板。V4 直接读 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `JUDGE_PROVIDERS` / `USE_MOCK_LLM`。
- **`backend/requirements.txt` 增 3 项 V4 依赖**：`python-dotenv>=1.0.0`、`requests>=2.31.0`、`pyyaml>=6.0`。
- **`docker-compose.yml` volume 路径调整**：`. / backend/app/output:/app/app/output` → `./backend/output:/app/output`，与 V3/V4 共享根目录。
- **`backend/app/v4/config.py` 环境加载路径**：`_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"`（原 `_v4_backend_raw/backend/.env`）改为 `parent.parent.parent / ".env"`（`backend/.env`）。

### Fixed

- **V4 DeepSeek URL 双 `/v1` 拼写 bug**：`backend/app/v4/llm/deepseek_client.py:34` 和 `backend/app/v4/matching/hlr_labeler.py:51` 各自拼 `f"{base_url.rstrip('/')}/v1/chat/completions"`，与 `.env` 里 `DEEPSEEK_BASE_URL=https://api.deepseek.com/v1`（带 `/v1` 后缀）叠加成 `/v1/v1/chat/completions`，DeepSeek 报 404。修复：两处都加 `if base.endswith('/v1'): base = base[:-3]` 幂等保护。
- **V4 trace_parser 硬编码中文文件名 brittleness**：`backend/app/v4/traceability/trace_parser.py:115,170` 直接 `trace_dir / "单模块需求矩阵分析（设备2软件高层）-裁剪.xlsx"` 等中文字符串，与 `enable_traceability_prefilter=true` 路径上 MSYS bash 编码降级冲突。修复：抽 `_discover_trace_files(trace_dir)` 工具函数，**先按主名精确匹配 → 失败则 `trace_dir.glob("*.xlsx")` 排序兜底**，并保证 Table 1 / Table 2 不会命中同一文件。
- **V4 outputs.py / coverage.py path 4 层 Bug（Issue A 期间发现）**：`parent.parent.parent.parent`（4 层）导致容器内落点 `/app/output/v4/...` 落到了 volume mount 之外。修到 3 层（`parent.parent.parent`），与 V3 router 一致。
- **V4 `parent.parent.parent.parent` / `parent.parent.parent` 4 vs 3 层 Bug（Issue A 期间发现）**：`jobs.py:70` 和 `outputs.py:32` 仍在 3 层，与 coverage.py 写盘 4 层错位，导致 `/result.outputs` 全部 false / 5 类下载 404。修到 4 层，对齐 coverage.py。
- **V4 `hlr_labeler` 直接 `requests.post`**：原 `_call_label_api` 独立拼 URL / Authorization 头 / retry 循环，不走 `get_llm()` factory，与 `comparison/*.py` 的 3 处调用方式分裂。修复：`_call_label_api` 改用 `get_llm("deepseek").chat(messages=..., max_retries=0)`，外层 retry 仅兜 JSON 解析错误；`label_hlrs` 去掉 `api_key/base_url/model` 参数，全部由 factory 从 env 读。V4 内部 `import requests` 从 2 处（deepseek_client.py + hlr_labeler.py）收敛为 1 处（deepseek_client.py 抽象层）。

### Notes

- 本次 Issue A 是 V4 后端工程化集成的"包装层"工作，V4 业务逻辑零修改（除 bug 1 / bug 2 两处修复外）。Issue A 期间两次对 V4 内部模块（deepseek_client.py、hlr_labeler.py、trace_parser.py）的修改按用户授权"特权"实施，未建立 ADR-002。
- 本期 V4 仅暴露 3 类对外下载（`eoicd-xlsx` / `consensus-docx` / `consistency/{model}`）；4 类内部 JSON（`multi_judge_results.json` / `consensus_results.json` / `reverse_matches.json` / `reverse_report.json` 等）按 D7 不暴露给 API。
- `/api/v4/jobs/{id}/result.outputs.eoicd_xlsx` 等 5 个布尔字段在 V4 落盘成功后均为 true，由 `runner.derive_outputs()` 反读盘与 SSoT 一致。
- V4 业务内部 import `requests` 仅 1 处（`backend/app/v4/llm/deepseek_client.py`）；其余 4 处 LLM 调用（`comparison/{semantic_judge,multi_judge,review_agent}.py` + `matching/hlr_labeler.py`）均走 `get_llm("deepseek").chat()` 工厂。

## [Unreleased] - 2026-07-31：V4 追溯表兜底机制与共识报告增强

### Added

- **追溯索引协议开销字段过滤**：`trace_parser.py` 新增 `_PROTOCOL_BLOCKKEY_SUFFIXES` 常量，在构建 HLR→BlockKey 追溯索引时自动跳过 `/SDI`、`/LABEL`、`/PARITY`、`/SSM`、`/OCTLBL` 等 A429 协议开销后缀的 block_key，避免虚增 traced-block 统计。
- **追溯表预筛选兜底机制**：`pipeline.py` 中 `_match_reverse_with_trace()` 新增 per-HLR fallback 逻辑——预筛选匹配结果为"无匹配"的 HLR 自动回退到全量 EoICD 匹配，防止因追溯表数据覆盖不全或 label 不匹配导致的漏判。
- **共识报告不一致属性栏输出**：`consensus_word_generator.py` 明细表新增"不一致属性"列（位于 ICD Block 和分析摘要之间），Review Agent 识别出的不一致属性（总线类型、信号方向等）以 " | " 分隔显式列出，并按判定状态（已覆盖/不一致/待确认/无匹配）分组展示。
- **前端 V4 专用组件**：新增 `V4FileUpload.tsx`（HLR Word + Pub/Sub Excel + 追溯表上传）、`V4ResultView.tsx`（状态分布卡片 + 星级柱状图 + 预览 + 下载），替换 V3 旧组件。
- **前端依赖**：新增 `lucide-react`（图标库）。
- **环境变量模板**：`.env.example` 新增 Qwen (DashScope) 配置段（`QWEN_API_KEY` 等 11 项）。

### Changed

- **管线步骤编号统一**：`pipeline.py` 中所有 Step 编号从 1/3、1.5/3、2/3、3/5、4/5、5/5 统一为 1/6、2/6、3/6、4/6、5/6、6/6。stage 映射同步调整：1→parse, 2→label, 3→match, 4→multi_judge, 5→review, 6→report。`runner.py` 中 `_parse_progress()` 同步更新。
- **V4 result.summary 字段调整**：`status_distribution` 增加"无匹配"键（值来自 `match_stats["unmatched_count"]`），使前端可直接展示四种状态分布。
- **前端轮询间隔**：V4 任务轮询从 2 秒/600 次调整为 10 秒/120 次（总超时约 20 分钟不变）。
- **前端 `App.tsx`**：完全切换为 V4 管线（`V4FileUpload` / `V4ResultView` / V4 API client），增加 V4 独立健康检查，保留 V3 旧组件文件不动。
- **HLR Labeler prompt 修正**：bus_types 标准名称明确化（CAN→A825、AFDX→A664、ARINC429→A429）。
- **DeepSeekClient**：默认 `max_tokens` 从 1024 调整为 4096，新增 `finish_reason=length` 截断告警。

### Fixed

- **共识报告"判定分布"表无匹配缺失**：`consensus_word_generator.py` 中"判定分布"表新增"无匹配"行（紫色标注，来自 `match_stats["hlr_无匹配"]`），合计 = 裁判数 + 无匹配数。
- **V4ResultView STATUS_META 键名不匹配**：前端 `STATUS_META` 键名从英文（`covered`/`inconsistent`/`needs_review`）改为中文（`已覆盖`/`不一致`/`待确认`），与后端 `status_distribution` 实际键名对齐。

## [Unreleased] - 2026-08-11：V4 Multi-Agent 降级处理机制

### Added

- **降级模块**：新增 `backend/app/v4/degradation/` 独立包（`config.py` / `context.py` / `fallback.py`），对 Step 4 Multi-Judge 和 Step 5 Review Agent 提供系统性异常兜底。
- **Case 级超时控制**：3 个 provider 并行裁判时，前 2 个完成后第三个给予固定额外等待时间（默认 120s），超时后生成 error judgment 而不中断 case。不足 2 个完成时使用兜底上限（默认 300s）。
- **Provider 熔断器**：连续失败达阈值（默认 3 次）后自动跳过该 provider，TTL 到期自动恢复。401/403 认证错误立即熔断。
- **Review 评审降级**：1 个 provider 存活 → 星 ≤ 1★，agreement = "single_source"；2 个存活 → 星 ≤ 2★。对 review_judgments() 输出做后处理。
- **降级可观测性**：`consensus_results.json` 和 API response 新增 `degradation` 字段，包含 provider 健康状态、超时次数、星级截断次数。
- **HTTP 超时提升**：DeepSeek / MiniMax / Qwen 三个 client 的 HTTP 请求超时从 60s → 120s，与 case 级超时配合。
- **新增 4 个环境变量**：`DEGRADATION_CASE_TIMEOUT`（300） / `DEGRADATION_EXTRA_WAIT`（120） / `DEGRADATION_CONSECUTIVE_FAILURES`（3） / `DEGRADATION_UNHEALTHY_TTL`（300），全部通过 `.env.example` 暴露，不配时用默认值。

### Changed

- **Step 4 调用切换**：pipeline 中 Step 4 从 `judge_with_panel()` 切换为 `_judge_with_degradation()`。
- **Step 5 增加后处理**：Review Agent 执行后增加 `_apply_degradation_review()` 对星级和 agreement 做硬上限约束。
- **LLM Client 默认参数**：`review_agent.py` 和 `semantic_judge.py` 的 `max_tokens` 从 8192 → 4096。

## [Unreleased] - 2026-08-11：V4 一星复查机制（Issue #53）

### Added

- **Step 5.5 一星复查（peer-aware 反思）**：新增 `comparison/re_review.py`，`re_review_judgments()` 对 `star_rating == 1` 的 case 由三个 provider 以 peer-aware 方式各自重新评判。每个 provider 看到自己之前的判断（Judgment A）和 peer 的判断（Judgment B/C），携带完整 analysis 文本触发反思纠正。返回类型 `tuple[MultiJudgeOutput, set[str]]`（更新后的 multi_out + 被复查 case_id 集合）。
- **Step 5.6 部分共识重跑**：仅对 `re_reviewed_ids` 中的 case 重跑 `review_judgments()`，其余 case 保持 Step 5 原结果不变。
- **新增 `prompts/re_review.md`**：一星复查 LLM prompt，包含 peer-aware 复查规则、反思引导和证据驱动逐项核对模板。
- **`hlr_labeler.py` max_tokens 调整**：DeepSeek HLR 标注 `max_tokens` 从 1024 调整为 2048，避免频繁截断告警。
- **workflow.md Step 5.5/5.6 更新**：V4 总体流程图、单步输入输出表、异常处理表均已同步新增两个步骤。

### Fixed

- **pipeline.py `review_judgments` 局部引用错误**：原 `re_review_judgments()` 内部存在局部 `from review_agent import review_judgments` 导入，导致 Step 5.6 的外层调用因变量遮蔽产生 `UnboundLocalError`。修复：移除内部局部 import，统一从模块级导入。
- **`re_review_judgments` 的 `multi_out=None` 崩溃**：集成测试中发现当 `multi_out` 传入 `None` 时，函数访问 `.results` 报 `AttributeError`。修复：从 `output_dir / "multi_judge_results.json"` 加载 MultiJudgeOutput 后再继续处理。
- **error provider 被重新查询的问题**：re-review 对所有一星 case 的所有 provider 都重新调用 LLM，即使该 provider 在原始 judgment 中已经是 `coverage_status="error"`。error judgment 被覆盖丢失，导致 degradation 统计错误。修复：跳过 `coverage_status == "error"` 的 provider，不重新查询。
- **`build_cases` case_id 格式错误**：测试脚本生成的 case_id 格式为 `REV-0199`（来自 HLR 编号），与 pipeline 实际生成的 `REV-0001`（顺序编号）不一致，导致 `case_map` 和 `mjr_map` 的 key 无法匹配，所有一星 case 被静默跳过。修复：`build_cases` 改用顺序编号。

### Notes

- 一星复查的测试方式为手动注入"错误但看似合理"的 analysis 文本，而非仅修改 coverage_status 标签。peer-aware 机制要求 provider 看到自己之前的错误分析才能触发真正反思和判断纠正。
- `re_review_results.json` 写入审计记录，`multi_judge_results.json` 更新供 Step 5.6 继续使用，两者落盘时机由 `re_review_judgments()` 内部管理。
- 集成测试三场景验证通过：3 providers 存活 → 3★；2 providers 存活 → cap 2★；1 provider 存活 → cap 1★。

## [Unreleased] - 2026-08-14：V4 降级机制修复与共识报告星级表调整（Issue #59）

### Fixed

- **裁判失败状态归一为 error**：`semantic_judge.py` 三种失败路径（JSON 解析失败 / API 错误 / 重试耗尽）的 `coverage_status` 从 `needs_review` / `unmatched` 统一改为 `error`，避免失败被误判为业务结论，保证 degradation 的 surviving provider 统计正确。
- **0 存活降级分支缺失**：`_apply_degradation_review()` 新增 0 个 provider 存活场景——强制星级 ≤ 1★、`agreement = "no_consensus"`、`final_coverage_status = "待确认"`，防止共识 LLM 在纯 error 输入上幻觉出高星级。
- **复查后降级封顶失效**：Step 5.6 部分共识重跑后重新应用 `_apply_degradation_review()` 并重建 summary，避免复查升星绕过降级封顶。

### Changed

- **共识报告星级分布表**：删除 1★ 主行（需人工复核），仅保留 3 个子类型行（分歧/仅单一来源/无有效裁判）；★☆☆ 显示在首个子行星列并纵向合并 3 行；子类型标签加粗、与主行格式统一。
- **共识明细表共识列标签映射**：新增 `no_consensus → 无有效裁判`、`single_source → 仅单一来源`。
- **降级配置**：`DegradationConfig` 新增 `zero_provider_star_cap=1`、`zero_provider_agreement="no_consensus"` 默认值。

## [Unreleased] - 2026-08-19：V4 反向管线多控制器 Profile 化（Issue #63）

### Added

- **Controller Profile 子包**：新增 `backend/app/v4/profiles/`，`base.py` 定义 `ControllerProfile` + 4 个 Config dataclass（`HLRParserConfig` / `TraceabilityConfig` / `ClassifierKeywords` / `AILabelingConfig`），`__init__.py` 提供 `ProfileRegistry` 单例。profile 配置以 `profiles/{id}/config.yaml` 声明，新控制器可通过新增目录接入，无需改动业务代码。
- **AMS profile（默认）**：从现状代码 1:1 抽取，行为与 Issue A 完全一致，向后兼容。
- **FGMC profile（燃油测量管理计算机）**：术语表位于 `tables[1]`、需求表 ≥12 行、支持"是否为需求"= "否" 行过滤、追溯表用 glob 模式（`*追溯*.xlsx` / `*矩阵分析*.xlsx`）、燃油域分类关键词与 AI 标注示例。
- **API 新增 `controller_profile` 字段**：`POST /api/v4/coverage-analysis` 新增 form 字段，默认 `ams`，白名单 `{ams, fgmc}`，非法值在创建任务前返回 422。
- **CLI 新增 `--controller-profile`**：`label` / `reverse` / `reverse-analyze` 三个子命令支持，`choices=["ams","fgmc"]`，默认 `ams`。
- **Profile 单元测试**：新增 `backend/app/v4/tests/profiles/`，覆盖 registry / models / HLR parser / classifier / labeler / 追溯表 / pipeline 共 24 个用例。

### Changed

- `HLRWordParser` / `trace_parser` / `hlr_classifier` / `hlr_labeler` 改为 profile-driven，profile 由 pipeline 统一注入，不再依赖模块级硬编码常量；未传 profile 时退化为 AMS 默认行为。
- `HLRRequirement` 模型扩展 6 个 optional 字段（`code` / `source` / `covered_ids` / `notes` / `input_data` / `output_data`），供 FGMC 需求表使用；AMS 侧保持为空不影响既有输出。

### Fixed

- V4 不再硬编码 AMS 专属的追溯表中文文件名、sheet 名、HLR 表行数阈值和字段名；接入新控制器不再需要修改 parser / matcher 源码。

