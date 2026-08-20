# ADR-003 移除 V4 早期正向原型与旧单模型反向 CLI

| 项目 | 内容 |
| --- | --- |
| 状态 | **Accepted** |
| 生效范围 | 后端 `app/v4/` |
| 提议日期 | 2026-08-20 |
| 关联 ADR | 取代 ADR-002 D4（保留 app/v4 正向原型的决定） |

---

## 背景

V4.0 反向管线（脚本匹配 + 多 Agent 裁判 + Review 共识 + 降级保护）已稳定运行，是当前唯一主线，Web API（`/api/v4`）是唯一入口。`app/v4/` 内遗留两类冗余代码：

1. **早期正向原型**（EoICD→HLR 属性级正向匹配）：`run_forward_pipeline` + `comparison/case_builder.py` + `matching/{candidate_matcher,text_matcher,unified_matcher}.py` + `prompts/forward_judge.md`，以及配套的正向 Pydantic 模型（`MatchCandidate` / `ComparisonCase` / `JudgmentResult` / `DifferenceReport` / `MatchOutput` / `JudgmentOutput`）与正向 config 常量（`MATCH_SCORE_WEIGHTS` / `MATCH_WEIGHTS` / `DATA_TYPE_EQUIV` / `UNIT_EQUIV` 等）。这些是 V4 早期尝试的「属性级」正向匹配，反向管线稳定后从未被 Web API 调用。
2. **旧单模型反向 CLI**：`reverse-judge` / `reverse-report` 两个 CLI 子命令及 `judge_reverse_cases` / `generate_reverse_report`，被多模型反向流程（`multi_judge` + `review_agent` + `generate_consensus_reverse_report`）取代。

ADR-002 D4 曾决定「保留正向原型供后续正向重构参考」，但该代码实际从未被复用，且其「属性级」建模与反向管线已稳定的「ICDBlock 级」信号画像/Block 聚合模型不一致，持续增加维护面与认知负担。

## 决策

### D1：删除早期正向原型整链

删除 `run_forward_pipeline`、`case_builder.py`、`candidate_matcher.py`、`text_matcher.py`、`unified_matcher.py`、`forward_judge.md`，以及配套的正向模型、正向 config 常量、正向 CLI 命令（`match` / `judge` / `report` / `analyze`）和仅被正向链引用的死代码符号。

### D2：删除旧单模型反向 CLI

删除 `reverse-judge` / `reverse-report` 两个 CLI 子命令及 `judge_reverse_cases` / `generate_reverse_report` 函数。反向主链（parse → label → reverse match → multi-judge → review consensus → report）不涉及这两个单模型 CLI 路径，删除后行为不变。

### D3：未来正向采用 ICDBlock 级 Case

未来若重新实现正向功能，Case 以 **ICDBlock 级**（复用现有 `signal_profiler` 的 ICDBlock 聚合产物）为基准，而不是已删除的「属性级」`ComparisonCase`。

### D4：未来正向的候选检索算法待定，不绑定 BM25

本次清理仅删除旧属性级正向原型及其耦合的 `TextMatcher`（BM25）实现。**这不等同于放弃 BM25**——未来正向的候选检索算法（BM25 / 向量 / 混合）尚未确定，待正向功能开发时另行决策，本 ADR 不做绑定。

## 原因

- 死代码增加维护面与认知负担；Web API 是唯一入口，上述代码无任何调用方。
- 旧属性级正向建模与反向管线已稳定的 ICDBlock 级模型不一致，即使后续做正向，也会以 ICDBlock 级为基准，旧属性级 `ComparisonCase` 无复用价值。
- BM25 只是旧 `TextMatcher` 的一个具体实现，删除该实现不意味着放弃 BM25 作为未来候选检索的选项。

## 影响

- **后端 `app/v4/` 精简**：删除 4 个匹配/比较模块文件、1 个 prompt 文件、6 个正向 Pydantic 模型、若干正向/死代码 config 常量、6 个 CLI 子命令，及若干仅在正向链中使用的函数。
- **反向主链不变**：parse → label → reverse match → multi-judge → review → report 六步行为与输出不变。
- **文档**：`current-architecture.md` 同步更新 prompts 资产清单与 ADR 引用；ADR-002 D4 标记被取代。

## 替代方案

| 方案 | 不选择理由 |
| --- | --- |
| 继续保留正向原型 | 从未复用，属性级建模与 ICDBlock 级模型不一致，持续增加维护面 |
| 只删正向、保留旧反向 CLI | 旧反向 CLI 已被多模型流程取代，无单独保留价值 |
| 未来正向沿用属性级 ComparisonCase | 与已稳定的 ICDBlock 聚合模型不一致，需重做而非复用 |

## 状态

**Accepted**（2026-08-20）。
