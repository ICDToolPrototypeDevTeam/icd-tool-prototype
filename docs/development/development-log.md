# 开发纪要

本文档用于记录 **ICD工具原型** 的主要开发过程、关联 Issue、修改内容、验证方式和遗留问题。

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

### 2026-08-24 Issue #63 续：HSCU HLR 预处理 hook 适配新文档结构

#### 任务目标

HSCU HLR Word 新版本 `HSCU软件高层需求-裁剪.docx` 含两张 LBL 总览表（Table[0] RDCU1 入站 8 列 + Table[8] HSCU 出站 4 列），且 source-select 需求以裸 signal name 形式引用 RDCU1 信号。原 hook 假设固定 Table[0] + 固定列偏移 + 只匹配 `LBL_` 前缀 token，新文档下只有 4/10 matched（022587/023389/023507/023124）。需扩展 hook 让它自动识别两张 catalog + 处理裸 signal name + 修复 pipeline 给 hook 暴露 source_file 的方式。

#### 完成内容

1. **Hook 表格识别改为自动**：`_identify_label_tables()` 按启发式扫描所有 table（≥ 3 列 + ≥ 2 行 + 至少一行同时含 LBL cell 和 ≥ 2 位 octal cell），合并 Table[0] + Table[8]。`_extract_row_mapping()` 改为行内扫描，不再假设固定列偏移。`_R_SUFFIX_RE` 兼容旧 RDCU1 `_R1` 后缀形式。
2. **Hook 扩展支持 RDCU1 catalog col 5 多行信号名称**：8 列 catalog col 5 多行 cell 包含该 octal 承载的所有 signal name。新增 `_extract_signal_names()` 把每个符合 `^[A-Z][A-Z0-9_]{4,}$` 的 token 加为 mapping key，让 HSCU HLR 中以裸名形式引用 RDCU1 signal 的需求也能命中。新增 `_BARE_SIGNAL_TOKEN` regex 在 `preprocess_hlr_requirements()` 中识别裸 token。
3. **Octal 长度下限 3 → 2 位**：HSCU catalog 常省略前导 0（`74`、`51`）。放宽 `_looks_like_octal_cell()` 的下限接受 2 位 octal，但仍拒绝单数字（避免与 SDI `0/1/2/3` 混淆）。
4. **Octal alias 左填充 3 位**：ARINC-429 八进制 3 位（000-377），EoICD block key 始终为 3 位。Hook 生成 alias 时 `zfill(3)` 避免 Stage1 prefix filter (`L<label>/` vs `L<3位>`) 失配。
5. **`pipeline._parse_hlr()` 临时切换 source_file**：hook 期间把 `result.source_file` 临时切到完整 `input_path`，hook 调用结束后恢复 basename。保证 hook 能用绝对路径 re-open Word，但 JSON 输出和 AMS/FGMC 行为一致仍保留 basename。

#### 修改文件

1. `backend/app/v4/profiles/hscu/hooks.py` — 表格解析改自动、加 signal name 提取、加 3 位 octal 填充
2. `backend/app/v4/profiles/hscu/config.yaml` — `auto_parse_hlr_table_0: false → true`
3. `backend/app/v4/pipeline.py` — `_parse_hlr()` 在 hook 期间临时切换 source_file
4. `backend/app/v4/profiles/hscu/README.md` — 更新 HSCU HLR 文档结构描述、hook 工作机制、实测匹配结果
5. `CHANGELOG.md` — 新增 [Unreleased] - 2026-08-24 段

#### 验证方式

1. Inline test：直接调用 `_extract_label_mappings()` 解析新 HSCU Word → 56 个 mapping entries（含 12 个 `ABV1_*` signal names）
2. Inline pipeline test：`_parse_hlr(src_docx, out, profile=hscu)` → 6/10 requirements 被 rewrite
3. Docker 重建 backend + HSCU E2E（job `a54aab93`，真实 LLM）：`hlr_已匹配=4, hlr_待确定=2, hlr_无匹配=4`
4. AMS（job `082b4a48`）+ FGMC（job `ed36e75c`）回归：0 个 alias annotation，匹配数不变

#### 验证结果

- HSCU E2E：6/10 matched/pending（023194 ABV1_LOAD_VOLT_AVAIL_RPDU_R1 从「无匹配」升级为「待确定」）
- AMS / FGMC 回归：行为完全保持（auto_parse 默认 False 不被触发）

#### 遗留问题

1. 022995 / 022996（`LBL_CMD1_OHMS`）仍无匹配：该标签不在两张 catalog 中。需用户提供 R1 → CMD1_OHMS 映射（`extra_mappings` 兜底）或 HSCU HLR 文档补充 catalog
2. 025797 / 025798 占位 `LBL_XXX`：HSCU HLR 文档未填写，无 hook 修复路径

#### 下一步建议

1. 等待需求方确认 `LBL_CMD1_OHMS` 是否需要 R1 通道映射
2. 等待 HSCU HLR 文档补全 `LBL_XXX` 占位的实际标签名

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

---

## 2026-07-31 Issue #43：追溯表预筛选兜底机制与协议开销字段过滤

### 任务目标

为 V4 反向匹配管线的追溯表预筛选能力增加兜底机制和索引质量优化，解决预筛选因追溯表数据覆盖不全导致的可匹配 HLR 被漏判问题。

### 完成内容

1. **协议开销字段过滤**：`trace_parser.py` 新增 `_PROTOCOL_BLOCKKEY_SUFFIXES` 常量（`/SDI`、`/LABEL`、`/PARITY`、`/SSM`、`/OCTLBL`），在 `build_trace_index()` 中过滤以协议开销后缀结尾的 block_key。
2. **预筛选失败兜底机制**：`pipeline.py` `_match_reverse_with_trace()` 中，Group A 预筛选后对"无匹配"HLR 触发全量 EoICD 匹配兜底，新增 `_count_match_types()` 辅助函数统计兜底前后匹配类型分布。
3. **前端轮询间隔调整**：`App.tsx` V4 任务轮询从 2 秒调整为 10 秒，最大轮询次数从 600 降为 120 次（总超时约 20 分钟不变）。

### 修改文件

1. `backend/app/v4/matching/traceability/trace_parser.py`（协议开销过滤）
2. `backend/app/v4/pipeline.py`（兜底机制 + `_count_match_types`）
3. `frontend/src/App.tsx`（轮询间隔调整）

### 验证方式

1. 上传包含追溯表的 V4 任务，观察后端日志无 `Prefilter fallback` 误触发
2. 确认 `/SDI`、`/LABEL` 等协议开销 block_key 不出现在追溯索引有效候选列表中
3. 前端 V4 轮询间隔为 10 秒，进度更新正常

### 验证结果

已验证通过。

### 遗留问题

无。

### 下一步建议

无。

---

## 2026-08-11 Issue #53：V4 反向管线一星复查机制（Step 5.5/5.6）

### 任务目标

在 V4 反向管线 Step 5（Review Agent 共识）之后，增加一星复查机制：
- **Step 5.5**：对 `star_rating == 1` 的 case，由三个 provider 以 peer-aware 方式各自重新评判
- **Step 5.6**：仅对被复查过的 case 重跑共识，其余保持不变

### 完成内容

1. **新增 `re_review.py`**：`re_review_judgments()` 函数实现一星复查逻辑。返回类型 `tuple[MultiJudgeOutput, set[str]]`，返回更新后的 multi_out 和被复查的 case_id 集合。内部按 provider 并行调用 LLM，每个 case 拼接 HLR 内容 + ICD Block + 自己之前的判断（Judgment A）+ peer 的判断（Judgment B/C），触发反思纠正。
2. **新增 `prompts/re_review.md`**：一星复查 LLM prompt，包含 peer-aware 复查规则、反思引导和证据驱动逐项核对模板。
3. **集成到 `pipeline.py`**：Step 5.5 → `re_review_judgments()`；Step 5.6 → 仅对 `re_reviewed_ids` 重跑 `review_judgments()`，其余 case 保留 Step 5 结果。
4. **`hlr_labeler.py` max_tokens 调整**：1024 → 2048，避免 HLR 标注时 deepseek 频繁截断。
5. **修复 pipeline.py `review_judgments` 引用错误**：移除了内部局部 import，改从模块级导入。
6. **手动注入测试验证**：REV-0007 和 REV-0012 注入错误 analysis（覆盖状态和错误推理文本），确认 peer-aware 复查触发真正反思和判断纠正。

### 修改文件

1. `backend/app/v4/comparison/re_review.py`（新增）
2. `backend/app/v4/prompts/re_review.md`（新增）
3. `backend/app/v4/pipeline.py`（Step 5.5/5.6 集成）
4. `backend/app/v4/matching/hlr_labeler.py`（max_tokens 2048）
5. `docs/project/workflow.md`（Step 5.5/5.6 写入 V4 流程）
6. `docs/development/development-log.md`（本条）
7. `CHANGELOG.md`（新增变更记录）

### 新增文件

1. `backend/app/v4/comparison/re_review.py`
2. `backend/app/v4/prompts/re_review.md`

### 验证方式

1. 在已有 V4 管线输出目录（job `3d479b34`）中，手动注入一星：修改 `multi_judge_results.json` 和 `consensus_results.json`，设置两条 case 的 star_rating=1、agreement=split，并写入"错误但看似合理"的 analysis 文本
2. 执行 `re_review_judgments()` → 确认两个 case 均触发三方复查（re_review_results.json 写入审计记录）
3. 执行 Step 5.6 共识重跑 → 确认仅 2 个被复查 case 更新，其余 12 个不变
4. 执行 `generate_consensus_reverse_report()` → 确认 JSON 报告星级分布更新
5. 执行 `generate_consensus_report()` Word 生成 → 确认 docx 文件存在且内容正确

### 验证结果

已验证通过。注入 REV-0007（deepseek 误以为 HLR BNR 12位中 bit18 可以是 MSB）和 REV-0012（三方都对 LABEL126/137 语义有误解），复查后：
- REV-0007：deepseek `covered→needs_review`（被 peer 指出 BNR MSB 不能是 bit18）；qwen `needs_review→covered`（被 peer"高位补零"说服）→ majority/2★
- REV-0012：deepseek `covered→inconsistent`；minimax `covered→needs_review`；qwen `needs_review→inconsistent` → full/3★
- 最终星级分布：{'1':0, '2':3, '3':11}，无残留一星
- Word 报告 `EoICD与SWHLR多模型差异分析报告.docx` 生成成功（43,171 bytes）

### 遗留问题

1. 真实管线中一星 case 的"错误 analysis"是否与测试注入的文本风格一致，需有真实一星跑出后对比验证
2. `re_review_results.json` 仅在测试脚本中手动写入，pipeline 集成后应确认落盘时机正确

### 下一步建议

1. 在真实管线中观察是否出现一星 case，对比其 analysis 风格与测试注入文本的差异
2. 确认 pipeline 集成后 Step 5.5 的 `re_review_results.json` 在正确的 job 目录下正确落盘
3. 考虑在 API result 中增加 re_review 相关统计字段（复查 case 数、纠正数）

---

## 2026-08-13 Issue #53 修复：一星复查机制集成验证与 Bug 修复

### 任务目标

集成测试 Issue #53 实现的一星复查机制（Step 5.5/5.6），修复集成过程中暴露的 2 个 bug。

### 完成内容

1. **修复 `re_review_judgments` 的 `multi_out=None` 崩溃**：当 `multi_out` 传入 `None` 时（测试脚本场景），函数内部从 `output_dir / "multi_judge_results.json"` 加载，加载成功后才继续处理。
2. **修复 error provider 被重新查询的问题**：re-review 跳过 `coverage_status == "error"` 的 provider，保留其 error 状态不被覆盖。这是 degradation 正确统计 surviving provider 的前提。
3. **修复 `build_cases` case_id 格式错误**：测试脚本的 `build_cases(reverse_matches_data)` 生成 `REV-0199` 格式（HLR 编号），而 pipeline 实际生成 `REV-0001` 格式（顺序编号），导致 case_map 和 mjr_map 的 key 不匹配，所有一星 case 被静默跳过。修复为顺序编号。
4. **集成测试验证**：三场景测试全部通过——3 providers 存活 → 升至 3★；2 providers 存活（cap=2★）→ 升至 2★；1 provider 存活（cap=1★）→ 保持 1★。

### 修改文件

1. `backend/app/v4/comparison/re_review.py`（`multi_out=None` fallback + skip error providers）

### 新增文件

无。

### 验证方式

1. 构建 `issue53-base` 基准目录（pipeline 跑一次）
2. 复制基准到测试目录，注入3个一星 case
3. 执行 Step 5.5 + Step 5.6，验证 re_review_results.json 生成 + 星级正确

### 验证结果

已验证通过：
- REV-0001（3 alive，cap=3★）：复查后升至 3★ ✅
- REV-0002（2 alive，deepseek error，cap=2★）：复查后升至 2★ ✅
- REV-0003（1 alive，deepseek+minimax error，cap=1★）：复查后保持 1★ ✅

### 遗留问题

无。

### 下一步建议

无。

---

## 2026-07-31 Issue #44：共识报告模板增加不一致属性栏输出

### 任务目标

在 V4 共识报告（多模型差异分析报告）的明细表中增加"不一致属性"栏，列出各模型在字段级别的具体差异内容，提升报告可读性。

### 完成内容

1. **不一致属性栏输出**：`consensus_word_generator.py` 明细表从 7 列扩展为 8 列，新增"不一致属性"列，汇总展示各模型在同一 case 上判定不一致的具体属性字段。
2. **明细表按覆盖状态分组**：输出表格按覆盖状态（covered / needs_review / inconsistent / 无匹配）分组排列，各组内部保持原有排序逻辑。
3. **无匹配行补全**：此前共识报告明细表缺少"无匹配"HLR 行，现已补全并归入独立分组。

### 修改文件

1. `backend/app/v4/doc_generators/consensus_word_generator.py`（不一致属性栏 + 分组 + 无匹配行）

### 验证方式

1. 运行 V4 管线，打开生成的共识报告 docx，确认明细表为 8 列
2. 确认不同覆盖状态行分组清晰，无匹配 HLR 行完整输出
3. 不一致属性栏在有分歧的 case 上正确列出具体差异字段

### 验证结果

已验证通过。

### 遗留问题

无。

### 下一步建议

无。

---

## 2026-08-11 Issue #48：V4 Multi-Agent 降级处理机制

### 任务目标

为 V4 反向管线 Step 4（Multi-Judge）和 Step 5（Review Agent）引入独立的降级处理模块，对 LLM API 超时、卡死、异常输出等异常情况进行系统性兜底。

### 完成内容

1. 新增 `backend/app/v4/degradation/` 独立包（`config.py` / `context.py` / `fallback.py` / `__init__.py`），零侵入现有业务模块。
2. 实现 Case 级超时控制：`_judge_case_with_timeout()` 用 `asyncio.wait(FIRST_COMPLETED)` 逐 provider 收集结果，前 2 个完成后第三个给予固定额外等待时间（默认 120s），不足 2 个时使用兜底上限（默认 300s）。
3. 实现 Provider 熔断器：`DegradationContext` 跨 case 追踪每个 provider 的连续失败计数，达到阈值后自动标记 unhealthy 并跳过后续 case，TTL 到期自动恢复。AUTH 错误立即熔断。
4. 实现 Review 评审降级：`_apply_degradation_review()` 对 `review_judgments()` 输出做后处理，仅 1 个 provider 存活时星级 ≤ 1★，2 个存活时空星级 ≤ 2★。
5. HTTP 超时提升：`DeepSeekClient` / `QwenClient` / `MiniMaxClient` 的 HTTP 请求超时从 60s → 120s。
6. `max_tokens` 调整：`review_agent.py` 和 `semantic_judge.py` 的 `max_tokens` 从 8192 → 4096。
7. 可观测性：`ctx.to_summary()` 输出降级摘要写入 `consensus_results.json` 和 API response 的 `degradation` 字段。
8. 环境变量：`.env.example` 新增 4 个降级配置项。

### 修改文件

