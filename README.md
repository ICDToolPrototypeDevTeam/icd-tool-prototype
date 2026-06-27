# ICD工具原型Ver2.0

ICD工具原型Ver2.0是一个面向EoICD源文件和软件高层需求文件的智能化需求生成与差异分析工具。

本工具用于根据用户输入的EoICD源文件（Word + Excel）生成条目化需求，通过MiniMax和DeepSeek双模型多智能体生成候选结果并评分择优，将最优条目化需求与软件高层需求进行比对，识别二者之间的不一致问题，最终输出条目化需求文档和差异分析报告。

## 1. 项目目标

ICD工具原型Ver2.0聚焦EoICD场景，目标是建立一套从接口文档解析、条目化需求生成、候选结果评分择优，到软件高层需求差异比对和报告输出的端到端工具流程。

当前版本定位为本地演示版本，优先保证流程可运行、结果可展示、逻辑可解释。

## 2. 工具输入

工具输入包括：

1. EoICD 源文件：

   * 一份 Word 主文件；
   * 一个或多个 Excel 附件。

2. 软件高层需求文件：

   * 第一版优先支持 Word 文件。

## 3. 工具输出

工具最终输出 4 份 Word 文档（实际为 4+1 份，其中 1 份复用旧文件名）：

1. `MiniMax条目化需求.docx` — MiniMax 模型生成的全量候选合并
2. `DeepSeek条目化需求.docx` — DeepSeek 模型生成的全量候选合并
3. `最优条目化需求.docx` / `EoICD条目化需求.docx` — 评分择优后的最佳条目化需求（同一份文件，两份文件名）
4. `EoICD与软件高层需求差异报告.docx` — 差异比对报告

## 4. 核心流程

工具主要流程如下：

1. 用户上传 EoICD 源文件（Word + Excel）和软件高层需求文件；
2. 后端解析输入文件（Word 文档解析 + PubSub Excel 嵌套数据预处理），构建统一分析输入包（chunk 粒度）；
3. MiniMax M2.7 和 DeepSeek 双模型 CrewAI 多智能体生成多个 EoICD 条目化需求候选结果；
4. 双模型评分智能体和 Python 4 维硬规则评分器对候选结果进行综合评分；
5. 系统选择最佳 EoICD 条目化需求（跨模型择优）；
6. 系统将最佳 EoICD 条目化需求与软件高层需求进行差异比对；
7. 系统生成条目化需求文档（按模型 + 最优）和差异报告。

## 5. 当前范围

当前版本只关注 EoICD。

详细项目范围见：

* `docs/project/scope.md`

## 6. 技术栈

前端：

* React
* TypeScript

后端：

* Python
* FastAPI
* Pydantic
* openpyxl
* python-docx
* CrewAI
* LiteLLM

部署：

* Docker Compose

## 7. 工程结构

当前工程主要目录如下：

```text
icd-tool-prototype/
├── frontend/                 # 前端工程
├── backend/                  # 后端工程
├── docs/                     # 正式工程文档
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
| `docs/development/development-log.md`       | 开发纪要                  |
| `docs/development/debug-log.md`             | 问题排查记录                |
| `.claude/rules/context-rules.md`            | Claude Code 上下文读取规则   |
| `.claude/rules/debug-rules.md`              | Claude Code 调试规则      |
| `.claude/rules/documentation-rules.md`      | Claude Code 文档更新规则    |

## 9. 当前阶段

当前工程处于 ICD 工具 2.0 端到端原型验证阶段，已完成真实 LLM 接入（MiniMax M2.7 + DeepSeek）、EoICD 真实文件解析（Word + PubSub Excel）、CrewAI 多智能体条目化生成与评分择优、差异比对和 DOCX 输出。

## 10. 本地运行

完成 Docker Compose 配置后，预期启动方式为：

```bash
docker compose up -d --build
```

预期访问地址：

```text
前端：http://localhost:3000
后端：http://localhost:8000
```

## 11. 开发约定

本项目采用 GitHub Repo + GitHub Projects + Claude Code 的轻量化敏捷开发方式。

### 11.1 基本原则：

1. `main` 分支保持稳定；
2. 每个明确任务通过 Issue 跟踪；
3. 重要功能开发使用独立分支；
4. 大功能开发前先形成计划；
5. Debug 任务先定位问题，再进行最小修改；
6. 工程事实源以 `CLAUDE.md` 和 `docs/` 下文档为准；

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
