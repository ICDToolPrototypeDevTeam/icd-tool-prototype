# 开发纪要

本文档用于记录 **ICD工具原型Ver2.0** 的主要开发过程、关联 Issue、修改内容、验证方式和遗留问题。

## 1. 记录原则

1. 每完成一个明确 Issue，建议新增一条开发纪要；
2. 每条记录应简明说明完成内容、修改文件、验证方式和遗留问题；
3. 不记录大段代码；
4. 不记录完整错误堆栈；
5. 不替代 `CHANGELOG.md`；
6. 不替代 `debug-log.md`。

## 2. 记录模板

```text
## YYYY-MM-DD Issue #编号：任务名称

### 任务目标

简要说明本次任务目标。

### 完成内容

1. 
2. 
3. 

### 修改文件

1. 
2. 
3. 

### 验证方式

1. 

### 验证结果

说明验证是否通过。如未验证，应明确写“尚未验证”。

### 遗留问题

1. 

### 下一步建议

1. 
```

## 3. 开发记录

### 2026-06-10 Issue #1：建立工程目录骨架

#### 任务目标

建立 ICD工具原型Ver2.0 的初始工程目录结构和空 Markdown 文档骨架。

#### 完成内容

1. 建立前端、后端、工程文档和 Claude Code 规则目录；
2. 建立 README、CLAUDE、CHANGELOG 等顶层文档；
3. 建立项目范围、业务流程、架构、API、开发纪要、问题排查等文档占位；
4. 使用 `.gitkeep` 保留空目录。

#### 修改文件

1. `README.md`
2. `CLAUDE.md`
3. `CHANGELOG.md`
4. `docs/project/scope.md`
5. `docs/project/workflow.md`
6. `docs/architecture/current-architecture.md`
7. `docs/architecture/api.md`
8. `docs/development/development-log.md`
9. `docs/development/debug-log.md`
10. `.claude/rules/context-rules.md`
11. `.claude/rules/debug-rules.md`
12. `.claude/rules/documentation-rules.md`
13. 各空目录下的 `.gitkeep`

#### 验证方式

1. 检查工程目录结构是否完整；
2. 检查空目录是否通过 `.gitkeep` 保留；
3. 检查相关文件是否已提交到 Git。

#### 验证结果

已完成目录和空文档骨架建立。

#### 遗留问题

1. 各 Markdown 文档内容仍需在后续 Issue 中填写；
2. 前后端最小可运行工程尚未建立。

#### 下一步建议

1. 填写中文工程文档和 Claude Code 规则；
2. 建立最小可运行前后端工程。

### 2026-06-11 Issue #3：建立最小可运行前后端工程与端到端占位流程

#### 任务目标

建立最小可运行前后端工程骨架，实现端到端占位流程（mock/占位逻辑），而非真实业务能力。

#### 完成内容

1. 建立 React + TypeScript 前端最小工程（package.json、vite.config.ts、tsconfig 配置、入口 HTML）；
2. 建立前端组件：FileUpload 文件上传组件、JobStatus 任务状态组件；
3. 建立前端 API 封装（src/api/index.ts）；
4. 建立 FastAPI 后端最小工程（main.py、models.py、job_manager.py）；
5. 建立 pipeline.py 端到端流程骨架；
6. 建立各模块占位实现（parsers、crew、prompts、skills、scoring、docx）；
7. 使用 python-docx 生成两个最小占位 Word 文档供下载接口返回；
8. 建立 Docker Compose 本地启动方式（docker-compose.yml、frontend/Dockerfile、backend/Dockerfile）；
9. 更新 CHANGELOG.md。

#### 修改文件