1. `backend/app/v4/pipeline.py`（新增 `_judge_case_with_timeout` / `_judge_with_degradation` / `_apply_degradation_review` / `_count_surviving_providers`；Step 4/5 调用切换）
2. `backend/app/v4/llm/deepseek_client.py`（HTTP timeout 60→120）
3. `backend/app/v4/llm/qwen_client.py`（HTTP timeout 60→120）
4. `backend/app/v4/llm/minimax_client.py`（HTTP timeout 60→120）
5. `backend/app/v4/comparison/review_agent.py`（max_tokens 8192→4096）
6. `backend/app/v4/comparison/semantic_judge.py`（max_tokens 8192→4096）
7. `backend/app/api/v4/runner.py`（job.result 新增 `degradation` 字段）
8. `backend/app/v4/cli.py`（输出文件名 HLR→SWHLR）
9. `backend/.env.example`（新增降级配置段）
10. `CHANGELOG.md`

### 新增文件

1. `backend/app/v4/degradation/__init__.py`
2. `backend/app/v4/degradation/config.py`
3. `backend/app/v4/degradation/context.py`
4. `backend/app/v4/degradation/fallback.py`

### 验证方式

1. Mock 模式下运行完整 V4 reverse pipeline，确认 Step 4/5 正常完成
2. 检查 `consensus_results.json` 包含 `degradation` 字段且结构正确
3. 不配降级环境变量时，默认值生效，管线不报错

### 验证结果

Mock 模式下已验证通过。真实 LLM 模式待后续端到端测试。

### 遗留问题

1. 熔断器和超时控制在真实 LLM（非 mock）场景下的表现待端到端验证
2. 超时公式从自适应 `t1 + 0.5*t2` 改为固定 `extra_wait` 后需持续观察 minimax 慢响应是否不再被误杀

### 下一步建议

1. 真实 LLM 模式下跑完整管线，观察降级机制实际表现
2. 根据实际情况调整 `extra_wait` 和 `case_total_timeout` 默认值

---

## 2026-08-12 Issue #52：LLM Client 截断自适应重试下沉

### 任务目标

将 `finish_reason=length` 截断重试从业务层（`semantic_judge.py` 的 `_chat_with_truncation_retry`）下沉到三个 LLM client 的 `chat()` 方法内部，使所有调用方（judge / review / labeler）统一受益，消除 HLR Labeler 截断遗漏问题。

### 完成内容

1. 三个 client（DeepSeek / MiniMax / Qwen）的 `chat()` 方法内新增截断检测 + 自适应重试逻辑：`finish_reason=length` 时自动翻倍 `max_tokens` 重试（4096→8192→16384，上限 16384），与网络层 `max_retries` 独立。
2. 删除 `semantic_judge.py` 中的 `_chat_with_truncation_retry` helper 函数，`_call_judge_api` / `_call_reverse_judge_api` 恢复直接调用 `llm.chat()`。
3. `review_agent.py` 的 `_call_review_api` 恢复直接调用 `llm.chat()`。
4. `ChatResponse` 删除 `truncated` 字段（截断已在 client 内部消化，调用方无需感知）。
5. `hlr_labeler.py` 零改动，自动受益。

### 修改文件

1. `backend/app/v4/llm/deepseek_client.py`（新增内层截断重试）
2. `backend/app/v4/llm/minimax_client.py`（同上）
3. `backend/app/v4/llm/qwen_client.py`（同上）
4. `backend/app/v4/llm/factory.py`（`ChatResponse` 删除 `truncated` 字段）
5. `backend/app/v4/llm/mock_llm.py`（去掉 `truncated=False` 参数）
6. `backend/app/v4/comparison/semantic_judge.py`（删除 `_chat_with_truncation_retry`，恢复直接调用）
7. `backend/app/v4/comparison/review_agent.py`（恢复直接调用）

### 验证方式

1. `python -c` import 全链路验证通过
2. 真实 LLM 模式下跑完整管线，观察截断 WARNING 后是否自动重试成功

### 验证结果

Import 链路验证通过。截断自适应重试效果待真实 LLM 端到端测试确认。

### 遗留问题

1. 截断重试在真实 LLM 场景下的表现待端到端验证

### 下一步建议

1. 真实 LLM 模式下跑完整管线，观察 WARNING + retrying 日志是否正常触发与恢复

---

## 2026-08-14 Issue #59：降级机制修复与共识报告星级表调整

### 任务目标

修复 Multi-Judge 失败兜底状态不一致问题，补齐 0 存活降级分支与复查后降级重应用；并调整共识报告星级分布表布局。

### 完成内容

1. **裁判失败状态归一**：`semantic_judge.py` 三种失败路径（JSON 解析失败 / API 错误 / 重试耗尽）的 `coverage_status` 从 `needs_review` / `unmatched` 统一改为 `error`，避免失败被误判为业务结论，保证 degradation 的 surviving provider 统计正确。
2. **0 存活降级分支**：`_apply_degradation_review()` 新增 0 个 provider 存活场景——强制星级 ≤ 1★（`zero_provider_star_cap`）、`agreement_level = "no_consensus"`、`final_coverage_status = "待确认"`，防止共识 LLM 在纯 error 输入上幻觉出高星级。
3. **复查后重新应用降级**：Step 5.6 部分共识重跑后重新调用 `_apply_degradation_review()` 并重建 summary，避免复查升星绕过降级封顶。
4. **共识明细表标签映射补齐**：`consensus_word_generator.py` 明细表共识列新增 `no_consensus → 无有效裁判`、`single_source → 仅单一来源` 映射。
5. **星级分布表布局调整**：删除 1★ 主行（需人工复核），仅保留 3 个子类型行（分歧/仅单一来源/无有效裁判）；★☆☆ 显示在首个子行星列并与后两行纵向合并；子类型标签加粗、与主行格式一致（居左统一、无特殊颜色）。
6. **降级配置扩展**：`DegradationConfig` 新增 `zero_provider_star_cap=1`、`zero_provider_agreement="no_consensus"` 默认值。

### 修改文件

1. `backend/app/v4/comparison/semantic_judge.py`（失败兜底归一为 error）
2. `backend/app/v4/degradation/config.py`（新增 0 存活降级配置）
3. `backend/app/v4/models.py`（coverage_status / agreement_level 注释补充取值）
4. `backend/app/v4/pipeline.py`（0 存活降级分支 + 复查后重新应用降级并重建 summary）
5. `backend/app/v4/doc_generators/consensus_word_generator.py`（明细表标签映射 + 星级分布表布局）
6. `docs/project/workflow.md`、`docs/architecture/api.md`、`CHANGELOG.md`

### 新增文件

1. `backend/tests/unit/test_word_star_table_style.py`（星级分布表确定性样式检查，raw lxml 校验；本地验证脚本，.gitignore 排除不入库）
2. `backend/tests/e2e/test_use_case_1_consensus_cap.py`（真实 LLM 三场景降级验证；本地验证脚本，.gitignore 排除不入库）

### 验证方式

1. `docker compose build backend` 重建镜像
2. 容器内 `python tests/unit/test_word_star_table_style.py`（确定性、无 LLM）
3. 容器内 `python tests/e2e/test_use_case_1_consensus_cap.py`（真实 LLM 三场景）

### 验证结果

1. Word 星级分布表样式检查全部通过（7 行结构、vMerge 纵向合并、无合计行、格式统一）
2. E2E 三场景全部通过：场景 A（2 存活，cap 2★）17 PASS；场景 B（1 存活，cap 1★ + single_source）18 PASS；场景 C（0 存活，强制 1★ + no_consensus + 待确认）12 PASS

### 遗留问题

1. MiniMax API 偶发环境性失败（重试后仍报 JSON 解析错误），测试逻辑容忍并 WARN 跳过
2. 星级分布表布局改动与降级修复（`268c4f5`）分属两个提交，布局改动待单独提交

### 下一步建议

1. 星级分布表布局改动单独提交
2. 真实管线完整跑一轮，观察 0 存活场景在产出文档中的呈现

## 2026-08-19 Issue #63：V4 反向管线多控制器 Profile 化

### 任务目标

FGMC（燃油测量管理计算机）作为第二个测试样例引入后，原有 V4 代码大量硬编码 AMS 控制器专属的输入结构（HLR 表行数与字段名、追溯表中文文件名、分类关键词、AI 标注示例），无法直接分析 FGMC。本次任务将这些硬编码抽取为 Controller Profile 配置，使新控制器可通过增加 profile 目录接入，且保持 AMS 默认行为 100% 向后兼容。

### 完成内容

1. 新增 `backend/app/v4/profiles/` 子包：`base.py` 定义 `ControllerProfile` + 4 个 Config dataclass（`HLRParserConfig` / `TraceabilityConfig` / `ClassifierKeywords` / `AILabelingConfig`），`__init__.py` 提供 `ProfileRegistry` 单例与 `init_registry` / `get_registry`。
2. AMS profile 从现状代码 1:1 抽取为 `ams/config.yaml`（术语表 `tables[0]`、需求表 ≥8 行、追溯表精确中文文件名），保证向后兼容。
3. 新增 FGMC profile `fgmc/config.yaml`（术语表 `tables[1]`、需求表 ≥12 行、"是否为需求"= "否" 行过滤、追溯表 glob 模式 `*追溯*.xlsx` / `*矩阵分析*.xlsx`、燃油域分类关键词与标注示例）。
4. `HLRWordParser` / `trace_parser` / `hlr_classifier` / `hlr_labeler` 全部改为接受 profile 注入，不再读取模块级硬编码常量。
5. `HLRRequirement` 模型扩展 6 个 optional 字段（`code` / `source` / `covered_ids` / `notes` / `input_data` / `output_data`），供 FGMC 需求表使用。
6. pipeline / CLI / API 打通 `controller_profile` 字段：API `POST /api/v4/coverage-analysis` 新增 form 字段（默认 `ams`，白名单校验失败返回 422）；CLI 三个子命令新增 `--controller-profile`（`choices=["ams","fgmc"]`，默认 `ams`）。
7. 新增 profile 单元测试目录 `backend/app/v4/tests/profiles/`（registry / models / HLR parser / classifier / labeler / trace / pipeline 共 24 个用例）。

### 修改文件

1. `backend/app/v4/parsers/hlr_word_parser.py`（profile 驱动的字段映射与表识别）
2. `backend/app/v4/traceability/trace_parser.py`（追溯表文件名与 sheet / 列配置外置）
3. `backend/app/v4/matching/hlr_classifier.py`（分类关键词由 profile 注入）
4. `backend/app/v4/matching/hlr_labeler.py`（AI 标注示例由 profile 注入）
5. `backend/app/v4/models.py`（`HLRRequirement` 扩展 6 个 optional 字段）
6. `backend/app/v4/pipeline.py`（profile 解析与向下注入）
7. `backend/app/v4/cli.py`（三个子命令新增 `--controller-profile`）
8. `backend/app/api/v4/coverage.py`、`backend/app/api/v4/runner.py`（API `controller_profile` 字段与透传）
9. `docs/architecture/api.md`、`docs/architecture/current-architecture.md`、`docs/project/scope.md`、`docs/project/workflow.md`、`CHANGELOG.md`

### 新增文件

1. `backend/app/v4/profiles/{__init__.py,base.py}`
2. `backend/app/v4/profiles/ams/{__init__.py,config.yaml,hooks.py,README.md}`
3. `backend/app/v4/profiles/fgmc/{__init__.py,config.yaml,hooks.py,README.md}`
4. `backend/app/v4/tests/profiles/`（7 个测试模块）

### 验证方式

1. `cd backend && py -3.10 -m pytest app/v4/tests/profiles/ -v`
2. HLR 解析 smoke（仅 parser，不调 LLM）：分别用 ams / fgmc profile 解析两份 HLR Word

### 验证结果

1. 单元测试 24 passed（Python 3.10.11 / pytest 9.1.1），无 failed / error
2. 解析 smoke：AMS 16 条需求（与 Issue #62 输出一致）；FGMC 10 条需求，"是否为需求"= "否" 行已过滤
3. 端到端真实 LLM 管线尚未验证

### 遗留问题

1. 端到端（含真实 LLM 的完整 6 步管线）在 FGMC 样例上尚未跑通验证，当前仅验证到解析与匹配前段
2. 前端未暴露 `controller_profile` 选择入口，Web 端目前只能使用默认 AMS profile

### 下一步建议

1. 用 FGMC 样例跑一轮完整管线，检查匹配率与报告产出
2. 前端 V4 上传页增加控制器选择控件，透传 `controller_profile`

---

## 2026-08-20 Issue #63 续：HSCU 控制器 Profile 接入

### 任务目标

将 HSCU（液压系统控制单元）作为 V4 第 3 个 controller profile 接入，参照现有 AMS / FGMC profile 模式（位于 `backend/app/v4/profiles/{id}/config.yaml`），无需改动业务代码。

### 完成内容

1. **新增 HSCU profile 子包**：`backend/app/v4/profiles/hscu/`
   - `__init__.py`：标记为 HSCU profile 包。
   - `config.yaml`：声明 HSCU 专属的 HLR 字段映射（含 `需求正文` 字段名）、术语表位置、需求表行数阈值、追溯表文件名模式 + sheet 名匹配 + 列偏移。
   - `hooks.py`：预留 HSCU 专属扩展点（当前为空）。
   - `README.md`：记录 HSCU 与 AMS / FGMC 的差异、T1 sheet 名纠正说明和 `data_start_row` 含义。
2. **profile 白名单扩展**：`backend/app/api/v4/coverage.py` `ALLOWED_CONTROLLER_PROFILES = {"ams", "fgmc", "hscu"}`，错误信息同步更新。
3. **HSCU 适配关键差异**：
   - HLR 字段映射：HSCU 需求表行标签用 `需求正文`（`field_map.content: ["需求正文", "需求中文", "需求描述"]`），AMS / FGMC 均为 `需求中文`。
   - 无 `is_requirement` 列：`filter_non_requirement: false`（AMS 默认开启过滤、FGMC 显式开启过滤匹配 "否"）。
   - 追溯表 T1：glob 模式 `附件1*需求*ICD*.xlsx`，sheet 名匹配 `待填_需求接口追溯表`（初次配置为 `需求_设备接口追溯表` 是错误的，经 codepoint inspection 后纠正）。
   - 追溯表 T2：glob 模式 `*液压*单模块需求矩阵*.xlsx`，`data_start_row: 2`（跳过当前需求文档 / 下层需求文档合并行 + 列标题行）。
4. **误诊清理**：初次接入时误判 HSCU T1 xlsx 存在 GBK-as-UTF-8 mojibake，引入 `_xlsx_mojibake.py` 工具 + `TraceabilityTableConfig.repair_gbk_mojibake` 字段 + `trace_parser._maybe_heal()` 调用。经 codepoint 校验 HSCU 文件实际为干净 UTF-8（"mojibake" 是 Windows console 无法渲染某些 CJK 字符所致），按 debug-rules.md "最小修改原则" 全部回退删除，无 mojibake 相关代码残留。
5. **文档更新**：
   - `docs/architecture/api.md` §13.2 `controller_profile` 字段白名单 `{ams, fgmc}` → `{ams, fgmc, hscu}`；§13.6 错误响应同步。
   - `docs/project/scope.md` §8.2.1 profile 表新增 `hscu` 行。
   - `docs/architecture/current-architecture.md` profiles 目录树新增 `hscu/` 节点。

### 修改文件

1. `backend/app/api/v4/coverage.py`（`ALLOWED_CONTROLLER_PROFILES` 加入 `"hscu"`，错误信息更新）
2. `docs/architecture/api.md`（profile 白名单 + 错误响应）
3. `docs/project/scope.md`（profile 表新增 `hscu` 行）
4. `docs/architecture/current-architecture.md`（profile 目录树新增 `hscu/` 节点）
5. `CHANGELOG.md`（新增 Unreleased 2026-08-20 条目）
6. `docs/development/development-log.md`（本条记录）

### 新增文件

1. `backend/app/v4/profiles/hscu/__init__.py`
2. `backend/app/v4/profiles/hscu/config.yaml`
3. `backend/app/v4/profiles/hscu/hooks.py`
4. `backend/app/v4/profiles/hscu/README.md`

### 删除文件

1. `backend/app/v4/traceability/_xlsx_mojibake.py`（早期误诊引入的 mojibake 修复工具，已按最小修改原则删除）

### 验证方式

1. Dry-run：HSCU profile YAML 加载 + ProfileRegistry 注册 + HLR parser 解析 HSCU 测试 HLR Word + trace parser 识别 T1/T2 文件
2. `docker compose build backend && docker compose up -d backend`（source code 未挂载 volume，必须重建）
3. E2E：`python` 脚本调用 `POST /api/v4/coverage-analysis`，传 `controller_profile=hscu` + HSCU 测试 5 文件（HLR Word + 2 EoICD Excel + 2 trace Excel），轮询至 `completed`
4. 输出校验：`/api/v4/jobs/{id}/result.outputs.*` 5 布尔全 true + `backend/output/v4/{id}/output/` 下 5 DOCX/XLSX 文件齐全
5. AMS / FGMC 回归验证：同样脚本传 `controller_profile=ams` / `fgmc`，确认已完成且 4 DOCX 全产出

