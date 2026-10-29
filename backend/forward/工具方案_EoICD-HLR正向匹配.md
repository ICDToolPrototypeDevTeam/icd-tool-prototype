# EoICD→HLR 正向检查工具方案（基于当前代码）

> 本文档依据 `正向检查集成` 工程的实际代码整理，用于向他人讲解工具的整体思路。
> 文中标注「已落地」= 代码已实现并生效；「方案阶段」= 已讨论设计、尚未编码。
> 全文分为两大部分：**第一部分 单 judge 五阶段流水线**（一次完整检查怎么跑），**第二部分 多智能体（multi-judge）并发编排**（多次检查如何交叉验证并聚合）。

---

# 第一部分：单 judge 五阶段流水线

## 1. 工具定位

工具做一件事：**把 EoICD（ICD 信号簇）与 HLR（软件高层需求）逐条对应起来**，并给出"对应得对不对"的结论。

- "是不是同一个信号" → **身份匹配**（阶段 3）
- "信号属性对不对得上" → **属性比对**（阶段 4）
- 上游还要先把两边的数据整理干净（阶段 1、2），下游把结果汇总成可交付报告（阶段 5）

系统集成入口是 `run_integration.py`，它按 `--step` / `--steps` 把 5 个阶段串成一条流水线，每个阶段是一个独立子脚本，通过"工作区目录 + 中间 json 文件"接力。

```
阶段1       阶段2        阶段3          阶段4          阶段5
EoICD处理 → HLR聚类 → 身份匹配 → 属性比对 → 结果汇总
(纯脚本)    (可选AI)    (含AI)        (含AI)        (纯脚本)
```

工程目录对应关系：

| 阶段 | 目录 / 入口 | 是否用 AI |
|---|---|---|
| 1 EoICD 处理 | `EoICD侧数据处理/run_data_processing.py` | 否 |
| 2 HLR 聚类 | `HLR需求聚类/hlr_cluster_tool/cluster_hlr.py` | 可选（维护映射表时） |
| 3 身份匹配 | `EoICD-HLR正向匹配与属性比对_支持追溯/identity_match/` | 是（含中文名精判） |
| 4 属性比对 | `EoICD-HLR正向匹配与属性比对_支持追溯/attribute_match/` | 是（mismatch 复核 / 冲突裁决） |
| 5 结果汇总 | `单judge结果汇总/summary.py` | 否 |

---

## 2. 阶段 1：EoICD 侧数据处理（纯脚本）

**目标**：把原始 EoICD 的 Publisher / Subscriber Excel 表，整理成"按信号 FullName 聚类的列表"，供下游匹配。

入口参数：`--project`（ams/eps/fgmc/hscu 等）、`--raw-dir`、`--output-dir`、`--config-dir`、`--step`（locate / crop / trace / dedup / all）。

内部子步骤（按 step 顺序）：

1. **locate（定位）**：按 `eoicd_locate.yaml` 里的 `Software.Name` 白名单，从原始 xlsx 里过滤出目标工作表的目标行，输出 `AMS_EoICD_Publisher_Located.xlsx` 等。
2. **crop（裁剪）**：按 `eoicd_crop.yaml` 的属性清单（按总线 A664/A825/A429/Analog/Discrete 分侧、分层列出要保留的字段），逐行抽取 `bus / direction(TX|RX) / label / name / bit_range` 等身份信息，输出 `eoicd_pub.json` / `eoicd_sub.json`（每行为一条 signal）。
3. **trace（追溯）**：**自动判断**是否启用——仅当存在追溯表且形成完整链路时，输出 `trace_result.json`（`enabled=true` + 命中的 `eoicd_fullnames` 集合）；否则 `enabled=false`，全量保留。
4. **dedup（去重聚类）**：按 `(side, sheet, signal_name)` 归簇（同名异值则拆分），输出 `eoicd_pub_clustered.json` / `eoicd_sub_clustered.json`；若 trace 启用，额外输出 `eoicd_pub_clustered_traced.json`（只保留追溯命中的簇）。

**产出**：下游身份匹配直接消费 `eoicd_*_clustered.json`（及可选的 `*_clustered_traced.json`）。结构为"按信号 FullName 聚类的列表"，每条含 `signal_name / identity / identity_key / attributes / _cluster`。

