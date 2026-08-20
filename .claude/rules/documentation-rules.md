# Claude Code 文档更新规则

本文档用于规定 Claude Code 在 **ICD工具原型** 项目中的文档更新原则、更新范围和文档职责边界。

## 1. 基本原则

文档更新应遵守以下原则：

1. 文档只记录其职责范围内的信息；
2. 不在多个文档中重复维护同一类详细内容；
3. 修改代码或流程后，应同步检查是否需要更新相关文档；
4. 不得把临时过程记录当作正式工程事实源；
5. 不得把开发日志写入架构文档；
6. 不得把 Bug 排查过程写入 README；
7. 不得把 API 字段散落写入多个文档；
8. 如不确定是否需要更新文档，应在任务完成汇报中说明。

## 2. 文档职责边界

| 文档或目录                                       | 主要职责                              | 不写入内容                     |
| ------------------------------------------- | --------------------------------- | ------------------------- |
| `README.md`                                 | 项目首页、输入输出概览、技术栈、文档入口、协作约定         | 详细架构、详细 API、开发日志、Bug 复盘   |
| `CHANGELOG.md`                              | 版本级变更记录，包括新增、变更、修复、废弃能力           | 每日开发过程、完整 Bug 排查过程、临时草稿变化 |
| `CLAUDE.md`                                 | Claude Code 工作入口、核心红线、读取入口、任务汇报要求 | 完整范围、完整流程、完整 API、每日日志     |
| `docs/project/scope.md`                     | 项目范围、输入输出边界、版本限制                  | API 字段、代码目录、开发日志          |
| `docs/project/workflow.md`                  | 业务流程、阶段输入输出、关键流程约束                | 代码实现、目录结构、GitHub 规则       |
| `docs/architecture/current-architecture.md` | 软件架构、模块职责、目录边界                    | 项目范围长篇说明、API 字段细节、开发日志    |
| `docs/architecture/api.md`                  | 接口路径、请求响应、任务状态、下载方式               | 页面布局、后端内部实现、Prompt 细节     |
| `docs/development/development-log.md`       | Issue 开发纪要、主要修改、验证结果、遗留问题         | 架构设计、完整错误堆栈、Bug 复盘        |
| `docs/development/debug-log.md`             | 问题现象、原因分析、修复方案、验证结果               | 普通开发进度、项目范围、API 草案        |
| `docs/decisions/`                           | 重大技术或项目决策记录，采用 ADR 形式             | 普通开发过程、临时想法、未经确认的方案堆积     |
| `docs/knowledge/`                           | EoICD、需求条目化、接口文档处理、领域规则等知识沉淀      | 当前任务过程记录、运行时输出、未经整理的样例全文  |
| `docs/testing/`                             | 测试计划、手工测试清单、验收用例、演示验证记录           | 普通开发日志、Bug 详细复盘、项目范围说明    |
| `.claude/rules/context-rules.md`            | Claude Code 上下文读取规则               | 业务细节、API 字段、Bug 内容        |
| `.claude/rules/debug-rules.md`              | Claude Code 调试行为规则                | 具体 Bug 记录、项目范围、API 草案     |
| `.claude/rules/documentation-rules.md`      | 文档职责边界、更新触发条件、去重原则、事实源关系          | 具体开发日志、具体 Bug 记录          |

## 3. 文档更新触发条件

出现以下情况时，应更新对应文档或目录。