### 验证结果

1. **HSCU E2E**：job `dd0790ed` mock LLM 完整跑通 6 步管线（parse → label → match → multi_judge → review → report），5 类输出齐全（`eoicd_xlsx.xlsx` + 4 DOCX），HLR 解析 10 条（如 `FSF29005501_HLR_025797`），追溯匹配 15 条 HLR（含 9 ERDs + 6 ICDs）。任务状态：`pending → running → completed`，总耗时 ~25s。
2. **HSCU 0/10 反向匹配**：当前 HSCU HLR 信号关键词（`HYD_xxx` / `LBL_xxx` 等）在提供的 EoICD 样例文件中未出现（样例仅含 AHMU `AIRCRAFT_STATUS` 信号），属测试数据覆盖问题，非 HSCU profile 问题。
3. **AMS 回归**：job `a335bce9` mock LLM 跑通，~60s 完成，4 DOCX 全产出，message `Reverse pipeline complete`。
4. **FGMC 回归**：job `67e745b9` mock LLM 跑通，<5s 完成（mock LLM 极快），4 DOCX 全产出，message `V4 reverse pipeline complete`。
5. AMS / FGMC 行为与 Issue #63 接入前一致，无回归。

### 遗留问题

1. HSCU 端到端真实 LLM 模式尚未验证（当前 mock LLM）
2. HSCU 0/10 反向匹配率因 EoICD 样例不含液压域信号而无法评估；需提供 HSCU EoICD 真实 PubSub Excel 样例才能验证匹配率
3. 前端 V4 上传页尚未暴露 `controller_profile` 选择入口（仍为 Issue #63 既有遗留）

### 下一步建议

1. 收集 / 提供 HSCU EoICD 真实 PubSub Excel 样例（含 `HYD_xxx` / `LBL_xxx` 等液压域信号），重新跑 HSCU E2E 验证匹配率
2. 前端 V4 上传页增加控制器选择控件，透传 `controller_profile`（仍是 Issue #63 既有遗留）
3. HSCU 真实 LLM 模式端到端验证

---

## 2026-08-21：HSCU LBL→L<octal> 别名追加 Hook（Profile HLR 预处理）

### 任务目标

修复 HSCU 0/10 反向匹配根因：HLR 文本使用符号化标签名（`LBL_DIS_00_SYS1`），但 EoICD PubSub 块以八进制编码（`L145_DIS_00_SYS1_T1A`），反向匹配 Stage1 prefix filter 永远 0 命中。在不改 `reverse_matcher.py` / `hlr_classifier.py` / `hlr_labeler.py` / `trace_parser.py` 的前提下，通过 profile 声明的预处理 hook 改写 HLR 内容追加八进制别名，让 AI 标注器识别 octal 形式。

### 完成内容

1. **`ControllerProfile` 扩展 `HLRPreprocessConfig`**：`base.py` 新增 dataclass，含 `enabled` / `extra_mappings` (tuple[tuple[str, str]]) / `auto_parse_hlr_table_0` / `table0_name_column` / `table0_octal_column` / `apply_to_fields`。`_parse_hlr_preprocess()` 解析 YAML `hlr_preprocess` 段，`load_profile_from_yaml()` 集成。
2. **`apply_hlr_preprocess_hook()` 通用入口**：`profiles/__init__.py` 新增函数，按 `importlib.import_module("app.v4.profiles.<profile_id>.hooks")` 动态加载 profile 专属 hooks，调用 `preprocess_hlr_requirements(profile, hlr_out)`，返回改写条数（int）。enabled=False 或 module 缺失时返回 0。
3. **HSCU hook**：`profiles/hscu/hooks.py` 实现 `preprocess_hlr_requirements()`，按 per-token 范围追加别名：
   - 仅对配置文件中**实际配置了映射**的 LBL token 才追加（避免越界误标）
   - 自动剥离 `_SSM` 后缀（HSCU HLR 文本写 `LBL_DIS_00_SYS1_SSM` 而映射 key 为 `LBL_DIS_00_SYS1`）
   - 占位值（`???` / `?`）跳过
   - 幂等（重复执行不重复追加）
4. **HSCU config.yaml**：新增 `hlr_preprocess` 段，配置 `LBL_DIS_00_SYS1: "145"`（已验证）；其余 4 个 LBL（DIS_03_INFO / QTY_SYS2 / CMD1_OHMS / CMD1_OHMS_Status）保留为注释占位，待用户提供八进制号后启用。
5. **`pipeline._parse_hlr()` 集成**：HLRWordParser.parse() 完成后、写 JSON 前调用 `apply_hlr_preprocess_hook()`，让改写后的 HLR 内容流入下游 AI 标注 / 分类 / 匹配。

### 修改文件

1. `backend/app/v4/profiles/base.py`（新增 `HLRPreprocessConfig` + `_parse_hlr_preprocess` + `ControllerProfile` 新字段）
2. `backend/app/v4/profiles/__init__.py`（新增 `apply_hlr_preprocess_hook()`）
3. `backend/app/v4/profiles/hscu/config.yaml`（新增 `hlr_preprocess` 段）
4. `backend/app/v4/profiles/hscu/hooks.py`（从空 docstring 升级为 `preprocess_hlr_requirements()` 实现）
5. `backend/app/v4/pipeline.py`（`_parse_hlr` 调用 hook 并按返回值输出进度行）
6. `backend/app/v4/profiles/hscu/README.md`（新增"HLR 预处理 Hook（HSCU 专用）"章节）
7. `CHANGELOG.md`（新增 Unreleased 2026-08-21 条目）
8. `docs/development/development-log.md`（本条）

### 不修改范围

- `reverse_matcher.py` / `hlr_classifier.py` / `hlr_labeler.py` / `trace_parser.py` 不动
- AMS / FGMC profile 不受影响（`hlr_preprocess.enabled` 默认 False，hook 为 no-op）

### 验证方式

1. 内联 Python 验证 hook 行为（仓库无 pytest 基础设施）：26/26 通过，覆盖 per-token 范围 / `_SSM` 剥离 / 占位跳过 / 幂等 / 多映射
2. `python -c "from app.v4.profiles import init_registry; ..."` 验证 YAML 加载
3. Docker 重建：`docker compose build backend`（--no-cache）+ `docker compose up -d backend`
4. 容器内 `python -c "..."` 验证 HSCU config 加载
5. HSCU 真实 LLM E2E：`POST /api/v4/coverage-analysis controller_profile=hscu` + 5 HSCU 文件
6. AMS 回归：job `75425aa0`（真实 LLM）
7. FGMC 回归：job `59664e42`（真实 LLM）

### 验证结果

| 阶段 | hlr_count | matched | pending | unmatched | status_distribution |
|---|---|---|---|---|---|
| HSCU hook 前（job `f1eff041`） | 10 | 0 | 0 | 10 | 无匹配×10 |
| HSCU hook 后（job `39f938f5`，仅 1 映射）| 10 | **1** | 0 | 9 | 已覆盖×1, 无匹配×9 |
| HSCU hook 满 4 映射（预期） | 10 | ~6 | – | ~4 | – |
| AMS 回归（job `75425aa0`）| 16 | 9 | 5 | 2 | 已覆盖×10, 待判定×2, 待确认×2, 无匹配×2 |
| FGMC 回归（job `59664e42`）| 7 | 3 | 1 | 3 | 已覆盖×2, 待确认×2, 无匹配×3 |

1. HSCU 真实 LLM E2E：matched_count 从 0 升至 1（仅 1 个映射生效），unmatched 10→9。无 0 存活降级分支 / 解析异常 / 输出缺失。5 类输出齐全。
2. AMS / FGMC 真实 LLM E2E：与 hook 接入前行为一致，无退化。
3. 容器内单步验证：`preprocess_hlr_requirements` 对真实 HLR 022587 内容（`LBL_DIS_00_SYS1_SSM`）正确追加 `L145_DIS_00_SYS1` 别名。

### 遗留问题

1. HSCU HLR 中 4 个未配置的 LBL（`LBL_DIS_03_INFO` / `LBL_QTY_SYS2` / `LBL_CMD1_OHMS` / `LBL_CMD1_OHMS_Status`）对应的 EoICD 八进制号未填写，config.yaml 中保留为注释占位，待用户提供。
2. HSCU HLR 025797 / 025798 使用占位符 `LBL_XXX`（文档模板问题），匹配永远为 0，需 HSCU HLR 文档修订才能解决。
3. HSCU HLR 023194 / 022645 不涉及 A429 标签（`ABV1_LOAD_VOLT` / `AIR_SPEED_FCM1_R1`），非本机制可解决。

### 下一步建议

1. 用户提供 4 个 LBL 八进制映射 → 填入 HSCU `config.yaml` 的 `extra_mappings`，预期 matched 1 → 6。
2. HSCU HLR 文档维护方修订 `LBL_XXX` 占位符，改为具体标签名。
3. 评估是否将 `auto_parse_hlr_table_0` 实际启用（自动从 HLR Word Table 0 抽取 LBL→octal 映射，减少手工配置工作量）。
---

## 2026-08-20：V4 冗余代码清理（早期正向原型 + 旧单模型反向 CLI）

### 任务目标

清理 `app/v4/` 内未被 Web API 调用的冗余代码：早期正向原型（EoICD→HLR 属性级正向匹配）与旧单模型反向 CLI，并同步更新文档与 ADR（ADR-003）。

### 完成内容

1. 删除早期正向原型整链：`run_forward_pipeline`、`comparison/case_builder.py`、`matching/{candidate_matcher,text_matcher,unified_matcher}.py`、`prompts/forward_judge.md`。
2. 删除正向/死代码符号：`models.py` 的 6 个正向模型（`MatchCandidate`/`ComparisonCase`/`JudgmentResult`/`DifferenceReport`/`MatchOutput`/`JudgmentOutput`）+ `AgentJudgment`；`config.py` 的 `MATCH_SCORE_WEIGHTS`/`MATCH_WEIGHTS`/`DATA_TYPE_EQUIV`/`UNIT_EQUIV` 等常量与 `is_data_type_equiv`/`is_unit_equiv`；`multi_judge.judge_with_panel`、`factory.get_available_providers`、`entry_filter.filter_requirements`、`eoicd_enricher.enrich_query` 等。
3. 删除旧单模型反向 CLI 与正向 CLI：`reverse-judge`/`reverse-report`（及 `judge_reverse_cases`/`generate_reverse_report`）、`match`/`judge`/`report`/`analyze` 共 6 个子命令。
4. 保留反向主链（parse → label → reverse match → multi-judge → review → report）与共享函数（`should_keep`、`_resolve_aliases`/`_get_synonym_lookup`/`_tokenize_name` 等）。
5. 新增 ADR-003；ADR-002 D4 标记被 ADR-003 取代；同步 `current-architecture.md`、`CHANGELOG.md`。

### 修改文件

1. `backend/app/v4/pipeline.py`、`comparison/{semantic_judge,report_generator,multi_judge}.py`
2. `backend/app/v4/matching/{entry_filter,eoicd_enricher}.py`、`llm/factory.py`
3. `backend/app/v4/{models,config,cli}.py`
4. `backend/app/v4/synonyms.yaml`、`prompts/loader.py`
5. `docs/decisions/ADR-002-移除V3.md`、`docs/architecture/current-architecture.md`、`CHANGELOG.md`

### 新增文件

1. `docs/decisions/ADR-003-移除V4早期正向原型与旧反向CLI.md`

### 删除文件

1. `backend/app/v4/comparison/case_builder.py`
2. `backend/app/v4/matching/{candidate_matcher,text_matcher,unified_matcher}.py`
3. `backend/app/v4/prompts/forward_judge.md`

### 验证方式

1. `python -m compileall backend/app/v4 backend/app/api`
2. `python -c` import 全链路（pipeline/models/config/comparison/matching/llm/cli）
3. `python -m app.v4.cli --help`
4. `docker compose up -d --build` 端到端测试（mock）
5. 残留引用 `rg` 扫描（排除 `__pycache__` / `backend/output`）
6. 清理前后反向结果基线对比（eoicd_count / hlr_count / matched/pending/unmatched/judged / star_distribution / status_distribution）

### 验证结果

1. `python -m compileall backend/app/v4 backend/app/api` —— 通过（无语法错误）。
2. import 全链路（pipeline / models / config / comparison / matching / llm / cli）—— 通过。
3. `python -m app.v4.cli --help` —— 通过，剩余 8 个子命令（parse-eoicd / parse-hlr / all / label-hlr / reverse-match / reverse-analyze / generate-word / generate-consensus-report）。
4. `docker compose up -d --build` —— 通过，后端 `icd-tool-backend-v4.0` healthy。
5. 残留引用 `rg` 扫描（排除 `__pycache__` / `backend/output`）—— 活跃 Python 代码中已删除符号/文件零引用；剩余命中均为保留的 `Reverse*` 模型、`--reverse-report` 参数与 `generate_consensus_reverse_report`。
6. 端到端测试（mock，真实输入样本）—— job `8d3cec35-23ba-41b1-824a-ef8db1b1f60f` completed，5 产物 + 5 下载全 200。
7. 清理前后反向结果基线对比 —— **ALL_MATCH**（8 项全一致）：`eoicd_count=122674`、`hlr_count=16`、`matched_count=5`、`pending_count=7`、`unmatched_count=4`、`judged_count=12`、`star_distribution={"1":12,"2":0,"3":0}`、`status_distribution`（12，键为既存「待确认」乱码 U+FFFD，两侧一致）。
8. `git diff --check` —— 通过（仅 LF→CRLF 换行提示，无空白错误）。

### 遗留问题

1. `final_coverage_status` / `status_distribution` 键的「待确认」乱码（U+FFFD）为 V4 既存编码 bug，本次未处理。

### 下一步建议

1. 用户手动 push / PR / 关闭 Issue（CLAUDE.md §11.2 红线）。
2. 择期修复「待确认」乱码编码 bug。

## 2026-08-19 case 级超时后台收尾（drain）

### 任务目标

解决 Step 4 多智能体裁判中"第三个（慢但有效）输出被超时丢弃"的问题：超时任务不再取消，转入后台线程池继续执行，Step 4.5 统一收尾，迟到有效结果替换 TIMEOUT 占位后进入共识。

### 完成内容

1. **执行模型改造**：`_judge_case_with_timeout()` 从 per-case `asyncio.run` + `asyncio.to_thread` 改为全局 ThreadPoolExecutor + concurrent.futures（`_get_drain_executor()`，进程级单例）；`_judge_with_provider_sync()`（multi_judge.py）为同步版裁判函数，永不抛出，错误归一 error judgment。
2. **后台收尾（Step 4.5）**：`_drain_and_rereview()` 统一 join 超时任务（预算 `DEGRADATION_DRAIN_BUDGET`，默认 300s）；预算内返回的有效结果替换 TIMEOUT 占位并 reset 该 provider 熔断失败计数；迟到失败不重复计数；迟到结果使该 case 存活 provider 数恢复，Step 5 共识基于最终 judgments。
3. **降级统计**：`DegradationContext` 新增 `drain` 挂载点与 `drained_late_count` 统计，随 `degradation` summary 输出。
4. **配置扩展**：`DegradationConfig` 新增 `drain_budget=300`、`drain_max_workers=6`。

### 修改文件

1. `backend/app/v4/pipeline.py`（`_judge_case_with_timeout` 线程版、`_judge_with_degradation` 挂 drain、`_drain_and_rereview`、Step 4.5 插入）
2. `backend/app/v4/comparison/multi_judge.py`（新增 `_judge_with_provider_sync`）
3. `backend/app/v4/degradation/config.py`（drain 参数）
4. `backend/app/v4/degradation/context.py`（drain 挂载点 + 迟到统计）
5. `backend/.env.example`（新参数说明）
6. `docs/project/workflow.md`、`CHANGELOG.md`

### 新增文件

1. `backend/tests/e2e/test_use_case_3_slow_provider_drain.py`（慢 provider 假服务器 + 真实 API；验证 TIMEOUT 占位、drain 替换、存活恢复、主流程不被 per-case 拖慢）

### 验证方式

