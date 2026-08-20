# 项目范围说明

本文档用于说明 **ICD工具原型Ver2.0** 的项目范围、输入输出边界和当前版本限制。

## 1. 项目定位

ICD工具原型Ver2.0 是一个面向 EoICD 源文件和软件高层需求文件的智能化需求生成与差异分析工具。

当前版本定位为本地演示原型，重点验证以下能力：

1. EoICD 源文件解析；
2. EoICD 条目化需求生成；
3. 多候选结果评分择优；
4. EoICD 条目化需求与软件高层需求的差异比对；
5. Word 文档形式的结果输出。

## 2. 当前支持范围

当前版本仅支持 **EoICD** 场景。

工具输入包括：

1. EoICD 源文件：

   * 一份 Word 主文件；
   * 一个或多个 Excel 附件。

2. 软件高层需求文件：

   * 第一版优先支持 Word 文件。

工具输出包括：

1. `MiniMax条目化需求.docx` — MiniMax 模型生成的条目化需求候选合并
2. `DeepSeek条目化需求.docx` — DeepSeek 模型生成的条目化需求候选合并
3. `最优条目化需求.docx` — 双模型评分择优后的最佳条目化需求
4. `EoICD条目化需求.docx` — 与最优条目化需求内容相同（复用旧文件名，保留向后兼容）
5. `EoICD与软件高层需求差异报告.docx` — 差异比对报告

## 3. 当前不支持范围

当前版本暂不支持以下内容：

1. EICD 处理；
2. MICD 处理；
3. 通用 ICD 文档处理；
4. 多项目管理；
5. 用户权限管理；
6. 在线协同编辑；
7. 数据库存储；
8. 云端部署；
9. 企业级审批流；
10. 正式生产环境发布。

如后续需要支持上述能力，应通过新的 Issue、设计文档或 ADR 进行明确。

## 4. 输入边界

当前版本默认输入文件由用户手动上传。

输入文件应满足以下基本要求：

1. EoICD Word 文件应包含可解析的接口说明、数据定义或相关说明内容；
2. EoICD Excel 附件应包含可解析的接口表格、信号表格或数据项信息；
3. 软件高层需求文件应包含可用于比对的软件高层需求内容；
4. 输入文件只在预处理阶段解析一次，后续流程复用解析后的结构化数据。

当前版本不保证处理所有历史格式、扫描件、图片化表格或严重非结构化文档。

## 5. 输出边界

当前版本输出结果用于辅助需求整理和差异分析，不替代人工工程判断。

输出文档应满足以下基本要求：

1. 条目化需求文档应包含 EoICD 条目化需求结果；
2. 差异报告应描述 EoICD 条目化需求与软件高层需求之间的主要差异；
3. 差异类型包括但不限于不一致、缺失、冗余和需人工确认；
4. 输出结果应尽量保留来源信息，便于人工追溯。

## 6. 当前版本限制

当前版本为原型阶段，存在以下限制：

1. 优先保证端到端流程可运行，不追求完整产品化能力；
2. 优先支持典型 EoICD 输入样例，不保证覆盖所有文档格式；
3. 生成结果和差异分析结果需要人工复核；
4. 多智能体评分结果仅作为候选结果择优依据；
5. 工具输出不作为最终适航、审查或交付结论。

## 7. 范围变更原则

如需调整项目范围，应遵守以下原则：

1. 范围变化应先通过 Issue 明确；
2. 涉及重大方向变化时，应新增 ADR 记录；
3. 修改范围后，应同步更新本文档；
4. 不应在普通开发任务中顺手扩大项目范围。

## 8. V4.0 反向管线范围（Issue A 落地，2026-07-28）

本节追加于原 7 节之后。V3 旧范围（§2-§6）保持不变；V4 与 V3 在同一 FastAPI 入口中双版本共存。

### 8.1 V4 支持范围

V4 是一套"**反向**"覆盖性分析工具：从软件高层需求（HLR）出发，判断每条 HLR 是否在 EoICD 文档（PubSub Excel）里有对应的接口定义；如缺失或不匹配，输出差异报告。

工具输入包括：

1. **HLR Word 文档**（必填）：从"软件需求"章节下的 8 行 × 2 列需求表中提取字段，命名为 `hlr_word_file`。
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

### 8.2 V4 不支持范围（与 V3 区分）

V4 工具输入**不**支持：
- EoICD Word 文档（V3 解析；V4 仅需 PubSub Excel）；
- `software_requirement_file` 字段（V3 命名；V4 用 `hlr_word_file`）。

V4 工具**不**输出：
- `EoICD条目化需求.docx`（V3 旧命名；V4 改名为 `EoICD条目化清单.xlsx`）；
- `EoICD与软件高层需求差异报告.docx`（V3 旧命名；V4 改名为 `EoICD与SWHLR单模型差异分析报告_*.docx` + `EoICD与SWHLR多模型差异分析报告.docx`）。

V4 旧文件命名（`_smoke` / `_smoke2` 期间遗留）不构成事实标准；V4 文档事实源以本节为准。

### 8.2.1 V4 Controller Profile（Issue #63）

V4 通过 `controller_profile` 字段控制输入解析规则（默认 `ams`）：

