# Claude Code 上下文读取规则

本文档用于规定 Claude Code 在 **ICD工具原型** 项目中的上下文读取方式。

## 1. 基本原则

Claude Code 执行任务前，应先明确任务类型，再读取对应文件。

基本原则如下：

1. 不默认读取整个工程；
2. 不默认读取大型文件；
3. 不默认读取样例输入文件；
4. 不默认读取历史过程记录；
5. 优先读取当前任务直接相关的文档和代码；
6. 如上下文不足，应先说明缺失信息，再请求用户确认是否继续读取。

## 2. 默认读取顺序

每次任务开始时，默认读取顺序如下：

```text
1. CLAUDE.md
2. .claude/rules/context-rules.md
3. 与当前任务直接相关的 docs 文档
4. 与当前任务直接相关的源代码文件
```

不得在未说明理由的情况下扫描全仓库。

## 3. 按任务类型读取文件

### 3.1 项目范围类任务

读取文件：

```text
CLAUDE.md
.claude/rules/context-rules.md
docs/project/scope.md
```

### 3.2 业务流程类任务

读取文件：

```text
CLAUDE.md
.claude/rules/context-rules.md
docs/project/workflow.md
docs/architecture/current-architecture.md
```

### 3.3 架构类任务

读取文件：

```text
CLAUDE.md
.claude/rules/context-rules.md
docs/architecture/current-architecture.md
docs/project/workflow.md
```

### 3.4 API 类任务

读取文件：

```text
CLAUDE.md
.claude/rules/context-rules.md
docs/architecture/api.md
docs/architecture/current-architecture.md
```

如涉及具体实现，再读取相关前后端代码文件。

### 3.5 前端开发任务

读取文件：

```text
CLAUDE.md
.claude/rules/context-rules.md
docs/project/workflow.md
docs/architecture/api.md
docs/architecture/current-architecture.md
frontend/src/ 下与任务相关的文件
```

不得默认读取整个 `frontend/`。

### 3.6 后端开发任务

读取文件：

```text
CLAUDE.md
.claude/rules/context-rules.md
docs/project/workflow.md
docs/architecture/current-architecture.md
docs/architecture/api.md
backend/app/ 下与任务相关的文件
```

不得默认读取整个 `backend/`。

### 3.7 Debug 任务

读取文件：

```text
CLAUDE.md
.claude/rules/context-rules.md
.claude/rules/debug-rules.md
docs/development/debug-log.md
用户提供的报错信息
与报错直接相关的代码文件
```

Debug 任务应先定位问题，再提出最小修复方案，经用户许可后最后修改代码。

### 3.8 文档更新任务

读取文件：

```text
CLAUDE.md
.claude/rules/context-rules.md
.claude/rules/documentation-rules.md
需要更新的目标文档
```

不得为了更新单个文档而默认读取全部 docs。

## 4. 不应默认读取的目录和文件

以下目录和文件不得默认读取，除非用户明确要求或当前任务确实需要：

```text
docs/knowledge/
docs/decisions/
docs/testing/
backend/output/
backend/app/output/
node_modules/
.venv/
frontend/dist/
frontend/build/
Superpowers 历史 plans/specs
大型 Word、Excel、PDF 样例文件
运行时生成的输出文件
```

原因如下：

1. 可能包含过时信息；
2. 可能占用大量上下文；
3. 可能与当前任务无关；
4. 可能导致 Claude Code 将样例或过程记录误认为正式规则。

## 5. 上下文不足时的处理方式

当当前上下文不足以完成任务时，Claude Code 应：

1. 明确说明缺少哪些信息；
2. 说明需要读取哪些额外文件；
3. 说明读取这些文件的原因；
4. 等待用户确认后再扩大读取范围。

不得在上下文不足时直接猜测项目设计。

## 6. 读取范围控制要求

1. 优先读取最小必要文件集合，不得为了“保险”读取整个仓库；
2. 不得将历史过程记录、运行时输出文件或样例输入文件作为项目设计依据；
3. 如需扩大读取范围，应先说明理由并等待用户确认。