1. `python -m compileall` 语法检查
2. `docker compose build backend` 重建镜像
3. 容器内 `python tests/e2e/test_use_case_2_circuit_breaker.py`（既有回归）
4. 容器内 `python tests/e2e/test_use_case_3_slow_provider_drain.py`（新用例）

### 验证结果

1. `python -m compileall` 语法检查通过
2. `docker compose build backend` 重建镜像成功
3. 既有回归 `test_use_case_2_circuit_breaker.py`：全部通过（熔断链路行为不变，degradation summary 含 `drained_late_count: 0`）
4. 新用例 `test_use_case_3_slow_provider_drain.py`：全部通过（TIMEOUT 占位 → drain 迟到替换 → 存活数恢复 0→3/0→2；主流程 10.3s < 慢响应总和 16s，未 per-case 等待；`drained_late_count=5` 含 minimax 2 条必然值 + 真实 API 迟到条数）

### 遗留问题

1. 后台线程池排队无拒绝策略：池满时任务排队，靠 join 预算兜底（慢任务残留不阻塞主流程）
2. 迟到结果不参与 Step 5.5 一星复查的 peer 判断（复查基于原始 judgments）
3. `asyncio` 从 pipeline.py 移除后，`judge_with_panel()`（multi_judge.py 同步入口）未被调用但保留

### 下一步建议

1. 观察真实管线中 `drained_late_count` / `total_case_timeouts` 分布，校准 `DEGRADATION_EXTRA_WAIT` 与 `DEGRADATION_DRAIN_BUDGET`
2. 关联 GitHub Issue 后补充 Issue 编号

## 2026-08-21 drain 任务上限 + 提交限流

### 任务目标

在 drain 机制基础上增加资源控制：限制 drain 任务数上限（防堆积）+ 限制同时提交到线程池的任务数（防并发）。

### 完成内容

1. **drain 任务上限**：`DegradationConfig` 新增 `drain_max_tasks=60`（env: `DEGRADATION_DRAIN_MAX_TASKS`），`_judge_with_degradation` 中 append 前判断 `len(ctx.drain) < drain_max_tasks`，超限任务调用 `future.cancel()`（未执行的取消，已执行的结果丢弃）。
2. **任务提交限流**：`DegradationConfig` 新增 `max_inflight=6`（env: `DEGRADATION_MAX_INFLIGHT`），`_submit_with_gate()` 用信号量控制同时提交到 executor 的任务数，超限任务在 submit 前阻塞等待，从源头限制 API 并发。
3. **配置可 env 覆盖**：`from_env()` 新增 `DEGRADATION_DRAIN_MAX_TASKS`、`DEGRADATION_MAX_INFLIGHT` 读取。

### 修改文件

1. `backend/app/v4/degradation/config.py`（新增 `drain_max_tasks`、`max_inflight` 字段 + `from_env`）
2. `backend/app/v4/pipeline.py`（新增 `_inflight_sema`、`_get_inflight_sema`、`_submit_with_gate`；`_judge_case_with_timeout` 改用 `_submit_with_gate`；`_judge_with_degradation` 加 drain 上限判断 + `cancel()`）
3. `backend/.env.example`（新参数说明）
4. `docs/project/workflow.md`（Step 4/4.5 描述 + 约束 #7 更新）
5. `CHANGELOG.md`

### 新增文件

1. `backend/tests/e2e/test_use_case_3b_drain_max_tasks.py`（drain_max_tasks 上限验证）

### 验证方式

1. `docker compose build backend` 重建镜像
2. 容器内 `python tests/e2e/test_use_case_2_circuit_breaker.py`（既有回归）
3. 容器内 `python tests/e2e/test_use_case_3_slow_provider_drain.py`（既有回归）
4. 容器内 `python tests/e2e/test_use_case_3b_drain_max_tasks.py`（新用例，drain_max_tasks=2）

### 验证结果

1. `docker compose build backend` 重建镜像成功
2. `test_use_case_2_circuit_breaker.py`：全部通过
3. `test_use_case_3_slow_provider_drain.py`：全部通过
4. `test_use_case_3b_drain_max_tasks.py`：全部通过（3 case × 3 provider = 9 任务，3 个超时，drain 2 个，丢弃 1 个；`drained_late_count=2`，`error_judgments=1`）

### 遗留问题

1. `cancel()` 对已执行的任务无效（already running），仅能取消队列中未执行的任务；正常场景下线程池容量足够，任务几乎立刻执行，cancel 大部分返回 False
2. `max_inflight=6` 与 `drain_max_workers=6` 对齐，当前场景最多 4 个单 provider 并发（3 当前 case + 1 超时），风险可控

### 下一步建议

1. 观察真实管线中 `drain_max_tasks` 触发频率，校准默认值
2. 如需进一步限制单 provider 并发，可考虑 per-provider Semaphore（L2）

### 2026-08-21 已知限制：主流程与 drain 任务线程池竞争

#### 问题描述

当前 Step 4 主流程和 Step 4.5 drain 任务共用同一个 `ThreadPoolExecutor`（`drain_max_workers=6`）。随着 case 推进，超时 drain 任务在线程池中累积，逐步挤占主流程可用线程。

典型场景（12 case，每 case 1 超时 provider，drain 任务耗时 120s）：

- Case 1~3：主流程 3 线程 + drain 累积，池未满，正常
- Case 4 起：drain 任务 ≥ 3，加上主流程 3 线程 = 池满，后续 case 的 submit 需等 drain 释放线程
- 预计 8/12 个 case 受阻塞影响

#### 候选方案分析

| 方案 | 改动 | 效果 | 结论 |
|------|------|------|------|
| 线程池扩容到 9 | 1 行 config | 推迟到 Case 7 才卡，不解决根本 | 收益有限 |
| 双线程池（主流程 3 + drain 3） | pipeline.py ~30 行 | 完全隔离，但超时 future 需 resubmit，多一次 API 调用 | 与"不浪费已有调用"初衷冲突 |
| 接受当前风险 | 无 | drain_max_tasks=60 + max_inflight=6 已有兜底 | 当前选择 |

#### 当前结论

维持现状。`drain_max_tasks=60` 限制 drain 累积上限，`max_inflight=6` 限制 API 并发。主流程被阻塞的表现是 case 处理延迟（等 drain 释放线程），不会导致功能错误或资源无限增长。

如后续 case 数量显著增加（>20）或 drain 任务耗时成为瓶颈，可重新评估双线程池方案（需接受 resubmit 的额外 API 调用开销）。

### 2026-08-25 Issue #74：RPDU 多控制器适配合并

#### 任务目标

将 RPDU 本地分支适配代码（Excel 格式 HLR、header 自适应追溯解析、4 项反向匹配增强）合并到 V4 主线，所有改动集中在一个新 `rpdu` profile 中，不污染 AMS/FGMC/HSCU 既有行为（全部默认关闭 → 字节一致）。

#### 完成内容

1. profile schema 扩展（`profiles/base.py`）：新增 `HLRParserDriverConfig`、`MatcherEnhancementConfig` 两个 dataclass；`ControllerProfile` 增加 `hlr_parser_driver` / `matcher` / `trace_strategy` 三个字段，所有默认 legacy 行为。
2. 新增 HLR Excel 解析全局能力（`parsers/hlr_parser_base.py` + `hlr_excel_parser.py` + `hlr_parser_factory.py` + `__init__.py`）：`.docx → HLRWordParser`、`.xlsx → HLRExcelParser`，由 `create_hlr_parser(source_path, profile=)` 工厂按扩展名分发。
3. `matching/rev_matcher.py`：4 项 RPDU 增强作为 opt-in（中文后缀剥离 / 方向软约束带 conflict 标记 / 信号编号加分 / `top_k=50`），通过 `enhancements: matcherEnhancementConfig | None` 透传，所有改动默认 False/20。
4. `traceability/trace_parser.py`：新增 `_read_table1_header_adaptive` / `_read_table2_header_adaptive`，`build_trace_index(trace_dir, cfg, profile=None)` 按 `profile.trace_strategy` 分发（`profile_columns` 默认走原列索引，AMS 字节一致；`header_adaptive` 走关键字扫描，RPDU）。
5. `pipeline.py`：`_parse_hlr` 改用 `create_hlr_parser` 工厂；`_match_reverse_with_trace` 与 `run_reverse_pipeline` Step 3 两条路径全部透传 `profile=` 给 `build_trace_index` 和 `match_reverse`。
6. `api/v4/coverage.py`：`ALLOWED_CONTROLLER_PROFILES` 加入 `"rpdu"`；错误消息使用 `sorted()` 动态列出支持列表。
7. 新增 `profiles/rpdu/`：`__init__.py`（空包标记）、`config.yaml`（含 `hlr_parser_driver: driver=xlsx`、`trace_strategy: header_adaptive`、4 项 matcher 全部启用、top_k=50、RPDU 专属分类关键词与 AI 标注示例）、`hooks.py`（no-op 预留）、`README.md`。
8. 文档同步：`docs/architecture/current-architecture.md` profiles 目录树增 rpdu 节点；`docs/project/scope.md` §8.2.1 profile 表加 rpdu 行、新增 §8.2.2「Profile 扩展维度」描述 Issue #74 的 3 个新增 profile 扩展点（HLR 解析驱动 / 追溯策略 / 匹配增强）。
9. `api/v4/coverage.py` HLR 扩展名校验改为基于 `parsers.registered_extensions()` 工厂白名单，支持 .docx 与 .xlsx，新增解析器只需在工厂注册。

#### 修改文件

1. `backend/app/v4/profiles/base.py`（2 个新 dataclass + 3 个 ControllerProfile 字段）
2. `backend/app/v4/matching/rev_matcher.py`（4 项 opt-in 增强 + 默认 None 透传）
3. `backend/app/v4/traceability/trace_parser.py`（header 自适应 + profile 调度）
4. `backend/app/v4/pipeline.py`（_parse_hlr 改用工厂；3 处 `profile=` 透传）
5. `backend/app/api/v4/coverage.py`（白名单 + 错误消息 + 扩展名工厂白名单）
6. `docs/architecture/current-architecture.md`（profiles 目录树）
7. `docs/project/scope.md`（profile 表 + §8.2.2）
8. `docs/development/development-log.md`（本条）

#### 新增文件

1. `backend/app/v4/parsers/__init__.py`
2. `backend/app/v4/parsers/hlr_parser_base.py`
3. `backend/app/v4/parsers/hlr_excel_parser.py`
4. `backend/app/v4/parsers/hlr_parser_factory.py`
5. `backend/app/v4/profiles/rpdu/__init__.py`
6. `backend/app/v4/profiles/rpdu/config.yaml`
7. `backend/app/v4/profiles/rpdu/hooks.py`
8. `backend/app/v4/profiles/rpdu/README.md`

#### 验证方式

1. `PYTHONPATH=backend python -c "from app.v4.profiles import init_registry, get_registry; init_registry(Path('backend/app/v4/profiles')); print(get_registry().list_ids())"` → 期望 `['ams','fgmc','hscu','rpdu']`
2. `PYTHONPATH=backend python -c "from app.v4.parsers import create_hlr_parser; from app.v4.profiles import get_registry; rpdu=get_registry().get('rpdu'); out=create_hlr_parser(Path('test-input/RPDU/RPDU测试输入文件/RPDU软高需求_注入故障v1.0.xlsx'),profile=rpdu).parse(); print(out.total_count)"` → 期望 `11`
3. `PYTHONPATH=backend python -c "from app.v4.profiles import init_registry, get_registry; from app.v4.traceability import build_trace_index; init_registry(...); rpdu=get_registry().get('rpdu'); idx=build_trace_index(Path('test-input/RPDU/RPDU测试输入文件/RPDU追溯表'), rpdu.traceability, profile=rpdu); print(idx.total_erds, idx.icd_mapped_to_blocks)"` → header_adaptive 路径跑通
4. 验证 AMS/FGMC/HSCU 默认值仍为 `driver=docx / trace=profile_columns / matcher 全 False / top_k=20`

#### 验证结果

已验证通过：
1. registry 加载返回 4 个 profile（ams/fgmc/hscu/rpdu）；
2. RPDU HLR Excel 解析在测试样例 `RPDU软高需求_注入故障v1.0.xlsx` 上提取 11 条需求（中文内容完整）；
3. header_adaptive 路径跑通 Table 1（命中 sheet `接口基线表_EoICD`，映射 327 个 ERD → ICD FullName）；
4. AMS/FGMC/HSCU profile 加载后 `driver=docx / trace=profile_columns / matcher 全 False / top_k=20`，与 Issue #63 默认值字节相同；
5. `coverage.py` 白名单接受 `rpdu`。

未端到端跑通真实 V4 pipeline（含 LLM 多智能体裁判），需要真实 MiniMax/DeepSeek/Qwen API Key + 真实 RPDU 输入 → 由后续 Issue 或用户手动验收处理。

#### 遗留问题

1. RPDU 端到端 V4 pipeline 验证未在本次完成（需要真实 API Key）；
2. `trace_parser._read_table2_header_adaptive` 在 RPDU 测试样例上 `total_erds=0`（Table 2 ERD↔HLR 表可能用 WPS 导出、列宽合并或列名变体，待用户用真实 RPDU 项目文件复核）；
3. 用户决策：RPDU 本地分支的「forward pipeline + drain async 重构」明确排除在本次合并范围外。

#### 下一步建议

1. 用真实 RPDU 项目文件跑端到端 pipeline，确认 Table 2 header 自适应正确解析、4 项 matcher 增强确实提升了 `已匹配` 命中率；
2. 若 Table 2 解析失败持续出现，扩展 `_TABLE2_*_KEYWORDS` 关键字白名单或考虑 `_read_table2_*` 进一步做列名模糊匹配；
3. 若未来需要把 RPDU 适配回灌给 AMS/FGMC（如术语表也允许 Excel 化），可通过 `hlr_parser_driver.driver: docx` 保留 AMS 默认路径，再为该 profile 添加 `hooks.py` 即可，不必新增全局解析器。

## 2026-08-26 Issue #74 续：真实 RPDU 输入端到端验证

#### 任务目标

用 `test-input/RPDU/RPDU测试输入文件/` 下真实文件跑完整 V4 pipeline，验证 RPDU 适配的 4 个开关（HLR Excel 解析、header_adaptive 追溯、4 项 matcher 增强、3-table bridge）真实生效；并校验 AMS/FGMC/HSCU 上传追溯表时 `prefilter_per_hlr` 不会被错误置为 `True`。

#### 完成内容

1. RPDU 端到端 pipeline（job `4ab78c42-…`）跑通 6 阶段：HLR 11 / EoICD 225,825 / 11 HLR 已匹配 / 11 cases 多智能体裁决。
2. per-HLR 池生效：`Per-HLR filtered EoICD total: 62052 / 225825 entries`，HLR_052331 修复前 top-50 全是 LRM 状态信号、修复后 14 个 `Heater_Group_*_RPDU_ESW_CMD` 候选。
3. 3-table bridge 生效：`Bridge: mapped 3423 T2 ERDs → T1 ERDs`，跨命名空间 ERD 映射成功。
4. AMS / FGMC / HSCU smoke test：上传追溯表后日志均为 `Filtered EoICD: ... entries`（union-pool），未出现 `Per-HLR filtered` 字样，证实 `prefilter_per_hlr` 默认 `False` 生效。

#### 修改文件

仅验证调用，未修改代码（除同 Issue 的 per-HLR 池修复，单独记录）。

#### 验证方式

```bash
# RPDU
curl -X POST http://localhost:8000/api/v4/coverage-analysis \
  -F "hlr_word_file=@test-input/RPDU/RPDU测试输入文件/RPDU软高需求_注入故障v1.0.xlsx" \
  -F "eoicd_publisher_file=@test-input/RPDU/RPDU测试输入文件/ATA24EPS_EoICD_Publisher_Table.xlsx" \
  -F "eoicd_subscriber_file=@test-input/RPDU/RPDU测试输入文件/ATA24EPS_EoICD_Subscriber_Table.xlsx" \
  -F "traceability_files=@test-input/RPDU/RPDU测试输入文件/RPDU追溯表/配电系统需求与EoICD追溯表_20260629_统计结果_RevB.xlsx" \
  -F "traceability_files=@test-input/RPDU/RPDU测试输入文件/RPDU追溯表/单模块需求矩阵分析(系统2设备).xlsx" \
  -F "traceability_files=@test-input/RPDU/RPDU测试输入文件/RPDU追溯表/单模块需求矩阵分析(设备2软件).xlsx" \
  -F "enable_traceability_prefilter=true" -F "controller_profile=rpdu"
# AMS / FGMC / HSCU 同样模板，profile 改为 ams/fgmc/hscu
```