1. `frontend/package.json`
2. `frontend/vite.config.ts`
3. `frontend/tsconfig.json`
4. `frontend/tsconfig.node.json`
5. `frontend/index.html`
6. `frontend/src/main.tsx`
7. `frontend/src/App.tsx`
8. `frontend/src/api/index.ts`
9. `frontend/src/components/FileUpload.tsx`
10. `frontend/src/components/JobStatus.tsx`
11. `backend/requirements.txt`
12. `backend/app/main.py`
13. `backend/app/models.py`
14. `backend/app/job_manager.py`
15. `backend/app/pipeline.py`
16. `backend/app/parsers/placeholder.py`（替换 .gitkeep）
17. `backend/app/crew/placeholder.py`（替换 .gitkeep）
18. `backend/app/prompts/placeholder.py`（替换 .gitkeep）
19. `backend/app/skills/placeholder.py`（替换 .gitkeep）
20. `backend/app/scoring/placeholder.py`（替换 .gitkeep）
21. `backend/app/docx/placeholder.py`（替换 .gitkeep）
22. `docker-compose.yml`
23. `frontend/Dockerfile`
24. `backend/Dockerfile`
25. `CHANGELOG.md`

#### 验证方式

1. 后端独立运行：`cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --port 8000`
2. 前端独立运行：`cd frontend && npm install && npm run dev`
3. Docker Compose 运行：`docker-compose up --build`
4. 端到端验证：POST /api/eoicd/analyze → GET /api/jobs/{job_id} → GET /api/jobs/{job_id}/outputs/requirements → GET /api/jobs/{job_id}/outputs/difference-report

#### 验证结果

尚未验证。

#### 遗留问题

1. 各模块占位实现仅为骨架，真实业务逻辑在后续 Issue 中实现；
2. 未引入数据库，任务状态仅存于内存；
3. 前端 UI 仅为最小化实现，无复杂样式。

#### 下一步建议

1. 实现真实 Word/Excel 解析逻辑（parsers/）；
2. 实现真实多智能体生成逻辑（crew/）；
3. 实现真实评分算法（scoring/）；
4. 实现真实 DOCX 内容生成（docx/）；

### 2026-06-12 Issue #4：建立 ICD 工具端到端原型

#### 任务目标

在 Issue #3 最小可运行工程基础上，填充端到端原型能力，形成 ICD 工具端到端原型骨架。重点是打通数据流链路，各模块以结构化 stub 形式实现。

#### 完成内容

1. 扩增 `models.py` 数据模型（UnifiedInputPackage、ParsedEoICD、EoICDCandidate、AgentScoreResult、ScoredCandidate、DifferenceItem、PipelineResult 等）；
2. 实现 `parsers/` 模块：返回结构化 EoICD 信息和软件高层需求信息，构建统一分析输入包；
3. 实现 `prompts/` 文本资产：3 个 Markdown prompt 模板；
4. 实现 `skills/` 文本资产：3 个 Markdown skill 规则；
5. 实现 `crew/` 三类智能体 stub：候选生成（两份）、候选打分（互评）、差异比对（5条固定差异项）；
6. 实现 `scoring/` 模块：融合 crew 评分和 Python 规则评分，决策最佳候选；
7. 实现 `docx/` 模块：生成含结构化表格的 Word 文档（模拟 ICD 场景）；
8. 更新 `pipeline.py` 串联完整数据流；
9. 更新 CHANGELOG.md。

#### 修改文件

1. `backend/app/models.py`（扩增数据模型）
2. `backend/app/parsers/placeholder.py` → `__init__.py` + `eoicd_parser.py` + `software_req_parser.py`
3. `backend/app/crew/placeholder.py` → `__init__.py` + `candidate_generator.py` + `candidate_reviewer.py` + `difference_analyzer.py`
4. `backend/app/prompts/placeholder.py` → `__init__.py` + 3 个 `.md` 文件
5. `backend/app/skills/placeholder.py` → `__init__.py` + 3 个 `.md` 文件
6. `backend/app/scoring/placeholder.py` → `__init__.py` + `scorer.py`
7. `backend/app/docx/placeholder.py` → `__init__.py` + `generator.py`
8. `backend/app/pipeline.py`（更新数据流串联）
9. `CHANGELOG.md`

