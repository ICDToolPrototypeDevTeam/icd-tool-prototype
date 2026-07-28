# ICD Tool — EoICD 与软件高层需求一致性分析工具

## 项目概述

对 EoICD PubSub Excel（Publisher/Subscriber Table）和软件高层需求 Word 文档做覆盖性/一致性分析，输出差异报告。

**一句话原则：脚本负责找，AI 负责判，脚本负责出报告。**

---

## 开发规则

### AI 行为规则

| 规则 | 详见 |
|------|------|
| 每次任务读取什么文件 | `.claude/rules/context-rules.md` |
| 调试流程（定位→修复→验证） | `.claude/rules/debug-rules.md` |
| 文档更新时机与目标 | `.claude/rules/documentation-rules.md` |

### 角色与职责

本项目由专业 ICD 工程师指导需求，AI 辅助编码实现。AI 的职责是：
- 理解需求后精确实现，不自行扩展或曲解
- 遇到模糊点必须询问，不允许猜测或编造
- 代码修改最小化，不越界修改不相关的模块
- **禁止私自使用 git 命令**：所有版本管理操作（commit、push、branch、merge、rebase 等）由人工介入。只有在用户明确要求使用 git 时才可执行

### 关键约束

1. **修改前先说明**：任何代码改动必须先向用户说明改动计划，获得确认后再执行
2. **model 变更 = 全量同步**：修改 `models.py` 中任何字段后，必须更新所有构造该 model 的代码
3. **修改后必须验证**：改了任何逻辑，用 `python -m app.main` 相关命令跑一遍确认输出
4. **不确定时回看 skill**：任何解析规则疑问，以 `ref_file/generation_skill_v4.md` 为准
5. **保持输出格式稳定**：JSON 字段变更需谨慎，下游管线依赖这些字段
6. **一次聚焦一个模块**：修改 parser 时不改 matching，修改 models 时不改 parser 逻辑

### 编码约定

- 文件头 `# -*- coding: utf-8 -*-`，JSON 序列化用 `ensure_ascii=False`
- 模型字段注释用行内 `# comment`，不写多行 docstring
- 不写无意义的注释，只注释 WHY 而非 WHAT
- 不过度抽象：三个相似代码块比一个过早的 helper 函数更好
- 不引入当前任务不需要的依赖或模块
- 不擅自重构现有代码

---

## 文件边界

| 文件/目录 | 职责 | 修改权限 |
|-----------|------|----------|
| `ref_file/` | **权威参考文件，只读** | 禁止修改 |
| `ref_file/generation_skill_v4.md` | EoICD 解析规则的唯一权威来源 | 禁止修改 |
| `ref_file/方案2.md` | 整体架构与方案设计 | 禁止修改 |
| `backend/app/config.py` | 常量配置（映射表、规则集、环境加载） | 可修改 |
| `backend/app/models.py` | Pydantic 数据模型 | 可修改，需同步更新所有构造点 |
| `backend/app/parsers/` | 解析器实现 | 可修改 |
| `backend/app/matching/` | 候选召回模块（正向7维匹配 + 反向两阶段匹配 + 信号画像 + HLR分类） | 可修改 |
| `backend/app/comparison/` | 对比裁判与报告生成（Case构造、单裁判、多Agent裁判、Review Agent、报告） | 可修改 |
| `backend/app/doc_generators/` | 文档生成器（Excel/Word/共识Word输出） | 可修改 |
| `backend/app/llm/` | LLM 抽象层（factory + deepseek client + mock） | 可修改 |
| `backend/app/prompts/` | 外部 Prompt 模板（.md） | 可修改 |
| `backend/app/traceability/` | 追溯表预筛选（HLR→ICD BlockKey 索引） | 可修改 |
| `backend/app/pipeline.py` | 管线编排（正向+反向） | 可修改 |
| `backend/app/job_manager.py` | 内存 Job 生命周期管理 | 可修改 |
| `backend/app/synonyms.yaml` | 别名映射表 | 可修改 |
| `backend/app/main.py` | CLI 入口 | 可修改 |
| `backend/requirements.txt` | Python 依赖声明 | 可修改 |
| `backend/.env.example` | API 配置模板（不含密钥） | 可修改 |
| `backend/output/` | 解析输出 JSON | 由脚本生成，勿手动编辑 |
| `CHANGELOG.md` | 功能级变更日志 | 完成大功能后更新 |
| `doc_input_file/` | 原始输入文件 | 禁止修改 |

完整边界与约束见 `docs/project/file-boundaries.md`。