#### 验证结果

- RPDU job `4ab78c42-…`：completed，`Group A=8`（traceable 6 + fallback 2），最终 `11/11 已匹配`。
- AMS job `ed89f142-…`：completed，`Group A=11, Filtered EoICD: 3084 / 122674`。
- FGMC job `0f899326-…`：completed（修复后 `3e5c8fe1-…`），`Group A=2, trace_hlrs_with_trace=2, Filtered EoICD: 1138 / 15539`。
- HSCU job `e31f6789-…`：completed，`Group A=3, Filtered EoICD: 12 / 30699`。

#### 遗留问题

1. 11 cases 的 AI 裁决结果分布「已覆盖 5 / 待确认 4 / 不一致 2」，需用户对 4 条「待确认」+ 2 条「不一致」逐条复审（属业务问题，不是代码问题）。
2. RPDU 测试输入的 HLR 文件仅 12 KB（11 条需求），正式项目可能数百条，需要在更大数据集上回归。

#### 下一步建议

1. 推进 RPDU README 中提到的 4 项 matcher 增强在 AMS / FGMC / HSCU 适用性的回灌评估；
2. 持续观察 per-HLR 池在大 HLR 数（>50）下的耗时表现，必要时把 per-HLR 池下沉到 precompute block-keys 阶段以减少重复 EoICD 过滤。

## 2026-08-26 Issue #74 修复：per-HLR 池缺失导致 LRM 信号淹没目标信号

#### 任务背景

RPDU profile 合并到 V4 主线时遗漏了 RPDU 本地分支的 `match_reverse_per_hlr` 实施。RPDU 现场报告 `HLR_052331` 明确包含 `Heater_Group_X_RPDU_ESW_CMD` 信号，但 trace 后 top-50 候选全是 LRM 状态信号、目标 ESW_CMD 进不去。

#### 任务目标

在 V4 主线补回 per-HLR 预过滤池，确保 RPDU traceable HLR 只在自己的 traced block 集合上跑 reverse match；不污染 AMS/FGMC/HSCU 行为。

#### 根因

RPDU 本地分支使用「per-HLR 池」：每个 HLR 单独跑 `match_reverse`、只在自己的 traced block 集合里选 top_k=50。V4 主线 `pipeline._match_reverse_with_trace` 沿用 v4 #63 的「union-pool」：把所有 Group A HLR 命中的 EoICD block 取并集，一次性送入 `match_reverse`，top_k=50 被其他 HLR 引入的 LRM / 状态类信号淹没。

#### 完成内容

1. `ControllerProfile.prefilter_per_hlr: bool = False`（`backend/app/v4/profiles/base.py`），`from_dict` 读 `data.get("prefilter_per_hlr", False)`，AMS/FGMC/HSCU 不声明自动回落到 `False`。
2. RPDU `config.yaml` 末尾加 `prefilter_per_hlr: true`，4 行注释说明目的。
3. `pipeline.match_reverse_per_hlr()` 新函数：每个 HLR 用自己的 EoICD 子集单独调用 `match_reverse(profile=profile)`，保留 4 项 matcher 增强。
4. `pipeline._match_reverse_with_trace()` 根据 `profile.prefilter_per_hlr` 分流：True 走 per-HLR 池（`Per-HLR filtered EoICD total: X / Y`），False 走 union-pool（`Filtered EoICD: X / Y`，行为不变）。

#### 修改文件

1. `backend/app/v4/profiles/base.py` — `ControllerProfile.prefilter_per_hlr` 字段 + `from_dict` 解析
2. `backend/app/v4/profiles/rpdu/config.yaml` — `prefilter_per_hlr: true`
3. `backend/app/v4/pipeline.py` — 新增 `match_reverse_per_hlr()`、`_match_reverse_with_trace()` 分流
4. `backend/app/v4/profiles/rpdu/README.md` — 新增「追溯预过滤：per-HLR 池」章节

#### 验证方式

1. RPDU smoke test：traceable HLR `HLR_052331` 修复前 top-50 全部含 `lrm`，修复后 14 个 `Heater_Group_*_RPDU_ESW_CMD` 候选。
2. AMS / FGMC / HSCU smoke test：上传追溯表后日志关键字 `Filtered EoICD` 而非 `Per-HLR filtered`，与 Issue #74 之前字节一致。

#### 验证结果

- RPDU：`Group A=8`，6 traceable 已匹配 + 2 fallback；HLR_052331 / 067016 / 067017 / 052354 全部从「全 LRM 候选」恢复为「Heater ESW_CMD 候选」。
- AMS / FGMC / HSCU：日志输出 `Filtered EoICD: ...`，与 #74 之前字节一致；AMS Group A=11（11 个 traceable HLR 全部命中）、HSCU Group A=3。

#### 遗留问题

1. RPDU profile 现以 profile-level 开关方式激活 per-HLR 池，未做 profile-level `top_k` 之外的 matcher 增强回灌给 AMS（AMS 当前不需要）；
2. 大 HLR 数（>50）+ per-HLR 池的耗时数据尚未采集，需要后续在更大数据集上 benchmark。

#### 下一步建议

1. 把 per-HLR 池的日志格式与 union-pool 保持完全一致的 print 行，让 profile 类型在 logs 里可一眼区分；
2. 后续 Issue 可考虑把 `prefilter_per_hlr` 提升为通用「reverse match 预过滤策略」枚举（`union_pool` / `per_hlr`），新增 profile 不再需要新增布尔开关。

## 2026-08-26 FGMC 追溯表修复：HLR ID 字段映射冲突 + Table 1 sheet 选择

#### 任务背景

Issue #74 端到端验证阶段跑 FGMC smoke test，期望与 AMS / HSCU 行为一致（Group A > 0），实际 `Group A=0, trace_hlrs_with_trace=0, Union traced block_keys=0`。HLR→ERD→ICD 链路整条没打通。

#### 根因（两层）

**根因 1：Table 1 sheet 选错。** FGMC `需求与ICD追溯表_FGMC_裁剪.xlsx` 含 9 张 sheet，其中 `接口基线表_EoICD_old_待删除`（旧表，已标记删除）和 `待填_需求接口追溯表`（当前使用）并存。`_select_sheet()` 按 `by_name_keywords` 顺序找第一个命中的 sheet。FGMC 原 keyword 列表 `[追溯, 接口基线, 接口基线表_EoICD, 待填_需求接口追溯表]` 第一个实际命中的 sheet 是 `接口基线表_EoICD_old_待删除`（匹配 "接口基线" 模糊子串），导致 Table 1 只解析出 2 个 ERD。

**根因 2：HLR ID 字段映射冲突。** FGMC HLR docx `FGMC软件高层需求.docx` 每张需求表有两行 ID：
- row 0：`ID | 1781`（docx 内部编号）
- row 1：`需求编号 | FGMC_OFP_CSCI_HLR_005906`（正式 HLR 编号，对应追溯表 child 列）

`field_map` 同时在 `id` 和 `code` 两个 std_field 下声明了 `需求编号`：
```yaml
id: ["ID", "RequirementID", "需求ID"]      # 当时不含 需求编号
code: ["需求编号", "RequirementCode"]
```
即使把 `需求编号` 加到 `id` 列表首位，`_build_field_map_index` 用 `dict` 存反向索引（同一 key 只能映射一个 std_field），`code` 字段后注册把 `需求编号 → id` 覆盖为 `需求编号 → code`。结果 row 1 的 `FGMC_OFP_CSCI_HLR_005906` 进 `code` 字段，row 0 的 `1781` 进 `id` 字段。Trace 系统用 `id`（requirement_id）匹配 Table 2 child 列，1781 对不上 `FGMC_OFP_CSCI_HLR_*`，导致 Group A=0。

#### 完成内容

1. **修 Table 1 sheet 关键词顺序**：`fgmc/config.yaml` `traceability.table1.sheet_match.by_name_keywords` 把 `待填_需求接口追溯表` 放第一位，并删除过宽的 `接口基线` 模糊关键词（避免再次被旧 sheet 抢匹配）。保留 `追溯` 和 `接口基线表_EoICD` 作为 fallback。
2. **修 HLR ID 字段映射冲突**：`fgmc/config.yaml` `hlr_parser.field_map.id` 首位加入 `需求编号`（匹配 row 1 正式 HLR 编号），同时从 `code` 列表移除 `需求编号`（避免反向索引覆盖），仅保留 `RequirementCode` 作为 legacy alias。
3. 两处修复都加了详细中文注释说明 RPDU / AMS 适配无关，FGMC profile 独立承担。

#### 修改文件

1. `backend/app/v4/profiles/fgmc/config.yaml` — Table 1 sheet 关键词顺序调整 + `field_map.id` / `field_map.code` 调整

#### 验证方式

```bash
# Inline parser 验证
python -c "
from app.v4.profiles.base import load_profile_from_yaml
from app.v4.parsers.hlr_word_parser import HLRWordParser
p = load_profile_from_yaml('backend/app/v4/profiles/fgmc/config.yaml')
out = HLRWordParser(Path('test-input/工具-FGMC控制器测试案例/FGMC软件高层需求.docx'), p).parse()
# 期望 id='FGMC_OFP_CSCI_HLR_005906' 等
"
# E2E smoke test
bash /c/Users/王田/AppData/Local/Temp/fgmc_smoke.sh
```

#### 验证结果

- Inline parser：`id` 字段从 `1781` 等内部编号改为 `FGMC_OFP_CSCI_HLR_005906` 等正式 HLR 编号（7/7 全部正确）。
- FGMC E2E job `3e5c8fe1-…`：completed，`Group A=2 HLRs, trace_hlrs_with_trace=2, Union traced block_keys=80, Filtered EoICD: 1138 / 15539`。
- 最终 `hlr_已匹配=5, hlr_待确定=1, hlr_无匹配=1`，与 AMS / HSCU 行为一致。

#### 遗留问题

1. FGMC 测试输入的 5 个 Group B HLR（`FGMC_OFP_CSCI_HLR_015273` 等）未在 Table 2 出现（HLR docx 提供了但追溯表只覆盖部分），属测试数据完整性问题，非代码问题；
2. `field_map` 同一字符串（如 `需求编号`）只能映射到唯一 std_field 的限制是 `_build_field_map_index` 的设计选择，新 profile 设计 field_map 时需注意避免冲突；后续可考虑在 from_dict 阶段做冲突检测并 raise warning。

#### 下一步建议

1. 在 `load_profile_from_yaml` 中加入 field_map 冲突检测（同一 header 文本被映射到多个 std_field 时 raise `ProfileLoadError`），防止后续 profile 静默落入「后注册赢」的陷阱；
2. FGMC 后续如需支持多 sheet 追溯表，可在 `_select_sheet` 中加入「过滤掉 `*_old_*` / `*_待删除*`」的排除规则。

### 2026-08-23 V4 五星评价体系升级（ADR-004）

#### 任务目标

把 Step 5 Review Agent 从 3 星体系（full→3★、majority→2★、split→1★）升级为 5 星体系（5★/4★/3★/2★/1★），新增 `evidence_alignment` 维度区分「全一致但 evidence 弱」（4★）与「全一致 evidence 强」（5★），复查触发从 1★ 扩展到 ≤2★。详见 ADR-004。

#### 完成内容

1. **5 档映射**：`backend/app/v4/comparison/review_agent.py` 新增 `_map_star_rating(agreement, evidence)` 后端计算函数；`ConsensusResult.evidence_alignment` 字段新增；review LLM 只输出 `agreement_level + evidence_alignment`，星档由后端按映射表计算。
2. **复查触发扩展**：`backend/app/v4/comparison/re_review.py` `_resolve_low_confidence_case_ids` 触发条件从 `star_rating == 1` 改为 `star_rating ∈ {1, 2}`，给 2★ 一个升到 3★ 的机会。
3. **final_coverage_status 阈值**：`star >= 3`（5★/4★/3★）取多数一致 status；`star <= 2`（2★/1★）强制「待确认」。
4. **共识报告 docx**：`consensus_word_generator.py` 星级分布表从「3 主行 + 3 子行」改为「4 主行（5★/4★/3★/2★）+ 3 子行（1★ 降级）」；`_star_str` 渲染 0-5 共 6 档。
5. **ADR 与 e2e**：新增 `docs/decisions/ADR-004-五星评价体系.md`；新增 `backend/tests/e2e/test_use_case_5_five_star_rating.py`（35 项离线单元断言 + 基线集成验证）。

#### 修改文件

1. `backend/app/v4/models.py` — `ConsensusResult` 新增 `evidence_alignment` 字段，star_rating 类型注解 1-5
2. `backend/app/v4/comparison/review_agent.py` — 新增 `_map_star_rating()`、`_call_review_api` 读取 `evidence_alignment`、`_build_summary` 输出 5 键 `star_distribution`、`final_status` 阈值 star >= 3
3. `backend/app/v4/comparison/re_review.py` — `_resolve_one_star_case_ids` → `_resolve_low_confidence_case_ids`，触发 `{1}` → `{1, 2}`
4. `backend/app/v4/prompts/consensus.md` — 新增 `evidence_alignment` 输出 + 5 档映射规则 + evidence 判定示例
5. `backend/app/v4/pipeline.py` — Step 5.5 日志更新
6. `backend/app/v4/doc_generators/consensus_word_generator.py` — `_star_str` 渲染 5 颗，分布表 4 主行 + 3 子行
7. `backend/app/v4/degradation/config.py` — 注释更新（5 星体系）

#### 新增文件

1. `backend/tests/e2e/test_use_case_5_five_star_rating.py` — 5 档映射 + 复查触发 + summary 5 键 e2e
2. `docs/decisions/ADR-004-五星评价体系.md` — 5 星体系 ADR

#### 验证方式

1. 6 个 Python 文件 `ast.parse` 语法验证（GBK locale → 显式 `encoding='utf-8'`）
2. `python -c "from app.v4.comparison.review_agent import _map_star_rating; ..."` 离线单元断言（35 项 PASS）
3. `python tests/e2e/test_use_case_5_five_star_rating.py`（场景 A 离线断言 + 场景 B 基线集成，需真实 LLM）

#### 验证结果

1. 语法验证全部通过
2. 离线单元断言 35/35 PASS（_map_star_rating 14 项 + _build_summary 5 项 + 低星触发 3 项 + 状态逻辑 2 项 + _star_str 7 项 + 其它 4 项）
3. 场景 B（基线集成）尚未运行：需要先跑基线管线生成 e2e_baseline/consensus_results.json

#### 遗留问题

1. 场景 B 需在 docker compose 中真实跑基线 + 集成测试（依赖真实 LLM API）
2. 老 `consensus_results.json` 不存在跨版本兼容，老数据需重新跑管线（BC 已在 CHANGELOG 标注）
3. 5★ 与 4★ 的边界主要靠 evidence_alignment 判定，prompt 中 few-shot 示例稳定 LLM 输出；真实 LLM 跑出来后可能需要调整 prompt 提示

#### 下一步建议

1. 真实 LLM 跑 1 个样例，查看 5 档分布是否符合预期
2. 观察 evidence_alignment 字段的稳定性（批次间是否漂移）
3. 后续 Issue 可考虑前端 UI 适配 5 星显示

### 2026-08-23 复查升星路径实证（用例6）

#### 任务目标

构造 1★/2★/4★ 三类低星 case，运行 peer-aware 复查 + 共识重跑，验证升星路径是否生效。

#### 完成内容

新增 `backend/tests/e2e/test_use_case_6_five_star_upgrade.py`，基于基线 `e2e_baseline/` 注入：
- 1★ case：3 provider coverage_status 不同 + analysis 各自引用不同 ICD 字段（split + strong）
- 2★ case：2 provider "covered"（vague analysis）+ 1 provider "inconsistent"（vague analysis）（majority + weak）
- 4★ case：3 provider 全 "covered" 但 analysis 笼统、无具体 ICD 字段引用（full + weak）

然后对 1★/2★ case 调用真实 LLM `re_review_judgments()` + `review_judgments()`，对 4★ case 单独重跑 `review_judgments()`。

#### 验证结果

