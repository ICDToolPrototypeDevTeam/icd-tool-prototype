# 当前架构说明

本文档用于说明 **ICD工具原型Ver2.0** 的当前软件架构、模块职责和目录边界。

## 1. 架构定位

ICD工具原型Ver2.0 采用前后端分离架构。

前端负责用户交互、文件上传、任务状态展示和结果下载。
后端负责文件解析、任务编排、多智能体调用、评分择优、差异比对和 Word 文档生成。

当前版本定位为本地演示原型，优先保证端到端流程可运行、模块边界清晰、后续能力便于扩展。

## 2. 总体架构

总体架构如下：

```text
用户浏览器
    ↓
React + TypeScript 前端
    ↓
FastAPI 后端
    ↓
业务流程编排层 pipeline
    ↓
文件解析 / 多智能体生成 / 评分择优 / 差异比对 / DOCX 输出
```

当前部署方式优先采用 Docker Compose，在本地同时启动前端服务和后端服务。

## 3. 前端职责

前端工程位于 `frontend/`。

前端主要职责包括：

1. 提供文件上传页面；
2. 支持上传 EoICD Word 主文件；
3. 支持上传一个或多个 EoICD Excel 附件；
4. 支持上传软件高层需求文件；
5. 调用后端分析接口创建任务；
6. 展示任务处理状态；
7. 展示处理结果摘要；
8. 提供输出文档下载入口。

前端不负责：

1. 解析 Word 或 Excel 文件；
2. 调用大模型；
3. 执行多智能体流程；
4. 执行评分规则；
5. 生成 Word 输出文档；
6. 保存正式业务数据。

## 4. 后端职责

后端工程位于 `backend/`。

后端主要职责包括：

1. 接收前端上传的输入文件；
2. 创建并维护本地分析任务；
3. 解析 EoICD Word 主文件；
4. 解析 EoICD Excel 附件；
5. 解析软件高层需求文件；
6. 构建统一分析输入包；
7. 编排多智能体生成 EoICD 条目化需求候选结果；
8. 执行多智能体评分和 Python 硬规则评分；
9. 选择最佳 EoICD 条目化需求；
10. 执行 EoICD 条目化需求与软件高层需求的差异比对；
11. 生成 Word 输出文档；
12. 提供任务状态查询和结果下载接口。

## 5. 后端模块划分

后端核心代码位于 `backend/app/`。

建议模块职责如下：

| 模块             | 职责                            |
| -------------- | ----------------------------- |
| `main.py`      | FastAPI 应用入口，负责路由注册、请求接收和响应返回 |
| `models.py`    | Pydantic 数据模型定义（含 chunk-level 模型） |
| `job_manager.py` | 内存任务状态管理（Job 创建、状态更新、结果存储） |
| `pipeline.py`  | 主业务流程编排，按 `for chunk in eoicd_chunks` 串联解析、生成、评分、差异比对和输出文档生成 |
| `parsers/`     | Word、Excel 等输入文件解析，输出 `List[EoICDChunk]` |
| `crew/`        | CrewAI 多智能体编排；含 `agents.py / tasks.py / crews.py`（5 Agent + 5 Task + 3 Crew）和 3 个 pipeline 入口文件 |
| `llm/`         | LLM Provider 抽象层：`factory.py`（env 驱动 + mock fallback）、`prompt_loader.py`（Python 端上下文拼接，不修改 prompts/skills 文本）、`mock_llm.py`（继承 `crewai.BaseLLM`） |
| `merge/`       | 跨 chunk 合并：所有 chunk 最佳 → MergedRequirementResult；按模型 → ModelRequirementResult |
| `prompts/`     | Prompt Markdown 文本资产（lru_cache 缓存） |
| `skills/`      | Skill Markdown 文本资产（lru_cache 缓存） |
| `scoring/`     | Python 硬规则评分（4 维 25×4=100）+ agent × 0.6 + python × 0.4 融合 |
| `docx/`        | Word 输出文档生成：MiniMax / DeepSeek / 最优 / 差异报告 |
| `output/`      | 运行时生成的输出文件存放目录                |

