# ICD工具原型Ver2.0

ICD工具原型Ver2.0是一个面向EoICD源文件和软件高层需求文件的智能化需求生成与差异分析工具。

本工具包含两条互补管线：V3 正向管线从 EoICD 出发生成条目化需求，并与软件高层需求进行差异比对；V4 反向管线从软件高层需求出发，验证其到 EoICD 接口定义的可追溯性。

**V3 正向管线**用于根据用户输入的 EoICD 源文件（Word + PubSub Excel）和软件高层需求文件，通过 MiniMax 和 DeepSeek 双模型 CrewAI 多智能体生成条目化需求候选结果并评分择优，再将最优条目化需求与软件高层需求进行差异比对，最终输出条目化需求文档和差异分析报告。

**V4 反向管线**用于根据用户输入的软件高层需求文件（HLR Word）和 EoICD PubSub Excel（Publisher/Subscriber 二选一），通过解析→HLR AI 标注→反向匹配→DeepSeek / MiniMax / Qwen 三模型并行裁判→Review Agent 共识复核，验证每条 HLR 需求是否能在 EoICD 中找到对应的接口定义（即 HLR 到 EoICD 的可追溯性），最终输出条目化清单、单模型一致性报告和多模型共识分析报告（含星级评分）。

## 1. 项目目标

ICD工具原型Ver2.0聚焦EoICD场景，目标是建立一套从接口文档解析、条目化需求生成、候选结果评分择优，到软件高层需求差异比对和报告输出的端到端工具流程。V4 在此基础上新增反向可追溯性分析能力，从 HLR 出发验证每条 HLR 需求在 EoICD 中是否有对应的接口定义。

当前版本定位为本地演示版本，优先保证流程可运行、结果可展示、逻辑可解释。

## 2. 工具输入

### V3 正向管线输入

1. EoICD 源文件：

   * 一份 Word 主文件；
   * 一个或多个 PubSub Excel 附件。

2. 软件高层需求文件：

   * 第一版优先支持 Word 文件。

### V4 反向管线输入

1. HLR Word 文件（必填）：

   * 软件高层需求 Word 文档（.docx），从"软件需求"章节下的需求表格中提取字段。

2. EoICD PubSub Excel 文件（二选一）：

   * Publisher Excel（.xlsx）；
   * Subscriber Excel（.xlsx）。

3. 追溯 Excel 文件（选填，0-N 个）：

   * 设备→HLR / 设备→ICD 追溯表，启用 `enable_traceability_prefilter` 时建议提供。

## 3. 工具输出

### V3 正向管线输出

工具输出 4 份 Word 文档（实际为 4+1 份，其中 1 份复用旧文件名）：

1. `MiniMax条目化需求.docx` — MiniMax 模型生成的全量候选合并
2. `DeepSeek条目化需求.docx` — DeepSeek 模型生成的全量候选合并
3. `最优条目化需求.docx` / `EoICD条目化需求.docx` — 评分择优后的最佳条目化需求（同一份文件，两份文件名）
4. `EoICD与软件高层需求差异报告.docx` — 差异比对报告

### V4 反向管线输出

工具输出 5 份文档（1 份 xlsx + 4 份 docx）：

1. `EoICD条目化清单.xlsx` — 条目化需求 Excel 清单（HL vs leaf 属性表）
2. `EoICD与SWHLR单模型差异分析报告_DeepSeek.docx` — DeepSeek 单模型一致性分析（真实 LLM）
3. `EoICD与SWHLR单模型差异分析报告_MiniMax.docx` — MiniMax 单模型一致性分析（当前 mock，待 Issue F 真实接入）
4. `EoICD与SWHLR单模型差异分析报告_Qwen.docx` — Qwen 单模型一致性分析（当前 mock，待 Issue F 真实接入）
5. `EoICD与SWHLR多模型差异分析报告.docx` — 三模型共识分析报告（含星级评分 1-3★ 和不一致属性栏明细）

> **说明**：当前 V4 仅 DeepSeek 为真实 LLM 接入；MiniMax 和 Qwen 在 `USE_MOCK_LLM=0` 且配置了 API Key 时走真实调用，否则走 mock。Mock 状态通过 `/api/v4/jobs/{id}/result.mock_models` 显式标识。

## 4. 核心流程

### V3 正向管线

工具主要流程如下：

