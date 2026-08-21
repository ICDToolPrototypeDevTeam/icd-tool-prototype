# 业务流程说明

本文档用于说明 **ICD工具原型** 的业务处理流程。当前版本仅保留 V4 反向管线流程；V3 旧流程已随 V3 代码移除（见 ADR-002）。

## 1. 总体流程

ICD工具原型 的总体流程（6 步反向管线）如下：

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
    3b: 信号画像聚类（按 (Label, LeafName) 聚类 → SignalProfile，仅取 leaf 层 DP/RP 条目）— matching/signal_profiler.py
    3c: ICD Block 聚合（按 (label, signal_family) 分组 → ICDBlock）
    3d: HLR 分类（4 路正则分类 + 提取 Label/位字段/SDI/方向）— matching/hlr_classifier.py
    3e: 两阶段 Block 级匹配（Label 前缀粗筛 → 6 维评分 → 三层过滤 → 三级分层）— matching/reverse_matcher.py
    3f: 可选追溯表预筛选 + 兜底机制 — matching/traceability.py

Step 4: 多智能体裁判（含降级保护）
    DeepSeek / MiniMax / Qwen 三模型并行独立判定（线程池 + concurrent.futures 执行模型）
    任务提交限流：信号量（DEGRADATION_MAX_INFLIGHT，默认 6）控制同时提交到线程池的任务数，超限任务在 submit 前阻塞等待
    Case 级超时控制：前 2 个完成后第三个固定额外等待（默认 120s），不足 2 个时兜底上限（300s）
    超时任务不取消：转后台线程池继续执行，等待 Step 4.5 统一收尾（迟到但有效的输出不丢弃）
    Provider 熔断器：连续失败达阈值后自动跳过，TTL 到期自动恢复
    模块: comparison/{multi_judge,semantic_judge}.py + degradation/{config,context,fallback}.py
    LLM 抽象: llm/factory.py → get_llm(provider)

Step 4.5: 超时任务后台收尾（drain）
    统一 join Step 4 中超时的裁判任务，总预算默认 300s（DEGRADATION_DRAIN_BUDGET）
    drain 任务数上限（DEGRADATION_DRAIN_MAX_TASKS，默认 60）：超限的超时任务被 cancel（未执行的取消，已执行的结果丢弃）
    预算内返回的有效结果替换该 provider 的 TIMEOUT 占位，进入 Step 5 共识
    预算到期未返回的维持 TIMEOUT 降级；迟到失败不重复计入熔断失败计数
    统计: degradation.drained_late_count
    模块: pipeline.py `_drain_and_rereview()` + degradation/context.py

Step 5: Review Agent 共识（含降级后处理）
    对三模型判定结果综合复核并给出星级评价（1-3★）
    降级后处理：存活 provider 不足时硬上限约束（0 个 → 强制 1★ + no_consensus + 待确认；1 个 → ≤1★；2 个 → ≤2★）
    模块: comparison/review_agent.py + degradation/context.py

Step 5.5: 一星复查（peer-aware 反思）
    对 star_rating == 1 的 case，由三个 provider 各自重新评判
    每个 provider 看到自己之前的判断（Judgment A）和 peer 的判断（Judgment B/C），触发反思纠正
    error provider 跳过：不重新查询 coverage_status="error" 的 provider，保留其 error 状态
    模块: comparison/re_review.py

Step 5.6: 部分共识重跑（含降级后处理）
    仅对被复查过的 case（re_reviewed_ids）重跑共识，更新对应条目
    其余 case 保持不变
    共识结果经 `_apply_degradation_review()` 重新应用 star cap 并重建 summary
    模块: comparison/review_agent.py + degradation/context.py

Step 6: 报告生成
    1 份 xlsx + 3 份单模型 docx + 1 份共识 docx
    模块: doc_generators/{excel_generator,word_generator,consensus_word_generator}.py
    + comparison/report_generator.py