## 6. 前端模块划分

前端核心代码位于 `frontend/src/`。

建议模块职责如下：

| 模块            | 职责          |
| ------------- | ----------- |
| `components/` | 页面组件        |
| `api/`        | 后端 API 调用封装 |

前端应通过 `frontend/src/api/` 统一访问后端接口，不建议在页面组件中分散编写原始请求逻辑。

## 7. 主流程数据流

后端主流程由 `pipeline.py` 统一编排，按 chunk-level 循环组织。

建议数据流如下：

```text
上传文件
    ↓
保存任务输入文件
    ↓
parsers/ 解析输入文件 → UnifiedInputPackage
   ├─ EoICD → List[EoICDChunk]（本 Issue 默认 1 个 chunk-001）
   └─ 软件高层需求 → ParsedSoftwareRequirements
    ↓
for chunk in unified_package.eoicd_chunks:
    crew/ generation crew (M MiniMax + DeepSeek)
       └─ ChunkCandidate × 2
    crew/ scoring crew (M MiniMax + DeepSeek)
       └─ ChunkAgentScoreResult × 4
    scoring/ chunk 内择优
       └─ BestChunkResult
    ↓
merge/ 跨 chunk 合并
   ├─ merge_best_chunks → MergedRequirementResult
   ├─ merge_model_candidates('MiniMax')  → ModelRequirementResult
   └─ merge_model_candidates('DeepSeek') → ModelRequirementResult
    ↓
crew/ comparison crew (仅 DeepSeek)
   └─ List[DifferenceItem]
    ↓
docx/ 生成 4 份 Word + 1 份辅助
   ├─ MiniMax条目化需求.docx
   ├─ DeepSeek条目化需求.docx
   ├─ 最优条目化需求.docx
   ├─ EoICD条目化需求.docx（旧 requirements 接口复用）
   └─ EoICD与软件高层需求差异报告.docx
    ↓
更新任务状态和输出文件路径
```

重要约束：

```text
输入文件只在解析阶段处理一次。
后续生成、评分和差异比对流程应复用解析后的结构化数据。
下游 crew / scoring / merge / docx 全部按 List[EoICDChunk] 编写，便于后续 parser 升级为多 chunk。
```

## 8. 模块边界约束

开发时应遵守以下模块边界：

1. `main.py` 不应承载复杂业务逻辑；
2. 文件解析逻辑应放在 `parsers/`；
3. 多智能体编排逻辑应放在 `crew/`；
4. Python 硬规则评分应放在 `scoring/`；
5. Word 文档生成逻辑应放在 `docx/`；
6. 任务状态管理应放在独立任务管理模块中，不应写入 `main.py`；；
7. 前端 API 调用应集中放在 `frontend/src/api/`；
8. 前端组件不应直接处理后端复杂业务逻辑。

不得将文件解析、智能体生成、评分、差异比对和 DOCX 输出混写在单个大文件中。

## 9. 输出文件管理

运行时生成的输出文件存放在后端输出目录中。

建议输出目录为：

```text
backend/app/output/
```

该目录用于存放运行时生成的 Word 输出文档。运行时生成的输出文件不应提交到 Git，目录中仅保留 `.gitkeep`。

## 10. 当前架构限制

当前架构服务于本地演示原型，存在以下限制：

1. 任务状态可优先采用内存或本地文件方式管理；
2. 暂不引入数据库；
3. 暂不实现用户认证和权限管理；
4. 暂不实现多用户并发任务管理；
5. 暂不实现云端部署和生产环境运维能力；
6. 暂不实现复杂任务队列。

如后续需要扩展上述能力，应通过新的 Issue 或 ADR 明确设计后再实施。

## 11. 架构变更原则

如需调整架构，应遵守以下原则：

