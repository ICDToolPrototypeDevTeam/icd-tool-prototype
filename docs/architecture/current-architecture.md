# 当前架构说明

本文档用于说明 **ICD工具原型** 的当前软件架构、模块职责和目录边界。

## 1. 架构定位

ICD工具原型采用前后端分离架构。

前端负责用户交互、文件上传、任务状态展示和结果下载。
后端负责文件解析、HLR 标注、反向匹配、多智能体裁判、共识复核和报告文档生成。

当前版本定位为本地演示原型，优先保证端到端流程可运行、模块边界清晰、后续能力便于扩展。

## 2. 总体架构

总体架构如下：

```text
用户浏览器
    ↓
React + TypeScript 前端
    ↓
FastAPI 后端（/api/v4）
    ↓
V4 反向管线 pipeline（6 步）
    ↓
解析 / HLR 标注 / 反向匹配 / 多智能体裁判 / 共识复核 / 报告生成
```

当前部署方式优先采用 Docker Compose，在本地同时启动前端服务和后端服务。

## 3. 前端职责

前端工程位于 `frontend/`。

前端主要职责包括：

1. 提供文件上传页面；
2. 支持上传 HLR Word 文件（必填）；
3. 支持上传 EoICD Publisher / Subscriber PubSub Excel（二选一）；
4. 支持上传一个或多个追溯 Excel（可选）；
5. 调用后端 `/api/v4/coverage-analysis` 创建任务；
6. 展示任务处理状态（六步进度）；
7. 展示处理结果摘要（统计卡片 + 星级分布）；
8. 提供输出文档预览与下载入口。

前端不负责：

1. 解析 Word 或 Excel 文件；
2. 调用大模型；
3. 执行反向匹配或裁判流程；
4. 执行降级保护规则；
5. 生成报告文档；
6. 保存正式业务数据。

## 4. 后端职责

后端工程位于 `backend/`。

后端主要职责包括：

1. 接收前端上传的输入文件；
2. 创建并维护本地分析任务；
3. 解析 HLR Word 文件与 EoICD PubSub Excel；
4. 用 DeepSeek 对每条 HLR 做 AI 标注；
5. 执行反向匹配（含追溯表预筛选）；
6. 编排多智能体裁判（DeepSeek / MiniMax / Qwen）与降级保护；
7. 执行 Review Agent 共识复核与星级评分；
8. 生成 1 份 xlsx + 4 份 docx 报告；
9. 提供任务状态查询和结果下载接口。

## 5. 后端模块划分

后端核心代码位于 `backend/app/`。

| 模块             | 职责                            |
| -------------- | ----------------------------- |
| `main.py`      | FastAPI 应用入口，仅 CORS + `/api/v4` 路由装载 |
| `job_manager.py` | 内存任务状态管理（`JobStatus` / `Job` / `JobManager`） |
| `api/v4/`      | V4 路由层：`router.py`（聚合）、`schemas.py`（响应模型）、`runner.py`（后台线程 + 5 个 derive_*）、`coverage.py` / `jobs.py` / `outputs.py` |
| `v4/pipeline.py` | V4 反向管线编排（6 步），`run_reverse_pipeline` |
| `v4/config.py` | V4 env 加载（DEEPSEEK_* / USE_MOCK_LLM / JUDGE_PROVIDERS）+ 业务常量 |
| `v4/models.py` | V4 Pydantic 模型（EoICDRequirement / HLRLabel / ReverseCase / ConsensusResult 等） |
| `v4/parsers/`  | EoICD PubSub Excel + HLR Word 解析 |
| `v4/matching/` | HLR 标注、条目过滤、信号画像、Block 聚合、HLR 分类、反向匹配、追溯预筛选 |
| `v4/comparison/` | 多模型裁判、Review Agent 共识、一星复查、报告生成 |
| `v4/degradation/` | Provider 健康跟踪、Case 超时、熔断、星级降级 |
| `v4/doc_generators/` | xlsx + 单模型 docx + 共识 docx 生成 |
| `v4/llm/`       | LLM 抽象层：`factory.py`（env 驱动 + mock fallback）、deepseek/minimax/qwen client、`mock_llm.py` |
| `v4/prompts/`   | Prompt Markdown 文本资产（reverse_judge / consensus / re_review） |
| `v4/traceability/` | 追溯表预筛选（独立零耦合模块） |
| `output/`      | 运行时生成的输出文件存放目录                |

## 6. 前端模块划分

前端核心代码位于 `frontend/src/`。

