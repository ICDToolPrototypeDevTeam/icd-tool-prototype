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

### 2026-06-29 Issue #30：前端 UI 重设计

#### 任务目标

参照 icd_demo v1.0 的视觉风格，完整重设计 ICD 工具原型 Ver2.0 前端，保持后端 API 不变。

#### 完成内容

1. 建立蓝色主题设计令牌系统（--primary: #0066cc），统一 CSS Variables
2. 实现 3 步工作流步骤条（上传文件 → 智能处理 → 查看结果），含 active/completed 状态动画
3. 重写 FileUpload 组件：三区文件上传（EoICD Word / Excel 附件 / 软件高层需求），文件列表管理
4. 新增 FilePreview 组件：Word（mammoth）和 Excel（xlsx）客户端实时预览
5. 新增 ProcessingView 组件：旋转动画 + 后端 pipeline 进度消息实时轮询显示
6. 新增 ResultView 组件：最优条目化需求和差异分析报告双卡预览 + 4 个输出文档下载
7. 完整 Header（logo + 在线状态）和 Footer（公司信息）布局
8. 响应式适配（900px 断点）
9. 后端 pipeline.py 增加 3 阶段进度更新：解析输入 → 生成评分择优 → 检查需求一致性
10. 前端通过 `getPreviewHtml()` 获取 DOCX 并用 mammoth 转 HTML 实现结果预览

#### 修改文件

1. `frontend/index.html`（更新标题）
2. `frontend/src/main.tsx`（添加 CSS import）
3. `frontend/src/App.tsx`（完整重写，状态机 + 工作流 + Header/Footer）
4. `frontend/src/api/index.ts`（增强类型，新增 getPreviewHtml）
5. `frontend/src/components/FileUpload.tsx`（重写，三区文件上传）
6. `frontend/package.json`（新增 xlsx、mammoth 依赖）
7. `backend/app/pipeline.py`（3 阶段进度更新）
8. `docs/architecture/current-architecture.md`（前端模块划分更新）
9. `docs/development/development-log.md`（本条记录）

#### 新增文件

1. `frontend/src/index.css`（完整样式表）
2. `frontend/src/types.ts`（UI 类型定义）
3. `frontend/src/vite-env.d.ts`（Vite 类型声明）
4. `frontend/src/components/FilePreview.tsx`（Word/Excel 预览）
5. `frontend/src/components/ProcessingView.tsx`（处理状态动画）
6. `frontend/src/components/ResultView.tsx`（结果预览与下载）

#### 验证方式

1. `cd frontend && npm install` 依赖安装
2. `cd frontend && npx tsc --noEmit` TypeScript 类型检查
3. `cd frontend && npm run build` Vite 生产构建
4. `cd frontend && npm run dev` 开发服务启动验证

#### 验证结果

所有验证通过。TypeScript 零错误，Vite build 成功（464 modules），dev server 正常启动。

#### 遗留问题

1. xlsx + mammoth 导致 bundle 体积偏大（~990KB），后续可考虑代码分割
2. 前端需配合启动的后端进行端到端验证

#### 下一步建议

1. 配合真实后端进行端到端上传→处理→结果下载验证
2. 考虑 lazy import 优化 xlsx/mammoth 包体积
3. 替换 logo1.png / logo2.jpg 为实际 logo 文件

### 2026-07-01：真实 SRS Parser + 差异比对输出结构升级

#### 任务目标

实现真实软件高层需求 Word 文档解析（替换硬编码 stub），升级差异比对输出的 schema 与 docx 渲染，使真实 LLM 能产出可追溯到具体 `requirement_id` 和 `entry_id` 的高质量差异报告。

#### 完成内容

1. **真实 SRS 解析器**：`parsers/software_req_parser.py` 全部重写，基于 python-docx 解析 8 行 × 2 列的需求表格。映射规则：`对象类型`（需求 → requirement / 注释 → comment）、`是否衍生`（是 → True / 其他 → False）、`实现方法`（手工编码 → manual_coding / 基于模型 → model_based）；空单元格和 "NA"/"N/A" 视为空字符串；缺失 `requirement_id` / `requirement_text` 时跳过该条 + warn log。Table[0] 缩略语表（3×3）按形状过滤。

