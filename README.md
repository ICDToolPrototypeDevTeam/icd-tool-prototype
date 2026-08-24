# ICD工具原型Ver4.0

ICD工具原型Ver4.0是一个面向EoICD源文件和软件高层需求（HLR）文件的智能化差异分析与需求生成工具。

当前工具运行版本为 **V4.0**：从软件高层需求（HLR）出发，验证每条 HLR 需求是否能在 EoICD 接口定义中找到对应项（即 HLR 到 EoICD 的可追溯性），从而表明HLR是否覆盖了对应的EoICD条目，通过 DeepSeek / MiniMax / Qwen 三模型并行裁判 + Review Agent 共识复核，输出条目化清单和一致性分析报告。早期 V3.0 代码已移除（见 [ADR-002](docs/decisions/ADR-002-移除V3.md)）。

## 1. 主要功能

- **HLR→EoICD 可追溯性分析**：从软件高层需求（HLR）出发，逐条验证每条 HLR 需求是否能在 EoICD 接口定义中找到对应项，从而判断 HLR 到 EoICD 的追溯与覆盖情况。
- **输入解析与 HLR 标注**：解析 HLR Word 和 EoICD PubSub Excel，得到结构化的 HLR 需求列表与 EoICD 接口清单；用 DeepSeek 为每条 HLR 需求标注总线类型、Label 号、关联设备、信号关键词，作为匹配线索。
- **反向匹配（含追溯表预筛选）**：根据标注线索为每条 HLR 需求寻找最可能对应的 EoICD 接口定义（Block）；支持上传追溯表预先缩小匹配范围，匹配失败时自动回退到全量匹配。
- **三模型裁判与共识评分**：DeepSeek / MiniMax / Qwen 三模型并行独立判定，Review Agent 综合复核并给出 1-3★ 星级评分。
- **多维度报告输出**：输出条目化清单（.xlsx）和单模型/多模型一致性分析报告（.docx），含不一致属性栏等分析明细。

## 2. 输入输出

### 输入

| 文件 | 格式 | 说明 |
|------|------|------|
| HLR Word 文件 | .docx | **必填**，从"软件需求"章节提取需求条目 |
| EoICD Publisher Excel | .xlsx | 与 Subscriber 二选一，发送侧接口定义 |
| EoICD Subscriber Excel | .xlsx | 与 Publisher 二选一，接收侧接口定义 |
| 追溯 Excel | .xlsx | 选填（0-N 个），设备需求（ERD）→HLR / 设备需求（ERD）→ICD 追溯表 |

### 输出

| 输出文件 | 内容说明 |
|------|------|
| `EoICD条目化清单.xlsx` | 把 EoICD PubSub Excel 中嵌套的接口信号（HL 高层 / DP 数据点 / RP 接收参数）解析、整理成的结构化条目清单 |
| `EoICD与SWHLR单模型差异分析报告_DeepSeek.docx` | DeepSeek 单独判定的结果：每条 HLR 需求是否在 EoICD 中找到对应接口、两者是否一致 |
| `EoICD与SWHLR单模型差异分析报告_MiniMax.docx` | MiniMax 单独判定的结果（内容同上） |
| `EoICD与SWHLR单模型差异分析报告_Qwen.docx` | Qwen 单独判定的结果（内容同上） |
| `EoICD与SWHLR多模型差异分析报告.docx` | 汇总三个模型的判定，经 Review Agent 复核后给出最终结论与 1-3★ 星级评分 |

> **报告含义**：本工具的核心是判断"每条 HLR 软件需求，是否能在 EoICD 接口定义中找到对应的接口"（即 HLR 到 EoICD 的可追溯性）。"一致性"指的就是"HLR 需求与 EoICD 接口定义之间是否对应、对应得是否一致"。3 份单模型报告是三个模型各自的独立判断，1 份多模型报告是综合三个模型结果共识度的最终结论和可靠度评分（星级）。