**是否用 AI**：否，纯 Python（openpyxl + yaml + 正则），无网络调用。

---

## 3. 阶段 2：HLR 需求聚类（可选 AI）

**目标**：把"软件高层需求 .docx"解析、拆条、聚类，产出可供身份匹配比对的"需求单元（unit）"。

入口参数：`--docx`、`--yaml`（属性清单）、`--out`（聚类结果）、`--dedup-out`（去重结果）、`--name-map/--state-map/--signal-map`（AI 辅助映射）、`--ai-update`（变更时调 AI 增量重建映射）。

内部流程：

1. **解析 docx**：从 `word/document.xml` 的**表格**中抽取需求（`需求ID` / `需求中文` 等）；非需求表、"是否为需求=否"、空表跳过并存 `skipped`。
2. **拆 unit**：每条需求经 `extract_identity`（正则抽 `bus/label/bit/name/direction`）+ `detect_attrs`（整表字段检测 `Label/BitOffset/RefreshPeriod/SSM/OneState/ZeroState/CodedSet…`），按 `signal_map`→按英文信号名拆分→按 label 拆，生成多个接口项（unit）。身份信息全空的项归档、不聚类。
3. **AI 映射（可选）**：仅当显式 `--ai-update` 且配置了 API key 时，用大模型生成三张映射表：
   - `name_map`：需求 → 信号名（全量覆盖，null=确认无信号名）
   - `state_map`：需求 → {OneState, ZeroState} 状态定义（部分覆盖）
   - `signal_map`：需求 → [{label, name, bit, direction}] 多信号拆分（部分覆盖）
   只对有变更的部分（stale/missing/text_changed）调用，**无 AI 或失败则回退纯正则**，结果照常产出。
4. **产出**：
   - `hlr_clustered.json`：按 yaml 属性名分组的**簇列表**（每簇含该属性的需求接口项 + 证据文本 + 需求视图 + 丢弃清单）。
   - `hlr_dedup.json`：需求级**去重列表**（`dedup_requirements`），每条需求合并全部 `interfaces`（label/name/bit/direction）、属性类并集，供下游身份匹配直接遍历。

**注意**：`hlr_dedup.json` 只提取 `bus/label/name/bit/direction/interface_idx` 等结构字段，不含需求原文——但"全空需求"的接口相关性分诊放在**聚类阶段**（`parse_docx` 已保留 `req_text`），无需从 `hlr_dedup.json` 取字段，故该限制不影响治理（详见 §14 已落地）。

**是否用 AI**：默认否（纯正则）；仅显式 `--ai-update` 时用于增量维护映射表。

---

## 4. 阶段 3：身份匹配（含 AI，核心）

**目标**：以 EoICD 信号为锚，遍历所有 HLR unit，判定"这个 EoICD 信号对应到哪条 HLR 需求"，同时用五道"防爆炸阀门"控制候选规模。

入口：`identity_match/run_identity_match.py`，内部 `run_bus_label_matching` 是主逻辑。

### 4.1 五道结构匹配

以每个 EoICD 信号为单位，对全部 HLR unit 依次过筛：

1. **Bus + Label + Bit 三道结构匹配**：`bus / label / bit` 任意一道相等即命中；带 `null_is_wildcard=True`，即该字段为空时视为"不约束"（全空 unit 本会匹配到所有信号，现已由聚类阶段的接口相关性分诊在源头分流，见 §14 已落地）。
2. **Direction 匹配**：TX/RX 与 PUB/SUB/发送/接收 方向归一后比对。
3. **Name 名称匹配**：见 4.3。

### 4.2 防爆炸阀门（已落地，按顺序生效）

身份匹配阶段共有五道阀门，逐级收紧候选：

| 阀门 | 机制 | 作用 |
|---|---|---|
| ① 名称门控 | 英文名相似度 < 0.3 直接拒；纯中文名不硬拒、交 LLM | 拦掉明显无关候选 |
| ② 每信号封顶 K | K = 已填属性数 + margin（地板 3） | 单信号候选数上限 |
| ③ 组0 不占 K | `name_sim=None`（翻译失败中文名 / 无名单元）全保送；组1（有相似度）独立按 K 截断 | 防止中文名挤掉组1召回 |
| ④ 含中文全 null 检索前置 | `identity_retrieval.py`：候选过多时用 LLM 检索缩减 | 中文名过多时降候选 |
| ⑤ 中文名翻译预通行 | `name_translator.py`：含中文 HLR 名先用大模型译出 `en + abbr`，abbr 优先参与相似度 | 让中文名也能算相似度门控 |

