# ICD Tool — EoICD 与软件高层需求一致性分析工具

对 EoICD PubSub Excel（Publisher/Subscriber Table）和软件高层需求（HLR）Word 文档做覆盖性/一致性分析，输出结构化差异报告。

## 技术栈

- **Language**: Python 3.x
- **CLI Framework**: argparse (stdlib)
- **Data Models**: Pydantic v2
- **Excel**: openpyxl
- **Word**: python-docx
- **AI**: DeepSeek API (OpenAI-compatible)
- **Similarity**: Okapi BM25 (in-house)

## 快速开始

```bash
cd backend

# 1. 配置环境
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY

# 2. 安装依赖
pip install -r requirements.txt

# 3. 一键全流程
python -m app.main analyze \
    --publisher "../doc_input_file/AMS_EoICD_Publisher_Table.xlsx" \
    --subscriber "../doc_input_file/AMS_EoICD_Subscriber_Table.xlsx" \
    --hlr "../doc_input_file/HLR_Requirements.docx" \
    --output-dir output \
    --top-k 5
```

## 文档索引

| 文档 | 内容 |
|------|------|
| [CLAUDE.md](CLAUDE.md) | AI 开发入口 — 项目原则、规则、边界 |
| [CHANGELOG.md](CHANGELOG.md) | 功能级版本变更记录 |
| [docs/architecture/current-architecture.md](docs/architecture/current-architecture.md) | 架构设计、8 条关键设计决策 |
| [docs/project/scope.md](docs/project/scope.md) | 项目范围、输入输出边界 |
| [docs/project/workflow.md](docs/project/workflow.md) | 正向+反向管线完整流程 |
| [docs/project/file-boundaries.md](docs/project/file-boundaries.md) | 文件边界与修改权限矩阵 |
| [docs/development/development-log.md](docs/development/development-log.md) | 迭代开发纪要 |
| [docs/development/debug-log.md](docs/development/debug-log.md) | Bug 排查记录 |
| [docs/decisions/](docs/decisions/) | 架构决策记录 (ADR) |
| [docs/knowledge/](docs/knowledge/) | ICD/航空领域知识 |
| [.claude/rules/](.claude/rules/) | AI 行为规则（上下文、调试、文档更新） |

## 分支命名

| 前缀 | 用途 |
|------|------|
| `feat/` | 新功能 |
| `fix/` | Bug 修复 |
| `refactor/` | 重构（不改变行为） |
| `docs/` | 文档更新 |
| `chore/` | 工程配置、依赖 |

## 开发流程

1. 从 `main` 创建功能分支
2. 按 `.claude/rules/context-rules.md` 读取相关文件
3. 修改代码（遵守 `CLAUDE.md` 中的编码约定和变更范围控制）
4. 用 `python -m app.main` 相关命令验证
5. 更新 `CHANGELOG.md` 和相关文档（参见 `.claude/rules/documentation-rules.md`）
6. 提交 PR（使用 PR 模板）
