# CLAUDE.md

本文件是 Claude Code 在本项目中的工作入口。Claude Code 每次开始任务前应先阅读本文件，并根据任务类型读取相关工程文档和源代码文件。

## 1. 项目身份

本项目为 **ICD工具原型Ver2.0**。

ICD工具原型Ver2.0是一个面向 EoICD 源文件和软件高层需求文件的智能化需求生成与差异分析工具。

项目范围以 `docs/project/scope.md` 为准。

## 2. 项目目标

本工具的核心目标是：

1. 根据用户输入的 EoICD 源文件生成 EoICD 条目化需求；
2. 将生成的 EoICD 条目化需求与软件高层需求进行差异比对；
3. 输出两个 Word 文档：

   * `EoICD条目化需求.docx`
   * `EoICD与软件高层需求差异报告.docx`

## 3. 核心业务流程

详细业务流程以 `docs/project/workflow.md` 为准。Claude Code 开发时必须遵守“输入文件只解析一次，后续流程复用统一分析输入包”的原则。

## 4. 多智能体基本规则

当前规划采用 CrewAI 建立多智能体流程。多智能体生成、评分和择优规则以 `docs/project/workflow.md` 为准。相关实现不得写死在 FastAPI 路由中，应由后端业务模块统一编排。

## 5. 按任务类型读取文档

各任务读取上下文方式以 `.claude/rules/context-rules.md` 为准。

每次开始任务时，必须先阅读：

1. `CLAUDE.md`
2. `.claude/rules/context-rules.md`
3. 与当前任务直接相关的工程文档
4. 与当前任务直接相关的源代码文件

不得默认读取整个工程。

## 6. 目录职责约束

目录职责以 `docs/architecture/current-architecture.md` 为准。不得把文件解析、智能体生成、评分、差异比对和 DOCX 输出混写在同一个大文件中。`main.py` 不得承载复杂业务逻辑。

## 7. 开发规则

执行开发任务时，应遵守以下流程：

1. 先说明任务理解；
2. 列出准备读取的文件；
3. 列出准备修改或新增的文件；
4. 大功能先形成计划，等待确认后再实施；
5. 只实现当前 Issue 范围内的内容；
6. 不得顺手实现后续 Issue；
7. 修改完成后给出验证命令；
8. 按需更新相关工程文档。

若使用 Superpowers 或类似插件，其 plan/spec 仅作为过程记录，仍需遵守本文件约束。

## 8. Debug 规则

Debug 任务必须遵守 `.claude/rules/debug-rules.md`。不得猜测式修改、不得顺手重构、不得修改无关文件。

## 9. 文档更新规则

文档更新任务必须遵守 `.claude/rules/documentation-rules.md`。不得把临时过程记录当作正式工程事实源。

## 10. 工程事实源优先级

如果不同文档或记录之间存在冲突，优先级按以下原则判断：

1. 用户当前明确指令优先；
2. `CLAUDE.md` 作为 Claude Code 工作入口和通用规则来源；
3. 项目范围以 `docs/project/scope.md` 为准；
4. 业务流程以 `docs/project/workflow.md` 为准；
5. 软件架构以 `docs/architecture/current-architecture.md` 为准；
6. API 设计以 `docs/architecture/api.md` 为准；
7. Claude 行为规则以 `.claude/rules/*.md` 为准；
8. 开发过程记录以 `docs/development/*.md` 为参考；
9. Superpowers plans/specs 和 remember.md 只作为过程记录，不作为最终工程事实源。

## 11. GitHub Issue、分支与 PR 规则

本项目采用 GitHub Repo + GitHub Projects + Claude Code 的轻量化敏捷开发方式。

### 11.1 基本原则

1. `main` 分支保持稳定；
2. 每个明确任务通过 GitHub Issue 跟踪；
3. 重要功能开发使用独立分支；
4. 代码已生成不等于功能已验收；
5. Issue 是否关闭以验收标准是否满足为准。

### 11.2 Claude Code 权限边界

当前阶段，Claude Code 只负责在用户已经准备好的本地分支中进行开发、修改、测试和说明。

Claude Code 不得主动执行以下操作，除非用户明确要求：

1. 不得主动创建 Git 分支；
2. 不得主动切换 Git 分支；
3. 不得主动向远程仓库 push；
4. 不得主动创建 Pull Request；
5. 不得主动合并 Pull Request；
6. 不得主动关闭 GitHub Issue；
7. 不得主动修改 GitHub Project 看板状态；
8. 不得主动修改仓库权限、分支保护规则或 Project 权限配置。

上述 GitHub 协作动作由用户手动完成。

## 12. 任务结束汇报要求

每次任务完成后，必须汇报：

1. 本次完成内容；
2. 修改文件；
3. 新增文件；
4. 删除文件；
5. 更新文档；
6. 验证命令；
7. 验证结果；
8. 遗留问题；
9. 下一步建议。

如果未进行验证，必须明确说明“尚未验证”，不得假装验证通过。