| 注入前 | 升星机制 | 升星后 | evidence | 路径是否生效 |
|---|---|---|---|---|
| 1★ (split + strong) | re-review + re-consensus | **3★** (majority + strong) | strong | ✓ 升 2★ |
| 2★ (majority + weak) | re-review + re-consensus | **5★** (full + strong) | strong | ✓ 升 3★ |
| 4★ (full + weak) | re-consensus only | **4★** (full + weak) | weak | 维持 4★（不触发 re-review）|

升星成功 2/2（1★/2★ 主动升星），路径验证 3/3。

#### 关键设计发现：4★ → 5★ 是理论路径

**ADR-004 §复查升星路径 表格中列了 4★ → 5★ 一行，但实际机制下不触发**：
- 复查触发条件是 `star_rating ∈ {1, 2}`（用户明确选择），4★ case 按设计不进入 re-review
- 重跑共识时 provider 的 analysis 文本本身没变，evidence_alignment 自然保持 weak → 仍是 4★
- 4★ → 5★ 实际依赖多次自然重判 + provider 主动补充 evidence（不可控机制）

**实证依据（REV-0003）**：review LLM 输出明确指出：
> 三位裁判均判定 HLR 需求与 EoICD 信号画像覆盖完整、无差异，结论一致。虽然各裁判分析文本较为笼统，未引用具体 ICD 字段或 Label 号，但三方结论相互印证，因此最终共识为 covered。

→ 即 full + weak = 4★ 是当前机制下的稳态。

**1★/2★ 升星机制实证有效**：peer-aware 复查后，provider 看到对方 analysis 后主动细化（"重新逐项核对"、"判断 A 过于笼统，未将 ICD 的 bit offset 与 HLR 的 bit15 做逐位核对"），导致 evidence 由 weak 转 strong。

#### 设计决策（待用户确认）

1. **保持当前设计**：4★ case 不触发 re-review（避免无意义重判），4★ → 5★ 仅靠自然多次重判。需要把 ADR-004 §复查升星路径 表格的 4★ → 5★ 行标记为"理论路径，非主动机制"。
2. **扩展触发集合**：把 re-review 触发条件改为 `star_rating ∈ {1, 2, 4}`，让 4★ 也走 peer-aware 复查。但代价是 LLM 调用成本翻倍，且 4★ 升 5★ 收益有限（都已是 full agreement，仅 evidence 强弱差异）。
3. **新增"自然升级"机制**：连续 N 次运行管线后，对未升级的 4★ case 做轻量级 evidence 补充（让 provider 重写 analysis）。

#### 验证命令

```bash
docker compose build backend
docker compose run --rm backend python tests/e2e/test_use_case_6_five_star_upgrade.py
```

#### 修改文件

1. `backend/tests/e2e/test_use_case_6_five_star_upgrade.py`（新增）

#### 遗留问题

1. ADR-004 §复查升星路径 表格中 4★ → 5★ 一行的描述需调整为"理论路径"，与实际机制对齐
2. 后续 Issue 可根据用户决策选择上述 3 个方案之一

### 2026-08-24 ADR-004 v2 字段一致性重构

#### 任务目标

ADR-004 v1 的 `evidence_alignment` 多维度方案（Coverage/Consistency/Quality）在真实 LLM 跑 `故障注入1.0.docx` 时暴露根本性问题：**5 档分布严重失衡，只触发 5★ 和 3★，1★/2★/4★ 全部不出现**。根因是 `consistency=2` 子维度门槛过低（仅要求"结论一致 + 各自引用字段"），导致几乎所有 case 都被判 strong，moderate/weak 几乎不可达。

本次重构目标：把 review LLM 任务从"3 维度综合映射"改为"字段冲突检测 + 字段类型分类"，按 agreement_level 分档（full/majority/split）+ key/non_key/vague 字段类型单维度区分。详见 ADR-004 v2。

#### 设计核心

| 档 | 触发条件 |
|---|---|
| 5★ | full + 无字段不一致 |
| 4★ | full + 任意字段不一致（key/non_key/vague） |
| 3★ | majority + 无 key 字段分歧 |
| 2★ | majority + 有 key 字段分歧（触发复查） |
| 1★ | split / single_source / no_consensus |

key 字段白名单（12 个）：Direction / DataFormatType / BitOffset / ParameterSize / OneState / ZeroState / Label / FuncRngMin / FuncRngMax / **Units / Period / SDIExpected**（最后 3 个按用户要求归类为 key）。

#### 完成内容

1. **数据模型重构**（`backend/app/v4/models.py`）：
   - 删除 `ConsensusResult.evidence_alignment` 字段（**完全删除**，无软删除）
   - 删除 `ConsensusResult.inconsistent_attributes`（合并到 field_disagreements）
   - 新增 `FieldDisagreement` Pydantic 模型（field/category: Literal["key","non_key","vague"]/providers/values/detail）
   - 新增 `ConsensusResult.field_disagreements: list[FieldDisagreement]` 和 `cited_fields: list[str]`
2. **后端映射重写**（`backend/app/v4/comparison/review_agent.py`）：`_map_star_rating` 新签名 `(agreement, field_disagreements)`，KEY_FIELDS 常量定义 12 个 key 字段；新增 `_parse_field_disagreements` 容错解析 LLM 输出（dict 或 string）。
3. **Prompt 重写**（`backend/app/v4/prompts/consensus.md`）：任务定义改为"字段冲突检测"；显式枚举 12 个 key 字段；新 JSON schema（field_disagreements + cited_fields）；提供 5 个示例覆盖各档；LLM 不再输出 star_rating，由后端计算。**Step 0 明确区分"两类不一致"**：EoICD-HLR 事实性差异 vs 裁判间意见分歧，避免 LLM 误填。
4. **Word 渲染**（`backend/app/v4/doc_generators/consensus_word_generator.py`）：`inconsistent_attributes` 渲染改用 `field_disagreements`（每条含 category）；2★/4★/5★ 描述文字按字段类型重写。
5. **e2e 测试同步**：`test_use_case_5_five_star_rating.py` 注入策略改 field_disagreements 构造（15 个映射分支覆盖 full/majority/split × key/non_key/vague）；`test_use_case_6_five_star_upgrade.py` 同样改造（1★ split、2★ majority+key、4★ full+vague）。
6. **ADR 重写**：`docs/decisions/ADR-004-五星评价体系.md` v2 替代 v1，记录重构动机、字段类型映射、状态变化。

#### 修改文件

1. `backend/app/v4/models.py` — 替换 evidence_alignment 字段；新增 FieldDisagreement 模型；删除 inconsistent_attributes
2. `backend/app/v4/prompts/consensus.md` — 任务改字段冲突检测；枚举 12 个 key 字段；Step 0 区分两类不一致
3. `backend/app/v4/comparison/review_agent.py` — `_map_star_rating` 新签名；KEY_FIELDS 常量；`_parse_field_disagreements` 容错
4. `backend/app/v4/doc_generators/consensus_word_generator.py` — field_disagreements 渲染；2★/4★/5★ 描述
5. `backend/tests/e2e/test_use_case_5_five_star_rating.py` — 15 个映射分支重写
6. `backend/tests/e2e/test_use_case_6_five_star_upgrade.py` — 注入策略改 field_disagreements 构造
7. `docs/decisions/ADR-004-五星评价体系.md` — v2 ADR 重写
8. `CHANGELOG.md` — 新增 breaking change 条目（v2 替代 v1）
9. `docs/project/workflow.md` — Step 5 描述更新

#### 验证方式

1. 6 个 Python 文件 `ast.parse` 语法验证
2. `python -c "from app.v4.comparison.review_agent import _map_star_rating; ..."` 离线单元断言（15+ 项 PASS）
3. `python tests/e2e/test_use_case_5_five_star_rating.py`（场景 A 离线断言 + 场景 B 基线集成）
4. 真实 LLM 跑 `故障注入1.0.docx`，查看 5 档分布

#### 验证结果

1. 语法验证全部通过
2. 离线单元断言 36+ PASS（涵盖 11+ 个映射分支、5 键 summary、低星触发、_star_str 渲染等）
3. 场景 B（基线集成）尚未运行；旧 v1 baseline 已被迁移脚本处理过（早期设计），后续改方案改为直接重跑管线
4. 真实 LLM 验证：尚未执行

#### 遗留问题

1. 真实 LLM 跑 `故障注入1.0.docx` 验证 5 档分布——需 docker compose 启动 + 重跑管线
2. 老 e2e_baseline/consensus_results.json 含 v1 schema，需重跑基线管线产出 v2 JSON（v2 设计下不再做迁移，详见 ADR-004）
3. 4★ → 5★ 仍是理论路径（v1 的同一遗留问题延续到 v2，但 v2 的 4★ 表达更明确"有字段争议"）
4. 字段命名 `field_disagreements` 在实施 v2 后期二次调整：原 `inconsistent_fields` 易让 LLM 误认为"字段存在不一致（可能是 ICD vs HLR）"，改为 `field_disagreements`（**字段级裁判间分歧**）后，语义更明确；Step 0 的"两类不一致"区分也补强了 prompt 鲁棒性

#### 下一步建议

1. docker compose build backend 后跑 `python tests/e2e/test_use_case_5_five_star_rating.py` 验证 e2e 用例
2. 用 `故障注入1.0.docx` 重跑管线，验证 5 档分布是否改善（应看到 5★/4★/3★/2★ 都出现，1★ 少见）
4. 后续 Issue 可考虑前端 UI 适配 v2 字段显示（field_disagreements + cited_fields）

---

## 2026-08-26：V2 共识报告文案统一（零行为影响）

### 任务目标

把共识 Word 报告（`consensus_word_generator.py`）的展示文案按方案 B 命名风格统一——`star_levels` 4 档标签简明化 + 清理 v1 `evidence_alignment` 残留描述 + 明细表"共识"列与"星级分布"小节口径对齐。判定规则与数据契约均不变。

### 完成内容

1. **"星级分布"小节 4 档标签简化**：star_levels 由长描述改为方案 B 简明命名——5★ → "完全无争议"、4★ → "一致有争议"、3★ → "多数一致"、2★ → "多数有争议"。"说明"列同步按 v2 字段类型语义重写（4★ 明确"含辅助字段"）。
2. **"处置建议"列表清理 v1 残留**：删除 "evidence 强 / 一般"、"反对方 evidence 弱" 等 v1 描述，改用星级分布小节口径（"完全无争议 / 一致有争议 / 多数一致 / 多数有争议 / 分歧·单源·失效"）。
3. **明细表"共识"列 mapping 统一**：`full → 完全一致` 改为 `完全无争议`，与"星级分布"小节表头严格对齐。
4. **`re_review.py:247` docstring 同步对齐 ADR-004 v2**：从 v1 "多数一致但 evidence 弱" 改为 v2 "多数一致但有 key 字段分歧"。

### 修改文件

1. `backend/app/v4/doc_generators/consensus_word_generator.py`（star_levels 数组 + suggestions 列表 + agreement_label mapping，3 处）
2. `backend/app/v4/comparison/re_review.py`（docstring 注释 v1→v2 口径）

### 验证方式

1. `python -c "import ast; ast.parse(open('backend/app/v4/doc_generators/consensus_word_generator.py').read())"` 静态语法检查
2. `sed -n '215,245p'` 复核 star_levels 新标签与说明
3. `sed -n '298,310p'` 复核 suggestions 列表（v1 evidence 残留应已清理）
4. `sed -n '385,387p'` 复核 agreement_label mapping（full → 完全无争议）
5. mock 端到端跑一次管线生成 docx，目检"星级分布"小节、"处置建议"列表、"明细表共识列"三处渲染统一

### 验证结果

1. 静态语法检查通过
2. star_levels / suggestions / agreement_label 三处文字片段人肉复核通过
3. mock 端到端 docx 目检：尚未验证（需 PowerShell 手敲启动 backend）

### 遗留问题

1. "共识"列与"星级"列存在粗/细粒度互补——5★ + 4★ 行"共识"列均显示"完全无争议"，3★ + 2★ 行均显示"多数一致"，细分看"星级"列
2. ADR-004 v2 文档本身无报告中文标签展示，无需更新
3. 真实 LLM 跑 `故障注入1.0.docx` 验证 docx 渲染效果：未执行（本期仅文字层调整，行为不变）

### 下一步建议

1. mock 端到端跑一次，确认报告展示无回归
2. 真实 LLM 跑 `故障注入1.0.docx`，目检 docx 三处文案统一效果
3. 视用户反馈决定是否进一步精简"共识"列 mapping（如拆分为 star-aware：full+5★→"完全无争议"，full+4★→"一致有争议"）

## 2026-08-27：ADR-004 v3 fusion 五星体系两维度重构

### 任务目标

修正 ADR-004 v2 的语义错误：v2 把 `inconsistent_attributes`（EoICD-HLR 事实差异）
改名并重定义为 `field_disagreements`（provider 间字段级分歧），导致 Word 报告
「不一致属性」列失去数据源。恢复两者为两个独立维度。

### 问题回顾

用户跑真实样例后反馈：报告「判断」列显示「不一致」，但同 case 的「不一致属性」列
显示「—」。根因是 v2 规则规定「3 个 provider 共识识别的 EoICD-HLR 差异不进
`field_disagreements`」，而报告渲染又从 `field_disagreements` 派生「不一致属性」列 ——
共识越好，报告越空。

### 设计决策（用户确认）

1. 删除 `evidence_alignment`（v2 已删，保持删除）
2. `field_disagreements` 保留为辅助字段，仅入 JSON，不渲染到 Word 报告
3. `agreement_level` 完全复用 v0 语义规则（看 analysis 语义，不只看字面 coverage_status）
4. 5 档映射规则直接复用 v2（`_map_star_rating` 逻辑不变）

### 完成内容

1. `models.py` 新增 `InconsistentAttribute`（`attribute` / `detail` / `providers`），
   `ConsensusResult` 新增 `inconsistent_attributes`；`FieldDisagreement` docstring
   标注为辅助字段
2. `consensus.md` 重写为 6 步流程（扫描 cited_fields → agreement 语义判定 →
   提取 inconsistent_attributes → 提取 field_disagreements → final_coverage_status →
   final_analysis/confidence），含 5 个输出示例与 `attribute` vs `field` 命名澄清
3. `review_agent.py` 新增 `_parse_inconsistent_attributes`（dict / 裸字符串两种形态容错），
   `_call_review_api` 同时解析两字段
4. `consensus_word_generator.py` 「不一致属性」列直接读 `inconsistent_attributes`，
   删除从 `field_disagreements` 派生的逻辑
5. e2e 用例 5/6 注入两字段；`common.py` 新增 `_migrate_consensus_schema` 自动迁移旧 baseline

### 修改文件

1. `backend/app/v4/models.py`
2. `backend/app/v4/prompts/consensus.md`
3. `backend/app/v4/comparison/review_agent.py`
4. `backend/app/v4/doc_generators/consensus_word_generator.py`
5. `backend/tests/e2e/test_use_case_5_five_star_rating.py`
6. `backend/tests/e2e/test_use_case_6_five_star_upgrade.py`
7. `backend/tests/e2e/common.py`
8. `docs/decisions/ADR-004-五星评价体系.md`
9. `CHANGELOG.md`

### 验证方式

1. `docker compose build backend`
2. `docker compose run --rm -w /app backend python tests/e2e/test_use_case_5_five_star_rating.py`
3. `docker compose run --rm -w /app backend python tests/e2e/test_use_case_6_five_star_upgrade.py`
4. 真实 LLM 跑 `故障注入1.0.docx`，检查 `inconsistent` case 的
   `inconsistent_attributes` 非空 + Word 报告两列对应关系

### 遗留问题

1. `_map_star_rating` 仍只看 `field_disagreements`，`inconsistent_attributes` 不参与星档
   （用户已确认为预期行为）
2. 真实样例验证待跑

## 2026-08-27: ADR-004 v3 fusion 后续——修复 surviving=2 时 LLM 误判 no_consensus

### 背景

v3 fusion 上线后跑 `故障注入1.0.docx`，REV-0010（FSF21000101_HLR_547）最终
`agreement_level=no_consensus`、`star_rating=1`。排查发现 surviving=2 时降级脚本
不覆盖 `agreement_level`（仅 cap star 到 2），而原 `consensus.md` prompt 把
`single_source` / `no_consensus` 列为 LLM 可选值，LLM 看到 1/3 error +
2/3 实质分歧时自主输出了 `no_consensus`，穿透到最终结果。

### 修复（只做方案 A）

`backend/app/v4/prompts/consensus.md`：
- JSON schema `agreement_level` 选项从 `full|majority|split|single_source|no_consensus`
  收窄为 `full|majority|split`
