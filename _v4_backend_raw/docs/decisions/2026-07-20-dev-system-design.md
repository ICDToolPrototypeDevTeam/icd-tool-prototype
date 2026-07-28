# Development System & Governance Design

## Overview

为 ICD Tool 构建完整的开发治理体系，支撑 GitHub 多人协作。包括：AI 行为规则系统、文档树体系、GitHub 协作模板。

分两阶段执行：
- **阶段1**：治理体系搭建（本次）— 创建 md 文件、规则文件、GitHub 模板，不碰代码
- **阶段2**：架构重构（后续）— C 完整版方案，先建抽象层（LLM 层、Pipeline 框架）再迁移现有代码

---

## 1. Document Tree Structure

```
icd-tool-refactor-v4.0.1/
├── .claude/
│   └── rules/                        # AI 行为规则
│       ├── context-rules.md          #   上下文读取清单（按任务类型）
│       ├── debug-rules.md            #   调试规则（定位→修复→验证）
│       └── documentation-rules.md    #   文档更新规则（触发→目标矩阵）
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug.md
│   │   ├── feature.md
│   │   └── config.yml
│   └── pull_request_template.md
├── docs/
│   ├── architecture/
│   │   └── current-architecture.md   #   架构设计 + 8条设计决策
│   ├── project/
│   │   ├── scope.md                  #   项目范围 + 输入输出边界
│   │   ├── workflow.md               #   正向+反向管线流程
│   │   └── file-boundaries.md        #   文件边界与修改权限矩阵
│   ├── development/
│   │   ├── development-log.md        #   迭代开发纪要
│   │   └── debug-log.md              #   Bug 排查记录（从根目录搬入）
│   ├── decisions/                    #   ADR（架构决策记录）
│   │   └── .gitkeep
│   ├── knowledge/                    #   ICD/航空领域知识
│   │   └── .gitkeep
│   └── testing/
│       └── .gitkeep
├── CLAUDE.md                         # 精简为入口文件（~60行）
├── CHANGELOG.md                      # 保留
├── README.md                         # 新建：人类可读的项目说明
└── (existing code directories)
```

---

## 2. CLAUDE.md Slimming

Reduce from ~280 lines to ~60 lines. Only retain:
- Project one-line definition + core principles
- Development rules summary + pointers to `.claude/rules/`
- File boundary matrix (most frequently used constraint)
- Simplified project structure diagram
- Document index table → `docs/`
- Verification commands

Content migration:

| Original content | Lines | Destination |
|-----------------|-------|-------------|
| Design decisions 1-8 | ~200 | `docs/architecture/current-architecture.md` |
| Completed progress | ~30 | Delete (CHANGELOG tracks this; progress is volatile) |
| Parsing rules summary | ~20 | Reference `ref_file/generation_skill_v4.md` |
| Verification results | ~15 | `docs/development/development-log.md` |

---

## 3. `.claude/rules/` Design

### 3.1 `context-rules.md`

Defines file reading checklists per task type. Core logic: read on demand, don't read everything by default.

Task type → required files:

| Task type | Required | Optional |
|-----------|----------|----------|
| Parsing | `parsers/` file, `ref_file/generation_skill_v4.md` | `models.py`, `config.py` |
| Matching | `matching/` file, `models.py`, `config.py` | `synonyms.yaml` |
| Judging/reporting | `comparison/` file, prompt content | `models.py` |
| Model changes | `models.py` + Grep all construction sites | — |
| Architecture/governance | `CLAUDE.md`, `docs/architecture/`, `docs/project/` | `.claude/rules/` |
| Debug | `.claude/rules/debug-rules.md` + relevant module | `debug-log.md` |
| Documentation | `.claude/rules/documentation-rules.md` | — |
| New module | `docs/architecture/`, `docs/project/scope.md`, `docs/project/file-boundaries.md` | — |

Directories NOT to read by default: `backend/output/`, `doc_input_file/`, `ref_file/` (except skill file).

### 3.2 `debug-rules.md`

