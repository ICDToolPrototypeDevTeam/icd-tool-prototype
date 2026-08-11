# 业务流程说明

本文档用于说明 **ICD工具原型Ver2.0** 的业务处理流程。

## 1. 总体流程

ICD工具原型Ver2.0 的总体流程如下：

```text
用户上传输入文件
    ↓
输入文件解析与结构化
    ↓
构建统一分析输入包
    ↓
生成 EoICD 条目化需求候选结果
    ↓
候选结果评分与择优
    ↓
EoICD 条目化需求与软件高层需求差异比对
    ↓
生成输出文档
```

## 2. 输入文件上传

用户上传以下文件：

1. EoICD Word 主文件；
2. 一个或多个 EoICD Excel 附件；
3. 软件高层需求文件。

上传完成后，系统创建分析任务，并返回任务标识。

## 3. 输入文件解析与结构化

系统对输入文件进行解析，提取后续分析所需内容。

解析内容包括：

1. EoICD Word 主文件中的接口说明、文字说明、数据定义等内容；
2. EoICD Excel 附件中的接口表格、信号表格或数据项信息；
3. 软件高层需求文件中的需求条目或需求描述内容。

解析结果应保留必要的来源信息，便于后续结果追溯。

## 4. 构建统一分析输入包

系统将解析后的内容整合为统一分析输入包。

统一分析输入包至少包含：

1. EoICD 源内容结构化结果；
2. 软件高层需求结构化结果；
3. 文件来源信息；
4. 任务标识信息。

重要原则：

```text
输入文件只解析一次，后续阶段复用统一分析输入包。
不得在差异比对阶段重新解析原始输入文件。
```

## 5. 生成 EoICD 条目化需求候选结果

系统基于统一分析输入包中的 EoICD 源内容，生成 EoICD 条目化需求候选结果。

当前规划采用多智能体方式生成多个候选结果。

候选结果应尽量包含：

1. 条目编号；
2. 条目化需求正文；
3. 关联来源信息；
4. 必要的说明或备注。

## 6. 候选结果评分与择优

系统对多个 EoICD 条目化需求候选结果进行评分，并选择综合评分最高的结果作为最佳条目化需求。

评分来源包括：

1. 多智能体评分；
2. Python 硬规则评分。

综合评分规则为：

```text
最终分数 = 其他智能体平均评分 × 0.6 + Python 硬规则评分 × 0.4
```

Python 硬规则评分用于检查结果的基本完整性、结构一致性、来源可追溯性等内容。

## 7. 差异比对

系统将最佳 EoICD 条目化需求与软件高层需求结构化结果进行比对。

差异分析重点包括：

1. EoICD 条目化需求中存在，但软件高层需求中缺失的内容；
2. 软件高层需求中存在，但 EoICD 条目化需求中未体现的内容；
3. 二者表达不一致、约束不一致或含义冲突的内容；
4. 表达不清晰、需要人工确认的内容。

差异比对结果应尽量保留关联条目和来源信息。

## 8. 输出文档生成

系统根据分析结果生成两个 Word 文档：

1. `EoICD条目化需求.docx`
2. `EoICD与软件高层需求差异报告.docx`

输出文档用于辅助人工检查和后续需求整理，不替代人工工程判断。

## 9. 任务状态

系统应记录分析任务的基本状态。

建议状态包括：

| 状态          | 含义           |
| ----------- | ------------ |
| `pending`   | 任务已创建，尚未开始处理 |
| `running`   | 任务正在处理       |
| `completed` | 任务处理完成       |
| `failed`    | 任务处理失败       |

任务完成后，用户可查看结果并下载输出文档。

## 10. 异常处理

当系统无法完成处理时，应返回明确的失败状态和错误信息。

常见异常包括：

1. 输入文件缺失；
2. 输入文件格式不支持；
3. 文件解析失败；
4. 多智能体生成失败；
5. 评分或择优失败；
6. 差异比对失败；
7. 输出文档生成失败。

异常信息应便于用户判断问题原因，并支持后续调试定位。

## 11. V4 反向管线流程（Issue A 落地，2026-07-28）

本节追加于原 10 节之后。V3 旧流程（§1-§10）保持不变；V4 是与 V3 并存的新主流程之一。

### 11.1 V4 总体流程（6 步）

