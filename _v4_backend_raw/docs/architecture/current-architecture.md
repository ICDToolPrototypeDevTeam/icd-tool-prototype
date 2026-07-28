# 当前架构

## 架构总览

### 正向管线（EoICD → HLR）— 暂不使用

> **注意：正向管线目前为遗留模式，暂不推荐使用。主力管线为反向管线。**

```
EoICD Excel + HLR Word
        ↓
条目化需求生成（parsers）
        ↓
候选召回（Candidate Matcher）
        ↓
ComparisonCase 构造
        ↓
AI 语义裁判（单模型）
        ↓
脚本汇总 + 差异报告
```

### 反向管线（HLR → EoICD）← 主力管线

```
HLR Requirements + EoICD Requirements
        ↓
HLR 4路分类（hlr_classifier）
        ↓
EoICD 信号画像构建（signal_profiler）
        ↓
ICD Block 聚合（build_blocks）
  信号族提取 + 跨通道合并
        ↓
两阶段 Block 级匹配（reverse_matcher）
  阶段1: Label前缀粗筛
  阶段2: 6维度 Block 评分 → 三级分层 → Top-K
        ↓
ReverseCase 构造（reverse_case_builder）
        ↓
多智能体并行裁判（multi_judge）          ← NEW Phase 2
  3 Agent 独立裁判（DeepSeek/MiniMax/Qwen）
        ↓
Review Agent 共识复核（review_agent）    ← NEW Phase 2
  星级评价 (1-3) + 共识等级 + 最终判定
        ↓
共识报告（report_generator + consensus_word_generator）
```

完整架构详见 `ref_file/方案2.md`。

## 模块划分

### parsers/

- `eoicd_excel_parser.py` — EoICD Excel → 条目化需求 JSON。支持 Rule 1-9 完整解析规则：DP/RP 层级解析、属性提取、单位自动追加、帧结构过滤、per-sheet 与跨 sheet 两级去重。`read_only` 模式优化大文件加载性能。
- `hlr_word_parser.py` — HLR Word → 需求列表 JSON。解析 .docx 表格（表0=术语表，表1+=需求表）。

### matching/

- `candidate_matcher.py` — 正向匹配编排入口（enrich → label → unified → Top K → 构造 ComparisonCase）
- `eoicd_enricher.py` — EoICD 查询富化：将结构化字段展开为多语言 token 集合
- `hlr_labeler.py` — HLR AI 预标注：DeepSeek API 批量提取结构化标签，结果缓存为 JSON 避免重复调用
- `unified_matcher.py` — 正向 7 维度加权评分（满分 100）
- `text_matcher.py` — BM25 文本匹配（第7维度，0-20 分，中英混合分词，Okapi BM25）
- `hlr_classifier.py` — HLR 4路分类 + regex 提取（Label/位字段/SDI/方向）
- `signal_profiler.py` — EoICD 信号画像聚类 + 信号族提取 + ICD Block 聚合
- `entry_filter.py` — EoICD 条目过滤（排除协议 DataFormatType）
- `reverse_matcher.py` — 反向匹配：4路分类路由 + 两阶段 Block 级匹配 + 三层脚本过滤 + 三级分层
- `reverse_case_builder.py` — ReverseCase 构造（Block 级输出，省略通道明细以减少 token 消耗）

### comparison/

- `case_builder.py` — ComparisonCase 构造（正向管线）
- `semantic_judge.py` — 单模型 AI 裁判（正向+反向）。通过 LLM 抽象层调用，prompt 从外部 .md 文件加载。保留用于正向管线。
- `multi_judge.py` — **多智能体裁判面板**：并行调用 N 个 LLM provider 对每个 ReverseCase 独立裁判，返回 MultiJudgeOutput。Provider 列表由 `JUDGE_PROVIDERS` 环境变量控制。
- `review_agent.py` — **Review Agent 共识复核**：读取 MultiJudgeOutput 中同一 case 的多份裁判结果，综合评估给出 ConsensusResult（含 agreement_level、star_rating 1-3、final_coverage_status）。当前 MiniMax/Qwen 为 mock 预留。
- `report_generator.py` — 报告生成：正向统计 + 差异明细 + 反向判定汇总 + 共识报告（`generate_consensus_reverse_report`）

### doc_generators/

- `excel_generator.py` — Excel 输出（EoICD 条目化清单）
- `word_generator.py` — Word 输出（单裁判一致性分析报告，正向/反向兼容）
- `consensus_word_generator.py` — **多模型共识分析 Word 报告**：读取 consensus_results.json + reverse_matches.json，生成含星级评价、共识等级、per-model 判断的详细报告