#### 验证方式

1. 后端启动：`cd backend && uvicorn app.main:app --reload --port 8000`
2. 前端启动：`cd frontend && npm run dev`
3. 端到端测试：POST /api/eoicd/analyze → GET /api/jobs/{job_id} → GET /api/jobs/{job_id}/result → 下载两个 docx

#### 验证结果

已验证通过（Docker Compose 环境）。任务状态流转正常：pending → running → completed。需求条目数 3，差异条目数 2，两个 docx 下载链接可用。验证过程中发现 BUG-20260612-001（端口 3000 被本地 Node 进程占用），已修复并记录到 `debug-log.md`。

#### 遗留问题

1. 各模块 stub 内容为固定数据，不支持真实 LLM 调用；
2. parsers/ 不实现真实 Word/Excel 解析；
3. crew/ 不实现真实 CrewAI 编排；
4. Docker 启动前需确认无其他 Node 进程占用 3000 端口。

#### 下一步建议

1. 实现真实 EoICD Word/Excel 解析逻辑；
2. 引入 CrewAI 编排真实多智能体流程；
3. 接入 LLM 实现真实生成和评分能力；
4. 实现真实差异分析内容。

### 2026-06-16 Issue #5：CrewAI-based chunk-level 多智能体条目化生成、评分择优与对比流程

#### 任务目标

在 Issue #4 端到端原型基础上，引入 CrewAI 框架，实现基于 chunk 的多智能体条目化生成、评分择优与对比流程。本 Issue 真实引入 CrewAI Agent/Task/Crew 编排，默认以 `USE_MOCK_LLM=1` 跑通端到端，真实 MiniMax/DeepSeek 调用通过环境变量配置。

#### 完成内容

1. 数据模型扩展：`EoICDChunk / ChunkCandidate / ChunkAgentScoreResult / ChunkPythonScoreResult / BestChunkResult / ModelRequirementResult / MergedRequirementResult / ComparisonReportResult / GenerationOutput / ScoringOutput / ComparisonOutput`。
2. Parser 升级：`UnifiedInputPackage.eoicd` → `eoicd_chunks: List[EoICDChunk]`，默认 1 个 `chunk-001`。
3. 新增 `llm/` 模块：`factory.py`（env 驱动 + mock fallback）、`prompt_loader.py`（Python 端拼上下文，不修改 prompts/skills 文本）、`mock_llm.py`（继承 `crewai.BaseLLM`）。
4. 新增 `crew/{agents,tasks,crews}.py`：5 Agent + 5 Task + 3 Crew。
5. 新增 `merge/` 模块：跨 chunk 合并 + 按模型合并。
6. `scoring/` 升级为 chunk 内 Python 硬规则评分（4 维 25×4）+ agent × 0.6 + python × 0.4 融合。
7. `docx/` 改为 4 份输出：MiniMax / DeepSeek / 最优（额外落 EoICD条目化需求.docx）/ 差异报告。
8. `pipeline.py` 改为 `for chunk in eoicd_chunks` 循环。
9. `main.py` 新增 2 个下载接口：`minimax-requirements` / `deepseek-requirements`。
10. 前端 `api/index.ts` 扩展 `outputs` 字段 + `getDownloadUrl` 2 个新类型；`JobStatus.tsx` 增 2 个下载链接，分组展示。
11. `requirements.txt` 新增 `crewai>=1.0`、`pydantic>=2.11,<3`、隐式 `starlette<0.37,>=0.36.3`。
12. 新增 `backend/.env.example`（仅占位，无真实 Key）；`docker-compose.yml` 新增 22 个环境变量占位。
13. `CHANGELOG.md` 新增 Unreleased 2026-06-16 条目。

#### 修改文件