其中 ⑤ 还包含 429 限流防护（识别 `Retry-After` 头，缺失/越界时退化指数退避），与 AI matcher 同款。

### 4.3 Name 名称匹配（含 AI 精判）

- `name_similarity`：抽英文名 token 算 containment；纯中文 / 无拉丁 token 返回 None（即落入组0）。
- `name_ai_matcher`：含中文、且结构匹配上来的候选，把"原始 HLR 名 + EoICD 信号名 + 机器翻译参考（含工程缩写 abbr）"交给 LLM 做精判，确认是否为同一信号。
- `name_cropper`：把 LLM 判定为不匹配的、或无 name 的 unit 收进 `skip_ai_pairs`，直接送属性比对（"无名 unit 跳过 name AI、直送属性比对"已落地）。

### 4.4 输出

每信号生成一个候选列表，写入 `match_report_pub.json` / `match_report_sub.json`，供阶段 4 消费；被拒信号写入 `rejected_*.json`，供阶段 5 兜底。

---

## 5. 阶段 4：属性比对（含 AI，核心）

**目标**：对身份匹配锁定的 (EoICD 信号, HLR 需求) 对，逐属性比对"值对不对得上"。

入口：`attribute_match/run_attribute_match.py`。每个属性比对产出四态结论之一：**一致 / 不一致 / 无提及 / 无具体值**。

### 5.1 两道脚本规则

- `attr_script_matcher`：按属性类别套用确定性比对规则（如数值带单位 "1000 vs 1000ms" 视为等价、bit 位映射等），能直接判的给出脚本结论。
- `attr_result_clustering` / `attr_conflict_extractor`：把脚本判定为 mismatch 的聚合、抽取冲突点，准备送 AI。

### 5.2 两处定向 LLM 调用（可关）

属性阶段确实用到 LLM，但只在两处"脚本拿不准"的节点，且都可关闭（`ai_review.enabled`、批处理 `batch_size=200`、有数量门槛）：

1. **mismatch 复核**（`attr_mismatch_ai_matcher`）：脚本判"不一致"的，交 LLM 复核是否真不一致（过滤误报，如单位等价）。
2. **冲突裁决**（`attr_conflict_ai_judge`）：多 HLR 需求对同一属性结论冲突时，交 LLM 裁决取最合理值。

### 5.3 输出

`attribute_match_report_pub.json` / `attribute_match_report_sub.json`：每条信号各属性的四态结论 + 证据，供阶段 5 汇总。

---

## 6. 阶段 5：结果汇总（纯脚本）

**目标**：把上游身份匹配 + 属性比对的结果，汇总成面向用户的最终报告。

入口：`单judge结果汇总/summary.py`，参数 `--config`、`--out`、`--match`（可多次）、`--device`。

输入：
- `attribute_match_report_{pub,sub}.json`：主输入，提供每条信号各属性的四态结论。
- `input/rejected/rejected_*.json`：身份识别阶段被拒信号，**兜底用**，防漏报。
- `input` 下含"高层需求规范"的 docx：仅用于自动提取**设备名**（不参与比对逻辑；`--device` 可显式覆盖）。

内部逻辑：
- `normalize_attribute_match`：把上游 `matched/mismatch/null_value/not_mentioned` 四类重排为统一 `comparisons`。
- `run_direction`：按 `eoicd_cluster_id` 聚合信号，`classify_signal` 按优先级 **不一致 > 未落实 > 部分落实 > 已落实** 判定信号最终结论，统计分布与需求覆盖率。
- `apply_identity_safeguard`：以 rejected 全量 cid 与属性匹配产物做**差集**，凡"被拒却未出现"的信号补一条"未落实(未识别)"，防止漏报。

输出（落在 `outputs/<设备名>EoICD到软件高层需求的落实检查_<时间戳>/`）：
- `report_{pub,sub}.json`：结构化中间产物（透传 `hlr_identity` 地址四元组），**供 multi-judge 聚合用**。
- `summary_{pub,sub}.docx`：人读汇总（Word）。
- `summary_{pub,sub}.xlsx`：全量明细（统计页 / 汇总页 / 按问题类型分类的多张 Sheet）。