- Core principles: find root cause first, minimal fix, verify after change
- Pre-fix output required: symptom, repro, root cause, fix plan, affected files, scope excluded, verification command
- Verification: run relevant `python -m app.main` command

### 3.3 `documentation-rules.md`

- Document responsibility matrix: what each of ~10 docs contains and does NOT contain
- Trigger table: what changes trigger which doc updates
- CHANGELOG and ADR update rules

Trigger examples:
- New module → `architecture/` + `file-boundaries.md` + `CHANGELOG.md`
- Model field change → `CHANGELOG.md`
- Architecture decision → `decisions/` new file
- Debug complete → `debug-log.md`
- Feature iteration done → `development-log.md` + `CHANGELOG.md`

---

## 4. `docs/` Content Design

### 4.1 `docs/architecture/current-architecture.md`

1. Core architecture overview (forward + reverse dual pipeline diagram)
2. Module division & responsibilities (parsers/matching/comparison/generators)
3. Design decisions (8 items migrated from CLAUDE.md)
4. Data flow diagrams
5. Module boundary constraints
6. Architecture limitations & evolution direction

### 4.2 `docs/project/scope.md`

1. Project positioning: CLI tool, local execution
2. Input boundaries: EoICD PubSub Excel + HLR Word
3. Output boundaries: requirement JSON/Excel + difference report JSON/Word
4. Currently supported: forward + reverse dual pipeline
5. Not supported: Web UI, real-time analysis, hot-swap models
6. Scope change principles

### 4.3 `docs/project/workflow.md`

1. Forward pipeline 6 steps: parse → enrich → match → case build → AI judge → report
2. Reverse pipeline 8 steps: parse → filter → profile → block aggregate → classify → match → AI judge → report
3. Key decision points (Top-K, score thresholds, tiering)
4. Output file descriptions

### 4.4 `docs/project/file-boundaries.md`

Migrated from CLAUDE.md file boundary matrix, with added per-file responsibility descriptions.

### 4.5 `docs/development/development-log.md`

Record each feature iteration: goal, completed items, modified files, verification results.

### 4.6 `docs/development/debug-log.md`

Migrated from root `DEBUGLOG.md`, preserving all 7 existing records.

---

## 5. GitHub Templates

### 5.1 `.github/ISSUE_TEMPLATE/bug.md`

Sections: symptom, repro steps, expected result, actual result, scope, acceptance criteria.

### 5.2 `.github/ISSUE_TEMPLATE/feature.md`

Sections: goal, scope, excluded, acceptance criteria.

### 5.3 `.github/ISSUE_TEMPLATE/config.yml`

Enable blank issues, no contact links.

### 5.4 `.github/pull_request_template.md`

Sections: changes made, excluded, verification method, notes.

---

## 6. README.md

New file for human developers. Content:
1. Project purpose (one paragraph)
2. Tech stack (Python CLI, DeepSeek API, openpyxl, python-docx, pydantic)
3. Quick start (env setup, install deps, run command)
4. Document index (pointers to all docs/)
5. Branch naming convention
6. Development workflow (refer to rules/)

---

## 7. Root Directory Cleanup

- `DEBUGLOG.md` → content moved to `docs/development/debug-log.md`, then deleted
- `CHANGELOG.md` → kept at root
- New `README.md` → created

---

## 8. Implementation Order (Phase 1 Only)

1. Read existing content that needs migration
2. Create directory structure (`.claude/rules/`, `.github/ISSUE_TEMPLATE/`, `docs/` subdirs)
3. Write `.claude/rules/context-rules.md`
4. Write `.claude/rules/debug-rules.md`
5. Write `.claude/rules/documentation-rules.md`
6. Write `docs/architecture/current-architecture.md`
7. Write `docs/project/scope.md`
8. Write `docs/project/workflow.md`
9. Write `docs/project/file-boundaries.md`
10. Migrate `DEBUGLOG.md` → `docs/development/debug-log.md`, delete root copy
11. Write `.github/ISSUE_TEMPLATE/` files + PR template
12. Write `README.md`
13. Slim down `CLAUDE.md`
14. Verify all cross-references are correct

