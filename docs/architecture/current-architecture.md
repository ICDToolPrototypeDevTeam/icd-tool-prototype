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