```

## 2. 输入文件上传

用户上传以下文件：

1. HLR Word 文档（必填）；
2. EoICD Publisher PubSub Excel（与 Subscriber 二选一）；
3. EoICD Subscriber PubSub Excel（与 Publisher 二选一）；
4. 追溯 Excel（可选，0-N 个）。

上传完成后，系统创建分析任务，并返回任务标识。

## 3. 输入解析与结构化

系统对输入文件进行解析，提取后续分析所需内容。

- HLR Word：从「软件需求」章节提取每条需求条目。
- EoICD PubSub Excel：解析生成**多层级属性**（HL 高层 / DP 数据点 / RP 接收参数，带 `layer_type` / `side` 等字段）。

> **说明**：EoICD 解析阶段会保留完整的多层级属性结构；后续 `SignalProfile` 聚类（Step 3b）才**只取 leaf 层 DP/RP 条目**（`layer_type ∈ {DP, RP}`，见 `matching/signal_profiler.py`），HL 层条目不参与信号画像。

解析结果应保留必要的来源信息，便于后续结果追溯。

## 4. 输入只解析一次

```text
输入文件只解析一次，后续阶段复用统一分析输入包。
不得在裁判或共识阶段重新解析原始输入文件。
```

Step 1 解析出的 `hlr_requirements.json` / `eoicd_requirements.json` 供后续所有步骤复用。

## 5. HLR AI 标注

DeepSeek 为每条 HLR 需求标注关键信息（总线类型 `bus_types`、Label 号 `labels`、关联设备 `devices`、信号关键词 `signal_keywords`），作为反向匹配的线索。

## 6. 反向匹配

根据标注线索，为每条 HLR 需求在 EoICD 接口清单中寻找最可能对应的接口定义（Block），得到候选匹配结果。

匹配链路：条目过滤 → 信号画像 → Block 聚合 → HLR 分类 → 两阶段匹配 → 可选追溯预筛选。匹配失败时自动回退到全量匹配。

## 7. 多智能体裁判

DeepSeek / MiniMax / Qwen 三个模型分别独立判断「每条 HLR 需求是否在 EoICD 中找到了正确对应的接口，且两者描述（数据类型、方向、范围等）是否一致」，各自给出结论。

## 8. Review 共识与星级评分

Review Agent 汇总三个模型的结论，对分歧处复核，给出最终判定和 1-3★ 星级（星级代表判定结果的可靠程度）。对 1★ case 执行一星复查（peer-aware 反思），复查后部分重跑共识。

## 9. 报告生成

将以上结果整理成 1 份 xlsx（条目化清单）+ 4 份 docx（差异分析报告）。

## 10. 任务状态

系统应记录分析任务的基本状态。

| 状态          | 含义           |
| ----------- | ------------ |
| `pending`   | 任务已创建，尚未开始处理 |
| `running`   | 任务正在处理       |
| `completed` | 任务处理完成       |
| `failed`    | 任务处理失败       |

任务完成后，用户可查看结果并下载输出文档。

## 11. 关键约束

1. **输入只解析一次**：HLROutput / EoICDOutput 一次解析后供后续步骤复用。
2. **mock_models 显式标识**：ADR-001 D5。`/result.mock_models` 与 `/status.mock_models` 都返回当前实际 mock 的 provider 列表。
3. **JSON 中间产物不暴露**：ADR-001 D7。7 类 JSON 仅落盘，不通过 API 下载。
4. **env 保存/恢复**：V4 runner 后台线程在进入时备份 `JUDGE_PROVIDERS` 与 `USE_MOCK_LLM`，`try/finally` 按 None/赋值恢复。
5. **追溯表预筛选（可选）**：仅 `enable_traceability_prefilter=true` 时走 `matching/traceability.py`，否则 `_match_reverse_with_trace` 跳过。
6. **降级保护**：Step 4 的 `_judge_with_degradation()` 在每 case 前过滤 unhealthy provider，已熔断的 provider 不再发起 HTTP 调用。任务提交受信号量限流（`DEGRADATION_MAX_INFLIGHT`，默认 6），超限任务在 submit 前阻塞等待。超时的 provider 先补 error judgment 占位而不中断 case，任务本体转入后台线程池。Step 4.5 在总预算（`DEGRADATION_DRAIN_BUDGET`，默认 300s）内统一 join，drain 任务数受上限约束（`DEGRADATION_DRAIN_MAX_TASKS`，默认 60），超限任务被 cancel（未执行的取消，已执行的结果丢弃）。迟到的有效结果替换占位后进入 Step 5 共识。Step 4 失败兜底（JSON 解析失败 / API 错误 / 重试耗尽）统一归一为 `coverage_status="error"`。Step 5 的 `_apply_degradation_review()` 对 Review Agent 输出做后处理，不修改 `review_judgments()` 本身；Step 5.6 复查后重新应用降级约束。

## 12. 异常处理

| 阶段 | 异常类型 | 失败后果 | 用户可观察 |
| --- | --- | --- | --- |
| Step 1 解析 | `parser` 抛异常 | `job.status = failed` | `/api/v4/jobs/{id}/result` 返 409，`message` 含异常摘要 |
| Step 2 HLR 标注 | DeepSeek API 错误 / 解析失败 | label 退化为空 + `errors: [...]` 累计 | `/api/v4/jobs/{id}/result.errors` 数组；UI 标注 `部分 HLR 标签缺失` |
| Step 3 反向匹配 | 反向匹配抛异常 | `job.status = failed` | 同 Step 1 |
| Step 4 多智能体 | 3 provider 全失败 | 各 provider judgment 归一为 `error`，Step 5 后 0 存活强制 1★ + no_consensus + 待确认 | `status_distribution` 出现 待确认；`degradation.review_star_capped_count` 递增 |
| Step 5 Review 共识 | Review 失败 | `consensus_results.json` 中 `summary: {"all_failed": true}` | `mock_models` 仅含失败 provider |
| Step 5.5 一星复查 | 单 provider 复查 API 失败 | 该 provider 标记为 `error`，其余 provider 继续；最终以已有结果计 | re_review_results.json 中该 provider 为 `error` 状态，不阻断其他 provider |
| Step 5.5 error provider | 原始 judgment 中 coverage_status="error" | 该 provider 不发起 LLM 调用，保留 error 状态 | degradation 统计 surviving provider 时正确排除 |
| Step 5.6 部分共识重跑 | 共识重跑失败 | 该 case 保持 Step 5 原结果 | 仅影响 re_reviewed_ids 中的失败条目 |
| Step 5.6 降级 | 存活 provider ≤ 2 | 星级受硬上限约束（0 个存活强制 1★，1 个 ≤1★，2 个 ≤2★），agreement 可能被覆盖为 single_source / no_consensus | `degradation.review_star_capped_count` 递增 |
| Step 6 报告生成 | docx 写盘失败 | `outputs.<key>` 某项为 false | `/result.outputs` 部分 false |
| Step 4 降级超时 | 单 case 超时（t2+120s 或 300s 兜底） | 超时 provider 先得 TIMEOUT error judgment，任务转后台；drain 上限（`drain_max_tasks`）内任务保留等待 join，超限任务 cancel；Step 4.5 预算内返回的有效结果替换占位 | `degradation.total_case_timeouts` 递增；迟到有效结果使 `degradation.drained_late_count` 递增 |
| Step 4 降级熔断 | Provider 连续失败 ≥ 3 次 | 该 provider 被标记 unhealthy，后续 case 跳过 | `degradation.provider_status` 显示 unhealthy |
| Step 4 全部 unhealthy | 所有 provider 均熔断 | `AllProvidersUnhealthyError` → job FAILED | `/result` 返 409 |

## 13. 流程变更原则

- 增加 / 删除 / 修改 stage 须同步更新本节；
- 新增 stage 须有 ADR；
- 流程变更后应同步更新本文档。
