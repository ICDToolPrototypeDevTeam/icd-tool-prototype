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