1. 用户上传 EoICD 源文件（Word + PubSub Excel）和软件高层需求文件；
2. 后端解析输入文件（Word 文档解析 + PubSub Excel 嵌套数据预处理），构建统一分析输入包（chunk 粒度）；
3. MiniMax 和 DeepSeek 双模型 CrewAI 多智能体生成多个 EoICD 条目化需求候选结果；
4. 双模型评分智能体和 Python 4 维硬规则评分器对候选结果进行综合评分；
5. 系统选择最佳 EoICD 条目化需求（跨模型择优）；
6. 系统将最佳 EoICD 条目化需求与软件高层需求进行差异比对；
7. 系统生成条目化需求文档（按模型 + 最优）和差异报告。

### V4 反向管线

V4 反向管线以 HLR 为起点，验证每条 HLR 需求是否能在 EoICD 中找到对应的接口定义（即 HLR 到 EoICD 的可追溯性）：

1. **Step 1 — 解析输入**：解析 HLR Word + EoICD PubSub Excel → 结构化需求列表，同时生成 EoICD 条目化清单 Excel
2. **Step 2 — HLR AI 标注**：DeepSeek 对每条 HLR 标注 bus_types / labels / devices / signal_keywords
3. **Step 3 — 反向匹配**：HLR → EoICD Block 级匹配（Label 前缀粗筛 → 6 维评分 → 三级分层），可选追溯表预筛选 + 兜底机制
4. **Step 4 — 多模型裁判**：DeepSeek / MiniMax / Qwen 三模型并行独立判定
5. **Step 5 — Review Agent 共识**：对三模型判定结果综合复核并给出星级评价（1-3★）
6. **Step 6 — 报告生成**：输出 1 份 xlsx + 4 份 docx（含不一致属性栏等多维度分析明细）

## 5. 当前范围

当前版本支持 EoICD 正向条目化（V3）和 HLR→EoICD 反向可追溯性分析（V4）两条独立管线，双版本在统一 FastAPI 入口下共存。

V3 正向管线聚焦"EoICD 怎么写"——从 EoICD 源文件生成条目化需求，并与软件高层需求比对。

V4 反向管线聚焦"HLR 到 EoICD 的可追溯性"——从 HLR 出发，判断每条 HLR 需求在 EoICD 中是否有对应的接口定义。

当前版本暂不支持：EICD / MICD / 通用 ICD 文档处理、多项目管理、用户权限管理、在线协同编辑、数据库存储、云端部署、企业级审批流、正式生产环境发布。

详细项目范围见：

* `docs/project/scope.md`

## 6. 技术栈

前端：

* React
* TypeScript
* mammoth（Word 文档预览）
* xlsx（Excel 表格预览）
* lucide-react（图标库）

后端：

* Python
* FastAPI
* Pydantic
* openpyxl
* python-docx
* python-dotenv
* PyYAML
* requests
* CrewAI（V3 多智能体编排）
* LiteLLM（V3 LLM 接入层）
* DeepSeek（V4 主要 LLM，真实接入）
* MiniMax（V3/V4 LLM，V4 当前 mock）
* Qwen / DashScope（V4 LLM，当前 mock）

部署：

* Docker Compose

## 7. 工程结构

当前工程主要目录如下：

```text
icd-tool-prototype/
├── frontend/                 # 前端工程
├── backend/                  # 后端工程
│   ├── app/
│   │   ├── api/v3/           # V3 API 路由
│   │   ├── api/v4/           # V4 API 路由（router / schemas / runner / coverage / jobs / outputs）
│   │   ├── v3/               # V3 业务模块（crew / merge / scoring / docx / parsers / llm / prompts / skills）
│   │   ├── v4/               # V4 业务模块（反向管线）
│   │   │   ├── comparison/   #   多模型裁判 + Review Agent 共识 + 报告生成
│   │   │   ├── matching/     #   反向匹配（HLR 标注、信号画像、Block 聚合、HLR 分类、6 维评分）
│   │   │   ├── llm/          #   LLM 抽象层（DeepSeek / MiniMax / Qwen Client + Mock）
│   │   │   ├── doc_generators/  # xlsx + 单模型 docx + 共识 docx 生成
│   │   │   ├── parsers/      #   EoICD PubSub Excel + HLR Word 解析
│   │   │   ├── traceability/ #   追溯表预筛选（独立 zero-coupling 模块）
│   │   │   └── prompts/      #   V4 Prompt 文本资产
│   │   ├── job_manager.py    # 共享任务状态管理（带 kind 字段区分 V3/V4）
│   │   └── main.py           # FastAPI 入口（thin shell，仅 CORS + 路由装载）
│   └── output/               # 运行时输出文件目录
│       ├── v3/{job_id}/      # V3 任务输出（平铺）
│       └── v4/{job_id}/      # V4 任务输出（input/ + output/ 分层）
├── docs/                     # 正式工程文档
├── issues_file/              # Issue 草稿文件
├── .claude/                  # Claude Code 规则和辅助文件
├── README.md                 # 项目说明
├── CLAUDE.md                 # Claude Code 工作入口
├── CHANGELOG.md              # 版本级变更记录
└── .gitignore
```