**是否用 AI**：否（仅依赖 python-docx、openpyxl）。

---

# 第二部分：多智能体（multi-judge）并发编排

> 单 judge 流水线解决了"一次检查"。但实际工程中，单个大模型结论可能有误、不同模型结论可能不同。**多智能体部分解决的是"多次检查如何交叉验证、并聚合成一份可信结论"**。

## 7. 为什么需要多 judge

两种典型用法（均已落地）：

- **异模型交叉验证**：多个 judge 用不同 `(model, base_url, api_key)`，相当于多个"评审专家"各自独立跑一遍完整检查，再投票。
- **同模型自洽采样**（self-consistency）：同一个模型跑 N 次，看结论是否稳定。

无论哪种，最终都由聚合层对齐、投票、生成共识结论与星级。

## 8. 多 judge 架构

编排器：`multi_judge_runner.py`。核心约定（已落地）：

- **judge = 一次完整正向检查运行（阶段 1~5）**。配置里 `judges` 列表每项是一套 `(model, base_url, api_key_env)`；允许 N 个 judge 完全相同。
- **阶段 1（无 AI）共享跑一次**：所有 judge 共用同一份 EoICD 处理产物，copy 给各 judge 工作区。
- **阶段 2/3/4（调 AI）per-judge 并发**：每个 judge 在**独立子进程 + 独立 env + 独立工作区 + 独立 map 缓存**里各自跑一遍。
- **阶段 5（无 AI）聚合一次**：每个 judge 产出 `report_*.json`，编排器按 `signal_full_name` 对齐，进入聚合层。
- **退化兼容**：`judges` 配 1 个 = 现有单 judge 行为。

### 隔离与健壮性（已落地）

- **子进程隔离**：AI 配置通过子进程 env 注入（`AI_MODEL/BASE_URL/API_KEY`），自动穿透到阶段 2/3/4；每个 judge 指向自己工作区的 `name/state/signal_map.json`，避免多 judge 交叉污染。
- **工作区白名单清理**：每轮开始前，`shared/` 与每个 `judge_*/` 重置为"只留 3 个 AI map 缓存"，其余（上一轮输入、产物、日志）全清，防止换分析对象后残留被误用。
- **超时/迟到补收**：不 kill 超时 judge，由后台线程继续跑、统一补收；软上限之外再给宽限，宽限结束仍存活的进程才强制 terminate（防卡死拖挂整个 runner）。
- **map 健康检查**：每个 judge 启动前自检 3 个 map（缺失 / 解析失败 / 半成品 / 老格式），分级告警、不阻断。

## 9. 聚合层（`multi_judge_aggregate.py`，无 LLM）

把 N 个 judge 的分片汇聚成最终报告。核心机制：

- **信号对齐**：`build_shard_from_reports` 读取每个 judge 的 `report_*.json`，按 `signal_full_name` 对齐成 `{信号: {judge: 结论}}`。
- **两维共识分开**：
  - **身份共识**：`verdict + matched_hlr + name_match_status + 地址四元组(bus/label/bit/direction)` 计票。
  - **属性共识**：每个 `attribute_class` 的单属性结论，按 `(judge, attribute_class, req_id)` 三键分组（多 HLR 修复：不再两键塌缩，避免"某 HLR 不一致被另一 HLR 一致覆盖"）。
- **严格二分两类"不一致"**：
  - `inconsistent_attributes`：**事实不一致**（任一 judge 判为不一致/属性不一致），跨 (judge, HLR) 全集取最差。
  - `field_disagreements`：**judge 间分歧**（仅在 KEY_FIELDS 上算，如 verdict 不同、匹配 HLR 不同），与事实不一致分离，避免误报。
- **多数投票 + 平票裁定**：身份计票取最高票派的 verdict；**平票时按严重度序列取最严重者**（`不一致 > 未落实 > 部分落实 > 已落实`，与阶段 5 `classify_signal`、报告展示序三处统一）。
- **星级（star_rating）**：对齐反向映射 full→5/4、majority+关键异议→2、split/0存活→1；单 judge 配置（n_configured==1）正常路径不降级。
- **need_review 路由（已落地，非人工待办）**：`NEED_REVIEW_RULES` 规则表（无有效源 / 多 judge 下星级≤2），命中即路由该信号进 Step5 仲裁。这是"该送第二裁决"的判定标记，**会自动送 LLM 三态复核，仅 `unconfirmed` 才退人工**，渲染按 AI 路由呈现。
- **分析摘要合成**（`build_analysis_summary`，纯规则不调 LLM）：按"判定句 + 匹配子句 + 属性子句 + 补充句"合成一段话，覆盖一致/多数/分歧/平票/仲裁翻转各情形；`sub_classification`（未落实二级子类）单一来源（`derive_sub_classification`），聚合层与摘要共用，避免口径漂移。