## 3. 处理流程

```text
上传文件 → 解析输入 → HLR 需求标注 → 反向匹配（找对应接口）
         → 三模型判定（是否对应一致） → 共识复核 → 生成报告
```

1. **解析输入**：读取 HLR Word（提取"软件需求"章节的每条需求）和 EoICD PubSub Excel（提取接口信号），整理成结构化的"HLR 需求列表"和"EoICD 接口清单"。
2. **HLR 需求标注**：用 DeepSeek 为每条 HLR 需求自动标注关键信息（总线类型、Label 号、关联设备、信号关键词），作为后续匹配的线索。
3. **反向匹配**：根据标注线索，为每条 HLR 需求在 EoICD 接口清单中寻找最可能对应的接口定义（Block），得到候选匹配结果。
4. **三模型判定**：DeepSeek / MiniMax / Qwen 三个模型分别独立判断"每条 HLR 需求是否在 EoICD 中找到了正确对应的接口，且两者描述（数据类型、方向、范围等）是否一致"，各自给出结论。
5. **共识复核**：由 Review Agent 汇总三个模型的结论，对分歧处复核，给出最终判定和 1-3★ 星级（星级代表判定结果的可靠程度）。
6. **生成报告**：将以上结果整理成 1 份 xlsx（条目化清单）+ 4 份 docx（差异分析报告）。

详细流程说明见 [`docs/project/workflow.md`](docs/project/workflow.md)。

## 4. 快速开始

### 4.1 环境要求

- Docker ≥ 24.0 + Docker Compose ≥ v2.26
- DeepSeek API Key（真实模式必填）
- MiniMax / Qwen API Key（可选，未配置时需从 `JUDGE_PROVIDERS` 中移除，否则报错）

### 4.2 配置

在项目根目录创建 `backend/.env`（复制 `backend/.env.example` 后按需填写）。

#### 方式一：Mock 模式（无需 API Key，开箱即用）

```bash
USE_MOCK_LLM=1
```

此模式下所有模型走本地 mock，无需任何 API Key，可快速验证完整流程。

#### 方式二：真实模式（以只接入 DeepSeek 为例）

```bash
USE_MOCK_LLM=0
DEEPSEEK_API_KEY=sk-xxxxxxxx
JUDGE_PROVIDERS=deepseek
```

> **重要**：`.env.example` 中的 `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 等字段**不要留空值**。留空会覆盖代码默认值（`https://api.deepseek.com` / `deepseek-v4-flash`），导致 `Invalid URL: No scheme supplied` 报错。要么删除这些行，要么填写正确值。

如需同时接入 MiniMax / Qwen，补充对应 Key 并更新 `JUDGE_PROVIDERS`：

```bash
MINIMAX_API_KEY=sk-xxxxxxxx
QWEN_API_KEY=sk-xxxxxxxx
JUDGE_PROVIDERS=deepseek,minimax,qwen
```

> **规则**：`JUDGE_PROVIDERS` 中列出的每个模型都必须有对应 API Key，否则任务会报错。配了几个模型，就只写几个模型名。

### 4.3 启动
在项目根目录中启动
```bash
docker compose up -d --build
```

### 4.4 访问

| 地址 | 说明 |
|------|------|
| `http://localhost:3000` | 前端界面 |
| `http://localhost:8000/api/v4/health` | 后端健康检查 |

### 4.5 使用演示

启动完成后，打开 `http://localhost:3000`，按以下步骤操作。

#### 步骤 1：打开前端界面

页面顶部为三步工作流导航（上传文件 → 文件处理 → 查看结果），右上角显示后端服务连接状态。
![alt text](docs/images/image-1.png)

#### 步骤 2：上传文件

在"文件上传"卡片中，按需上传三类文件：