| 模块            | 职责          |
| ------------- | ----------- |
| `components/` | 页面组件        |
| `api/`        | 后端 API 调用封装 |

前端应通过 `frontend/src/api/` 统一访问后端接口，不建议在页面组件中分散编写原始请求逻辑。

## 7. 主流程数据流

后端主流程由 `v4/pipeline.py` 统一编排，按 6 步组织。

```text
上传文件
    ↓
Step 1 解析输入
   HLR Word + EoICD PubSub Excel → hlr_requirements.json + eoicd_requirements.json
   模块: v4/parsers/{eoicd_excel_parser,hlr_word_parser}.py
    ↓
Step 2 HLR AI 标注
   DeepSeek 标注 bus_types / labels / devices / signal_keywords → hlr_labels.json
   模块: v4/matching/hlr_labeler.py
    ↓
Step 3 反向匹配
   条目过滤 → 信号画像 → Block 聚合 → HLR 分类 → 两阶段匹配 → 可选追溯预筛选
   模块: v4/matching/{entry_filter,signal_profiler,reverse_case_builder,hlr_classifier,reverse_matcher,traceability}.py
    ↓
Step 4 多智能体裁判（含降级保护）
   DeepSeek / MiniMax / Qwen 并行判定 → multi_judge_results.json
   模块: v4/comparison/{multi_judge,semantic_judge}.py + v4/degradation/*
    ↓
Step 5 Review Agent 共识
   综合复核 + 星级评分 → consensus_results.json（含一星复查 + 部分共识重跑）
   模块: v4/comparison/{review_agent,re_review}.py + v4/degradation/context.py
    ↓
Step 6 报告生成
   1 份 xlsx + 3 份单模型 docx + 1 份共识 docx
   模块: v4/doc_generators/* + v4/comparison/report_generator.py
    ↓
更新任务状态和输出文件路径
```

重要约束：

```text
输入文件只在解析阶段处理一次。
后续标注、匹配、裁判和共识复核流程应复用解析后的结构化数据。
```

## 8. 模块边界约束

开发时应遵守以下模块边界：

1. `main.py` 不应承载复杂业务逻辑；
2. 文件解析逻辑应放在 `v4/parsers/`；
3. 反向匹配逻辑应放在 `v4/matching/`；
4. 多智能体裁判与共识逻辑应放在 `v4/comparison/`；
5. 降级保护逻辑应放在 `v4/degradation/`；
6. 报告文档生成逻辑应放在 `v4/doc_generators/`；
7. 任务状态管理应放在 `job_manager.py`，不应写入 `main.py`；
8. 前端 API 调用应集中放在 `frontend/src/api/`；
9. 前端组件不应直接处理后端复杂业务逻辑。

不得将文件解析、匹配、裁判、共识和报告输出混写在单个大文件中。

## 9. 输出文件管理

运行时生成的输出文件存放在后端输出目录中。

```text
backend/output/v4/{job_id}/
├── input/                 # 用户上传的原始文件
│   ├── hlr.docx
│   ├── pub.xlsx
│   ├── sub.xlsx
│   └── traceability/      # 仅 enable_traceability_prefilter=true 时存在
└── output/                # pipeline 写出的产物（5 份对外 + 7 份内部 JSON）
```

运行时生成的输出文件不应提交到 Git。既有 `backend/output/v3/` 历史产物保留不删除（ADR-002 D7）。

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

## 12. V4 后端集成细节

### 12.1 V4 顶层入口与子包