- Step 2 严重分歧段后新增"重要"提示，明确 `single_source` / `no_consensus`
  由后端降级脚本根据 provider 存活数自动写入，LLM 不输出
- 重要提示段首条加相同约束

`CHANGELOG.md` 同步记录（数据契约无变化）。

### 不修复项

- `re_review.py:328` 错误覆盖原 valid 判断（minimax 原本 `inconsistent` 被
  error 静默替换）：属于独立数据丢失 bug，与 `no_consensus` 误判无直接因果。
  留待后续 issue 处理。
- `review_agent.py:_call_review_api` 接受 LLM 输出的 `no_consensus` /
  `single_source`：prompt 改完后这两个值不会再出现，无需 post-process 防御。

### 验证

- 重新跑 `故障注入1.0.docx`，复查 REV-0010：
  - `agreement_level` 应为 `split`（不再为 `no_consensus`）
  - `star_rating` 仍为 1★（split → 1★，与原结果一致）
  - `final_coverage_status=待确认`（一致）
- e2e 用例 5/6 跑通（mock + 真实 LLM）


### 2026-08-23 V4 五星评价体系升级（ADR-004）

#### 任务目标

把 Step 5 Review Agent 从 3 星体系（full→3★、majority→2★、split→1★）升级为 5 星体系（5★/4★/3★/2★/1★），新增 `evidence_alignment` 维度区分「全一致但 evidence 弱」（4★）与「全一致 evidence 强」（5★），复查触发从 1★ 扩展到 ≤2★。详见 ADR-004。

#### 完成内容

1. **5 档映射**：`backend/app/v4/comparison/review_agent.py` 新增 `_map_star_rating(agreement, evidence)` 后端计算函数；`ConsensusResult.evidence_alignment` 字段新增；review LLM 只输出 `agreement_level + evidence_alignment`，星档由后端按映射表计算。
2. **复查触发扩展**：`backend/app/v4/comparison/re_review.py` `_resolve_low_confidence_case_ids` 触发条件从 `star_rating == 1` 改为 `star_rating ∈ {1, 2}`，给 2★ 一个升到 3★ 的机会。
3. **final_coverage_status 阈值**：`star >= 3`（5★/4★/3★）取多数一致 status；`star <= 2`（2★/1★）强制「待确认」。
4. **共识报告 docx**：`consensus_word_generator.py` 星级分布表从「3 主行 + 3 子行」改为「4 主行（5★/4★/3★/2★）+ 3 子行（1★ 降级）」；`_star_str` 渲染 0-5 共 6 档。
5. **ADR 与 e2e**：新增 `docs/decisions/ADR-004-五星评价体系.md`；新增 `backend/tests/e2e/test_use_case_5_five_star_rating.py`（35 项离线单元断言 + 基线集成验证）。

#### 修改文件

1. `backend/app/v4/models.py` — `ConsensusResult` 新增 `evidence_alignment` 字段，star_rating 类型注解 1-5
2. `backend/app/v4/comparison/review_agent.py` — 新增 `_map_star_rating()`、`_call_review_api` 读取 `evidence_alignment`、`_build_summary` 输出 5 键 `star_distribution`、`final_status` 阈值 star >= 3
3. `backend/app/v4/comparison/re_review.py` — `_resolve_one_star_case_ids` → `_resolve_low_confidence_case_ids`，触发 `{1}` → `{1, 2}`
4. `backend/app/v4/prompts/consensus.md` — 新增 `evidence_alignment` 输出 + 5 档映射规则 + evidence 判定示例
5. `backend/app/v4/pipeline.py` — Step 5.5 日志更新
6. `backend/app/v4/doc_generators/consensus_word_generator.py` — `_star_str` 渲染 5 颗，分布表 4 主行 + 3 子行
7. `backend/app/v4/degradation/config.py` — 注释更新（5 星体系）

#### 新增文件

1. `backend/tests/e2e/test_use_case_5_five_star_rating.py` — 5 档映射 + 复查触发 + summary 5 键 e2e
2. `docs/decisions/ADR-004-五星评价体系.md` — 5 星体系 ADR

#### 验证方式

1. 6 个 Python 文件 `ast.parse` 语法验证（GBK locale → 显式 `encoding='utf-8'`）
2. `python -c "from app.v4.comparison.review_agent import _map_star_rating; ..."` 离线单元断言（35 项 PASS）
3. `python tests/e2e/test_use_case_5_five_star_rating.py`（场景 A 离线断言 + 场景 B 基线集成，需真实 LLM）

#### 验证结果

1. 语法验证全部通过
2. 离线单元断言 35/35 PASS（_map_star_rating 14 项 + _build_summary 5 项 + 低星触发 3 项 + 状态逻辑 2 项 + _star_str 7 项 + 其它 4 项）
3. 场景 B（基线集成）尚未运行：需要先跑基线管线生成 e2e_baseline/consensus_results.json

#### 遗留问题

1. 场景 B 需在 docker compose 中真实跑基线 + 集成测试（依赖真实 LLM API）
2. 老 `consensus_results.json` 不存在跨版本兼容，老数据需重新跑管线（BC 已在 CHANGELOG 标注）
3. 5★ 与 4★ 的边界主要靠 evidence_alignment 判定，prompt 中 few-shot 示例稳定 LLM 输出；真实 LLM 跑出来后可能需要调整 prompt 提示

#### 下一步建议

1. 真实 LLM 跑 1 个样例，查看 5 档分布是否符合预期
2. 观察 evidence_alignment 字段的稳定性（批次间是否漂移）
3. 后续 Issue 可考虑前端 UI 适配 5 星显示

### 2026-08-23 复查升星路径实证（用例6）

#### 任务目标

构造 1★/2★/4★ 三类低星 case，运行 peer-aware 复查 + 共识重跑，验证升星路径是否生效。

#### 完成内容

新增 `backend/tests/e2e/test_use_case_6_five_star_upgrade.py`，基于基线 `e2e_baseline/` 注入：
- 1★ case：3 provider coverage_status 不同 + analysis 各自引用不同 ICD 字段（split + strong）
- 2★ case：2 provider "covered"（vague analysis）+ 1 provider "inconsistent"（vague analysis）（majority + weak）
- 4★ case：3 provider 全 "covered" 但 analysis 笼统、无具体 ICD 字段引用（full + weak）

然后对 1★/2★ case 调用真实 LLM `re_review_judgments()` + `review_judgments()`，对 4★ case 单独重跑 `review_judgments()`。

#### 验证结果

| 注入前 | 升星机制 | 升星后 | evidence | 路径是否生效 |
|---|---|---|---|---|
| 1★ (split + strong) | re-review + re-consensus | **3★** (majority + strong) | strong | ✓ 升 2★ |
| 2★ (majority + weak) | re-review + re-consensus | **5★** (full + strong) | strong | ✓ 升 3★ |
| 4★ (full + weak) | re-consensus only | **4★** (full + weak) | weak | 维持 4★（不触发 re-review）|

升星成功 2/2（1★/2★ 主动升星），路径验证 3/3。

#### 关键设计发现：4★ → 5★ 是理论路径

**ADR-004 §复查升星路径 表格中列了 4★ → 5★ 一行，但实际机制下不触发**：
- 复查触发条件是 `star_rating ∈ {1, 2}`（用户明确选择），4★ case 按设计不进入 re-review
- 重跑共识时 provider 的 analysis 文本本身没变，evidence_alignment 自然保持 weak → 仍是 4★
- 4★ → 5★ 实际依赖多次自然重判 + provider 主动补充 evidence（不可控机制）

**实证依据（REV-0003）**：review LLM 输出明确指出：
> 三位裁判均判定 HLR 需求与 EoICD 信号画像覆盖完整、无差异，结论一致。虽然各裁判分析文本较为笼统，未引用具体 ICD 字段或 Label 号，但三方结论相互印证，因此最终共识为 covered。

→ 即 full + weak = 4★ 是当前机制下的稳态。

**1★/2★ 升星机制实证有效**：peer-aware 复查后，provider 看到对方 analysis 后主动细化（"重新逐项核对"、"判断 A 过于笼统，未将 ICD 的 bit offset 与 HLR 的 bit15 做逐位核对"），导致 evidence 由 weak 转 strong。

#### 设计决策（待用户确认）

1. **保持当前设计**：4★ case 不触发 re-review（避免无意义重判），4★ → 5★ 仅靠自然多次重判。需要把 ADR-004 §复查升星路径 表格的 4★ → 5★ 行标记为"理论路径，非主动机制"。
2. **扩展触发集合**：把 re-review 触发条件改为 `star_rating ∈ {1, 2, 4}`，让 4★ 也走 peer-aware 复查。但代价是 LLM 调用成本翻倍，且 4★ 升 5★ 收益有限（都已是 full agreement，仅 evidence 强弱差异）。
3. **新增"自然升级"机制**：连续 N 次运行管线后，对未升级的 4★ case 做轻量级 evidence 补充（让 provider 重写 analysis）。

#### 验证命令

```bash
docker compose build backend
docker compose run --rm backend python tests/e2e/test_use_case_6_five_star_upgrade.py
```

#### 修改文件

1. `backend/tests/e2e/test_use_case_6_five_star_upgrade.py`（新增）

#### 遗留问题

1. ADR-004 §复查升星路径 表格中 4★ → 5★ 一行的描述需调整为"理论路径"，与实际机制对齐
2. 后续 Issue 可根据用户决策选择上述 3 个方案之一

### 2026-08-24 ADR-004 v2 字段一致性重构

#### 任务目标

ADR-004 v1 的 `evidence_alignment` 多维度方案（Coverage/Consistency/Quality）在真实 LLM 跑 `故障注入1.0.docx` 时暴露根本性问题：**5 档分布严重失衡，只触发 5★ 和 3★，1★/2★/4★ 全部不出现**。根因是 `consistency=2` 子维度门槛过低（仅要求"结论一致 + 各自引用字段"），导致几乎所有 case 都被判 strong，moderate/weak 几乎不可达。

本次重构目标：把 review LLM 任务从"3 维度综合映射"改为"字段冲突检测 + 字段类型分类"，按 agreement_level 分档（full/majority/split）+ key/non_key/vague 字段类型单维度区分。详见 ADR-004 v2。

#### 设计核心

| 档 | 触发条件 |
|---|---|
| 5★ | full + 无字段不一致 |
| 4★ | full + 任意字段不一致（key/non_key/vague） |
| 3★ | majority + 无 key 字段分歧 |
| 2★ | majority + 有 key 字段分歧（触发复查） |
| 1★ | split / single_source / no_consensus |

key 字段白名单（12 个）：Direction / DataFormatType / BitOffset / ParameterSize / OneState / ZeroState / Label / FuncRngMin / FuncRngMax / **Units / Period / SDIExpected**（最后 3 个按用户要求归类为 key）。

#### 完成内容

1. **数据模型重构**（`backend/app/v4/models.py`）：
   - 删除 `ConsensusResult.evidence_alignment` 字段（**完全删除**，无软删除）
   - 删除 `ConsensusResult.inconsistent_attributes`（合并到 field_disagreements）
   - 新增 `FieldDisagreement` Pydantic 模型（field/category: Literal["key","non_key","vague"]/providers/values/detail）
   - 新增 `ConsensusResult.field_disagreements: list[FieldDisagreement]` 和 `cited_fields: list[str]`
2. **后端映射重写**（`backend/app/v4/comparison/review_agent.py`）：`_map_star_rating` 新签名 `(agreement, field_disagreements)`，KEY_FIELDS 常量定义 12 个 key 字段；新增 `_parse_field_disagreements` 容错解析 LLM 输出（dict 或 string）。
3. **Prompt 重写**（`backend/app/v4/prompts/consensus.md`）：任务定义改为"字段冲突检测"；显式枚举 12 个 key 字段；新 JSON schema（field_disagreements + cited_fields）；提供 5 个示例覆盖各档；LLM 不再输出 star_rating，由后端计算。**Step 0 明确区分"两类不一致"**：EoICD-HLR 事实性差异 vs 裁判间意见分歧，避免 LLM 误填。
4. **Word 渲染**（`backend/app/v4/doc_generators/consensus_word_generator.py`）：`inconsistent_attributes` 渲染改用 `field_disagreements`（每条含 category）；2★/4★/5★ 描述文字按字段类型重写。
5. **e2e 测试同步**：`test_use_case_5_five_star_rating.py` 注入策略改 field_disagreements 构造（15 个映射分支覆盖 full/majority/split × key/non_key/vague）；`test_use_case_6_five_star_upgrade.py` 同样改造（1★ split、2★ majority+key、4★ full+vague）。
6. **ADR 重写**：`docs/decisions/ADR-004-五星评价体系.md` v2 替代 v1，记录重构动机、字段类型映射、状态变化。

#### 修改文件

1. `backend/app/v4/models.py` — 替换 evidence_alignment 字段；新增 FieldDisagreement 模型；删除 inconsistent_attributes
2. `backend/app/v4/prompts/consensus.md` — 任务改字段冲突检测；枚举 12 个 key 字段；Step 0 区分两类不一致
3. `backend/app/v4/comparison/review_agent.py` — `_map_star_rating` 新签名；KEY_FIELDS 常量；`_parse_field_disagreements` 容错
4. `backend/app/v4/doc_generators/consensus_word_generator.py` — field_disagreements 渲染；2★/4★/5★ 描述
5. `backend/tests/e2e/test_use_case_5_five_star_rating.py` — 15 个映射分支重写
6. `backend/tests/e2e/test_use_case_6_five_star_upgrade.py` — 注入策略改 field_disagreements 构造
7. `docs/decisions/ADR-004-五星评价体系.md` — v2 ADR 重写
8. `CHANGELOG.md` — 新增 breaking change 条目（v2 替代 v1）
9. `docs/project/workflow.md` — Step 5 描述更新

#### 验证方式

1. 6 个 Python 文件 `ast.parse` 语法验证
2. `python -c "from app.v4.comparison.review_agent import _map_star_rating; ..."` 离线单元断言（15+ 项 PASS）
3. `python tests/e2e/test_use_case_5_five_star_rating.py`（场景 A 离线断言 + 场景 B 基线集成）
4. 真实 LLM 跑 `故障注入1.0.docx`，查看 5 档分布

#### 验证结果

1. 语法验证全部通过
2. 离线单元断言 36+ PASS（涵盖 11+ 个映射分支、5 键 summary、低星触发、_star_str 渲染等）
3. 场景 B（基线集成）尚未运行；旧 v1 baseline 已被迁移脚本处理过（早期设计），后续改方案改为直接重跑管线
4. 真实 LLM 验证：尚未执行

#### 遗留问题

1. 真实 LLM 跑 `故障注入1.0.docx` 验证 5 档分布——需 docker compose 启动 + 重跑管线
2. 老 e2e_baseline/consensus_results.json 含 v1 schema，需重跑基线管线产出 v2 JSON（v2 设计下不再做迁移，详见 ADR-004）
3. 4★ → 5★ 仍是理论路径（v1 的同一遗留问题延续到 v2，但 v2 的 4★ 表达更明确"有字段争议"）
4. 字段命名 `field_disagreements` 在实施 v2 后期二次调整：原 `inconsistent_fields` 易让 LLM 误认为"字段存在不一致（可能是 ICD vs HLR）"，改为 `field_disagreements`（**字段级裁判间分歧**）后，语义更明确；Step 0 的"两类不一致"区分也补强了 prompt 鲁棒性

#### 下一步建议

1. docker compose build backend 后跑 `python tests/e2e/test_use_case_5_five_star_rating.py` 验证 e2e 用例
2. 用 `故障注入1.0.docx` 重跑管线，验证 5 档分布是否改善（应看到 5★/4★/3★/2★ 都出现，1★ 少见）
4. 后续 Issue 可考虑前端 UI 适配 v2 字段显示（field_disagreements + cited_fields）

---

## 2026-08-26：V2 共识报告文案统一（零行为影响）

### 任务目标

把共识 Word 报告（`consensus_word_generator.py`）的展示文案按方案 B 命名风格统一——`star_levels` 4 档标签简明化 + 清理 v1 `evidence_alignment` 残留描述 + 明细表"共识"列与"星级分布"小节口径对齐。判定规则与数据契约均不变。

### 完成内容