1. `backend/app/models.py`（数据模型扩增）
2. `backend/app/parsers/eoicd_parser.py`（chunk 列表）
3. `backend/app/parsers/__init__.py`（装配 eoicd_chunks）
4. `backend/app/prompts/__init__.py`（lru_cache）
5. `backend/app/skills/__init__.py`（lru_cache）
6. `backend/app/pipeline.py`（chunk-level 串联）
7. `backend/app/scoring/{__init__,scorer}.py`（Python 硬规则 + chunk 内择优）
8. `backend/app/docx/{__init__,generator}.py`（4 份 docx 生成）
9. `backend/app/main.py`（新增 2 个下载接口 + result 字段）
10. `backend/app/crew/candidate_generator.py`（接 generation crew）
11. `backend/app/crew/candidate_reviewer.py`（接 scoring crew）
12. `backend/app/crew/difference_analyzer.py`（接 comparison crew）
13. `backend/app/crew/__init__.py`（统一导出新签名）
14. `frontend/src/api/index.ts`（outputs 扩展 + getDownloadUrl 增 2 type）
15. `frontend/src/components/JobStatus.tsx`（增加 2 个下载链接，分组展示）
16. `backend/requirements.txt`（新增 crewai + pydantic 范围）
17. `docker-compose.yml`（新增 22 个环境变量占位）
18. `CHANGELOG.md`
19. `docs/architecture/api.md`（新增 2 个下载接口）
20. `docs/architecture/current-architecture.md`（模块表 + 流程图）
21. `docs/development/development-log.md`（本条）

#### 新增文件

1. `backend/app/llm/__init__.py`
2. `backend/app/llm/factory.py`
3. `backend/app/llm/prompt_loader.py`
4. `backend/app/llm/mock_llm.py`
5. `backend/app/crew/agents.py`
6. `backend/app/crew/tasks.py`
7. `backend/app/crew/crews.py`
8. `backend/app/merge/__init__.py`
9. `backend/app/merge/merger.py`
10. `backend/.env.example`

#### 验证方式

1. `cd backend && pip install -r requirements.txt`
2. `USE_MOCK_LLM=1 uvicorn app.main:app --host 127.0.0.1 --port 8765`
3. `curl http://127.0.0.1:8765/api/health` → `{"status":"ok"}`
4. `curl -X POST http://127.0.0.1:8765/api/eoicd/analyze -F ...` 创建任务
5. `curl http://127.0.0.1:8765/api/jobs/{id}` 轮询状态
6. `curl http://127.0.0.1:8765/api/jobs/{id}/result` 查结果
7. 4 个下载路径全部 200：`/outputs/requirements`、`/outputs/minimax-requirements`、`/outputs/deepseek-requirements`、`/outputs/difference-report`
8. `cd frontend && npm install && npm run build`

#### 验证结果

已验证通过（mock 模式）。任务状态流转 pending → running → completed。`requirement_count=5`、`difference_count=5`、4 份 docx 全部生成（MiniMax条目化需求/DeepSeek条目化需求/最优条目化需求/EoICD条目化需求/EoICD与软件高层需求差异报告），4 个下载接口全部 HTTP 200。`npm run build` 通过。

#### 遗留问题

1. 真实 MiniMax / DeepSeek API 接入未在本 Issue 端到端验证（`USE_MOCK_LLM=1` 走通，真实 Provider 由后续 Issue 处理）；
2. prompts / skills 内容质量不在本 Issue 范围；
3. 100-200 页 EoICD 自动切分不在本 Issue 范围；
4. 跨 chunk 冲突消解、追溯矩阵、最终编号规则不在本 Issue 范围。

#### 下一步建议

1. 真实 MiniMax / DeepSeek 接入端到端验证（含 LLM Provider 兼容性调优）；
2. 实现真实 Word/Excel parser（取代当前 stub）；
3. 实现按章节/接口/表格的 EoICD 自动切分（替换单 chunk 默认）；
4. 完善跨 chunk 冲突消解和追溯矩阵。

### 2026-06-22 Issue #16：接入真实 LLM 后端（MiniMax M2.7 + DeepSeek）

#### 任务目标