2. **数据模型扩展与重构**：
   - `ParsedSoftwareRequirement` 从 3 字段扩展为 8 字段（新增 `object_type`、`is_derived`、`rationale`、`verification_method`、`implementation_method`、`source_file`）。
   - `DifferenceEntry` 和 `DifferenceItem` 拆 `difference_id` 为 `difference_requirement_id`（关联 SRS 端）和 `difference_eoicd_entry_id`（关联 EoICD 端），并把 `requirement_text` 改名为 `eoicd_requirement_text` 以消除两边歧义。

3. **结构化 description 格式**：约定每条 diff 的 `description` 字段为多属性对比结构化文本（每行 `属性 <名>: SWHLR=<值> IRD=<值> <判定> - <分析>` + 末尾 3 行 `整体判定 / 整体分析 / 整体建议`），5 种判定值（一致 / 不一致 / 仅IRD定义 / 仅SWHLR描述 / 待确认）与 `difference_type` 取值映射。同步更新 `prompts/comparison_prompt.md` 和 `skills/comparison_skill.md`。

4. **docx 渲染升级**：
   - 汇总表 4 列 → 5 列（差异编号 / 关联定位 / 差异类型 / 差异描述 / 建议处理方式）。
   - 详情区新增"关联定位" block（分行列出 SRS ID 和 EoICD 条目 ID）。
   - 新增 `_render_description()` 函数，按 `\n` 拆行渲染，并对"属性 XX:" / "整体XX:" 前缀加粗。

5. **`crew/tasks.py` 与 `mock_llm.py` 同步**：
   - `expected_output` 字段名同步新 schema。
   - `_comparison_mock_data()` 5 条 mock diff 全部改写为新 schema 字段 + 结构化 description 文本。

#### 修改文件

1. `backend/app/parsers/software_req_parser.py`（stub → 真实解析，全部重写）
2. `backend/app/models.py`（`ParsedSoftwareRequirement` 扩字段；`DifferenceEntry` + `DifferenceItem` 拆分 ID + 字段改名）
3. `backend/app/crew/difference_analyzer.py`（字段搬运同步新 schema）
4. `backend/app/crew/tasks.py`（`expected_output` 字段名同步）
5. `backend/app/docx/generator.py`（汇总表 5 列 + 关联定位详情 block + `_render_description` 函数）
6. `backend/app/prompts/comparison_prompt.md`（清 stub + description 结构化格式章节）
7. `backend/app/skills/comparison_skill.md`（重写 Step 5 为 JSON 输出 + description 结构化格式）
8. `backend/app/llm/mock_llm.py`（5 条 mock diff 改写）
9. `CHANGELOG.md`（新增 Unreleased 2026-07-01 条目）
10. `docs/development/development-log.md`（本条）

#### 验证方式

1. 静态校验：调用 `parse_software_requirement()` dump 32 条 `ParsedSoftwareRequirement`，校验 8 字段全部存在且映射正确（对象类型全部为 `requirement`、实现方法全部为 `manual_coding`、ID 范围 `FSF21000101_HLR_225` ~ `FSF21000101_HLR_3573`）。
2. docx 渲染静态校验：构造 3 条 `DifferenceItem`（覆盖缺失 / 冗余 / 不一致 3 类场景），调用 `generate_difference_report_docx`，验证汇总表 5 列、详情区"关联定位"block 正确显示、description 按 `\n` 拆行渲染、前缀加粗。
3. 端到端验证：`USE_MOCK_LLM=0` 上传 Test_AMS + 真实 SRS，完整跑通 pipeline。两次成功跑通：job `a2137d03`（47 reqs / 8 diffs / 7min27s）、job `c7e21cca`（37 reqs / 9 diffs / ~4min）。

#### 验证结果

已验证通过。真实 LLM 严格按 description 结构化格式输出，每条 diff 平均 5-10 个属性判定（如 `Label / Direction / DataFormatType / BitRange / Units / Period` 等），关联 ID 精确（`FSF21000101_HLR_378 ↔ REQ-041` 等真实案例）。docx 详情区每行属性单独成段且前缀加粗，可读性显著提升。