### llm/ — LLM 抽象层

- `__init__.py` — 导出 `get_llm`, `use_mock_llm`, `LLMClient`, `ChatResponse`
- `factory.py` — Provider factory + registry：`get_llm(provider)` 返回 `LLMClient`。`USE_MOCK_LLM=1` 覆盖所有 provider 返回 `MockLLMClient`。Registry 已注册 deepseek/minimax/qwen，后两者当前为 mock 预留。
- `deepseek_client.py` — DeepSeek API client（OpenAI 兼容接口），含重试/超时
- `mock_llm.py` — Mock LLM client：`MOCK_JUDGE_RESULT` 环境变量控制返回值预设

### prompts/ — 外部 Prompt 模板

- `loader.py` — `load_prompt(name)` 读取 .md 文件，无缓存（CLI 每次独立进程）
- `forward_judge.md` — 正向裁判 system prompt
- `reverse_judge.md` — 反向裁判 system prompt
- `consensus.md` — Review Agent 共识复核 system prompt

### traceability/ — 追溯表预筛选

- `__init__.py` — 导出 `TraceabilityIndex`, `build_trace_index`, `name_to_block_key`
- `trace_parser.py` — 读取两张追溯 Excel 表（设备→高层需求矩阵 + 设备→ICD追溯表），通过 ERD 桥接构建 HLR ID → ICD BlockKey 索引。`name_to_block_key()` 作为独立函数（零耦合），将 ICD FullName 转换为 BlockKey，镜像 `signal_profiler.py` 逻辑。

### pipeline.py — 管线编排

从 `main.py` 抽离的编排逻辑：
- `run_forward_pipeline()` — 正向管线（暂不使用）
- `run_reverse_pipeline()` — 反向管线 5 步：parse → label → match（可选追溯表预筛选）→ multi_judge → review_agent → report
- `_match_reverse_with_trace()` — 追溯表预筛选反向匹配：拆分为可追溯组（过滤后 EoICD）和回退组（全量 EoICD），分别匹配后合并

### job_manager.py — Job 生命周期

内存 Job 管理：`Job`（uuid4、status、message、timestamps、result）+ `JobManager`（create/get/list）。CLI 每次运行一个 Job。

### config.py / models.py / synonyms.yaml / main.py

- `config.py` — 常量配置 + 环境加载（python-dotenv）。包含：LLM 环境变量、`JUDGE_PROVIDERS`、属性中文映射表、匹配权重、属性类别映射、方向动词表、中→英信号关键词映射（~250 条）等。
- `models.py` — Pydantic 模型：EoICD/HLR 解析模型、匹配/裁判模型、**MultiJudgeResult/MultiJudgeOutput**（多智能体）、**ConsensusResult/ConsensusOutput**（共识复核）、PipelineResult。
- `synonyms.yaml` — 别名映射表
- `main.py` — CLI 入口（14 个子命令）：`parse-eoicd` / `parse-hlr` / `all` / `match` / `judge` / `report` / `analyze` / `label-hlr` / `reverse-match` / `reverse-judge` / `reverse-report` / `reverse-analyze` / `generate-word` / `generate-consensus-report`

## 设计决策

### 1. 多智能体裁判 + 共识复核（Phase 2）

**架构**：3 个 Agent 使用不同 LLM 基座（DeepSeek/MiniMax/Qwen）并行独立裁判同一 case，1 个 Review Agent 综合 3 份结果给出共识判定。

```
Case → Agent 1 (DeepSeek) → JudgmentResult
     → Agent 2 (MiniMax)  → JudgmentResult    (当前 mock)
     → Agent 3 (Qwen)     → JudgmentResult    (当前 mock)
                ↓
     Review Agent → ConsensusResult
       - agreement_level: full | majority | split
       - star_rating: 1-3
       - final_coverage_status
       - final_analysis
```

**当前状态**：DeepSeek 已接入真实 API，MiniMax/Qwen 为 mock 预留。`USE_MOCK_LLM=1` 时所有 provider 走 mock。

### 2. LLM 抽象层（Phase 1）

`get_llm(provider)` factory 模式，Provider registry 支持扩展。环境变量驱动配置，mock 模式支持离线开发。

### 3. 外部 Prompt 模板（Phase 1）

Prompt 从 `backend/app/prompts/*.md` 文件加载，不在 Python 代码中内联。无需重启即可编辑 prompt，CLI 每次独立进程自动读取最新内容。

