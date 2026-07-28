# Development Log

## 2026-07-24 — 同事分支一致性改进合并 + DeepSeek 单模型报告

**目标：** 融合同事分支（`icd-tool-refactor-v4.0.1.1`）的一致性对比优化，保留我方多模型裁判架构，新增 DeepSeek 单模型 Word 报告。

**完成事项：**
- 直接采用同事改进（4 文件）：`prompts/reverse_judge.md`（四步审查法）、`comparison/semantic_judge.py`（JSON 截断修复+sub_signals）、`comparison/report_generator.py`、`llm/mock_llm.py`（四分类简化）
- 手工合并冲突（2 文件）：`models.py`（coverage_status 注释）、`matching/reverse_matcher.py`（保留两遍 bit_field 评分，采纳去 [:5] 截断）
- 保留我方文件（3 文件）：`pipeline.py`、`main.py`、`doc_generators/consensus_word_generator.py`（多模型共识架构不动）
- 重写 `doc_generators/word_generator.py`：`generate-consistency-report` 改为输出 DeepSeek 单模型报告，新增判定分类说明、概览表（含占比）、分析明细表（HLR ID/判定/ICD Block/分析摘要/置信度），数据来源 cross-reference `reverse_report.json` + `reverse_matches.json`

**改动原则：** 同事的提示词和输出简化适用于单模型也适用于多模型；匹配层增强在裁判之前独立于架构。融合后仅暴露 DeepSeek 单模型结果到一致性报告。

**涉及文件：**
- 直接复制：`prompts/reverse_judge.md`、`comparison/semantic_judge.py`、`comparison/report_generator.py`、`llm/mock_llm.py`
- 手工合并：`models.py`、`matching/reverse_matcher.py`
- 保留我方：`pipeline.py`、`main.py`、`doc_generators/consensus_word_generator.py`
- 重写：`doc_generators/word_generator.py`
- 文档更新：`CHANGELOG.md`、`docs/development/development-log.md`

**补充（同日后续） — 管线文档集成：**
- 管线自动产出 Word/Excel：`reverse-analyze` Step 1 自动输出 `EoICD条目化清单.xlsx`，Step 5 自动输出 3 份单模型报告 + 1 份共识报告（`pipeline.py` +6 行）
- `word_generator.py` 通用化：`model` 参数替代硬编码 deepseek，`_MODEL_DISPLAY` 映射模型名
- `main.py` `generate-word --model` 支持 `nargs="+"`，一条命令生成三份
- 文档同步：`workflow.md` 更新输出文件表和管线步骤、`CHANGELOG.md` v4.1.2 补充管线条目

**涉及文件（补充）：**
- 修改：`pipeline.py`、`word_generator.py`（model 参数化）、`main.py`（--model nargs）
- 文档：`CHANGELOG.md`、`docs/development/development-log.md`、`docs/project/workflow.md`

**验证：**
- Mock 模式全流程通过：16 HLR → 9 已匹配 / 5 待确定 / 2 无匹配，14 进入 3 Agent 并行裁判，Review Agent 共识正常
- 真实 DeepSeek LLM 全流程通过（故障注入版 HLR）：9 covered / 2 inconsistent / 3 needs_review，平均星级 2.4，Word 报告生成正常
- `generate-word --reverse-report` 命令输出 DeepSeek 单模型报告验证通过

---

## 2026-07-21 — 架构重构 v4 + 多智能体裁判系统

**目标：** 重构架构，引入 LLM 抽象层、多智能体裁判面板和共识复核系统，清理目录结构。

### Phase 1：架构基础

**完成事项：**
- 创建 `app/llm/` — LLM 抽象层（工厂模式 + provider 注册表，支持 deepseek/minimax/qwen + mock 客户端）
- 创建 `app/prompts/` — 外部 prompt 模板（forward_judge.md、reverse_judge.md、consensus.md），含文件加载器
- 创建 `app/pipeline.py` — 管线编排逻辑，从 main.py 抽离（run_forward_pipeline、run_reverse_pipeline）
- 创建 `app/job_manager.py` — 内存 Job 生命周期跟踪（JobStatus、Job、JobManager）
- 重构 `app/comparison/semantic_judge.py` — 内联 prompt 字符串和硬编码 API 调用替换为 llm + prompts 抽象层
- 重构 `app/main.py` — 精简为 CLI 调度层
- 目录迁移 `backend/generators/` → `backend/app/doc_generators/` — 修正目录位置
- models.py 新增 `ConsensusResult`、`PipelineResult`
- config.py 和 .env.example 新增 LLM 环境变量
- 清理旧产物：`.superpowers/sdd/`、`docs/superpowers/plans/`

**涉及文件：**
- 新建：llm/（4 个文件）、prompts/（4 个文件）、pipeline.py、job_manager.py
- 修改：semantic_judge.py、main.py、models.py、config.py、.env.example、CLAUDE.md
- 移动：generators/ → doc_generators/
- 删除：backend/generators/、.superpowers/sdd/（部分）、docs/superpowers/plans/

### Phase 2：多智能体裁判 + Review Agent