## 10. 仲裁层（`multi_judge_arbitrator.py`，含 LLM，多智能体"第二裁决"）

聚合层结论"该复核"的信号，由仲裁层送独立模型再判一次（对齐反向 Step5 + Step5.5 + Step5.6）：

- **Step5 仲裁者共识**：对聚合产出的分歧 / 覆盖缺口 / 低置信信号，由独立固定模型再判一次，输出 `agreement_level / final_verdict / inconsistent_attributes / field_disagreements / confidence / final_analysis`。**合并而非覆盖**——后端事实不丢，LLM 仅补 category/summary 或增补语义层事实（白名单约束，越界的身份层字段/自造类不并入）。
- **Step5.5 peer-aware 复查**：对 ≤2★ 信号，把各 judge 结论**匿名化为 A/B/C（抹去模型名）**，按"独立视角"分组（同模型重复采样不算独立视角），交仲裁者反思并产出修正结论，再触发 Step5.6 重仲裁。
- **熔断器 / 并发 / 预算**：LLM 累计失败达阈值（默认 15）即整体跳闸跳过剩余；并发度默认 8（上限 16）；可选复核预算上限；401/403 不重试、瞬时故障指数退避。所有"跑不了"的情况一律记账 `skipped`，不假装复核过。
- **零成本兜底**：扫描"叙述已推翻、结构化未回写"的自相矛盾信号，标记"待人工确认"。

## 11. 汇总统计与报告（`multi_judge_summary.py` / `multi_judge_report.py`）

- **汇总统计**（`build_summary`）：信号总数、星级分布、共识度分布、判定分布（按 `不一致 > 未落实 > 部分落实 > 已落实 > 覆盖缺口 > 待确认` 顺序）、AI 复核覆盖率（哪些信号拿到第二裁决、哪些因故跳过）。
- **Word 共识报告**：一、一致性分析总结（判定分布 / 星级 / 处置建议）；二、分析明细（默认只列异常信号，可选全量），列头含 序号 / 信号 / 判定 / 子分类 / 匹配 HLR / 属性比对 / 共识 / 星级 / 分析摘要。缺 python-docx 时优雅跳过。
- **Excel 共识报告**：与 Word 同源，多 Sheet 明细。缺 openpyxl 时优雅跳过。
- **运行清单**：每次 run 一份 manifest，记录时间戳 / 配置 / judge / 产物路径，便于跨 run 溯源。

---

# 第三部分：配置开关速查与数据流

## 12. 关键配置开关

`match_config.json`（身份匹配）：

| 开关 | 默认 | 作用 |
|---|---|---|
| `name_translation_enabled` | true | 中文名翻译预通行 |
| `translation_batch_size` | 50 | 翻译批次 |
| `per_signal_cap_group0_exempt` | true | 组0 不占 K、全部保送；false 回退旧行为 |
| `per_signal_cap_floor` | 3 | K 最小下限 |
| `--no-blank-triage`（默认开启） | 关掉则全空项全归档 | §14 已落地：全空需求接口相关性分诊（聚类阶段，AI 不可用降级正则） |

`attribute_match_config.json`（属性比对）：

| 开关 | 默认 | 作用 |
|---|---|---|
| `ai_review.enabled` | true | mismatch 复核 / 冲突裁决 总开关 |
| `ai_review.batch_size` | 200 | AI 复核批大小 |

`multi_judge_config.json`（多智能体）：

| 项 | 说明 |
|---|---|
| `judges[]` | 每个 judge 的 model / base_url / api_key_env（可多个 identical） |
| `aggregation.need_review_on_split` | 分歧是否触发复核 |
| `arbitrator` | 仲裁模型（缺省回落 judge[0]；同源时如实警示） |
| `re_review.enabled` | 是否启用 Step5.5 复查循环 |