### 4. 管线编排分离（Phase 1）

`pipeline.py` 承载管线编排逻辑，`main.py` 只做 CLI 参数解析和调度。Job 生命周期由 `job_manager.py` 跟踪。

### 5. 多对多关系

- 一条 HLR 可覆盖多条 EoICD
- 一条 EoICD 可能由多条 HLR 共同覆盖
- 反向管线解决"每条 HLR 是否在 EoICD 中有对应的接口定义"

### 6. 反向匹配：4路分类 + ICD Block 聚合 + 两阶段匹配 + 三层过滤 + 三级分层

详见 workflow.md。匹配层只负责找对应关系，一致性判断由 AI Agent 完成。

### 7. 信号画像聚类、信号族提取与 ICD Block 聚合

详见 workflow.md。三层聚合：条目过滤 → 信号画像聚类 → ICD Block 合并。

### 8. AI 只输出结构化判断，不生成报告

AI 输出结构化 JSON（JudgmentResult / ConsensusResult），报告由脚本生成。好处：格式稳定、编号稳定、统计可控。

5 种差异类型：`covered` / `partial` / `missing` / `inconsistent` / `needs_review`

### 9. 追溯表预筛选（Phase 3）

**背景**：反向匹配在 ~69,000 条 EoICD 中全量搜索，大多数 HLR 实际只与少数 ICD Block 相关。

**架构**：通过两张追溯 Excel 表（设备→高层需求矩阵 + 设备→ICD追溯表）构建 HLR ID → ICD BlockKey 的桥接索引，在反向匹配前预筛选 EoICD 搜索空间。

```
追溯表1 (ERD→ICD) + 追溯表2 (ERD→HLR)
        ↓
HLR ID → ICD BlockKey 索引 (trace_parser)
        ↓
HLR 分流: Group A (可追溯, 过滤EoICD) + Group B (无追溯, 全量回退)
        ↓
分别匹配 → 合并结果 → 后续管线不变
```

**效果**：可追溯 HLR 的 EoICD 搜索空间缩减约 96%（84,114 → 2,854），不可追溯 HLR 回退全量搜索不受影响。

**当前状态**：已集成至 `run_reverse_pipeline()`，通过 `--traceability-dir` 可选启用。`trace_parser.py` 零耦合，不导入项目内其他模块。

## 数据流

反向管线（主力）：HLR + EoICD → 条目过滤 → 信号画像 → ICD Block 聚合 → 4路分类 → [可选: 追溯表预筛选] → 反向匹配 → 3 Agent 并行裁判 → Review Agent 共识 → 报告

正向管线（暂不使用）：EoICD Excel + HLR Word → 解析 → 富化 → 匹配（Top-K）→ 构造 Case → 单 AI 裁判 → 报告

## 模块边界约束

- `parsers/` — 不导入 `matching/`、`comparison/`、`doc_generators/`
- `matching/` — 导入 `models.py`、`config.py`、`synonyms.yaml`；不导入 `comparison/`
- `comparison/` — 导入 `models.py`、`config.py`、`llm/`、`prompts/`；不导入 `parsers/`
- `doc_generators/` — 导入 `models.py`；不导入 `parsers/`、`matching/`、`comparison/`
- `llm/` — 不导入项目内其他模块
- `prompts/` — 不导入项目内其他模块
- `traceability/` — 不导入项目内其他模块（零耦合）
- `pipeline.py` — 可导入所有模块（编排器角色）
- `main.py` — 可导入所有模块（编排器角色）

## 架构改进（v4.0.1 → Current）

| 改进 | 说明 |
|------|------|
| LLM 抽象层 | factory + provider registry，支持多模型扩展 |
| 外部 Prompt 模板 | .md 文件，热编辑无需重启 |
| 管线编排分离 | pipeline.py 独立于 CLI |
| Job 生命周期 | job_manager.py 跟踪运行状态 |
| 目录规范化 | generators/ → doc_generators/，统一在 app/ 下 |
| 多智能体裁判 | 3 Agent 并行 + Review Agent 共识，mock 预留 |
| 清理开发产物 | 移除 .superpowers/sdd/ 等过程文件 |

## 架构局限

- 仅 CLI，无 API 服务端
- MiniMax/Qwen 当前为 mock，待 Phase 3 接入真实 API
- 3 Agent 当前串行调用（非真正并行），待后续优化为 asyncio
- 无自动化测试框架
- 无依赖注入——模块间硬导入