| 文件 | 必填性 | 说明 |
|------|------|------|
| HLR Word 文件 | 必填 | 软件高层需求 Word（.docx） |
| EoICD Publisher Excel | 与 Subscriber 至少填一 | 发送侧接口定义（.xlsx/.xls） |
| EoICD Subscriber Excel | 与 Publisher 至少填一 | 接收侧接口定义（.xlsx/.xls） |
| 追溯表 | 选填（0-N 个） | 设备需求（ERD）→HLR / 设备需求（ERD）→ICD 追溯表，可多选 |

点击文件条目可在右侧"文件预览"卡片查看内容；点击文件旁的 ✕ 可移除已选文件。
![alt text](docs/images/image-2.png)

#### 步骤 3：开始处理

点击底部"开始处理"按钮。若未上传 HLR Word 文件，或 Publisher/Subscriber 均未填写，前端会弹窗提示。进入"文件处理"页面，显示六步进度（解析文件 → HLR标注 → 反向匹配 → 多模型裁判 → 共识复核 → 报告生成）及当前处理的 Case 计数。
![alt text](docs/images/image-3.png)

#### 步骤 5：查看结果与下载

处理完成后自动进入"查看结果"页面，包含三部分：

1. **统计卡片与星级评价分布**：EoICD 条目数、HLR 需求数，以及已覆盖 / 不一致 / 待确认 / 无匹配的数量分布，1-3★ 星级分布与平均星级。
![alt text](docs/images/image-4.png)
2. **EoICD 条目化清单与多模型差异分析报告预览**：展示条目化需求 Excel 清单和三模型差异分析报告的预览
![alt text](docs/images/image-5.png)
3. **下载输出文档**：点击下方下载卡片即可下载对应产物（条目化清单 XLSX、三份单模型分析报告、多模型共识报告）。
![alt text](docs/images/image-6.png)

处理完成后如需再次分析，点击"处理新文件"按钮即可回到上传界面重新开始。

## 5. 工程结构

```text
icd-tool-prototype/
├── frontend/                     # 前端工程
│   ├── src/                      #   源代码
│   │   ├── api/                  #     后端 API 调用封装
│   │   ├── components/           #     页面组件
│   │   ├── App.tsx               #     应用入口
│   │   └── types.ts              #     TypeScript 类型定义
│   ├── Dockerfile
│   └── package.json
├── backend/                      # 后端工程
│   ├── app/
│   │   ├── api/v4/               # V4 API 路由（router / schemas / runner / coverage / jobs / outputs）
│   │   ├── v4/                   # V4 业务模块
│   │   │   ├── comparison/       #   多模型裁判 + Review Agent 共识 + 一星复查 + 报告生成
│   │   │   ├── degradation/      #   多智能体降级保护（超时、熔断、星级降级）
│   │   │   ├── matching/         #   反向匹配（HLR 标注、信号画像、Block 聚合、HLR 分类）
│   │   │   ├── llm/              #   LLM 抽象层（DeepSeek / MiniMax / Qwen Client + Mock）
│   │   │   ├── doc_generators/   #   xlsx + 单模型 docx + 共识 docx 生成
│   │   │   ├── parsers/          #   EoICD PubSub Excel + HLR Word 解析
│   │   │   ├── traceability/     #   追溯表预筛选
│   │   │   └── prompts/          #   V4 Prompt 文本资产
│   │   ├── job_manager.py        # 内存任务状态管理（JobStatus / Job / JobManager）
│   │   └── main.py               # FastAPI 入口（thin shell，仅 CORS + 路由装载）
│   ├── output/                   # 运行时输出文件（v4/{job_id}/）
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
├── docs/                         # 正式工程文档
│   ├── project/                  #   项目范围与流程说明
│   ├── architecture/             #   架构与 API 设计
│   ├── development/              #   开发纪要与问题记录
│   └── decisions/                #   重大技术决策（ADR）
├── .github/                      # GitHub Issue 模板与 PR 模板
├── .claude/                      # Claude Code 规则
├── docker-compose.yml
├── CLAUDE.md
├── CHANGELOG.md
└── README.md
```