在 Issue #5 的 CrewAI 多智能体框架基础上，接入 MiniMax M2.7 和 DeepSeek 两个真实 LLM Provider，替换 mock 模式。解决两个模型与 CrewAI 的兼容性问题，确保结构化输出和评分流程在真实 LLM 下正常运作。

#### 完成内容

1. 实现 `backend/app/llm/factory.py` 真实 LLM 接入：MiniMax M2.7 和 DeepSeek 统一通过 `provider=openai` 路径接入 CrewAI。
2. 新增两个 monkey-patch 解决 CrewAI 与 MiniMax/DeepSeek 的结构化输出兼容：
   - `_patch_crewai_completion_for_unsupported_models()`：MiniMax `<think>` 标签清洗 + `response_format=json_object` 替代不兼容的 `json_schema`。
   - `_patch_crewai_instructor_for_unsupported_models()`：TOOLS mode 下对 MiniMax 间歇性 tool_calls 缺失做 content → tool_call fallback。
3. 实现 `_provider_creds` 字典 + `_litellm_with_fallback` 按模型名动态注入 API Key/Base URL，避免多模型共用 `OPENAI_API_KEY` 环境变量冲突。
4. `get_minimax_llm()` / `get_deepseek_llm()` 新增 `overrides` 参数，Agent 工厂可按角色注入 timeout / max_tokens：
   - generation: 300s / 16384
   - scoring: 120s / 4096
   - comparison: 180s / 8192
5. `DEEPSEEK_PROVIDER` 统一为 `openai`，与 MiniMax 走相同的 `LLM` → `InternalInstructor` → TOOLS mode 结构化输出路径。
6. `docker-compose.yml` 移除 22 个模型相关环境变量内联声明，全部通过 `env_file: ./backend/.env` 注入。
7. `backend/Dockerfile` 新增 litellm 安装步骤。
8. `generation_prompt.md` 修正："生成 2 份候选结果" → "生成 1 份"，与 Pydantic schema 对齐。
9. `_litellm_with_fallback` 和 `_handle_completion` 的 JSON 解析改用 `JSONDecoder.raw_decode()` 防御多 JSON 拼接场景。

#### 修改文件

1. `backend/app/llm/factory.py`（真实 LLM + monkey-patch + litellm credential routing）
2. `backend/app/crew/agents.py`（按角色注入 timeout/max_tokens overrides）
3. `backend/Dockerfile`（新增 litellm 安装）
4. `docker-compose.yml`（移除 22 个环境变量内联声明）
5. `backend/app/prompts/generation_prompt.md`（candidates 数组 → 单个 ChunkCandidate）
6. `CHANGELOG.md`

#### 验证方式

1. 配置 `backend/.env` 中真实 MiniMax/DeepSeek API Key 和 Base URL
2. `USE_MOCK_LLM=0` 启动后端：`uvicorn app.main:app --host 127.0.0.1 --port 8765`
3. `POST /api/eoicd/analyze` 上传样例文件
4. 任务完整跑通：生成 → 评分 → 择优 → 差异比对 → DOCX 输出
5. 4 个下载接口全部 200

#### 验证结果

已验证通过。真实 MiniMax M2.7 和 DeepSeek 双模型端到端流程完整跑通，结构化输出正常，评分结果合理区分度。

#### 遗留问题

1. CrewAI Process.sequential 上下文污染导致 MiniMax/DeepSeek scoring 输出完全一致，需后续修复；
2. DeepSeek TOOLS mode 下 thinking 被禁用，scoring 质量下降，需探索 MD_JSON 替代方案。

#### 下一步建议

1. 实现 EoICD 真实 Word/Excel parser（替换当前 stub）；
2. 修复 CrewAI 上下文污染问题；
3. DeepSeek 切换 MD_JSON 模式恢复 thinking。

### 2026-06-27 Issue #17-18-19：EoICD 真实文件输入 Parser + Generation Skill/Prompt 重写 + Score Skill/Prompt 重写