1. 小范围模块调整可通过普通 Issue 实施；
2. 涉及主流程、目录结构、API 契约或多智能体方案变化时，应先形成设计说明；
3. 重大架构变化应新增 ADR；
4. 架构变化后应同步更新本文档；
5. 不应在普通 Bug 修复任务中顺手进行架构重构。

## 12. V4 后端集成（Issue A 落地，2026-07-28）

本节追加于原 11 节之后。V3 旧架构（§5-§10）保持不变；V4 与 V3 在同一 FastAPI 入口中双版本共存。

### 12.1 V3 / V4 双版本共存

| 维度 | V3 | V4 |
| --- | --- | --- |
| FastAPI 入口 | `backend/app/main.py`（顶层 thin shell，仅挂 router） | 同 V3（`app.include_router(v4_router, prefix="/api/v4")`） |
| 路由文件 | `backend/app/api/v3/router.py` | `backend/app/api/v4/router.py` |
| 业务模块 | `backend/app/{crew,merge,scoring,docx,llm,parsers,prompts,skills,models,pipeline,job_manager}.py` | `backend/app/v4/{comparison,degradation,doc_generators,llm,matching,parsers,prompts,traceability,config,models,pipeline,cli}.py` |
| 任务目录 | `backend/output/v3/{job_id}/` | `backend/output/v4/{job_id}/input/` + `output/` |
| JobManager | 共享（带 `kind: Literal["v3","v4"]` 字段） | 同 V3 |
| LLM | `crewai.BaseLLM` 派生 + `litellm` | `get_llm("deepseek")` → `DeepSeekClient` + `MockLLMClient` |
| 解析器 | EoICD Word + Excel + 软件高层需求 | EoICD PubSub Excel + HLR Word + 追溯 Excel（可选） |
| 多智能体 | CrewAI 5 Agent / 5 Task / 3 Crew | 工厂：3 provider 平行 judge + Review Agent 共识 |
| 输出文档 | 4 份 docx | 1 份 xlsx + 4 份 docx |
| docker-compose 端口 | 8000 | 8000（同） |

### 12.2 V4 顶层入口与子包

```text
backend/app/
├── main.py                 # 顶层 FastAPI 入口（仅 CORS + V3/V4 router 装载；约 33 行）
├── job_manager.py          # 共享 Job / JobManager（带 kind 字段）
├── models.py               # V3 Pydantic 模型（含 JobStatus / V3 响应 schema）
├── api/
│   ├── v3/
│   │   └── router.py       # V3 路由（173 行；从原 main.py 机械拆分）
│   └── v4/
│       ├── router.py       # V4 路由聚合
│       ├── schemas.py      # V4Job* Pydantic（与 V3 schema 完全隔离）
│       ├── runner.py       # V4 后台线程 + env 保存/恢复 + 5 个 derive_* 函数
│       ├── coverage.py     # POST /api/v4/coverage-analysis
│       ├── jobs.py         # GET /api/v4/jobs/{id}[/result]
│       └── outputs.py      # GET /api/v4/jobs/{id}/outputs/{kind}
└── v4/                     # V4 业务子包（从 _v4_backend_raw/backend/app/ 整体迁入；import 改写为 app.v4.X）
    ├── cli.py              # V4 CLI 入口（原 main.py 重命名）
    ├── config.py           # V4 env 加载（DEEPSEEK_*、USE_MOCK_LLM、JUDGE_PROVIDERS）+ 业务常量
    ├── models.py           # V4 Pydantic（EoICDRequirement / HLRLabel / ReverseCase / ConsensusResult / …）
    ├── pipeline.py         # V4 反向管线编排（run_reverse_pipeline / _match_reverse_with_trace）
    ├── parsers/            # EoICD Excel + HLR Word 解析
    ├── matching/           # 反向匹配、信号画像、HLR 分类、entry filter
    ├── comparison/         # multi_judge + review_agent + 报告生成
    ├── doc_generators/     # xlsx + 3 类 docx 生成
    ├── prompts/            # forward_judge / reverse_judge / consensus .md
    ├── llm/                # factory / deepseek_client / mock_llm
    ├── traceability/       # 追溯表预筛选（独立 zero-coupling 模块）
    ├── degradation/        # Provider 健康跟踪 / Case 超时 / 熔断 / Review 降级
    └── synonyms.yaml       # 别名映射
```