详细架构说明见 [`docs/architecture/current-architecture.md`](docs/architecture/current-architecture.md)。

## 6. 文档索引

| 文档 | 说明 |
|------|------|
| `CLAUDE.md` | Claude Code 工作入口和项目规则 |
| `docs/project/scope.md` | 项目范围、输入输出边界、V4 能力说明 |
| `docs/project/workflow.md` | 业务流程、各阶段输入输出 |
| `docs/architecture/current-architecture.md` | 软件架构、模块职责 |
| `docs/architecture/api.md` | API 接口定义 |
| `docs/decisions/ADR-001-V4后端接入策略.md` | V4 后端工程化集成关键决策（Partially Superseded） |
| `docs/decisions/ADR-002-移除V3.md` | 移除 V3 旧版代码决策 |
| `docs/development/development-log.md` | 开发纪要 |
| `docs/development/debug-log.md` | 问题排查记录 |
| `.claude/rules/context-rules.md` | Claude Code 上下文读取规则 |
| `.claude/rules/debug-rules.md` | Claude Code 调试规则 |
| `.claude/rules/documentation-rules.md` | Claude Code 文档更新规则 |

## 7. 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React、TypeScript、mammoth、xlsx、lucide-react |
| 后端框架 | Python、FastAPI、Pydantic |
| 文件处理 | openpyxl、python-docx、PyYAML、python-dotenv |
| AI/LLM | DeepSeek、MiniMax、Qwen / DashScope |
| 部署 | Docker Compose |

## 8. 当前阶段

工程处于 ICD 工具 4.0 端到端原型验证阶段。

- **V4.0**：已完成 HLR→EoICD 可追溯性分析全流程（6 步），三模型并行裁判 + 星级评分共识，输出 5 份产物。DeepSeek 为必填（同时用于 HLR 标注和 Review Agent），MiniMax / Qwen 为可选（未配置时从 `JUDGE_PROVIDERS` 中移除即可）。
- **前端**：默认使用 V4.0 界面。
- **V3.0 旧版代码**：已移除（见 [ADR-002](docs/decisions/ADR-002-移除V3.md)）。

## 9. 开发约定

本项目采用 GitHub Repo + GitHub Projects + Claude Code 的轻量化敏捷开发方式。

### 9.1 基本原则

1. `main` 分支保持稳定；
2. 每个明确任务通过 Issue 跟踪；
3. 重要功能开发使用独立分支；
4. 大功能开发前先形成计划；
5. Debug 任务先定位问题，再进行最小修改；
6. 工程事实源以 `CLAUDE.md` 和 `docs/` 下文档为准；
7. Claude Code 不得主动创建分支、push、创建/合并 PR、关闭 Issue 或修改 Project 看板状态（由用户手动完成）。

### 9.2 分支命名约定

推荐分支命名格式如下：

```text
类型/issue编号-任务简述
```

常用类型包括：

```text
docs/      文档任务
feature/   功能开发
fix/       问题修复
chore/     工程杂项
refactor/  重构
```

示例：

```text
docs/issue-2-project-docs
feature/issue-3-minimal-skeleton
feature/issue-4-end-to-end-prototype
fix/issue-12-upload-error
```

### 9.3 Issue 与 PR 关联

PR 描述中可以使用以下语法关联或关闭 Issue：

```text
Closes #2
Related to #3
Part of #4
```

使用规则：

1. 当该 PR 完整满足某个 Issue 的验收标准时，可以使用 `Closes #编号`；
2. 当该 PR 只与某个 Issue 相关，但尚未完成验收时，应使用 `Related to #编号`；
3. 当该 PR 只完成某个 Issue 的一部分时，应使用 `Part of #编号`；
4. 不得关闭尚未验收通过的 Issue。