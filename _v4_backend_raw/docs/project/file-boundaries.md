# 文件边界与修改权限

定义哪些文件可以修改及其约束条件。
参见 `CLAUDE.md` 中的精简版本。

## 只读（禁止修改）

| 文件/目录 | 原因 |
|-----------|------|
| `ref_file/` | 权威参考文件 |
| `ref_file/generation_skill_v4.md` | EoICD 解析规则 — 唯一权威来源 |
| `ref_file/方案2.md` | 架构设计文档 |
| `doc_input_file/` | 原始输入文件 — 人工管理 |

## 自动生成输出（禁止手动编辑）

| 文件/目录 | 生成方 |
|-----------|--------|
| `backend/output/` | 所有管线阶段 |

## 可编辑源文件

### 配置与模型

| 文件 | 何时修改 | 约束 |
|------|----------|------|
| `backend/app/config.py` | 添加常量、映射、权重、规则 | 所有消费者必须保持兼容 |
| `backend/app/models.py` | 添加/更改/删除 Pydantic 字段 | **必须同步所有构造点** |
| `backend/app/synonyms.yaml` | 添加规范名/别名条目 | 遵循现有 4 大类结构 |
| `backend/.env.example` | 添加新环境变量 | 绝不包含真实密钥 |
| `backend/.env` | 本地开发配置 | 不提交到 git |
| `backend/requirements.txt` | 添加/删除 Python 依赖 | 以原型 `icd-tool-prototype-3.0.0-alpha` 为基准对齐；当前项目专用包追加在后 |

### 解析器

| 文件 | 何时修改 | 约束 |
|------|----------|------|
| `backend/app/parsers/eoicd_excel_parser.py` | 添加/修改 EoICD 解析规则 | 必须遵循 `ref_file/generation_skill_v4.md` |
| `backend/app/parsers/hlr_word_parser.py` | 添加/修改 HLR 解析逻辑 | 必须处理 .docx 表格结构 |

### 匹配

| 文件 | 何时修改 | 约束 |
|------|----------|------|
| `backend/app/matching/candidate_matcher.py` | 修改正向匹配编排 | Enrich -> Label -> Score -> Top-K 流程 |
| `backend/app/matching/eoicd_enricher.py` | 修改 EoICD token 富化 | 必须与 unified_matcher 保持兼容 |
| `backend/app/matching/hlr_labeler.py` | 修改 HLR AI 标注 | 缓存 JSON 格式必须向后兼容 |
| `backend/app/matching/unified_matcher.py` | 修改 7 维评分权重/逻辑 | 如需修改，同步更新 config 中 `MATCH_WEIGHTS` |
| `backend/app/matching/text_matcher.py` | 修改 BM25 索引/评分 | 必须产出 0-20 分数范围 |
| `backend/app/matching/hlr_classifier.py` | 修改 HLR 分类正则/规则 | 影响反向管线路由 |
| `backend/app/matching/signal_profiler.py` | 修改画像聚类或 Block 聚合 | 影响所有反向管线下游模块 |
| `backend/app/matching/entry_filter.py` | 修改条目过滤规则 | 影响哪些条目进入反向管线 |
| `backend/app/matching/reverse_matcher.py` | 修改反向匹配/评分/过滤 | 影响反向 case 构造 |
| `backend/app/matching/reverse_case_builder.py` | 修改 ReverseCase 格式 | 影响 AI 裁判 prompt 和报告 |

### 对比裁判

| 文件 | 何时修改 | 约束 |
|------|----------|------|
| `backend/app/comparison/case_builder.py` | 修改正向 ComparisonCase 格式 | 影响正向裁判 prompt |
| `backend/app/comparison/semantic_judge.py` | 修改单模型 AI 裁判逻辑 | 正向和反向单裁判均在此文件；通过 llm + prompts 调用 |
| `backend/app/comparison/multi_judge.py` | 修改多 Agent 裁判编排 | 调用 semantic_judge 内部方法 + 多 provider |
| `backend/app/comparison/review_agent.py` | 修改共识复核逻辑/星级规则 | 输出 ConsensusResult |
| `backend/app/comparison/report_generator.py` | 修改报告结构/统计 | 含 `generate_consensus_reverse_report` |

### LLM 抽象层

| 文件 | 何时修改 | 约束 |
|------|----------|------|
| `backend/app/llm/factory.py` | 添加新 LLM provider | 遵循 LLMClient Protocol |
| `backend/app/llm/deepseek_client.py` | 修改 DeepSeek API 调用 | OpenAI 兼容接口 |
| `backend/app/llm/mock_llm.py` | 修改 mock 行为 | `MOCK_JUDGE_RESULT` 环境变量控制 |

### Prompt 模板

| 文件 | 何时修改 | 约束 |
|------|----------|------|
| `backend/app/prompts/forward_judge.md` | 修改正向裁判 prompt | 纯文本，JSON 输出格式 |
| `backend/app/prompts/reverse_judge.md` | 修改反向裁判 prompt | 纯文本，JSON 输出格式 |
| `backend/app/prompts/consensus.md` | 修改共识复核 prompt | 纯文本，JSON 输出格式 |
| `backend/app/prompts/loader.py` | 修改 prompt 加载逻辑 | 无缓存 |

### 追溯表预筛选

| 文件 | 何时修改 | 约束 |
|------|----------|------|
| `backend/app/traceability/__init__.py` | 导出新符号 | — |
| `backend/app/traceability/trace_parser.py` | 修改追溯表读取或 BlockKey 映射逻辑 | 零耦合，不导入项目内其他模块；`name_to_block_key()` 需与 `signal_profiler.py` 逻辑保持一致 |

### 管线编排

| 文件 | 何时修改 | 约束 |
|------|----------|------|
| `backend/app/pipeline.py` | 修改管线步骤/顺序 | 输出 JSON 格式保持兼容 |
| `backend/app/job_manager.py` | 修改 Job 生命周期 | 内存管理，不引入数据库 |

### 文档生成器

| 文件 | 何时修改 | 约束 |
|------|----------|------|
| `backend/app/doc_generators/excel_generator.py` | 修改 EoICD Excel 输出格式 | — |
| `backend/app/doc_generators/word_generator.py` | 修改单裁判 Word 报告格式 | 读 reverse_report.json |
| `backend/app/doc_generators/consensus_word_generator.py` | 修改共识 Word 报告格式 | 读 consensus_results.json + reverse_matches.json |

### CLI 入口

| 文件 | 何时修改 | 约束 |
|------|----------|------|
| `backend/app/main.py` | 添加/删除 CLI 命令 | 保持现有命令签名稳定 |

## 跨模块约束

1. **parsers/** — 不得导入 matching/、comparison/、doc_generators/、llm/、prompts/
2. **matching/** — 可导入 models、config、synonyms；不得导入 comparison/
3. **comparison/** — 可导入 models、config、llm、prompts；不得导入 parsers/
4. **doc_generators/** — 可导入 models；不得导入 parsers/matching/comparison/
5. **llm/** — 不导入项目内其他模块
6. **prompts/** — 不导入项目内其他模块
7. **traceability/** — 不导入项目内其他模块（零耦合）
8. **pipeline.py** — 可导入所有模块（编排者角色）
9. **main.py** — 可导入所有模块（编排者角色）