### 12.3 V4 业务流程（6 步反向管线）

```text
1. 解析输入
   EoICD PubSub Excel + HLR Word → 结构化需求列表
   模块: parsers/{eoicd_excel_parser,hlr_word_parser}.py

2. HLR AI 标注
   DeepSeek 对每条 HLR 标注 bus_types / labels / devices / signal_keywords
   模块: matching/hlr_labeler.py

3. 反向匹配（含条目过滤→信号画像→Block 聚合→HLR 分类→匹配→可选追溯预筛选+兜底）
   3a. 条目过滤: 排除协议 DataFormatType 条目 — matching/entry_filter.py
   3b. 信号画像聚类: 按 (Label, LeafName) 聚类 → SignalProfile — matching/signal_profiler.py
   3c. ICD Block 聚合: 按 (label, signal_family) 分组 → ICDBlock
   3d. HLR 分类: 4 路正则分类 + 提取 Label/位字段/SDI/方向 — matching/hlr_classifier.py
   3e. 两阶段 Block 级匹配（Label 前缀粗筛 → 6 维评分 → 三层过滤 → 三级分层）— matching/reverse_matcher.py
   3f. 可选追溯表预筛选 + 兜底机制 — matching/traceability.py

4. 多智能体裁判
   3 Agent 平行裁判（DeepSeek/MiniMax/Qwen）
   模块: comparison/{multi_judge,semantic_judge}.py
   LLM 抽象: llm/factory.py → get_llm(provider)

5. Review Agent 共识
   综合复核 + 星级评价（1-3★）
   模块: comparison/review_agent.py

6. 报告生成
   1 份 xlsx + 3 份单模型 docx + 1 份共识 docx
   模块: doc_generators/{excel_generator,word_generator,consensus_word_generator}.py
   + comparison/report_generator.py
```

### 12.4 V4 输出文档结构

```text
{job_dir}/                              # = backend/output/v4/{job_id}/
├── input/                              # 用户上传的原始文件（HTTP 接收时保存）
│   ├── hlr.docx
│   ├── pub.xlsx
│   ├── sub.xlsx
│   └── traceability/                   # 仅 enable_traceability_prefilter=true 时存在
│       ├── <file1>.xlsx
│       └── <file2>.xlsx
└── output/                             # V4 pipeline 写出的产物
    ├── EoICD条目化清单.xlsx           # 对外下载 (URL: .../outputs/eoicd-xlsx)
    ├── EoICD与SWHLR单模型差异分析报告_DeepSeek.docx   # 对外 (URL: .../consistency/deepseek)
    ├── EoICD与SWHLR单模型差异分析报告_MiniMax.docx    # 对外 (URL: .../consistency/minimax)
    ├── EoICD与SWHLR单模型差异分析报告_Qwen.docx       # 对外 (URL: .../consistency/qwen)
    ├── EoICD与SWHLR多模型差异分析报告.docx         # 对外 (URL: .../consensus-docx)
    ├── eoicd_requirements.json          # 内部
    ├── hlr_requirements.json            # 内部
    ├── hlr_labels.json                  # 内部（Step 2 输出）
    ├── reverse_matches.json             # 内部（Step 3 输出）
    ├── multi_judge_results.json         # 内部（Step 4 输出；mock_models 也从此文件提取）
    ├── consensus_results.json           # 内部（Step 5 输出）
    └── reverse_report.json              # 内部（Step 6 输出）
```

5 类对外下载对应 `V4_OUTPUT_FILES` 常量（`backend/app/api/v4/runner.py`），是 SSoT。7 个 JSON 中间产物按 ADR-001 D7 不作为下载 API 暴露。

### 12.5 V3 / V4 JobManager 共享