```text
backend/app/
├── main.py                 # 顶层 FastAPI 入口（仅 CORS + V4 router 装载）
├── job_manager.py          # 共享 Job / JobManager（JobStatus 唯一来源）
├── api/
│   └── v4/
│       ├── router.py       # V4 路由聚合（health + coverage + jobs + outputs）
│       ├── schemas.py      # V4Job* Pydantic
│       ├── runner.py       # V4 后台线程 + env 保存/恢复 + 5 个 derive_* 函数
│       ├── coverage.py     # POST /api/v4/coverage-analysis
│       ├── jobs.py         # GET /api/v4/jobs/{id}[/result]
│       └── outputs.py      # GET /api/v4/jobs/{id}/outputs/{kind}
└── v4/                     # V4 业务子包
    ├── cli.py              # V4 CLI 入口
    ├── config.py           # V4 env 加载 + 业务常量
    ├── models.py           # V4 Pydantic 模型
    ├── pipeline.py         # V4 反向管线编排（run_reverse_pipeline / _match_reverse_with_trace）
    ├── parsers/            # EoICD Excel + HLR Word 解析
    ├── profiles/           # Controller profile registry（Issue #63 引入）
    │   ├── __init__.py     # ProfileRegistry 单例 + init_registry / get_registry
    │   ├── base.py         # ControllerProfile + 4 个 Config dataclass
    │   ├── ams/            # AMS profile（默认；从现状代码 1:1 抽取，向后兼容）
    │   │   ├── __init__.py
    │   │   ├── config.yaml # HLR 字段映射 / 分类关键词 / 追溯表配置 / AI 标注示例
    │   │   ├── hooks.py    # profile 专属可选钩子
    │   │   └── README.md
    │   └── fgmc/           # FGMC profile（燃油测量管理计算机）
    │       ├── __init__.py
    │       ├── config.yaml
    │       ├── hooks.py
    │       └── README.md
    │   └── hscu/           # HSCU profile（液压系统控制单元）
    │       ├── __init__.py
    │       ├── config.yaml
    │       ├── hooks.py
    │       └── README.md
    │   └── rpdu/           # RPDU profile（远程功率分配单元，Issue #74 多控制器适配）
    │       ├── __init__.py
    │       ├── config.yaml
    │       ├── hooks.py
    │       └── README.md
    ├── matching/           # 反向匹配、信号画像、HLR 分类、entry filter
    ├── comparison/         # multi_judge + review_agent + 报告生成
    ├── doc_generators/     # xlsx + 3 类 docx 生成
    ├── prompts/            # reverse_judge / consensus / re_review .md
    ├── llm/                # factory / deepseek_client / minimax_client / qwen_client / mock_llm
    ├── traceability/       # 追溯表预筛选（独立零耦合模块）
    ├── degradation/        # Provider 健康跟踪 / Case 超时 / 熔断 / Review 降级
    └── synonyms.yaml       # 别名映射
```

### 12.2 V4 业务流程（6 步反向管线）

```text
1. 解析输入
   EoICD PubSub Excel + HLR Word → 结构化需求列表
   说明: EoICD 解析生成多层级属性（HL 高层 / DP 数据点 / RP 接收参数，带 layer_type / side）
   模块: parsers/{eoicd_excel_parser,hlr_word_parser}.py

2. HLR AI 标注
   DeepSeek 对每条 HLR 标注 bus_types / labels / devices / signal_keywords
   模块: matching/hlr_labeler.py

3. 反向匹配（含条目过滤→信号画像→Block 聚合→HLR 分类→匹配→可选追溯预筛选+兜底）
   3a. 条目过滤: 排除协议 DataFormatType 条目 — matching/entry_filter.py
   3b. 信号画像聚类: 按 (Label, LeafName) 聚类 → SignalProfile，仅取 leaf 层（layer_type ∈ {DP, RP}）条目 — matching/signal_profiler.py
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

### 12.3 V4 输出文档结构

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

### 12.4 V4 LLM 抽象层

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

三个 client（DeepSeek / MiniMax / Qwen）的 `chat()` 均内置 `finish_reason=length` 截断自适应重试：截断时自动翻倍 `max_tokens` 重试（上限 16384，最多 2 次倍增），所有调用方自动受益，无需在业务层自行处理。

V4 内 `import requests` 仅 3 处（`deepseek_client.py` / `minimax_client.py` / `qwen_client.py`），其余全部走 `get_llm(provider).chat()`。

### 12.5 V4 工程约束（ADR-001 / ADR-002 引用）

V4 工程化集成的关键决策由 `docs/decisions/ADR-001-V4后端接入策略.md` 与 `docs/decisions/ADR-002-移除V3.md` 维护。

- ADR-001 中 D3（`/api/v4` 命名空间）、D4（import 适配但不改业务逻辑）、D5（mock_models 显式标识）、D6（`consistency/{model}` 扩展点）、D7（JSON 不暴露）仍有效。
- ADR-001 中 D2 / D8 及 D1 的「暂不删 V3」部分已由 ADR-002 取代（V3 已移除、`kind` 字段已删除）。
- ADR-002 记录了 V3 移除范围、决策点 A1（去 kind）与 B（保留 app/v4 正向原型）；其中 D4（保留正向原型）已由 ADR-003 取代（正向原型已删除）。
- ADR-003 记录了 V4 早期正向原型与旧单模型反向 CLI 的移除范围，并约定未来正向采用 ICDBlock 级 Case、候选检索算法待定。

如未来 V4 路由 / Schema / JobManager / LLM 抽象层有变化，需同时更新本节与相应 ADR。