| profile id | 适用控制器 | 术语表位置 | HLR 需求表结构 | 追溯表文件名 |
| --- | --- | --- | --- | --- |
| `ams` | 空气管理系统控制器（默认） | `tables[0]` | ≥ 8 行 × ≥ 2 列 | `设备需求与系统ICD追溯表.xlsx` + `单模块需求矩阵分析（设备2软件高层）-裁剪.xlsx`（精确名） |
| `fgmc` | 燃油测量管理计算机 | `tables[1]` | ≥ 12 行 × ≥ 2 列（含"是否为需求"= "否" 过滤） | `*追溯*.xlsx` + `*矩阵分析*.xlsx`（glob 模式） |

profile 配置位于 `backend/app/v4/profiles/{id}/config.yaml`，覆盖 HLR 字段映射、分类关键词（analog / discrete / bus 等）、追溯表配置和 AI 标注示例四类内容。

不带 `controller_profile` 字段时行为与 Issue A 完全一致（AMS 默认），向后兼容。

### 8.3 V4 输入边界

| 字段 | 扩展名 | 大小限制 | 必填 |
| --- | --- | --- | --- |
| `hlr_word_file` | `.docx` | ≤ 50 MB | 是 |
| `eoicd_publisher_file` | `.xlsx` | ≤ 50 MB | 二选一 |
| `eoicd_subscriber_file` | `.xlsx` | ≤ 50 MB | 二选一 |
| `traceability_files` (list) | `.xlsx` | ≤ 50 MB / 文件 | 0-N；启用 `enable_traceability_prefilter=true` 时建议 ≥1 |

整请求 ≤ 200 MB。

文件名校验：仅保留 `[A-Za-z0-9._\-一-龥]`，否则替换为 `_`（V4 `coverage._safe_filename()` 实现）。

### 8.4 V4 输出边界

5 类对外下载对应 `V4_OUTPUT_FILES` 常量（`backend/app/api/v4/runner.py`）。当前稳定版本：

```text
eoicd_xlsx                  -> EoICD条目化清单.xlsx
consistency_deepseek_docx   -> EoICD与SWHLR单模型差异分析报告_DeepSeek.docx
consistency_minimax_docx    -> EoICD与SWHLR单模型差异分析报告_MiniMax.docx
consistency_qwen_docx       -> EoICD与SWHLR单模型差异分析报告_Qwen.docx
consensus_docx              -> EoICD与SWHLR多模型差异分析报告.docx
```

5 类文件输出均带命名空间 `EoICD与HLR`（不是 V3 的 `EoICD条目化需求` / `EoICD与软件高层需求差异报告`）。

### 8.5 V4 mock 行为与真实 LLM 行为

V4 当前 LLM 接入现状（Issue A 落地后）：
- DeepSeek 真实接入（`backend/app/v4/llm/deepseek_client.py`，含 URL 幂等 /v1 拼接修复）。
- MiniMax / Qwen 走 `get_llm("minimax")` / `get_llm("qwen")` 时**仍**固定返回 `MockLLMClient`（`backend/app/v4/llm/factory.py:57-59`），与 V3 行为一致。
- V4 `mock_models` 字段（`/result.mock_models` + `/status.mock_models`）按 D5 规则从 `multi_judge_results.json.providers ∩ {"minimax","qwen"}` 自动提取，无需前端配置。
- 前端若要按 `mock_models` 区分"哪些是真实 / 哪些是 mock"，可以按列表内容判别。

V4 MiniMax / Qwen 真实 Provider 接入留作 Issue F。

### 8.6 V4 bug 与局限声明（Issue A 期间发现 / 未修）

| 编号 | 现象 | 状态 |
| --- | --- | --- |
| BUG-20260728-001 | `backend/.env` 仍含真实 DeepSeek / MiniMax Key（已多次实调消费） | 建议轮换；记为 debug-log |
| BUG-20260728-002 | MSYS bash 编码降级导致上传中文文件名变成 `______________.xlsx`；V4 `_discover_trace_files` 已 glob 兜底，但前端 Content-Disposition 头标准化 deferred | Issue G |
| BUG-20260728-003 | V4 `pipeline.py:360` 与 `runner.V4_OUTPUT_FILES` 文件名常量双重硬编码 | Issue B |
| BUG-20260728-004 | V4 `factory.py` / `mock_llm.py` 直接 `os.getenv` 绕开 `config.py` module attr | Issue B |
| BUG-20260728-005 | 4 处 retry + temperature 模板近相同（`semantic_judge.py × 2` + `review_agent.py` + `hlr_labeler.py`） | Issue B |
| BUG-20260728-006 | `runner.py` 写完 `job.result` 后 `/result` endpoint 又重读盘 | Issue B（不阻塞使用） |
| 局限 | `SYSTEM_PROMPT` 在 hlr_labeler.py 内嵌 vs `comparison/*.py` 走 `app.prompts.load_prompt` 的 .md | Issue B（不阻塞使用） |
| 局限 | V4 多模型对比结果仍非真实（MiniMax / Qwen 走 mock） | Issue F |
| 局限 | ADR-002 未建（本次 2 处修改 V4 内部模块的"特权"实施未走 ADR 流程） | Issue B 启动后由其处理 |

### 8.7 V4 范围变更原则

1. V4 与 V3 各自的输入字段、输出文件命名、API 路径互不替代（避免混淆）；
2. V4 输出文档命名变更须同步更新本节 §8.4 与 `docs/architecture/api.md` §13.5；
3. 启用新的 V4 LLM provider（如 `minimax` / `qwen` 真实接入）须同步更新本节 §8.5 + ADR-001 + `backend/app/v4/llm/factory.py`；
4. 改变 V4 反向管线的步骤（增加 stage）须同步更新 `docs/project/workflow.md` §11。
