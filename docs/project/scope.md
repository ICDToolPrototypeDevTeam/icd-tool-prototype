# 项目范围说明

本文档用于说明 **ICD工具原型** 的项目范围、输入输出边界和当前版本限制。当前版本仅保留 V4 反向管线；V3 旧范围已随 V3 代码移除（见 ADR-002）。

## 1. 项目定位

ICD工具原型 是一套「**反向**」覆盖性分析工具：从软件高层需求（HLR）出发，判断每条 HLR 是否在 EoICD 文档（PubSub Excel）里有对应的接口定义；如缺失或不匹配，输出差异报告。

当前版本定位为本地演示原型，重点验证以下能力：

1. EoICD PubSub Excel 与 HLR Word 解析；
2. HLR 需求 AI 标注；
3. HLR→EoICD 反向匹配（含追溯表预筛选）；
4. 多模型裁判与 Review Agent 共识评分；
5. 条目化清单（xlsx）与差异分析报告（docx）输出。

## 2. 当前支持范围

工具输入包括：

1. **HLR Word 文档**（必填）：从「软件需求」章节下的 8 行 × 2 列需求表中提取字段，命名为 `hlr_word_file`。
2. **EoICD Publisher PubSub Excel**（与 Subscriber 二选一）：`.xlsx`，命名为 `eoicd_publisher_file`。
3. **EoICD Subscriber PubSub Excel**（与 Publisher 二选一）：`.xlsx`，命名为 `eoicd_subscriber_file`。
4. **追溯 Excel**（可选，0-N 个）：设备→HLR / 设备→ICD 追溯表，启用预筛选时必填，命名为 `traceability_files`。

工具输出（5 类对外 + 7 类内部 JSON）包括：

1. `EoICD条目化清单.xlsx` — EoICD 条目结构化清单（HL vs leaf 属性表）。
2. `EoICD与SWHLR单模型差异分析报告_DeepSeek.docx` — DeepSeek 单模型裁判结论。
3. `EoICD与SWHLR单模型差异分析报告_MiniMax.docx` — MiniMax 单模型裁判结论（当前 mock fallback）。
4. `EoICD与SWHLR单模型差异分析报告_Qwen.docx` — Qwen 单模型裁判结论（当前 mock fallback）。
5. `EoICD与SWHLR多模型差异分析报告.docx` — 3 模型共识 + Review Agent 复核 + 星级 1-3。
6. 7 个内部 JSON 中间产物（按 ADR-001 D7 **不**作为下载 API 暴露）：
   - `multi_judge_results.json` / `consensus_results.json` / `reverse_matches.json` / `reverse_report.json`
   - `eoicd_requirements.json` / `hlr_requirements.json` / `hlr_labels.json`

> **说明**：EoICD PubSub Excel 解析阶段会生成**多层级属性**（HL 高层 / DP 数据点 / RP 接收参数，带 `layer_type` / `side`）；后续 `SignalProfile` 聚类才**只取 leaf 层 DP/RP 条目**（`layer_type ∈ {DP, RP}`，见 `matching/signal_profiler.py`）。

## 3. 当前不支持范围

当前版本暂不支持以下内容：

1. EICD 处理；
2. MICD 处理；
3. 通用 ICD 文档处理；
4. EoICD Word 主文件解析（V4 仅需 PubSub Excel）；
5. 多项目管理；
6. 用户权限管理；
7. 在线协同编辑；
8. 数据库存储；
9. 云端部署；
10. 企业级审批流；
11. 正式生产环境发布。

如后续需要支持上述能力，应通过新的 Issue、设计文档或 ADR 进行明确。

## 4. 输入边界

| 字段 | 扩展名 | 大小限制 | 必填 |
| --- | --- | --- | --- |
| `hlr_word_file` | `.docx` | ≤ 50 MB | 是 |
| `eoicd_publisher_file` | `.xlsx` | ≤ 50 MB | 二选一 |
| `eoicd_subscriber_file` | `.xlsx` | ≤ 50 MB | 二选一 |
| `traceability_files` (list) | `.xlsx` | ≤ 50 MB / 文件 | 0-N；启用 `enable_traceability_prefilter=true` 时建议 ≥1 |

整请求 ≤ 200 MB。

文件名校验：仅保留 `[A-Za-z0-9._\-一-龥]`，否则替换为 `_`（V4 `coverage._safe_filename()` 实现）。

输入文件只在预处理阶段解析一次，后续流程复用解析后的结构化数据。当前版本不保证处理所有历史格式、扫描件、图片化表格或严重非结构化文档。