---

## 架构概览

### 正向管线（EoICD → HLR）— 暂不使用

```
EoICD Excel + HLR Word → 解析 → 富化 → 匹配(Top-K) → Case构造 → 单AI裁判 → 报告
```

### 反向管线（HLR → EoICD）← 主力管线

```
HLR + EoICD → 条目过滤 → 信号画像 → ICD Block聚合 → 4路分类
→ [可选: 追溯表预筛选] → 反向匹配
→ 3 Agent 并行裁判（DeepSeek/MiniMax/Qwen）
→ Review Agent 共识（星级 1-3）
→ 报告
```

完整架构与 9 条设计决策见 `docs/architecture/current-architecture.md`。

---

## 项目结构

```
backend/app/
├── parsers/          ← EoICD Excel + HLR Word 解析
├── matching/         ← 候选召回（正向7维 + 反向6维 + Block聚合）
├── comparison/       ← Case构造 + 单裁判 + 多Agent裁判 + Review Agent + 报告
├── doc_generators/   ← Excel + Word 输出（含共识报告）
├── llm/              ← LLM 抽象层（factory + deepseek + mock）
├── prompts/          ← 外部 Prompt 模板（.md）
├── traceability/     ← 追溯表预筛选（HLR→BlockKey 索引）
├── config.py         ← 常量配置
├── models.py         ← Pydantic 数据模型（含 ConsensusResult）
├── synonyms.yaml     ← 别名映射表
├── pipeline.py       ← 管线编排（正向+反向）
├── job_manager.py    ← Job 生命周期管理
└── main.py           ← CLI 入口（14 个子命令）
```

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [README.md](README.md) | 项目说明与快速开始（给人类开发者） |
| [CHANGELOG.md](CHANGELOG.md) | 功能级版本变更记录 |
| [docs/architecture/current-architecture.md](docs/architecture/current-architecture.md) | 架构设计、8 条关键设计决策 |
| [docs/project/scope.md](docs/project/scope.md) | 项目范围、输入输出边界 |
| [docs/project/workflow.md](docs/project/workflow.md) | 正向+反向管线完整流程 |
| [docs/project/file-boundaries.md](docs/project/file-boundaries.md) | 文件边界与修改权限矩阵 |
| [docs/development/debug-log.md](docs/development/debug-log.md) | Bug 排查记录 |
| [docs/decisions/](docs/decisions/) | 架构决策记录 (ADR) |
| [docs/superpowers/specs/2026-07-21-architecture-refactor-v4-design.md](docs/superpowers/specs/2026-07-21-architecture-refactor-v4-design.md) | 架构重构 v4 设计说明 |
| `ref_file/generation_skill_v4.md` | EoICD 解析规则权威来源 |

---

## 验证命令

```bash
cd backend

# 反向管线全流程（主力：解析 → 匹配 → 3Agent裁判 → Review共识 → 报告）
python -m app.main reverse-analyze \
    --hlr "../doc_input_file/marked_doc_file/空气管理系统控制器控制通道控制软件高层需求规范(1).docx" \
    --publisher "../doc_input_file/marked_doc_file/AMS_EoICD_Publisher_Table.xlsx" \
    --subscriber "../doc_input_file/marked_doc_file/AMS_EoICD_Subscriber_Table.xlsx" \
    --output-dir output

# Mock 模式（不消耗 API，快速验证管线连通性）
USE_MOCK_LLM=1 python -m app.main reverse-analyze \
    --hlr "../doc_input_file/marked_doc_file/空气管理系统控制器控制通道控制软件高层需求规范(1).docx" \
    --publisher "../doc_input_file/marked_doc_file/AMS_EoICD_Publisher_Table.xlsx" \
    --subscriber "../doc_input_file/marked_doc_file/AMS_EoICD_Subscriber_Table.xlsx" \
    --output-dir output

# 生成共识 Word 报告（含星级）
python -m app.main generate-consensus-report \
    --consensus output/<run>/consensus_results.json \
    --output-dir output/

# 正向管线（暂不使用，兼容保留）
python -m app.main analyze \
    --publisher "../doc_input_file/marked_doc_file/AMS_EoICD_Publisher_Table.xlsx" \
    --subscriber "../doc_input_file/marked_doc_file/AMS_EoICD_Subscriber_Table.xlsx" \
    --hlr "../doc_input_file/marked_doc_file/空气管理系统控制器控制通道控制软件高层需求规范(1).docx" \
    --output-dir output --limit 20
```