```text
用户上传 HLR Word + EoICD PubSub Excel (Publisher 或 Subscriber 至少一个) + 可选追溯 Excel
    ↓
Step 1: 解析输入
    HLR Word + EoICD PubSub Excel → 结构化需求列表
    模块: parsers/{eoicd_excel_parser,hlr_word_parser}.py

Step 2: HLR AI 标注
    DeepSeek 对每条 HLR 标注 bus_types / labels / devices / signal_keywords
    模块: matching/hlr_labeler.py

Step 3: 反向匹配（含条目过滤→信号画像→Block 聚合→HLR 分类→匹配→可选追溯预筛选）
    3a: 条目过滤（排除协议 DataFormatType 条目）— matching/entry_filter.py
    3b: 信号画像聚类（按 (Label, LeafName) 聚类 → SignalProfile）— matching/signal_profiler.py
    3c: ICD Block 聚合（按 (label, signal_family) 分组 → ICDBlock）
    3d: HLR 分类（4 路正则分类 + 提取 Label/位字段/SDI/方向）— matching/hlr_classifier.py
    3e: 两阶段 Block 级匹配（Label 前缀粗筛 → 6 维评分 → 三层过滤 → 三级分层）— matching/reverse_matcher.py
    3f: 可选追溯表预筛选 + 兜底机制 — matching/traceability.py

Step 4: 多智能体裁判（含降级保护）
    DeepSeek / MiniMax / Qwen 三模型并行独立判定
    Case 级超时控制：前 2 个完成后第三个固定额外等待（默认 120s），不足 2 个时兜底上限（300s）
    Provider 熔断器：连续失败达阈值后自动跳过，TTL 到期自动恢复
    模块: comparison/{multi_judge,semantic_judge}.py + degradation/{config,context,fallback}.py
    LLM 抽象: llm/factory.py → get_llm(provider)

Step 5: Review Agent 共识（含降级后处理）
    对三模型判定结果综合复核并给出星级评价（1-3★）
    降级后处理：存活 provider 不足时硬上限约束（1 个 → ≤1★，2 个 → ≤2★）
    模块: comparison/review_agent.py + degradation/context.py

Step 5.5: 一星复查（peer-aware 反思）
    对 star_rating == 1 的 case，由三个 provider 各自重新评判
    每个 provider 看到自己之前的判断（Judgment A）和 peer 的判断（Judgment B/C），触发反思纠正
    模块: comparison/re_{review,view_agent}.py

Step 5.6: 部分共识重跑
    仅对被复查过的 case（re_reviewed_ids）重跑共识，更新对应条目
    其余 case 保持不变

Step 6: 报告生成
    1 份 xlsx + 3 份单模型 docx + 1 份共识 docx
    模块: doc_generators/{excel_generator,word_generator,consensus_word_generator}.py
    + comparison/report_generator.py
```

### 11.2 V4 vs V3 关键差异

| 维度 | V3（chunk-level 多智能体条目化） | V4（反向管线） |
| --- | --- | --- |
| 主方向 | EoICD → HLR（生成式） | HLR → EoICD（覆盖性） |
| 输入 | EoICD Word + Excel 附件 + 软件需求 Word | EoICD PubSub Excel (Pub/Sub) + HLR Word + 可选追溯 Excel |
| 智能体范式 | CrewAI 5 Agent / 5 Task / 3 Crew（chunk 循环） | 工厂：3 provider 平行 judge + Review Agent 共识 |
| LLM 接入 | CrewAI / LiteLLM 内部 | `get_llm(provider)` 抽象 + DeepSeekClient + MockLLMClient |
| 多模型策略 | generation + scoring 多模型择优 | multi_judge 3 模型 + Review Agent 共识复核 |
| 输出文档 | 4 份 docx（条目化 + 差异） | 1 份 xlsx + 4 份 docx（条目化清单 + 3 类一致性 + 共识） |
| 任务语义 | "EoICD 怎么写" | "HLR 在 EoICD 是否落实" |
| API 入口 | `POST /api/eoicd/analyze` | `POST /api/v4/coverage-analysis` |
| 任务目录 | `backend/output/v3/{job_id}/` | `backend/output/v4/{job_id}/{input/,output/}` |

### 11.3 V4 单步输入输出