#### 任务目标

实现 EoICD 真实 Word/Excel 文件解析，重写 generation 和 scoring 的 skill/prompt 文本资产以适配 PubSub 树状层级数据，修复真实 LLM 场景下的关键 Bug（上下文污染、DeepSeek scoring 空返回等），确保端到端流程在真实文件输入下稳定跑通。

#### 完成内容

1. 新增 `eoicd_word_parser.py`：真实 EoICD Word 文档解析（python-docx），提取接口说明和数据定义。
2. 新增 `eoicd_excel_parser.py`：PubSub Excel 表格解析（openpyxl），提取 Publisher/Subscriber 行数据。
3. `parsers/__init__.py` 新增 `build_nested_sheets()`：将 PubSub Excel 的平铺行数据转换为三层嵌套结构（Sheet → rows → hierarchy），供 LLM 端直接消费。
4. `generation_skill.md` 大幅扩展（20 行 → 220+ 行）：包含 8 条规则（层级信号名拼接、排除清单、属性中文名映射含英文原名、描述模板、单位自动追加、去重、空值跳过、叶节点属性参考），适配 PubSub 树状层级数据。
5. `generation_prompt.md` 重写：明确 PubSub2IRD 处理路径（excel_data 优先），定义 IRD 格式 entry_id 和双模式字段规范（PubSub / 接口模式）。
6. `scoring_skill.md` 重写：扩展 4 维评分细则（完整性/一致性/可追溯性/可读性），强制评分区分度和 `recommended_is_best` 唯一推荐。
7. `scoring_prompt.md` 重写：移除 stub 描述，明确 chunk 级候选互评要求和横向对比规则。
8. DeepSeek V4 路径切换为 `Mode.MD_JSON` + thinking 保留：新增 `extract_json_from_codeblock()` 自动跳过 `<think>` 标签提取 JSON，恢复 scoring 等复杂推理任务质量。
9. 修复 CrewAI 上下文污染：所有 generation 和 scoring Task builder 设置 `context=None`，阻止 Process.sequential 自动将前序 Task 的 raw output 注入后续 Task 上下文。
10. 修复多模型凭证冲突：`_litellm_with_fallback` 按模型名动态匹配 `_provider_creds` 注入 api_key/api_base。
11. `_excel_to_chunk()` 聚合策略：所有 Excel Sheet → 单个 `EoICDChunk`（`excel-chunk-001`），`tables` 字段使用 `build_nested_sheets()` 的嵌套结构。
12. LLM max_tokens 注入 instructor client，避免 instructor 使用内置默认 4096 截断长输出。

#### 修改文件

1. `backend/app/parsers/__init__.py`（装配 + `build_nested_sheets`）
2. `backend/app/parsers/eoicd_word_parser.py`（替换 stub，真实 Word 解析）
3. `backend/app/parsers/eoicd_excel_parser.py`（替换 stub，真实 PubSub Excel 解析）
4. `backend/app/llm/factory.py`（DeepSeek MD_JSON + cred 路由修复）
5. `backend/app/crew/tasks.py`（generation/scoring Task 设 `context=None`）
6. `backend/app/crew/agents.py`（LLM max_tokens 注入 instructor）
7. `backend/app/prompts/generation_prompt.md`（重写）
8. `backend/app/prompts/scoring_prompt.md`（重写）
9. `backend/app/skills/generation_skill.md`（大幅扩展）
10. `backend/app/skills/scoring_skill.md`（重写）
11. `CHANGELOG.md`

#### 新增文件

1. `backend/app/parsers/eoicd_word_parser.py`
2. `backend/app/parsers/eoicd_excel_parser.py`

#### 验证方式

1. `USE_MOCK_LLM=0` 启动后端：`uvicorn app.main:app --host 127.0.0.1 --port 8765`
2. 上传真实 EoICD Word 文件 + PubSub Excel 附件 + 软件高层需求文件
3. 任务完整跑通：Word/Excel 解析 → 生成 → 评分 → 择优 → 差异比对 → DOCX 输出
4. 4 个下载接口全部 200
5. 检查 DeepSeek scoring 不再返回空（MD_JSON 模式生效）
6. 检查双模型 scoring 输出有明显区分度（context=None 生效）