| 变化类型                       | 应更新文档或目录                                                       |
| -------------------------- | -------------------------------------------------------------- |
| 项目名称、定位或输入输出范围变化           | `README.md`、`docs/project/scope.md`，必要时更新 `CLAUDE.md`          |
| 当前支持范围或不支持范围变化             | `docs/project/scope.md`                                        |
| 业务处理流程变化                   | `docs/project/workflow.md`                                     |
| 前后端职责变化                    | `docs/architecture/current-architecture.md`                    |
| 后端模块职责变化                   | `docs/architecture/current-architecture.md`                    |
| API 路径、请求字段、响应字段变化         | `docs/architecture/api.md`                                     |
| 任务状态定义变化                   | `docs/architecture/api.md`，必要时更新 `docs/project/workflow.md`    |
| 输出文件名称或下载方式变化              | `README.md`、`docs/project/scope.md`、`docs/architecture/api.md` |
| 新增主要功能能力                   | `CHANGELOG.md`，必要时更新相关设计文档                                     |
| 修改已有主要能力                   | `CHANGELOG.md`，必要时更新相关设计文档                                     |
| 修复影响功能使用的问题                | `CHANGELOG.md`，如需复盘则更新 `docs/development/debug-log.md`         |
| 完成一个明确 Issue               | `docs/development/development-log.md`                          |
| 修复一个需要复盘的问题                | `docs/development/debug-log.md`                                |
| Claude Code 工作边界变化         | `CLAUDE.md`                                                    |
| Claude Code 读取规则变化         | `.claude/rules/context-rules.md`                               |
| Debug 行为规则变化               | `.claude/rules/debug-rules.md`                                 |
| 文档维护规则变化                   | `.claude/rules/documentation-rules.md`                         |
| 发生重大技术决策                   | `docs/decisions/ADR-xxx-决策名称.md`                               |
| 沉淀 EoICD、需求条目化、接口文档处理等领域知识 | `docs/knowledge/` 下相应知识文档                                      |
| 新增测试策略、验收清单或演示验证要求         | `docs/testing/` 下相应测试文档                                        |

当前 `docs/decisions/`、`docs/knowledge/`、`docs/testing/` 可作为预留目录存在，未启用时仅保留 `.gitkeep`。

## 4. 临时记录与正式事实源

以下内容属于过程记录，不作为最终工程事实源：

1. Superpowers plans；
2. Superpowers specs；
3. remember.md；
4. 临时讨论记录；
5. 草稿 Prompt；
6. 临时测试输出；
7. 运行时生成的 Word 或 Excel 文件。

如果过程记录中的内容需要长期保留，应整理后写入对应正式文档，而不是直接复制全部过程内容。

## 5. 文档更新方式

更新文档时，应遵守以下方式：

1. 优先修改最相关的一份文档，必要时再同步更新相关文档；
2. 不为小变化大范围重写文档，不把未确认设计写成确定结论；
3. 原型阶段未确定内容可标记为“草案”“待细化”或“后续实现阶段确认”。

## 6. CHANGELOG 更新规则

`CHANGELOG.md` 只记录版本级变化，不记录每日开发流水账。

适合写入 `CHANGELOG.md` 的内容包括：

1. 新增主要能力；
2. 修改主要能力；
3. 修复影响功能的问题；
4. 删除或废弃能力；
5. 影响用户使用方式的变化。

不适合写入 `CHANGELOG.md` 的内容包括：

1. 单次普通文档修改；
2. 每日开发纪要；
3. 详细 Bug 排查过程；
4. 临时调试记录；
5. 中间草稿变化。

## 7. ADR 更新规则

当出现重大技术或项目决策时，应新增 ADR。

适合记录为 ADR 的情况包括：

1. 是否继续只支持 EoICD；
2. 是否引入数据库；
3. 是否改变多智能体方案；
4. 是否调整主要技术栈；
5. 是否改变部署方式；
6. 是否改变评分策略；
7. 是否改变核心输出文档格式。

ADR 文件建议放在：

```text
docs/decisions/
```

命名格式建议为：

```text
ADR-001-决策名称.md
```

ADR 内容建议包括：

```text
# ADR-001 决策名称

## 背景

## 决策

## 原因

## 影响

## 替代方案

## 状态
```

## 8. 文档完成汇报要求

完成文档修改后，Claude Code 应汇报：

```text
修改文档：
主要修改内容：
是否涉及范围变化：
是否涉及流程变化：
是否涉及架构变化：
是否涉及 API 变化：
是否需要同步更新其他文档：
尚未确认或待细化内容：
```

如果未更新某个相关文档，应说明原因。