#### 遗留问题

1. **`deepseek_comparison` agent 偶发 max_tokens 截断**：description 结构化后 LLM 输出变长，存在触发 `max_tokens=8192` 上限导致 `IncompleteOutputException` 任务失败的情况。job `d1e870b8` 第一次跑因此失败，重试后通过。**未修改 `agents.py` 配置**，留待后续根据实际输出长度评估调整或加 description 行数约束。
2. **Docker for Windows 文件名编码乱码**：`backend/app/output/<job_id>/` 目录下用户上传的中文文件名因 Docker NTFS 卷挂载的 UTF-8/latin-1 编码错位而出现 mojibake（不影响 pipeline 行为，path 通过 Python 内部传递）。**经用户确认 deferred**，不修复。
3. 真实测试样本仅 1 份（空气管理系统 SRS），未覆盖多文档、多章节、多级标题等边界情况。

#### 下一步建议

1. 评估并调整 `deepseek_comparison` agent 的 `max_tokens` 配置（或在 prompt 中加入 description 行数上限约束）以消除偶发失败。
2. 收集更多真实 SRS 样例（含 `object_type=comment`、`implementation_method=model_based` 等边界值）补全 parser 验证。
3. 跨 chunk 冲突消解和追溯矩阵仍未实现，后续 Issue 处理。

### 2026-07-28 Issue A：V4.0 后端工程化集成

#### 任务目标

把 `_v4_backend_raw/backend/app/` 整体迁入正式 `backend/app/v4/`，新增 `/api/v4` FastAPI 路由命名空间，V3 与 V4 双版本共存。V3 旧 API 保持不变。

#### 完成内容

1. **V3 router 机械拆分**：原 `backend/app/main.py` 拆分到 `backend/app/api/v3/router.py`（173 行），其中 `/api/jobs/{job_id}` 与 `/api/jobs/{job_id}/result` 加 `job.kind != "v3"` 跨版本 404 检查；其他路由 URL、字段、后台线程逻辑、文件保存、下载 helper 全部保持原状。
2. **V4 子包搬运**：`_v4_backend_raw/backend/app/` 全部迁移到 `backend/app/v4/`，含 6 个子目录（`comparison/`、`doc_generators/`、`llm/`、`matching/`、`parsers/`、`prompts/`、`traceability/`）+ 5 个顶层 .py（`cli.py` / `config.py` / `models.py` / `pipeline.py` / `synonyms.yaml`）。V4 内部 import 按白名单机械改写为 `from app.v4.X`。
3. **V4 FastAPI 路由子包**：新增 `backend/app/api/v4/{__init__,router,schemas,runner,coverage,jobs,outputs}.py`，5 个端点（POST coverage-analysis / GET status / GET result / 3 类下载 / health），均不与 V3 路由交叉。
4. **共享 JobManager + cross-version 404**：`backend/app/job_manager.py` 给 `Job` 加 `kind: Literal["v3","v4"]` 字段（默认 "v3"），V4 路由显式传 `kind="v4"`；V3/V4 路由跨版本查询返回 404 + 友好提示（`use /api/v4/jobs/... instead`）。
5. **V4 路径布局调整**：V3 → `backend/output/v3/{job_id}/`（平铺）；V4 → `backend/output/v4/{job_id}/input/` + `backend/output/v4/{job_id}/output/`（分层）。`docker-compose.yml` volume mount 改为 `./backend/output:/app/output`。
6. **V4 路由路径 4 层修复**：`coverage.py:job_dir` / `outputs.py:root` / `jobs.py:base_outputs_dir` 都改为 `parent.parent.parent.parent`（4 层），与 V3 router 一致。
7. **V4 config.py env load 路径调整**：`backend/app/v4/config.py` 中 `_ENV_PATH` 改为 `parent.parent.parent.parent / ".env"`，即 `backend/.env`。
8. **`.env.example` 收尾**：删除之前 V4 `_V4` 后缀占位段（实际 V4 代码读无后缀变量名），恢复 39 行 V3-only 模板。
9. **`requirements.txt` 增 3 项 V4 依赖**：`python-dotenv>=1.0.0` / `requests>=2.31.0` / `pyyaml>=6.0`。
10. **修复 V4 业务 bug**：
    - `deepseek_client.py:34` 与 `hlr_labeler.py:51` URL 双 `/v1/chat/completions` 拼写（DeepSeek 404）—— 加 `if base.endswith('/v1'): base = base[:-3]` 幂等保护。
    - `trace_parser.py:115,170` 硬编码中文文件名（MSYS bash 编码降级时）—— 抽 `_discover_trace_files()` 工具函数，主名匹配失败时 `trace_dir.glob("*.xlsx")` 排序兜底，Table 1 / Table 2 不命中同一文件。
    - `hlr_labeler._call_label_api` 改用 `get_llm("deepseek").chat()`，去掉 `api_key/base_url/model` 参数。V4 内 `import requests` 从 2 处收敛为 1 处（仅 `deepseek_client.py`）。
