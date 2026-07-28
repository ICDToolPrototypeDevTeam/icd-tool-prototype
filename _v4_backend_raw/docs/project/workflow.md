# 业务流程

## 正向管线（EoICD → HLR）— 暂不使用

> 以下为遗留文档。主力管线为反向管线。

共 6 步：

```
1. 解析
   EoICD Excel + HLR Word → 结构化需求列表
   CLI: parse-eoicd, parse-hlr, all

2. 富化
   EoICD 结构化字段 → 多语言 token 集合
   模块: matching/eoicd_enricher.py

3. AI 预标注
   HLR 文本 → 结构化标签
   模块: matching/hlr_labeler.py（DeepSeek API，JSON 缓存）

4. 匹配
   7 维度评分 → 每条 EoICD 条目召回 Top-K HLR 候选
   模块: matching/unified_matcher.py, candidate_matcher.py

5. 构造 Case
   模块: comparison/case_builder.py

6. AI 裁判 + 报告
   单模型 DeepSeek 逐 Case 裁判 → 结构化 JSON 判断结果
   模块: comparison/semantic_judge.py, report_generator.py
```

## 反向管线（HLR → EoICD）← 主力管线

共 8 步：

```
1. 解析（与正向共享）
   复用 EoICD + HLR 解析结果

2. 条目过滤
   排除协议 DataFormatType 条目
   模块: matching/entry_filter.py

3. 信号画像聚类
   按 (Label, LeafName) 聚类 EoICD 条目 → SignalProfile
   模块: matching/signal_profiler.py → build_profiles()

4. ICD Block 聚合
   按 (label, signal_family) 分组 → ICDBlock
   模块: matching/signal_profiler.py → build_blocks()

5. HLR 分类
   4 路正则分类 + 提取 Label/位字段/SDI/方向
   模块: matching/hlr_classifier.py

6. 反向匹配
   两阶段 Block 级匹配 → 6 维评分 → 三层过滤 → 三级分层
   分层：已匹配/待确定/无匹配
   模块: matching/reverse_matcher.py

7. 多智能体裁判 + Review Agent 共识 ← NEW
   3 Agent 并行裁判（DeepSeek/MiniMax/Qwen）
   → Review Agent 共识复核（星级 1-3 + agreement_level）
   "无匹配" HLR → 报告阶段由脚本填充
   模块: comparison/multi_judge.py, comparison/review_agent.py

8. 报告生成
   JSON 汇总报告 + 3 份单模型 Word 报告 + 1 份共识 Word 报告
   模块: comparison/report_generator.py, doc_generators/word_generator.py, doc_generators/consensus_word_generator.py
```

## 关键决策参数

| 参数 | 取值 | 位置 |
|------|------|------|
| 正向 Top-K | 5（默认） | `config.py: DEFAULT_TOP_K` |
| 反向 Top-K Blocks | 20 | `reverse_matcher.py: _TOP_K` |
| 已匹配分层阈值 | score >= 25, dims >= 2, sn > 0 | `reverse_matcher.py` |
| 待确定分层阈值 | score >= 12 | `reverse_matcher.py` |
| AI 超时 | 60s，最多 2 次重试 | `deepseek_client.py` |
| 多智能体 Provider | deepseek,minimax,qwen | `config.py: JUDGE_PROVIDERS` |
| 共识星级 | 3星=完全一致, 2星=多数一致, 1星=分歧 | `review_agent.py` |

## 输出文件

所有输出存放在 `backend/output/<run_dir>/` 下：

| 文件 | 所属管线 | 说明 |
|------|----------|------|
| `eoicd_requirements.json` | 共享（解析） | EoICD 条目化需求 |
| `hlr_requirements.json` | 共享（解析） | HLR 需求列表 |
| `hlr_labels.json` | 共享（标注） | HLR AI 标签缓存 |
| `multi_judge_results.json` | 反向 | 3 Agent 裁判明细 |
| `consensus_results.json` | 反向 | Review Agent 共识结果 |
| `reverse_matches.json` | 反向 | 反向匹配结果 |
| `reverse_report.json` | 反向 | 共识汇总报告 |
| `EoICD条目化清单.xlsx` | 反向（自动） | EoICD 条目化 Excel |
| `EoICD与HLR一致性分析报告_{model}.docx` | 反向（自动） | 单模型一致性 Word 报告 |
| `EoICD与HLR多模型共识分析报告.docx` | 反向（自动） | 共识 Word 报告（含星级） |
| `enriched_queries.json` | 正向 | 富化查询 |
| `profiles.json` | 正向 | 信号画像 |
| `match_results.json` | 正向 | 正向匹配结果 |
| `judgment_results.json` | 正向 | 正向裁判结果 |
| `difference_report.json` | 正向 | 正向差异报告 |

## 文档输出

管线运行时自动生成以下文档（无需单独命令）：

| 输出文件 | 生成阶段 | 说明 |
|----------|----------|------|
| `EoICD条目化清单.xlsx` | Step 1（解析） | EoICD 条目化明细 Excel |
| `EoICD与HLR一致性分析报告_{model}.docx` | Step 5（报告） | 各模型单模型一致性报告（DeepSeek/MiniMax/Qwen） |
| `EoICD与HLR多模型共识分析报告.docx` | Step 5（报告） | Review Agent 共识报告（含星级） |

也可用独立命令手动重生成：

| CLI 命令 | 输入 | 输出 |
|----------|------|------|
| `generate-word --reverse-report <path> --model <model>...` | `reverse_report.json` + `reverse_matches.json` | `EoICD与HLR一致性分析报告_{model}.docx` |
| `generate-consensus-report --consensus <path> --match <path>` | `consensus_results.json` + `reverse_matches.json` | `EoICD与HLR多模型共识分析报告.docx`（含星级） |