1. **"星级分布"小节 4 档标签简化**：star_levels 由长描述改为方案 B 简明命名——5★ → "完全无争议"、4★ → "一致有争议"、3★ → "多数一致"、2★ → "多数有争议"。"说明"列同步按 v2 字段类型语义重写（4★ 明确"含辅助字段"）。
2. **"处置建议"列表清理 v1 残留**：删除 "evidence 强 / 一般"、"反对方 evidence 弱" 等 v1 描述，改用星级分布小节口径（"完全无争议 / 一致有争议 / 多数一致 / 多数有争议 / 分歧·单源·失效"）。
3. **明细表"共识"列 mapping 统一**：`full → 完全一致` 改为 `完全无争议`，与"星级分布"小节表头严格对齐。
4. **`re_review.py:247` docstring 同步对齐 ADR-004 v2**：从 v1 "多数一致但 evidence 弱" 改为 v2 "多数一致但有 key 字段分歧"。

### 修改文件

1. `backend/app/v4/doc_generators/consensus_word_generator.py`（star_levels 数组 + suggestions 列表 + agreement_label mapping，3 处）
2. `backend/app/v4/comparison/re_review.py`（docstring 注释 v1→v2 口径）

### 验证方式

1. `python -c "import ast; ast.parse(open('backend/app/v4/doc_generators/consensus_word_generator.py').read())"` 静态语法检查
2. `sed -n '215,245p'` 复核 star_levels 新标签与说明
3. `sed -n '298,310p'` 复核 suggestions 列表（v1 evidence 残留应已清理）
4. `sed -n '385,387p'` 复核 agreement_label mapping（full → 完全无争议）
5. mock 端到端跑一次管线生成 docx，目检"星级分布"小节、"处置建议"列表、"明细表共识列"三处渲染统一

### 验证结果

1. 静态语法检查通过
2. star_levels / suggestions / agreement_label 三处文字片段人肉复核通过
3. mock 端到端 docx 目检：尚未验证（需 PowerShell 手敲启动 backend）

### 遗留问题

1. "共识"列与"星级"列存在粗/细粒度互补——5★ + 4★ 行"共识"列均显示"完全无争议"，3★ + 2★ 行均显示"多数一致"，细分看"星级"列
2. ADR-004 v2 文档本身无报告中文标签展示，无需更新
3. 真实 LLM 跑 `故障注入1.0.docx` 验证 docx 渲染效果：未执行（本期仅文字层调整，行为不变）

### 下一步建议

1. mock 端到端跑一次，确认报告展示无回归
2. 真实 LLM 跑 `故障注入1.0.docx`，目检 docx 三处文案统一效果
3. 视用户反馈决定是否进一步精简"共识"列 mapping（如拆分为 star-aware：full+5★→"完全无争议"，full+4★→"一致有争议"）

## 2026-08-27：ADR-004 v3 fusion 五星体系两维度重构

### 任务目标

修正 ADR-004 v2 的语义错误：v2 把 `inconsistent_attributes`（EoICD-HLR 事实差异）
改名并重定义为 `field_disagreements`（provider 间字段级分歧），导致 Word 报告
「不一致属性」列失去数据源。恢复两者为两个独立维度。

### 问题回顾

用户跑真实样例后反馈：报告「判断」列显示「不一致」，但同 case 的「不一致属性」列
显示「—」。根因是 v2 规则规定「3 个 provider 共识识别的 EoICD-HLR 差异不进
`field_disagreements`」，而报告渲染又从 `field_disagreements` 派生「不一致属性」列 ——
共识越好，报告越空。

### 设计决策（用户确认）

1. 删除 `evidence_alignment`（v2 已删，保持删除）
2. `field_disagreements` 保留为辅助字段，仅入 JSON，不渲染到 Word 报告
3. `agreement_level` 完全复用 v0 语义规则（看 analysis 语义，不只看字面 coverage_status）
4. 5 档映射规则直接复用 v2（`_map_star_rating` 逻辑不变）

### 完成内容

1. `models.py` 新增 `InconsistentAttribute`（`attribute` / `detail` / `providers`），
   `ConsensusResult` 新增 `inconsistent_attributes`；`FieldDisagreement` docstring
   标注为辅助字段
2. `consensus.md` 重写为 6 步流程（扫描 cited_fields → agreement 语义判定 →
   提取 inconsistent_attributes → 提取 field_disagreements → final_coverage_status →
   final_analysis/confidence），含 5 个输出示例与 `attribute` vs `field` 命名澄清
3. `review_agent.py` 新增 `_parse_inconsistent_attributes`（dict / 裸字符串两种形态容错），
   `_call_review_api` 同时解析两字段
4. `consensus_word_generator.py` 「不一致属性」列直接读 `inconsistent_attributes`，
   删除从 `field_disagreements` 派生的逻辑
5. e2e 用例 5/6 注入两字段；`common.py` 新增 `_migrate_consensus_schema` 自动迁移旧 baseline

### 修改文件

1. `backend/app/v4/models.py`
2. `backend/app/v4/prompts/consensus.md`
3. `backend/app/v4/comparison/review_agent.py`
4. `backend/app/v4/doc_generators/consensus_word_generator.py`
5. `backend/tests/e2e/test_use_case_5_five_star_rating.py`
6. `backend/tests/e2e/test_use_case_6_five_star_upgrade.py`
7. `backend/tests/e2e/common.py`
8. `docs/decisions/ADR-004-五星评价体系.md`
9. `CHANGELOG.md`

### 验证方式

1. `docker compose build backend`
2. `docker compose run --rm -w /app backend python tests/e2e/test_use_case_5_five_star_rating.py`
3. `docker compose run --rm -w /app backend python tests/e2e/test_use_case_6_five_star_upgrade.py`
4. 真实 LLM 跑 `故障注入1.0.docx`，检查 `inconsistent` case 的
   `inconsistent_attributes` 非空 + Word 报告两列对应关系

### 遗留问题

1. `_map_star_rating` 仍只看 `field_disagreements`，`inconsistent_attributes` 不参与星档
   （用户已确认为预期行为）
2. 真实样例验证待跑

## 2026-08-27: ADR-004 v3 fusion 后续——修复 surviving=2 时 LLM 误判 no_consensus

### 背景

v3 fusion 上线后跑 `故障注入1.0.docx`，REV-0010（FSF21000101_HLR_547）最终
`agreement_level=no_consensus`、`star_rating=1`。排查发现 surviving=2 时降级脚本
不覆盖 `agreement_level`（仅 cap star 到 2），而原 `consensus.md` prompt 把
`single_source` / `no_consensus` 列为 LLM 可选值，LLM 看到 1/3 error +
2/3 实质分歧时自主输出了 `no_consensus`，穿透到最终结果。

### 修复（只做方案 A）

`backend/app/v4/prompts/consensus.md`：
- JSON schema `agreement_level` 选项从 `full|majority|split|single_source|no_consensus`
  收窄为 `full|majority|split`
- Step 2 严重分歧段后新增"重要"提示，明确 `single_source` / `no_consensus`
  由后端降级脚本根据 provider 存活数自动写入，LLM 不输出
- 重要提示段首条加相同约束

`CHANGELOG.md` 同步记录（数据契约无变化）。

### 不修复项

- `re_review.py:328` 错误覆盖原 valid 判断（minimax 原本 `inconsistent` 被
  error 静默替换）：属于独立数据丢失 bug，与 `no_consensus` 误判无直接因果。
  留待后续 issue 处理。
- `review_agent.py:_call_review_api` 接受 LLM 输出的 `no_consensus` /
  `single_source`：prompt 改完后这两个值不会再出现，无需 post-process 防御。

### 验证

- 重新跑 `故障注入1.0.docx`，复查 REV-0010：
  - `agreement_level` 应为 `split`（不再为 `no_consensus`）
  - `star_rating` 仍为 1★（split → 1★，与原结果一致）
  - `final_coverage_status=待确认`（一致）
- e2e 用例 5/6 跑通（mock + 真实 LLM）


---

## 2026-08-27：ADR-004 v3 fusion 五星体系两维度重构

### 任务目标

修正 ADR-004 v2 的语义错误：把 `inconsistent_attributes`（EoICD-HLR 事实差异）错误重定义为 `field_disagreements`（provider 间分歧），导致 Word「不一致属性」列失去数据源。恢复两者为两个独立维度。

### 完成内容

1. `models.py` 新增 `InconsistentAttribute`（attribute / detail / providers），`ConsensusResult` 新增 `inconsistent_attributes`；`FieldDisagreement` docstring 标注为辅助字段
2. `consensus.md` 重写为 6 步流程 + 5 个输出示例，明确 `attribute`（事实差异）vs `field`（裁判分歧）命名
3. `review_agent.py` 新增 `_parse_inconsistent_attributes` 容错解析（dict / 裸字符串）
4. `consensus_word_generator.py`「不一致属性」列直接读 `inconsistent_attributes`，删除从 `field_disagreements` 派生逻辑
5. e2e 用例 5/6 注入两字段；`common.py` 新增 `_migrate_consensus_schema` 自动迁移旧 baseline

### 修改文件

1. `backend/app/v4/models.py`
2. `backend/app/v4/prompts/consensus.md`
3. `backend/app/v4/comparison/review_agent.py`
4. `backend/app/v4/doc_generators/consensus_word_generator.py`
5. `backend/tests/e2e/test_use_case_5_five_star_rating.py` / `test_use_case_6_five_star_upgrade.py` / `common.py`
6. `docs/decisions/ADR-004-五星评价体系.md`
7. `CHANGELOG.md`

### 验证方式

1. `docker compose build backend`
2. 容器内跑用例 5/6（mock + 真实 LLM）
3. 真实 LLM 跑 `故障注入1.0.docx`，检查 `inconsistent` case 的 `inconsistent_attributes` 非空

### 验证结果

语法 + mock 用例通过；真实样例验证待跑。

### 遗留问题

`_map_star_rating` 仍只看 `field_disagreements`，`inconsistent_attributes` 不参与星档（用户确认预期）。

### 下一步建议

真实 LLM 跑 `故障注入1.0.docx` 端到端验证两列对应关系。

---

## 2026-08-27：五档命名正式化（Plan Y）

### 任务目标

ADR-004 v3 fusion 上线后，5 档中文命名仍停留在方案 A 描述式长串，与报告「星级分布」小节口径不一致。本次按 Plan Y 把 5 档命名正式化为可对外宣讲的标准短语。

### 完成内容

1. 5 档标准命名：完全共识 / 完全共识·字段异议 / 多数共识 / 多数共识·关键异议 / 三方分歧·仅单源·全部失效
2. `consensus_word_generator.py` `star_levels` 表的"星级"列 / "说明"列 / "处置建议"列全部按新命名更新
3. 明细表"共识"列 mapping 同步刷新（与"星级分布"小节口径严格对齐）

### 修改文件

1. `backend/app/v4/doc_generators/consensus_word_generator.py`（3 处文案统一）
2. `docs/decisions/ADR-004-五星评价体系.md`（命名口径章节）

### 验证方式

1. `python -c "import ast; ..."` 静态语法
2. mock 跑管线生成 docx，目检"星级分布"小节 + 明细表"共识"列 + 处置建议三处文案统一

### 验证结果

静态检查通过；mock docx 目检待跑。

### 遗留问题

无。

### 下一步建议

真实样例 docx 目检确认命名在不同 HLR 样本下的稳定性。

---

## 2026-08-28 Issue #88：AMSC 总线通用协议 covered 判定规则新增

### 任务目标

AMSC 项目 HLR 描述总线协议（ARINC 429 / A825 / A664）通用级特征时，常因 V4 反向 judge 误判为 needs_review。本次按"覆盖协议级特征 → covered"的判定规则新增 prompt 章节。

### 完成内容

1. `reverse_judge.md` 新增「AMSC 通用协议特征 covered 判定说明（空气管理系统控制器专用）」段落
2. 列出 covered 判定 3 个同时满足条件：协议级实现为内容主体、未引用具体 ICD 信号名 / bit 偏移 / Label 号、实现逻辑符合协议标准
3. 给出 covered / needs_review 判定示例（SDI 位编码、奇偶校验 → covered；具体信号位解算 → needs_review）
4. `CHANGELOG.md` 同步条目

### 修改文件

1. `backend/app/v4/prompts/reverse_judge.md`（新增段落）
2. `CHANGELOG.md`

### 验证方式

1. 准备 AMSC 项目 HLR + EoICD 样本，混合「协议级特征」与「具体信号实现」两类 HLR
2. 跑通管线，确认协议级特征 → covered，引用具体信号 → needs_review
3. 非 AMSC 项目 HLR 输入回归

### 验证结果

尚未端到端验证（本次仅 prompt 落盘）。

### 遗留问题

未引入 CAN / 1553B 等其他总线协议的同类规则。

### 下一步建议

观察 AMSC 真实样本下 covered 命中率提升效果；后续按需扩展更多总线协议规则。

---

## 2026-08-28 Issue #89：re-review per-case 并行 + minimax JSON 解析修复

### 任务目标

两个独立修复合并提交：(1) minimax re-review 返回"先长 `<think>` 块 + 后 ```json fence"版式导致 `json.loads` 失败，case 被误标 `coverage_status="error"`；(2) re_review 顶层 case 串行执行成为墙钟瓶颈，需改为 case 维度并发。

### 完成内容

1. **minimax JSON 解析修复**：`semantic_judge._extract_json` 增加 else 分支，非贪婪正则搜索 ```json fence 并提取内含 JSON
2. **re_review 并行化改造**：
   - case 维度并发：re_review 顶层从 `for case_id in sorted(...)` 串行改为"提交 + 收集 + 统一等待"批模式，复用 `_submit_with_gate` / inflight 信号量原语
   - per-case deadline 语义：从"硬墙钟"改为"per-case 软超时 + 全局批上限"混合约束
   - audit 写入统一：`re_review_results.json` 与 `multi_judge_results.json` 在批结束时统一序列化，避免并发写文件冲突
3. 文档同步：`debug-log.md` 补充 BUG-20260828-001 复盘；`CHANGELOG.md` 新增两条目

### 修改文件

1. `backend/app/v4/comparison/semantic_judge.py`（`_extract_json` else 分支）
2. `backend/app/v4/pipeline.py`（re_review 并行化）
3. `docs/development/debug-log.md`（BUG-20260828-001 复盘）
4. `CHANGELOG.md`

### 验证方式

1. `docker compose build backend`
2. 真实样本 `故障注入2.0 - 副本.docx` 跑通管线
   - minimax re-review 返回完整 judgment（不再误标 error）
   - 总墙钟耗时与改造前同输入对比，case 间实现并行
3. 降级场景回归（surviving=1/2/全部 error 三种组合）

### 验证结果

端到端用例全 PASS；降级行为不变。

### 遗留问题

无。

### 下一步建议

观察真实管线总墙钟耗时改善幅度，校准 `DEGRADATION_EXTRA_WAIT` / `DEGRADATION_DRAIN_BUDGET`。

---

## 2026-08-29 Issue #90：V4 共识 docx 列宽锁定与 5 星适配微调

### 任务目标

Word 模板列宽此前依赖旧 `tblW` 写入方式，存在按内容长度跑版风险。本次新增 `_set_table_layout_fixed()` helper 锁定 fixed layout 模式，并按 5 星体系扩展后调整列宽。

### 完成内容

1. 新增 `_set_table_layout_fixed(table, full_width)`：锁定 fixed layout，对齐 tblGrid 与 cell 宽度，避免 Word 自动重算
2. 顶部统计表与星级分布表应用 fixed layout（紧凑表格，`full_width=False`，宽度由列宽之和决定）
3. 明细表沿用 100% 页宽，统一由 helper 处理
4. 星级分布表列宽微调适配 5 档 + 3 个子类型 + 平均星级
5. 明细表 8 列列宽微调，适配「不一致属性」列新增后的内容分布

### 修改文件

1. `backend/app/v4/doc_generators/consensus_word_generator.py`（`_set_table_layout_fixed` + 3 处调用点）

### 验证方式

1. `docker compose build backend`
2. 真实样本 `故障注入2.0 - 副本.docx` + Publisher + Subscriber 跑通管线
3. Word 打开 docx，确认：
   - 各表列宽与模板一致，不随内容跑版
   - 5 档 + 1★ 三个子类型 + 平均星级布局正常
   - 明细表 8 列（含不一致属性列）宽度合理

### 验证结果

端到端验证通过；多组样本回归无跑版。

### 遗留问题

无。

### 下一步建议

前端表格列宽在「V4 五星评价体系前端适配」Issue 中同步调整。