```python
# backend/app/job_manager.py
class Job:
    def __init__(self, kind: Literal["v3", "v4"] = "v3"):
        self.job_id: str = str(uuid.uuid4())
        self.kind: Literal["v3", "v4"] = kind
        ...

class JobManager:
    def create_job(self, kind: Literal["v3", "v4"] = "v3") -> Job:
        ...
```

V3 路由仍调 `job_manager.create_job()`（默认 kind="v3"），V4 路由显式传 `kind="v4"`。`Job.kind` **不**对外暴露在 `/result` schema（D8 决策）；仅做路由层分发。

跨版本查询：
- V3 路由收到 `kind="v4"` Job → 404 + `use /api/v4/jobs/...`
- V4 路由收到 `kind="v3"` Job → 404 + `use /api/jobs/...`

### 12.6 V4 LLM 抽象层

```text
llm/factory.py
  get_llm(provider: str) -> LLMClient
    if USE_MOCK_LLM=1: return MockLLMClient
    if provider == "deepseek": return DeepSeekClient(api_key, base_url, model)
    if provider == "qwen": return QwenClient(api_key, base_url, model)
    if provider == "minimax": return MiniMaxClient(api_key, base_url, model)

llm/deepseek_client.py
  DeepSeekClient.chat(messages, temperature, max_tokens=1024, timeout=120, max_retries=2, ...)
    幂等 URL:  base = base_url.rstrip('/'); if base.endswith('/v1'): base = base[:-3]
    url = f"{base}/v1/chat/completions"

llm/qwen_client.py
  QwenClient.chat(messages, temperature, max_tokens=1024, timeout=120, max_retries=2, ...)

llm/minimax_client.py
  MiniMaxClient.chat(messages, temperature, max_tokens=1024, timeout=120, max_retries=2, ...)

llm/mock_llm.py
  MockLLMClient.chat(messages, ...) -> ChatResponse
    返写死 JSON，按 MOCK_JUDGE_RESULT 切 covered / inconsistent / needs_review
```

V4 内 LLM 调用入口：
- `comparison/semantic_judge.py` × 2 处
- `comparison/multi_judge.py`（按 provider 列表循环调）
- `comparison/review_agent.py`
- `matching/hlr_labeler.py`（_call_label_api）

V4 内 `import requests` 仅 1 处（`deepseek_client.py`），其余全部走 `get_llm(provider).chat()`。

### 12.7 V3 / V4 模块边界约束

| 类别 | 约束 |
| --- | --- |
| V3 业务模块 | `crew/` / `merge/` / `scoring/` / `docx/` / `parsers/` 保持 V3 现状，本期不修改 |
| V4 业务模块 | V4 业务逻辑按 ADR-001 D4 保护，Issue A 期间 2 处修复（`hlr_labeler` URL bug / `trace_parser` 文件名 brittleness）已写为本次"特权"实施 |
| 共享模块 | `backend/app/{job_manager,models,main}.py` 与 `backend/app/api/v3/router.py` 同源演进（V3 路由不删，V4 router 走独立子包） |
| 路由层 | V3 / V4 路由完全独立文件，跨版本查询 404 |
| Schema 层 | V3JobResponse 与 V4JobResponse 互不 import；Pydantic 模型分别定义 |
| 入口层 | `backend/app/main.py` 仅 33 行（CORS + V3 router 装载 + V4 router 装载），不写业务逻辑 |

### 12.8 V4 工程约束（ADR-001 引用）

V4 工程化集成的关键决策由 `docs/decisions/ADR-001-V4后端接入策略.md` 维护。本节 §12 与 ADR-001 在以下方面保持一致：
- D1 / D2 / D3 / D4 / D5 / D6 / D7 在 §12.1（双版本共存）、§12.5（JobManager 共享 + 跨版本 404）、§12.6（LLM 抽象层）体现。
- ADR-002（涉及 V4 内部修改的本次"特权"实施）未建，留作 Issue B 启动后由 B 处理。

如未来 V4 路由 / Schema / JobManager / LLM 抽象层有变化，需同时更新本节与 ADR-001。