| Step | 输入 | 输出 | 模块 |
| --- | --- | --- | --- |
| 1 解析输入 | `hlr.docx` + `pub.xlsx` / `sub.xlsx` | `hlr_requirements.json` (16 reqs) + `eoicd_requirements.json` (122674 条目) | parsers/ |
| 2 HLR AI 标注 | `hlr_requirements.json` (16 HLRs) | `hlr_labels.json` (16 标签) | matching/hlr_labeler.py |
| 3 反向匹配 | `eoicd_requirements.json` + `hlr_labels.json` | `reverse_matches.json` (1568 Block, 含分层层级、HLR 分类标记) | matching/{entry_filter,signal_profiler,reverse_case_builder,hlr_classifier,reverse_matcher,traceability}.py |
| 4 多智能体裁判 | `reverse_matches.json` + 3 LLM | `multi_judge_results.json` (12 cases × 3 providers) | comparison/{multi_judge,semantic_judge}.py |
| 5 Review 共识 | `multi_judge_results.json` | `consensus_results.json` (12 cases, agreement + star_rating) | comparison/review_agent.py |
| 5.5 一星复查 | `multi_judge_results.json` + `consensus_results.json` | `re_review_results.json`（审计）+ `multi_judge_results.json`（更新） | comparison/re_review.py |
| 5.6 部分共识重跑 | `multi_judge_results.json`（复查后） | `consensus_results.json`（更新，仅涉及复查条目） | comparison/review_agent.py |
| 6 报告生成 | `consensus_results.json` + `reverse_matches.json` | 1 xlsx + 4 docx + JSON | doc_generators/* + comparison/report_generator.py |

### 11.4 V4 关键约束

1. **输入只解析一次**：与 V3 一致，HLROutput / EoICDOutput 一次解析后供后续步骤复用。
2. **mock_models 显式标识**：ADR-001 D5。`/result.mock_models` 与 `/status.mock_models` 都返回当前实际 mock 的 provider 列表。
3. **JSON 中间产物不暴露**：ADR-001 D7。7 类 JSON 仅落盘，不通过 API 下载。
4. **env 保存/恢复**：V4 runner 后台线程在进入时 `os.environ.get()` 备份 `JUDGE_PROVIDERS` 与 `USE_MOCK_LLM`，`try/finally` 按 None/赋值恢复（Issue A 修正 #2）。
5. **跨版本查询 404**：V3/V4 路由严格按 `Job.kind` 分发（V3 路由查 V4 job 返 404 + 提示 `use /api/v4/...`；反之亦然）。
6. **追溯表预筛选（可选）**：仅 `enable_traceability_prefilter=true` 时走 `matching/traceability.py`，否则 `_match_reverse_with_trace` 跳过。
7. **降级保护**：Step 4 的 `_judge_with_degradation()` 在每 case 前过滤 unhealthy provider，已熔断的 provider 不再发起 HTTP 调用，超时的 provider 补 error judgment 而不中断 case。Step 5 的 `_apply_degradation_review()` 对 Review Agent 输出做后处理，不修改 `review_judgments()` 本身。

### 11.5 V4 异常处理

| 阶段 | 异常类型 | 失败后果 | 用户可观察 |
| --- | --- | --- | --- |
| Step 1 解析 | `parser` 抛异常 | `job.status = failed` | `/api/v4/jobs/{id}/result` 返 409，`message` 含异常摘要 |
| Step 2 HLR 标注 | DeepSeek API 错误 / 解析失败 | label 退化为空 + `errors: [...]` 累计 | `/api/v4/jobs/{id}/result.errors` 数组；UI 标注 `部分 HLR 标签缺失` |
| Step 3 反向匹配 | 反向匹配抛异常 | `job.status = failed` | 同 Step 1 |
| Step 4 多智能体 | 3 provider 全失败 | `consensus_results.json` 中全失败标记 | `/result.summary.status_distribution` 全 `failed` |
| Step 5 Review 共识 | Review 失败 | `consensus_results.json` 中 `summary: {"all_failed": true}` | `mock_models` 仅含失败 provider |
| Step 5.5 一星复查 | 单 provider 复查 API 失败 | 该 provider 标记为 `error`，其余 provider 继续；最终以已有结果计 | re_review_results.json 中该 provider 为 `error` 状态，不阻断其他 provider |
| Step 5.6 部分共识重跑 | 共识重跑失败 | 该 case 保持 Step 5 原结果 | 仅影响 re_reviewed_ids 中的失败条目 |
| Step 6 报告生成 | docx 写盘失败 | `outputs.<key>` 某项为 false | `/result.outputs` 部分 false |
| Step 4 降级超时 | 单 case 超时（t2+120s 或 300s 兜底） | 超时 provider 得 error judgment，其余正常 | `degradation.total_case_timeouts` 递增 |
| Step 4 降级熔断 | Provider 连续失败 ≥ 3 次 | 该 provider 被标记 unhealthy，后续 case 跳过 | `degradation.provider_status` 显示 unhealthy |
| Step 4 全部 unhealthy | 所有 provider 均熔断 | `AllProvidersUnhealthyError` → job FAILED | `/result` 返 409 |
| Step 5 降级 | 存活 provider ≤ 2 | 星级受硬上限约束，agreement 可能被覆盖 | `degradation.review_star_capped_count` 递增 |

### 11.6 V4 流程变更原则

按 §1-§10 同样的"流程变更原则" + ADR-001：
- 增加 / 删除 / 修改 stage 须同步更新本节；
- 新增 stage 须有 ADR；
- 流程变更不修改 V3 旧流程（V3 与 V4 互不污染）。