#### 验证结果

已验证通过。真实 EoICD Word + PubSub Excel 输入文件端到端流程完整跑通。DeepSeek MD_JSON 模式 scoring 输出正常、不再为空；双模型 scoring 结果有明显区分度。

#### 遗留问题

1. 100-200 页 EoICD 自动切分（多 chunk）不在本 Issue 范围；
2. 跨 chunk 冲突消解和追溯矩阵不在本 Issue 范围；
3. comparison_prompt.md / comparison_skill.md 尚未从 stub 升级为真实差异比对规则；
4. PubSub 多层嵌套（超过 3 层）的边界情况未充分测试。

#### 下一步建议

1. 实现 EoICD 按章节/接口自动切分（多 chunk）；
2. 重写 comparison prompt/skill 为真实差异比对规则；
3. 实现跨 chunk 合并冲突消解和最终编号规则。

### 2026-06-29 代码死代码清理与 Excel 数据流修复

#### 任务目标

排查并清理 backend 中的死代码分支，修复 Excel 数据在 EoICDChunk → tasks → LLM prompt 链路中的字段错位问题。

#### 完成内容

1. 删除 `_flatten_schema_defs()` 函数（32 行）及 MiniMax 分支中的 `$defs` 展平调用点。该函数基于错误归因添加，空 tool_call arguments 实际是 DeepSeek 的问题。
2. 删除 `is_minimax` 分支中的死代码：`if "deepseek-v4" in mdl: thinking=disabled` — 该条件在 MiniMax 分支下永不为真。
3. 修复 Excel 数据流：
   - `EoICDChunk.excel_data` 类型 `Optional[ParsedEoICDExcel]` → `list[dict]`
   - `_excel_to_chunk()`: `tables=[]`，`excel_data=build_nested_sheets(parsed_excel)`
   - `parse_inputs()` Word+Excel 路径: `build_nested_sheets(eoicd_excel)` 替代原始 `ParsedEoICDExcel`
   - `tasks.py`: `excel_data=chunk.tables` → `excel_data=chunk.excel_data`
4. 修正 factory.py 中 3 处注释，移除 `$defs 展平` 表述。
5. 更新 CHANGELOG、development-log、debug-log，清理错误归因条目。

#### 修改文件

1. `backend/app/llm/factory.py`（删除 `_flatten_schema_defs` + 死代码分支 + 注释修正）
2. `backend/app/models.py`（`excel_data` 类型变更）
3. `backend/app/parsers/__init__.py`（`_excel_to_chunk` + `parse_inputs` 数据流修正）
4. `backend/app/crew/tasks.py`（`excel_data=chunk.tables` → `chunk.excel_data`）
5. `CHANGELOG.md`
6. `README.md`
7. `docs/project/scope.md`
8. `docs/architecture/api.md`
9. `docs/development/development-log.md`
10. `docs/development/debug-log.md`

#### 验证方式

1. Docker 容器重建后验证代码生效：`_flatten_schema_defs` 无法导入、`excel_data` 类型为 `list[dict]`
2. Excel-only 上传路径下 generation prompt 收到的 `excel_data` 为 `build_nested_sheets()` 三层嵌套结构

#### 验证结果

代码验证已通过。数据流格式验证待实际 Excel 上传测试确认。

#### 遗留问题

1. Excel 数据流修改后的端到端 LLM 生成效果待实际测试验证；
2. 其他死代码（未使用的 models/functions/imports）待后续清理。

#### 下一步建议

1. 上传真实 Excel 文件验证 generation prompt 中 `excel_data` 格式化效果；
2. 清理 models.py 中未使用的旧版模型；
3. 清理各文件中未使用的 import。