11. **新建 ADR-001**：`docs/decisions/ADR-001-V4后端接入策略.md`（D1-V4 主线；D2-V3 旧 API 暂留；D3-`/api/v4` 命名空间；D4-V4 业务逻辑保护；D5-mock_models 显式标识；D6-`consistency/{model}` 扩展点；D7-JSON 不暴露）。

#### 修改文件

1. `backend/app/main.py`（替换为 33 行 thin shell：CORS + V3 router 装载）
2. `backend/app/job_manager.py`（加 `kind` 字段 + `create_job(kind=...)` 参数）
3. `backend/app/api/__init__.py`、`backend/app/api/v3/__init__.py`（新增空）
4. `backend/app/api/v3/router.py`（从原 main.py 机械拆分；加 2 处跨版本 404 检查）
5. `backend/app/v4/`（整棵子包：6 子目录 + 5 顶层 .py + 1 .yaml；所有 import 改写；`main.py` → `cli.py` 重命名；`config.py` env load 路径调整；3 处 path 4 层修复；2 处 URL bug 修复；`hlr_labeler._call_label_api` 改走 factory）
6. `backend/.env.example`（删除 V4 占位段，恢复 39 行 V3-only）
7. `backend/requirements.txt`（+3 行 V4 依赖）
8. `docker-compose.yml`（volume mount 路径调整）
9. `docs/decisions/ADR-001-V4后端接入策略.md`（新建）

#### 验证方式

1. `cd backend && python -c "from app.main import app; print(len(app.routes))"` → 12 (V3 8 + V4 7 - docs 共享 3)
2. `cd backend && uvicorn app.main:app --port 8000`
3. `curl http://127.0.0.1:8000/api/v4/coverage-analysis` POST 5 文件 → 200 + job_id
4. `curl http://127.0.0.1:8000/api/v4/jobs/{id}/result` → outputs 全 true + mock_models
5. 5 类下载 curl → 200 + 文件 magic 正确
6. 跨版本 404 + 参数校验 + JSON 不暴露（13 项 acceptance）
7. `docker compose build backend && docker compose up -d backend`（同步骤在容器内复跑）
8. `docker compose exec backend` 内 `ls -la /app/output/v4/{id}/{input,output}/`

#### 验证结果

- 8 项 V3 旧路由（GET /api/health、POST /api/eoicd/analyze、GET /api/jobs/{id}、GET /api/jobs/{id}/result、4 个 /outputs/）全部存活。
- 7 项 V4 新路由（POST /api/v4/coverage-analysis、GET /api/v4/health、GET /api/v4/jobs/{id}、GET /api/v4/jobs/{id}/result、3 个 /outputs/）全部 200。
- 跨版本 /result 路由：V3 → V4 job 返 404 + `use /api/v4/...`；V4 → V3 job 返 404 + 同样。
- 参数校验：consistency/openai → 400；judge_providers=claude → 422。
- JSON 不暴露：multi-judge-json / reverse-report-json → 404。
- 5 文件 + trace 预筛选走通：1 HLR 1/16 拿到真 `bus=['Analog'] devices=['光耦传感器']`；11 blocks_matched；5 类 docx + 7 类中间 JSON 全部落盘。