## 13. 数据流全景（一次 multi-judge 运行）

```
EoICD xlsx ─┐
            ├─[阶段1 共享]→ eoicd_*_clustered.json ─────────────┐
HLR docx  ──┴─[阶段2 每judge]→ hlr_clustered/dedup.json ─┐      │
                                                         │      │
                        ┌──────── 每个 judge 独立子进程 ───┼──────┤
                        │  [阶段3 身份匹配]→ match_report  │      │
                        │  [阶段4 属性比对]→ attr_report   │      │
                        │  [阶段5 汇总]→ report_*.json ────┘      │
                        └───────────────────────────────────────┘
                                          │
                                  按 signal_full_name 对齐
                                          ↓
                        [聚合层] 两维共识 / 多数投票 / 星级 / need_review
                                          ↓
                        [仲裁层] Step5 仲裁 → Step5.5 复查 → Step5.6 重仲裁
                                          ↓
                        [汇总+报告] summary.json / Word / Excel / manifest
```

## 14. 全空需求接口相关性分诊（已落地，阶段 2 聚类内）

**背景**：`bus/label/bit/name` 全为空的需求（如只写"系统应在 50ms 内响应"或"信号 X 刷新周期 100ms"），无法用结构身份匹配，但两者命运不同——前者是系统级指标、与接口无关；后者虽有接口属性但抽取时未识别出 name/bit。**若一律丢弃，会误丢"接口相关"的需求；若一律保留，又会让"系统级时间要求"匹配到所有 EoICD 信号（爆炸）。**

**已澄清的误区**（决定方案形态）：
- "时间描述"指需求原文里的"XXms"被误当刷新周期/发送间隔属性，文本里通常**没有**信号名/总线名/bit 号可抽 → 不能用关键词/锚点 token 缩候选，只能靠语义判断。
- 因此分诊放在**聚类阶段**（此处 `parse_docx` 已保留需求原文 `req_text`，无需再去 `hlr_dedup.json` 找字段），比放在身份匹配阶段更合理。

**实现**（`cluster_hlr.py`）：
1. 聚类主循环原来用 `_has_identity`（bus/label/name/bit 任一非空）把全空项直接剔除、归档 `dropped_requirements`；现在改为先对全空项做**二元接口相关性分诊**，再决定去留。
2. `run_blank_triage(args, items)`：对全空项批量调用 LLM 判"是否与具体接口/信号相关"，prompt **不塞信号列表**（只判是/否）→ 成本低；AI 不可用/失败时**降级为正则关键词兜底**（含接口相关词→相关，否则→不相关），失败不中断主流程。
3. 判定结果：
   - **接口相关** → 放回 `results`，按**原本方法**继续流到下游（即无名 unit 路径：下游 `null_is_wildcard` 仍会匹配同总线/全部信号——这是用户确认的"接口相关就按原本方法"的直接结果，非遗漏）。
   - **非接口相关** → 归档 `dropped_requirements`（reason 注明"经接口相关性分诊判定为非接口相关"），不送匹配。
4. 配置开关：**`--no-blank-triage`**（默认开启；加上即退回"全空项全归档"旧行为）。`meta.blank_triage` 记录全空数 / 判相关数 / 判不相关数 / 判定方式（ai / regex / disabled）。

**与身份匹配层的关系**：本治理在聚类阶段就分流，下游身份匹配/属性比对**无需任何改动**——相关全空项以"无名 unit"身份自然走 `skip_ai_pairs → 直送属性比对` 路径（见 §4.3）。

**注意**：正则兜底只作 AI 不可用时的降级，对"否定句内含接口关键词"（如"不指向具体信号"）可能误判，故 AI 配置就绪时以 AI 判定为准（代码对此打了 warning）。

---

## 15. 一句话结论（讲给别人听）

> 这个工具把"ICD 信号"和"软件需求"逐条对齐并验证属性一致性。单条检查走 5 个阶段（EoICD 处理 → HLR 聚类 → 身份匹配 → 属性比对 → 结果汇总），其中身份匹配和属性比对会在拿不准时调用大模型。为了提升可信度，外层用多智能体（multi-judge）把"同一次检查跑多遍（不同模型或同模型多次）"，再通过聚合层投票、仲裁层第二裁决，最终给出带共识星级和 AI 复核覆盖的报告。