详细架构说明见：

* `docs/architecture/current-architecture.md`

## 8. 重要文档

项目文档入口如下：

| 文档                                          | 说明                    |
| ------------------------------------------- | --------------------- |
| `README.md`                                 | 项目总体说明                |
| `CLAUDE.md`                                 | Claude Code 工作入口和项目规则 |
| `docs/project/scope.md`                     | 项目范围说明                |
| `docs/project/workflow.md`                  | 业务流程说明                |
| `docs/architecture/current-architecture.md` | 当前软件架构说明              |
| `docs/architecture/api.md`                  | API 设计说明              |
| `docs/decisions/ADR-001-V4后端接入策略.md`       | V4 后端工程化集成关键决策        |
| `docs/development/development-log.md`       | 开发纪要                  |
| `docs/development/debug-log.md`             | 问题排查记录                |
| `.claude/rules/context-rules.md`            | Claude Code 上下文读取规则   |
| `.claude/rules/debug-rules.md`              | Claude Code 调试规则      |
| `.claude/rules/documentation-rules.md`      | Claude Code 文档更新规则    |

## 9. 当前阶段

当前工程处于 ICD 工具 2.0 端到端原型验证阶段。

**V3 正向管线**已完成：真实 LLM 接入（MiniMax + DeepSeek）、EoICD 真实文件解析（Word + PubSub Excel）、CrewAI 多智能体条目化生成与评分择优、差异比对和 DOCX 输出。

**V4 反向管线**已完成：HLR → EoICD 可追溯性分析（解析→HLR AI 标注→反向匹配→三模型裁判→Review Agent 共识→报告生成），DeepSeek / MiniMax / Qwen 三智能体并行判定 + 星级评分共识，输出 5 份产物（含不一致属性栏等多维度分析明细）。当前仅 DeepSeek 为真实 LLM 接入，MiniMax 和 Qwen 在 V4 中走 mock（待 Issue F 真实接入），mock 状态通过 API 响应的 `mock_models` 字段显式标识。

**前端**当前默认使用 V4 反向管线界面（V4 专用上传组件、处理进度、结果展示与下载），V3 后端路由保留可用。

V3 与 V4 通过独立 API 命名空间（`/api` 与 `/api/v4`）双版本共存，共享 JobManager（带 `kind` 字段区分），跨版本查询返回 404。

## 10. 本地运行

### 10.1 环境准备

在项目根目录下配置 `backend/.env` 文件（参考 `backend/.env.example`）：

```bash
# DeepSeek（V4 主要 LLM，必填）
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash

# MiniMax（V3 必填，V4 可选）
MINIMAX_API_KEY=your_minimax_api_key
MINIMAX_BASE_URL=https://api.minimax.chat
MINIMAX_MODEL=abab7-chat

# Qwen / DashScope（V4 可选）
QWEN_API_KEY=your_qwen_api_key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus

# V4 裁判模型白名单（逗号分隔，默认 deepseek,minimax,qwen）
JUDGE_PROVIDERS=deepseek,minimax,qwen

# Mock 模式开关（设为 1 时所有 LLM 调用走 mock，无需配置 API Key）
USE_MOCK_LLM=0
```

### 10.2 启动方式

```bash
docker compose up -d --build
```

预期访问地址：

```text
前端：http://localhost:3000
后端：http://localhost:8000
后端 V4 健康检查：http://localhost:8000/api/v4/health
```

## 11. 开发约定

本项目采用 GitHub Repo + GitHub Projects + Claude Code 的轻量化敏捷开发方式。

### 11.1 基本原则

1. `main` 分支保持稳定；
2. 每个明确任务通过 Issue 跟踪；
3. 重要功能开发使用独立分支；
4. 大功能开发前先形成计划；
5. Debug 任务先定位问题，再进行最小修改；
6. 工程事实源以 `CLAUDE.md` 和 `docs/` 下文档为准；
7. Claude Code 不得主动创建分支、push、创建/合并 PR、关闭 Issue 或修改 Project 看板状态（由用户手动完成）。

### 11.2 分支命名约定

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

### 11.3 Issue 与 PR 关联

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