## 5. 输出边界

5 类对外下载对应 `V4_OUTPUT_FILES` 常量（`backend/app/api/v4/runner.py`）。当前稳定版本：

```text
eoicd_xlsx                  -> EoICD条目化清单.xlsx
consistency_deepseek_docx   -> EoICD与SWHLR单模型差异分析报告_DeepSeek.docx
consistency_minimax_docx    -> EoICD与SWHLR单模型差异分析报告_MiniMax.docx
consistency_qwen_docx       -> EoICD与SWHLR单模型差异分析报告_Qwen.docx
consensus_docx              -> EoICD与SWHLR多模型差异分析报告.docx
```

当前版本输出结果用于辅助需求整理和差异分析，不替代人工工程判断；输出结果应尽量保留来源信息，便于人工追溯。

## 6. 当前版本限制

当前版本为原型阶段，存在以下限制：

1. 优先保证端到端流程可运行，不追求完整产品化能力；
2. 优先支持典型 EoICD 输入样例，不保证覆盖所有文档格式；
3. 生成结果和差异分析结果需要人工复核；
4. 多模型裁判结果仅作为判定依据，Review Agent 共识给出可靠度星级；
5. 工具输出不作为最终适航、审查或交付结论。

## 7. 范围变更原则

如需调整项目范围，应遵守以下原则：

1. 范围变化应先通过 Issue 明确；
2. 涉及重大方向变化时，应新增 ADR 记录；
3. 修改范围后，应同步更新本文档；
4. 不应在普通开发任务中顺手扩大项目范围。

## 8. V4 mock 行为与真实 LLM 行为

V4 当前 LLM 接入现状：

- DeepSeek 真实接入（`backend/app/v4/llm/deepseek_client.py`，含 URL 幂等 /v1 拼接修复）。
- MiniMax / Qwen 走 `get_llm("minimax")` / `get_llm("qwen")` 时**仍**固定返回 `MockLLMClient`（`backend/app/v4/llm/factory.py`），与 V3 移除前行为一致。
- V4 `mock_models` 字段（`/result.mock_models` + `/status.mock_models`）按 D5 规则从 `multi_judge_results.json.providers ∩ {"minimax","qwen"}` 自动提取，无需前端配置。
- 前端若要按 `mock_models` 区分「哪些是真实 / 哪些是 mock」，可以按列表内容判别。

V4 MiniMax / Qwen 真实 Provider 接入留作后续 Issue。

## 9. V4 bug 与局限声明

| 编号 | 现象 | 状态 |
| --- | --- | --- |
| BUG-20260728-001 | `backend/.env` 仍含真实 DeepSeek / MiniMax Key（已多次实调消费） | 建议轮换；记为 debug-log |
| BUG-20260728-002 | MSYS bash 编码降级导致上传中文文件名变成 `______________.xlsx`；V4 `_discover_trace_files` 已 glob 兜底，但前端 Content-Disposition 头标准化 deferred | 后续 Issue |
| BUG-20260728-003 | V4 `pipeline.py` 与 `runner.V4_OUTPUT_FILES` 文件名常量双重硬编码 | 后续 Issue |
| BUG-20260728-004 | V4 `factory.py` / `mock_llm.py` 直接 `os.getenv` 绕开 `config.py` module attr | 后续 Issue |
| BUG-20260728-005 | 4 处 retry + temperature 模板近相同（`semantic_judge.py × 2` + `review_agent.py` + `hlr_labeler.py`） | 后续 Issue |
| BUG-20260728-006 | `runner.py` 写完 `job.result` 后 `/result` endpoint 又重读盘 | 后续 Issue（不阻塞使用） |
| 局限 | `SYSTEM_PROMPT` 在 hlr_labeler.py 内嵌 vs `comparison/*.py` 走 `app.prompts.load_prompt` 的 .md | 后续 Issue（不阻塞使用） |
| 局限 | V4 多模型对比结果仍非真实（MiniMax / Qwen 走 mock） | 后续 Issue |

## 10. V4 范围变更原则

1. V4 输出文档命名变更须同步更新本节 §5 与 `docs/architecture/api.md` §7；
2. 启用新的 V4 LLM provider（如 `minimax` / `qwen` 真实接入）须同步更新本节 §8 + ADR-001 + `backend/app/v4/llm/factory.py`；
3. 改变 V4 反向管线的步骤（增加 stage）须同步更新 `docs/project/workflow.md`。