**完成事项：**
- 创建 `app/comparison/multi_judge.py` — 3 Agent 并行裁判面板（deepseek/minimax/qwen），minimax/qwen 使用 mock 脚手架
- 创建 `app/comparison/review_agent.py` — 共识复核，含星级评价（1-3）和共识等级（full/majority/split）
- 创建 `app/doc_generators/consensus_word_generator.py` — Word 报告，含星级、共识质量指标、per-model 裁判明细
- models.py 新增 `MultiJudgeResult`、`MultiJudgeOutput`、`ConsensusOutput`
- 扩展 `llm/factory.py` 注册表，加入 minimax/qwen provider（mock）
- 填充 `prompts/consensus.md` 为实际 review agent prompt
- 更新 `pipeline.py` 反向流程：Step 3 multi_judge → Step 4 review_agent → Step 5 report
- report_generator.py 新增 `generate_consensus_reverse_report()`
- 新增 `generate-consensus-report` CLI 命令
- 更新 `JUDGE_PROVIDERS` 配置

**涉及文件：**
- 新建：multi_judge.py、review_agent.py、consensus_word_generator.py
- 修改：models.py、factory.py、config.py、pipeline.py、report_generator.py、main.py、consensus.md

### 验证

- 正向管线：mock 和真实 LLM 验证通过（--limit 20，9 条 case，约 2m25s）
- 反向管线：mock 和真实 LLM 验证通过（全量，14 条已裁判 + 2 条无匹配，约 5m57s）
- 多智能体裁判：3 Agent 面板验证通过（deepseek 真实 + minimax/qwen mock），review agent 判定正确（3 条完全一致，11 条多数一致）
- 共识 Word 报告：共 16 条 HLR（14 条已裁判 + 2 条无匹配），星级评价，共识质量表
- 所有输出 JSON 格式向后兼容

### 文档更新

- `docs/architecture/current-architecture.md` — 全面重写，标注正向管线为暂不使用
- `docs/project/workflow.md` — 更新反向管线多智能体裁判步骤、输出文件表
- `docs/project/file-boundaries.md` — 新增所有模块（llm、prompts、multi_judge、review_agent、consensus_word_generator）
- `CLAUDE.md` — 更新边界、结构、验证命令

### 下一步

- Phase 3：接入 MiniMax/Qwen 真实 API，启用真正并行异步裁判
- 添加自动化测试

---

## 2026-07-21 — 追溯表预筛选集成

**目标：** 将他人已开发完成的追溯表预筛选模块（`ref_file/traceability/`）移植到当前架构，实现反向匹配前通过追溯表缩小 EoICD 搜索范围。

**完成事项：**
- 新建 `app/traceability/` — 追溯表预筛选模块（`trace_parser.py` + `__init__.py`），零耦合设计
  - `build_trace_index()`：读取两张追溯 Excel 表，通过 ERD 桥接构建 HLR ID → ICD BlockKey 索引
  - `name_to_block_key()`：独立函数，ICD FullName → BlockKey 转换，镜像 `signal_profiler.py` 逻辑
- 修改 `app/pipeline.py` — 新增 `_match_reverse_with_trace()`（分流+过滤+合并）、`_merge_reverse_match_outputs()`（合并统计）；`run_reverse_pipeline()` 新增可选参数 `trace_dir`
- 修改 `app/main.py` — `reverse-match` 和 `reverse-analyze` 各新增 `--traceability-dir` 可选参数

**改动原则：** 不动现有架构，追溯解析器作为独立新模块加入，分流过滤逻辑作为管线可选步骤嵌入。`trace_dir=None` 时完全走原有路径。

**涉及文件：**
- 新建：`app/traceability/__init__.py`、`app/traceability/trace_parser.py`
- 修改：`app/pipeline.py`（+~110 行）、`app/main.py`（+~12 行）
- 不移植：`ref_file/traceability/run_trace_filtered.py`（编排逻辑吸收进 pipeline.py）
- 文档更新：`CLAUDE.md`、`docs/architecture/current-architecture.md`（新增设计决策 #9）、`docs/project/file-boundaries.md`、`CHANGELOG.md`

**验证：**
- Mock 模式全流程验证通过：EoICD 搜索空间从 84,114 缩减至 2,854（可追溯组，缩减 96.6%）
- 分流正确：12 条 HLR 入可追溯组（Group A），4 条入回退组（Group B）
- 全部输出文件生成正常（7 个 JSON），统计信息含追溯命中率指标
- 不带 `--traceability-dir` 时行为完全不变

---

## 2026-07-20 — Development Governance System

**Goal:** Build complete development governance system for team collaboration on GitHub.

**Completed:**
- Created `.claude/rules/` with context-rules.md, debug-rules.md, documentation-rules.md
- Created `docs/` tree: architecture, project, development, decisions, knowledge, testing
- Created `.github/` templates: bug report, feature request, pull request
- Slimmed `CLAUDE.md` from ~468 to ~100 lines
- Migrated `DEBUGLOG.md` to `docs/development/debug-log.md`
- Created `README.md` for human developers

**Files:**
- Create: 14 new files across `.claude/`, `.github/`, `docs/`
- Modify: `CLAUDE.md`, `.gitignore`
- Delete: `DEBUGLOG.md` (migrated to docs/development/)

**Verification:**
- All cross-references between documents verified correct
- Directory structure matches design spec at `docs/decisions/2026-07-20-dev-system-design.md`

---

## Historical Iterations

See `CHANGELOG.md` for feature-level version history from v0.1.0 to v0.9.1.
