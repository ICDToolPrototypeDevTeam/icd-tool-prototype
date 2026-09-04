# 变更记录

本文档记录 ICD工具原型 的版本级变化。

## [Unreleased] - 2026-09-03

### Fixed

- **判定 error 高发修复（截断重试 timeout 死循环）**：大 case（多 ICD Block）判定响应频繁截断（finish_reason=length），翻倍 max_tokens 重试时单请求 timeout 仍固定 120s，16384 tokens 生成必然超时 → 整链重试 → 空响应/error，同一缺陷使 deepseek/minimax 在重 case 上轮番 error（c2cfdfc6 REV-0003、ed72ffc6 REV-0004/0005）。修复：三个 LLM client（`deepseek_client.py`/`minimax_client.py`/`qwen_client.py`）截断重试循环内单请求 timeout 随 max_tokens 同步 ×2（120→240s），warning 打印同步带 timeout。桩测验证：三家截断重试 timeout=[120, 240] 断言通过。
- **FGMC 反向判定标签/证据集对称性修复（含保送尝试与撤销）**：E2E（job `c2cfdfc6`）中镜像需求 005906（inconsistent）与 005907（covered）行为不对称，根因与处理：(1) 输入文档把通道写成 `FGMC_ CHB`（下划线后空格），AI labeler 因此漏标 devices/keywords——`hlr_labeler.py` 在送 LLM 前压缩 `_\s+`，标注输入与排版无关（保留生效）；(2) 曾尝试对 bit_field>0（位号精确/部分命中 ICD 位范围）的块免 top-K 截断补全 SDI 目标证据集——实测"所有 ARINC 429 标签"类 HLR 的候选含 127 个同质 SDI 块（同为 offset8/size2 + 同一 CodedSet），全量保送把 case 从 20 块撑到 131 块，prompt 膨胀使长输出 provider（deepseek，输出含大量推理 token）8192 起步即截断、翻倍仍难写完→error→连续失败触发熔断后全链 SKIPPED（单块小 case 019482 亦 error 即熔断所致），已撤销保送（2026-09-03）——归一化后两案打分同构，top-K 截断天然对称（复测 005906/005907 top-20 块顺序+集合完全一致），无需保送。005906 的 inconsistent 本身定性为 AI 位序解码偶发错位（通道↔SDI 一致性判断语义保留，不改判定提示词）；strap/config 块为合法数据块不予排除；通道 A/B 区分确认不需要匹配层专门机制。验证：无保送复跑 005906/005907 均回到 20 块且完全一致。
- **FGMC 反向判定待确认回归修复（ICD 数据驱动）**：FGMC 全流程回归（job `a6bb1b5b`）相对 golden 待确认 1→4（005906/005907/005915/019482），005916 由 covered 翻 inconsistent。根因：HLR"第N位"为物理 1 基、ICD BitOffsetWithinDS 为 0 基，各 provider 换算不一致导致误判。修复全部由 ICD 数据驱动、无协议规则注入：`hlr_classifier.py` 新增中文位号提取并统一换算 0 基（"第9和10位"→offset8/size2 等）；`reverse_matcher.py` SDI 协议块门控由"HLR 显式 SDI 词元"放宽为"位字段与 ICD 位范围重叠即放行"；`signal_profiler.py` word_protocol_fields 泛化为 LABEL/SDI/SSM/PARITY 全协议位锚点；`semantic_judge.py` 附 ICD 锚点基制说明、BNR 负量程符号位推导行（量程 -512 且 LsbRes/ParameterSize 在场时由算术强制"补码有符号、最高位为符号位"，仅条件触发）、HLR 位声明结构化呈现（物理→0 基逐条列出，防裁判换算错位）。验证：真实 LLM 探针 005916 三家全票 covered（此前 1:2 inconsistent）、005906 回 covered 多数、005907 covered、005910 covered；005915/019482 的 SSM 取值编码在 FGMC Publisher ICD 无定义（无 CodedSet），三家判定靠领域记忆互相矛盾，列为已知数据天花板（不注入协议知识，建议人工抽查）。

- **判定位对组装位序修正（reverse_judge.md 位号方向约定）**：FGMC 005906（FGMC_CHA→"所有标签"第9位="1"/第10位="0"）与 005907（CHB→"0"/"1"）被 multi_judge 多轮判 needs_review/inconsistent，判词称"编码'10'=值2=Channel B 与 CHA 矛盾"。实为各 provider 把 HLR 书写顺序当作位序：ARINC 429 字内位号越大越高位（第10位是第9位的高位），(9位,10位)=(1,0) 即字段值 1=Channel A、(0,1) 即值 2=Channel B，与 ICD CodedSet（`1=LEFT Channel or Channel A / 2=RIGHT Channel or Channel B`）自洽，两案均应 covered。该盲区与思考开关无关（deepseek disabled/enabled/effort-low 与 qwen 同错）。修复：`prompts/reverse_judge.md` 比对关注点补一句位号方向约定（第10位是第9位的高位、第31位是第30位的高位，不得按 HLR 书写顺序当作高位在前），不注入协议编码细节。E2E（job `94b8aa9b`）验证：005906/005907 均翻回多数 covered。
- **deepseek 思考模式定案（enabled + reasoning_effort=low）**：为压制 error 曾显式关闭 deepseek 思考（reasoning 计入 max_tokens，思考会吃光判定输出预算），error 确实消失但质量下降（用户观察"很多待确认"；单块探针 005916 因缺符号位等价推理从 covered 翻 inconsistent）。定案折中：`deepseek_client.py` 恢复 `"thinking": {"type": "enabled"}` 并加 `"reasoning_effort": "low"`——20-block case 实测 reasoning≈3068 tokens，8192 预算内 finish=stop，既保留关键推理又控制输出长度；截断重试 max_tokens/timeout ×2 机制继续兜底。E2E（job `94b8aa9b`）验证：0 error，deepseek 6 case 中 5 covered + 1 合规 needs_review（019482，ICD 无 SSM 定义）、0 inconsistent。

## [Unreleased] - 2026-09-02

### Changed

- **反向匹配：SDI 位独立成 Block + SSM 位定义随 case 附注**：HSCU 样例暴露两条证据缺口——(1) HLR 明确断言 SDI（"设置Label和SDI"）时，SDI 叶子（含 CodedSet，如 1=System1）被 build_blocks 按协议族整体跳过，裁判无 SDI 定义可看；(2) HLR 断言 SSM（"将 LBL_xxx_SSM 置为 SSM_DIS_NO"）时，SSM 位定义（bit29/2bit/A429_SSM_DIS）同样不可见。修改点：`config.py` PROTOCOL_DATAFORMATS 移除 A429SDI/A429_SSM_BNR（entry 层放行，SSM 附注补 dtype）；`signal_profiler.py` build_blocks 放行 SDI 族生成 Block、ICDBlock 新增 `word_protocol_fields` 承载同 label 的 SSM 位定义附注、CodedSet（profile 层）与 SDIExpected/CodedSet（block 层）改为多值合并（修复 first-wins 使裁判看不到 `2=System2` 等编码、误判 inconsistent/needs_review）；`reverse_matcher.py` 新增协议族词元门控（HLR 文本含"SDI"才允许 SDI 块候选）、显式提及按精确信号名命中给 signal_name 30 分、top-K 豁免（显式断言的协议块与 HLR 正文显式列名的信号块追加不替换）、sn-zero 过滤触发条件排除协议块、SDI 维度与 Gate 2 改为多值集合匹配、SDI 值级命中纳入方向矛盾 soft 救援条件（修复 labeler 关键词碎片化导致 sn<30 时 FCM1 word 被方向门误删的回归）；`reverse_case_builder.py` 序列化附注每 label 每 case 一次；`semantic_judge.py` 渲染附注为裁判上下文；`hooks.py` 目录表提取扩展 SDI号/SSM类型列（8 列表与 4 列表分别处理，单数字 SDI 写入别名 `SDI=n`，多值映射跳过），别名形如 `L206_AIR_SPEED_FCM1_R1（SDI=1，SSM类型=BNR）`；`prompts/reverse_judge.md` 内部逻辑判 covered 增加"相关性前提"（HLR 引用的信号与全部匹配 Block 均不相关时判 needs_review，仅共享通用后缀不视为相关）。E2E 结果：HSCU 10 条 HLR 全管线跑通，7 个进入裁判的 case 中 6 个 covered、1 个 needs_review（022996 匹配块完全不相关，符合新语义）；历史误判 023124/023194/022645/023389/023507 全部修复。
- **SPAR 备用位块匹配降级**：`reverse_matcher.py` 新增 `_filter_spar_when_data_matched`——同 label 已有 signal_name>0 的真实数据块命中时，SPAR（备用位）块从裁判上下文剔除（HLR 不会对备用位做断言，SPAR 命中纯属词名巧合）；HLR 只提词名、无数据块命中时保留 SPAR（它是词名上下文的唯一线索）。协议族块（SDI）不计入"数据命中"判定，避免"只提词名"场景下 SDI 块误触发过滤。验证：022587 从 21 块（含 SPAR 噪音）变为 33 块全数据块，三方 covered 5★（0.95）；023389 保留 19 SPAR+SDI，covered 2★。
- **删除「AMSC 通用协议特征 covered 判定说明」章节（reverse_judge.md）**：SDI 位独立成 Block 后，协议类 HLR（如 HLR_544"按通道位置写 SDI 位固定数据"）已能凭 SDI 块的位定义与 CodedSet 证据级验证。A/B 测试实证：移除该章节后 HLR_544 仍三方 covered、consensus 5★（0.95），与有章节时一致；AMS 样例中无奇偶校验类协议 HLR，删除对当前结果零影响。无块证据的协议断言（如未来文档出现"奇偶校验位设为奇校验"）将按判定规则 4(b) 落入 needs_review——比"协议标准保证 covered"更诚实；如需自动 covered 可后续将 PARITY 位同样块化。
- **判定初始 max_tokens 4096→8192**（`semantic_judge.py`）：FGMC 慢跑排查发现 deepseek 判定响应频繁截断、触发翻倍重试（截断→4096→8192→16384 从头重新生成，单次耗时成倍）。大 case（20 Block）下 4096 几乎必截断，初始提到 8192 后多数 case 一次生成完成，减少一轮"生成→截断→重来"。共识（review_agent）与复查（re_review）本就是 8192，不受影响。

## [Unreleased] - 2026-09-01

### Fixed

- **反向管道 needs_review 误判修正（判定语义对齐"只比对 HLR 明确写出的声明"）**：真实 HSCU 样例暴露两类误判——(1) HLR 仅描述软件内部数据路由/状态传递、引用信号 Label 但未断言接口属性（BNR 格式/位偏移/位宽/LSB/量程/周期/方向）时，三方裁判判 needs_review；(2) 一个 HLR 匹配到多个 ICD Block 而只写了其中一个时，被误判"需求缺失"其余 Block。修正后判定语义：HLR 未提及的属性与多 Block 中未提及的部分 Block 不在比对范围内，不构成不一致、也不构成 needs_review，判 covered；needs_review 仅限三种情形——HLR 明确断言了接口属性但所给 ICD 信息不足以验证、HLR 对所有匹配 Block 均未引用（Block 无法支持判定）、provider 分歧。修改点：`prompts/reverse_judge.md`（判定规则重写 + 新增「needs_review 禁用情形」章节 + 逐项比对多 Block 规则）、`comparison/semantic_judge.py`（"待确定"匹配的用户提示词注入精简为谨慎提醒）、`prompts/consensus.md`（inconsistent_attributes 排除"未提及"属性 + 2 处教学示例修正）、`comparison/report_generator.py`（待确认说明文案同步）。对外 API、数据契约、星级机制无变化。

## [Unreleased] - 2026-08-29

### Changed

- **共识报告 docx 列宽锁定（fixed layout + full_width 区分）**：`consensus_word_generator.py` 新增 `_set_table_layout_fixed(table, full_width=True)` helper，给 3 张表（判定分布 / 星级分布 / 分析明细）加 `<w:tblLayout w:type="fixed"/>` 并同步 `<w:tblGrid>` 到首行 tcW，让 cell.width 真正生效不再被 Word autofit 按内容撑开。判定 / 星级分布表调用时 `full_width=False`（tblW 保持 auto，按内容算总宽，不再被强制拉到 100% 页宽）；分析明细表调用时 `full_width=True` 默认（占满 100% 页宽保持原行为）。分析明细表 cm 值调整：列 2「SWHLR ID」5.25→4.61、列 4「ICD Block」4.5→5.1、列 5「不一致属性」2.0→2.11、列 6「分析摘要」10.5→9.93、列 8「星级」1.39→1.93，其余 3 列微调 ±0.02。判定规则、数据契约、对外 API、下载文件均无变化。

## [Unreleased] - 2026-08-28

### Changed

- **Step 5.5 re-review per-case 内并行**：re-review 阶段两层串行循环（先 case、后 provider）改为 per-case gather：每个 case 内部的 3 个 provider 调用一次性 submit 到 Step 4 共享的 `_get_drain_executor()` 线程池（通过 `_submit_with_gate()` 走信号量闸门），用 `concurrent.futures.wait` + `FIRST_COMPLETED` 在固定 `case_total_timeout` ceiling 内收集结果，单 case wall time 从 `sum(providers)` 降到 `max(providers)`。复用 Step 4 已有的 `_get_drain_executor` / `_get_inflight_sema` / `_submit_with_gate` / `make_error_judgment` / `classify_exception` 基础设施，**对外契约零变化**（`re_review_results.json` schema、`re_review_judgments()` 入参返回、`multi_judge_results.json` 落盘时机不变）；仅内部执行模型从串行改为并行。
- **AMSC 通用协议特征 covered 判定规则（reverse_judge.md）**：在 `prompts/reverse_judge.md` 的「审查方法」之后新增「AMSC 通用协议特征 covered 判定说明（空气管理系统控制器专用）」章节，给出 AMSC 项目背景下判定「协议级 covered」的 3 个同时满足条件（HLR 描述协议级实现 / 不引用 AMSC 具体信号名 / 符合协议标准要求）+ 3 个判定示例（SDI 位 → covered、奇偶校验 → covered、fan RPM 解算 → needs_review）。作用域暂设为全局（fgmc / hscu 等其他 profile 同样适用，但 AMSC 关键词不命中时无实际影响）。

### Fixed

- **minimax re-review JSON 解析失败**：`semantic_judge.py::_extract_json` 在 minimax 返回内容以 ```json fence 开头、但前面带 markdown 分析段时，无法从 fence 内提取 JSON，导致 `JSONDecodeError`，所有 minimax re-review 调用落入 `coverage_status="error"`。修复：增加 else 分支——text 不以 ``` 开头时，先在 text 内用正则 `r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```'` 搜索 ```json fence 并提取其中的 `{...}`；找不到再退到找首个 `{`。think 块剥离、markdown fence 移除、JSON 截断修复逻辑均不变。

## [Unreleased] - 2026-08-27

### Changed

- **5 星评价体系重构（ADR-004 v3 fusion：两维度并行）**：修正 v2 把「EoICD-HLR 事实差异」与「provider 间字段级分歧」混为一谈的语义错误。v2 上线后真实样例暴露：Word 报告「判断」列显示「不一致」而「不一致属性」列大面积显示「—」，因为 3 个 provider 对同一 EoICD-HLR 差异往往共识识别，按 v2 规则不进 `field_disagreements`，渲染源为空。v3 fusion 恢复 `inconsistent_attributes`（语义回到 v0/v1：HLR 与 ICD 实际不符的 EoICD 属性，**不管 provider 是否一致都填**），结构为 `{attribute, detail, providers}`，作为 Word 报告「不一致属性」列的唯一数据源；`field_disagreements`（provider 间分歧，v2 设计）保留但降为辅助字段，**仅入 JSON、不再渲染**。`agreement_level` 明确按 v0 语义规则判定（看 analysis 语义，不只看字面 coverage_status）。5 档星档映射规则不变（`_map_star_rating(agreement, field_disagreements)`），复查触发条件 `{1, 2}` 不变。详见 ADR-004。

### Breaking Change

- **`ConsensusResult.inconsistent_attributes`**：**恢复**为 `list[InconsistentAttribute]`（`{attribute, detail, providers}`），语义 = EoICD-HLR 事实差异。
- **`ConsensusResult.field_disagreements`**：字段保留、结构不变，但不再作为 Word 报告渲染源。
- **`ConsensusResult.evidence_alignment`**：保持删除（v2 起）。
- **旧 baseline 兼容**：`backend/tests/e2e/common.py:_migrate_consensus_schema` 在拷贝 baseline 时自动补齐缺失的 `inconsistent_attributes` / `field_disagreements`（并把 v0/v1 的字符串形态转为 dict），无需重新生成 baseline。
- **`consensus.md` 移除 `single_source` / `no_consensus` 描述**：prompt 中不再出现这两个值，LLM 只能输出 `full` / `majority` / `split`。这两个值由后端降级脚本根据 provider 存活数自动写入（surviving=1 → `single_source`、surviving=0 → `no_consensus`）。修复 surviving=2 时 LLM 误判 `no_consensus` 穿透到最终结果的盲区。数据契约无变化，后端解析照旧。
- **`ConsensusResult.cited_fields: list[str]` 完全移除**：v2 引入的字段（列出所有被 provider analysis 引用的字段名）始终未被消费（不进入 Word 渲染、不被任何业务逻辑使用），仅作为调试占位字段。删除后 prompt 无需让 LLM 输出该数组，节省 token；`models.py` / `review_agent.py` / `consensus_word_generator.py` / e2e 注入逻辑同步清理。已存 baseline JSON 中的 `cited_fields` 字段会被 Pydantic 静默忽略（`extra="ignore"` 默认行为），不需迁移。
- **`final_coverage_status` 阈值扩展**：v2 设计 `2★ → 强制「待确认」` 会让 majority provider 已达成共识的判断被吞（少数意见仅关 key 字段却让整条 case 看起来"毫无信息"），改为 2★ 取 majority coverage_status。复盘链路：`review_agent.py:134` 阈值 `star >= 3` 放宽到 `star >= 2`；2★ case 从 Word 报告「待确认」组迁到 majority 的实际组（covered/inconsistent/needs_review），但 `field_disagreements` 仍记录少数 key 字段反对意见。复查触发条件 `{1, 2}` 不变（仍由 `star_rating` 驱动）。e2e 用例5断言同步调整。
- **5 星档位命名正式化（方案 Y：共识轴 + 异议子档）**：v3 fusion 上线后旧命名"完全无争议 / 一致有争议 / 多数一致 / 多数有争议"存在两处歧义——(1) 「一致有争议」自相矛盾，「争议」易与 HLR-ICD 实际差异混淆；(2) 1★ 子档「分歧 / 仅单一来源 / 无有效裁判」口语化。重构后命名锚定到「共识」轴，4 主档：「完全共识（5★）/ 完全共识·字段异议（4★）/ 多数共识（3★）/ 多数共识·关键异议（2★）」；1★ 三降级子类型独立正式化：「三方分歧（split）/ 仅单源（single_source）/ 全部失效（no_consensus）」；fallback 标签仍兜底为「降级」。三档颜色映射不变（5★ 绿 / 4★·3★ 黄 / 2★·1★ 红）。受影响文案：`consensus_word_generator.py` 的 `_map_consensus_label` / `star_levels` / `one_star_subs` / `suggestions` 4 处（共 7 个标签字面量）；`prompts/consensus.md` 的 4 行档位描述；`re_review.py` 2 处 docstring；`docs/project/workflow.md` 的 final_coverage_status 阈值描述同步对齐。判断逻辑（`_map_star_rating` / 阈值规则 / 复查触发）完全不变，仅 UI 文字调整。

## [Unreleased] - 2026-08-26

### Added

- **V4 正向完整性分析（EoICD → HLR 漏写检测）**：新增正向分析管线（解析 → 追溯范围 → 业务对象块 → 确定性 HLR 身份索引 → 候选召回 → 确定性覆盖判定 → AI 三态复核 → JSON/Excel/Word 报告），回答「EoICD 业务对象在 HLR 正文中是否漏写」，与既有反向分析（正确性比对）互补。正向分析复用确定性 HLR 身份索引（不依赖 AI 标注），AI 复核采用单模型（`FORWARD_REVIEW_PROVIDER`，默认 DeepSeek），无三模型裁判/共识。
- **正向 API 与下载分发**：新增 `POST /api/v4/completeness-analysis`（`analysis_mode` ∈ `full`/`trace`）、`GET /api/v4/jobs/{job_id}/forward-result`、`GET /api/v4/jobs/{job_id}/outputs/forward-xlsx`、`GET /api/v4/jobs/{job_id}/outputs/forward-docx`；`Job` 新增 `task_type` 字段（`reverse`/`forward`）区分两类任务。
- **EoICD 解析字段**：`EoICDRequirement` 新增 `layer_path_types`（层级路径类型，加性字段），供正向协议分类（A429/A825/模拟量/离散量/A664）使用，不影响反向解析计数。

## [Unreleased] - 2026-08-27

### Changed

- **正向判定统一规则（业务信号/字段颗粒度）**：正向覆盖判定固定为「EoICD 业务信号/字段是否在 HLR 中被描述」，不扩展到通道/设备副本/冗余来源/接口实例的逐一覆盖检查。A429 子对象身份仅在证据可靠时参与判定：SDI 仅当该 Label 存在 >1 个不同非 N/A SDI 值（`sdi_is_discriminator`）且双方显式带 SDI 时用作区分依据；bit 通过结构化关系（叶子自身 BitOffsetWithinDS，或 dp_ref 子字段名与叶名对应）推导，不依据 DataFormatType/ParameterSize 做「排除 BOOL / 优先 BNR」等类型推断，推导不可靠则不留 bit 证据。缺失候选统一收口：在场候选均被确定性规则证明为其他对象时，若存在追溯缺失候选（未出现在上传 HLR）判 `possible` 并记录缺失，无缺失候选才判 `uncovered`。确定性身份冲突（名称/Label/SDI/bit 充分时）不可被 AI 覆盖，语义无法确定性确认时仍进入 AI 复核。正向结果新增 `reason` 字段（缺失候选 / 通道条件审计信息）。反向管线和反向基线保持不变。

## [Unreleased] - 2026-08-26

### Fixed

- **RPDU per-HLR 追溯预过滤池（Issue #74 修复）**：新增 `ControllerProfile.prefilter_per_hlr: bool`（默认 `False`）和 `pipeline.match_reverse_per_hlr()`。RPDU profile 显式声明 `prefilter_per_hlr: true`，每个 traceable HLR 只在自己的 traced EoICD block 集合上跑 reverse match，避免其他 HLR 引入的 LRM / 状态类信号淹没 `Heater_Group_*_RPDU_ESW_CMD` 这类目标信号。修复前 HLR_052331 top-50 全是 LRM 状态信号，修复后 14 个 `Heater_Group_*` ESW_CMD 候选。AMS/FGMC/HSCU profile 不声明该字段自动回落到 `False`，仍走原有 union-pool 路径，行为字节一致。

- **FGMC 追溯表 HLR ID 字段映射冲突**：`profiles/fgmc/config.yaml` 的 `hlr_parser.field_map` 中 `id` 和 `code` 两个 std_field 同时声明了 header 文本 `需求编号`，被 `_build_field_map_index` 的反向 dict 索引机制覆盖（`code` 后注册赢），导致 docx row 1 的正式 HLR 编号（`FGMC_OFP_CSCI_HLR_005906`）落到 `code` 字段、docx row 0 的内部编号（`1781`）落到 `id` 字段。修复：`id` 首位加入 `需求编号`，`code` 列表移除 `需求编号`（保留 `RequirementCode` 作为 legacy alias）。

- **FGMC Table 1 sheet 选择冲突**：`需求与ICD追溯表_FGMC_裁剪.xlsx` 含 9 张 sheet，其中 `接口基线表_EoICD_old_待删除`（旧表，标记删除）和 `待填_需求接口追溯表`（当前使用）并存。原 `by_name_keywords` 列表把模糊关键词 `接口基线` 放在 `待填_需求接口追溯表` 之前，导致 `_select_sheet` 选到了已废弃 sheet，Table 1 只产出 2 个 ERD。修复：把 `待填_需求接口追溯表` 移至关键词列表首位，并删除过宽的 `接口基线` 模糊关键词。

## [Unreleased] - 2026-08-26

### Changed

- **V2 共识报告文案统一（零行为影响）**：`consensus_word_generator.py` 3 处文字层面对齐方案 B 命名风格——"星级分布"小节 4 档标签改为"完全无争议 / 一致有争议 / 多数一致 / 多数有争议"；"处置建议"列表清理 v1 `evidence_alignment` 残留（"evidence 强 / 一般 / 反对方 evidence 弱"），改用星级分布小节口径；明细表"共识"列 `full → 完全一致` 改为 `完全无争议`，与"星级分布"小节表头对齐。判定规则与数据契约均未变，仅 UI 文字调整。`re_review.py:247` docstring 同步从 v1 "多数一致但 evidence 弱"改为 v2 "多数一致但有 key 字段分歧"，保持与 ADR-004 v2 口径一致。

## [Unreleased] - 2026-08-24

### Changed

- **5 星评价体系重构（ADR-004 v2：字段不一致驱动）**：V4 反向管线 Step 5 Review Agent 由 v1 的 `evidence_alignment` 多维度方案重构为「字段类型驱动的单维度映射」。review LLM 改为扫描 3 provider 的 analysis 文本输出结构化字段级**裁判间分歧**列表 `field_disagreements: list[{field, category, providers, values, detail}]`（注意：仅追踪 provider 之间的判断分歧，EoICD-HLR 事实性差异由各 provider 自己的 analysis 承载），后端 `_map_star_rating(agreement, field_disagreements)` 按 agreement_level 分档 + 是否有 key 字段分歧映射到 5 档星评。映射规则：full+无字段不一致→5★、full+任意字段不一致→4★、majority+无 key 字段分歧→3★、majority+有 key 字段分歧→2★（触发复查）、split/single_source/no_consensus→1★。key 字段白名单（12 个）：Direction / DataFormatType / BitOffset / ParameterSize / OneState / ZeroState / Label / FuncRngMin / FuncRngMax / Units / Period / SDIExpected。vague 表达（无具体字段名）不影响降级。Prompt 增加 Step 0 明确区分「EoICD-HLR 事实性不一致」与「裁判间意见分歧」两类语义。e2e 用例5/6 同步重构注入策略。详见 ADR-004。

### Breaking Change

- **`ConsensusResult.evidence_alignment`**：**完全移除**（无软删除），保留 1 个版本。
- **`ConsensusResult.inconsistent_attributes`**：删除（合并到 `field_disagreements`，后者带 `category` 分类）。
- **`ConsensusResult.field_disagreements: list[FieldDisagreement]`**：新增字段（Pydantic Literal 类型 `category ∈ {key, non_key, vague}`）。
- **`ConsensusResult.cited_fields: list[str]`**：新增字段，列出所有被 provider 引用的字段名（vague 案例为空数组）。
- **e2e_baseline 需重新生成**：旧 v1 baseline（含 `evidence_alignment`）无法被 pydantic 解析；直接跑基线管线产出 v2 JSON 即可，无迁移脚本。

## [Unreleased] - 2026-08-23

### Added

- **5 星评价体系（ADR-004 v1，已被 v2 替代）**：V4 反向管线 Step 5 Review Agent 升级为 5 档星评（5★/4★/3★/2★/1★），新增 `evidence_alignment` 字段（strong/moderate/weak）由 review LLM 自评 evidence 强度，后端按 `(agreement_level, evidence_alignment)` 二维映射到 5 档星评，避免 LLM 直接选星的批次漂移。映射规则：full+strong→5★、full+moderate/weak→4★、majority+strong/moderate→3★、majority+weak→2★、split/single_source/no_consensus→1★。新增 e2e 用例5（`backend/tests/e2e/test_use_case_5_five_star_rating.py`）。**该版本在真实 LLM 跑 `故障注入1.0.docx` 暴露根本性问题（5 档分布失衡：仅 5★/3★ 触发），2026-08-24 由 v2 字段不一致方案替代**。

### Changed

- **Step 5.5 一星复查触发条件扩展**：`_resolve_low_confidence_case_ids` 触发条件从 `star_rating == 1` 扩展到 `star_rating ∈ {1, 2}`，peer-aware 复查给 2★（多数一致但 evidence 弱）一个升到 3★ 的机会。
- **共识报告星级分布表**：从「3 主行 + 3 子行」扩展为「4 主行（5★/4★/3★/2★）+ 3 子行（1★ 三降级子类型）」，`_star_str` 渲染 0-5 共 6 档（含无匹配 0 颗）。
- **`final_coverage_status` 阈值**：5★/4★/3★（star ≥ 3）取多数一致的 coverage_status；2★/1★ 强制「待确认」，防止 majority+weak 的低 evidence 共识被当成业务结论采纳。v2 阈值未变。

### Breaking Change

- **`ConsensusResult.star_rating`**：类型注解从 `1-3` 扩展到 `1-5`（数据契约变化）。
- **`star_distribution` summary 字段**：键从 `{1, 2, 3}` 扩展为 `{1, 2, 3, 4, 5}`，前端读取要兼容。
- **`ConsensusResult.evidence_alignment`**：新增字段（默认 ""），老 mock 数据缺失可视为 ""；review LLM 不再输出 `star_rating`，仅输出 `agreement_level` + `evidence_alignment`，由后端按映射规则算星。**该字段在 v2 中已完全移除**。
- 老 `consensus_results.json` 不存在跨版本兼容，老数据需重新跑管线。v2 提供迁移脚本回填关键字段。

## [Unreleased] - 2026-08-28

### Changed

- **Step 5.5 re-review per-case 内并行**：re-review 阶段两层串行循环（先 case、后 provider）改为 per-case gather：每个 case 内部的 3 个 provider 调用一次性 submit 到 Step 4 共享的 `_get_drain_executor()` 线程池（通过 `_submit_with_gate()` 走信号量闸门），用 `concurrent.futures.wait` + `FIRST_COMPLETED` 在固定 `case_total_timeout` ceiling 内收集结果，单 case wall time 从 `sum(providers)` 降到 `max(providers)`。复用 Step 4 已有的 `_get_drain_executor` / `_get_inflight_sema` / `_submit_with_gate` / `make_error_judgment` / `classify_exception` 基础设施，**对外契约零变化**（`re_review_results.json` schema、`re_review_judgments()` 入参返回、`multi_judge_results.json` 落盘时机不变）；仅内部执行模型从串行改为并行。

### Fixed

- **minimax re-review JSON 解析失败**：`semantic_judge.py::_extract_json` 在 minimax 返回内容以 ```json fence 开头、但前面带 markdown 分析段时，无法从 fence 内提取 JSON，导致 `JSONDecodeError`，所有 minimax re-review 调用落入 `coverage_status="error"`。修复：增加 else 分支——text 不以 ``` 开头时，先在 text 内用正则 `r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```'` 搜索 ```json fence 并提取其中的 `{...}`；找不到再退到找首个 `{`。think 块剥离、markdown fence 移除、JSON 截断修复逻辑均不变。

## [Unreleased] - 2026-08-27

### Changed

- **5 星评价体系重构（ADR-004 v3 fusion：两维度并行）**：修正 v2 把「EoICD-HLR 事实差异」与「provider 间字段级分歧」混为一谈的语义错误。v2 上线后真实样例暴露：Word 报告「判断」列显示「不一致」而「不一致属性」列大面积显示「—」，因为 3 个 provider 对同一 EoICD-HLR 差异往往共识识别，按 v2 规则不进 `field_disagreements`，渲染源为空。v3 fusion 恢复 `inconsistent_attributes`（语义回到 v0/v1：HLR 与 ICD 实际不符的 EoICD 属性，**不管 provider 是否一致都填**），结构为 `{attribute, detail, providers}`，作为 Word 报告「不一致属性」列的唯一数据源；`field_disagreements`（provider 间分歧，v2 设计）保留但降为辅助字段，**仅入 JSON、不再渲染**。`agreement_level` 明确按 v0 语义规则判定（看 analysis 语义，不只看字面 coverage_status）。5 档星档映射规则不变（`_map_star_rating(agreement, field_disagreements)`），复查触发条件 `{1, 2}` 不变。详见 ADR-004。

### Breaking Change

- **`ConsensusResult.inconsistent_attributes`**：**恢复**为 `list[InconsistentAttribute]`（`{attribute, detail, providers}`），语义 = EoICD-HLR 事实差异。
- **`ConsensusResult.field_disagreements`**：字段保留、结构不变，但不再作为 Word 报告渲染源。
- **`ConsensusResult.evidence_alignment`**：保持删除（v2 起）。
- **旧 baseline 兼容**：`backend/tests/e2e/common.py:_migrate_consensus_schema` 在拷贝 baseline 时自动补齐缺失的 `inconsistent_attributes` / `field_disagreements`（并把 v0/v1 的字符串形态转为 dict），无需重新生成 baseline。
- **`consensus.md` 移除 `single_source` / `no_consensus` 描述**：prompt 中不再出现这两个值，LLM 只能输出 `full` / `majority` / `split`。这两个值由后端降级脚本根据 provider 存活数自动写入（surviving=1 → `single_source`、surviving=0 → `no_consensus`）。修复 surviving=2 时 LLM 误判 `no_consensus` 穿透到最终结果的盲区。数据契约无变化，后端解析照旧。
- **`ConsensusResult.cited_fields: list[str]` 完全移除**：v2 引入的字段（列出所有被 provider analysis 引用的字段名）始终未被消费（不进入 Word 渲染、不被任何业务逻辑使用），仅作为调试占位字段。删除后 prompt 无需让 LLM 输出该数组，节省 token；`models.py` / `review_agent.py` / `consensus_word_generator.py` / e2e 注入逻辑同步清理。已存 baseline JSON 中的 `cited_fields` 字段会被 Pydantic 静默忽略（`extra="ignore"` 默认行为），不需迁移。
- **`final_coverage_status` 阈值扩展**：v2 设计 `2★ → 强制「待确认」` 会让 majority provider 已达成共识的判断被吞（少数意见仅关 key 字段却让整条 case 看起来"毫无信息"），改为 2★ 取 majority coverage_status。复盘链路：`review_agent.py:134` 阈值 `star >= 3` 放宽到 `star >= 2`；2★ case 从 Word 报告「待确认」组迁到 majority 的实际组（covered/inconsistent/needs_review），但 `field_disagreements` 仍记录少数 key 字段反对意见。复查触发条件 `{1, 2}` 不变（仍由 `star_rating` 驱动）。e2e 用例5断言同步调整。
- **5 星档位命名正式化（方案 Y：共识轴 + 异议子档）**：v3 fusion 上线后旧命名"完全无争议 / 一致有争议 / 多数一致 / 多数有争议"存在两处歧义——(1) 「一致有争议」自相矛盾，「争议」易与 HLR-ICD 实际差异混淆；(2) 1★ 子档「分歧 / 仅单一来源 / 无有效裁判」口语化。重构后命名锚定到「共识」轴，4 主档：「完全共识（5★）/ 完全共识·字段异议（4★）/ 多数共识（3★）/ 多数共识·关键异议（2★）」；1★ 三降级子类型独立正式化：「三方分歧（split）/ 仅单源（single_source）/ 全部失效（no_consensus）」；fallback 标签仍兜底为「降级」。三档颜色映射不变（5★ 绿 / 4★·3★ 黄 / 2★·1★ 红）。受影响文案：`consensus_word_generator.py` 的 `_map_consensus_label` / `star_levels` / `one_star_subs` / `suggestions` 4 处（共 7 个标签字面量）；`prompts/consensus.md` 的 4 行档位描述；`re_review.py` 2 处 docstring；`docs/project/workflow.md` 的 final_coverage_status 阈值描述同步对齐。判断逻辑（`_map_star_rating` / 阈值规则 / 复查触发）完全不变，仅 UI 文字调整。

## [Unreleased] - 2026-08-26

### Changed

- **V2 共识报告文案统一（零行为影响）**：`consensus_word_generator.py` 3 处文字层面对齐方案 B 命名风格——"星级分布"小节 4 档标签改为"完全无争议 / 一致有争议 / 多数一致 / 多数有争议"；"处置建议"列表清理 v1 `evidence_alignment` 残留（"evidence 强 / 一般 / 反对方 evidence 弱"），改用星级分布小节口径；明细表"共识"列 `full → 完全一致` 改为 `完全无争议`，与"星级分布"小节表头对齐。判定规则与数据契约均未变，仅 UI 文字调整。`re_review.py:247` docstring 同步从 v1 "多数一致但 evidence 弱"改为 v2 "多数一致但有 key 字段分歧"，保持与 ADR-004 v2 口径一致。

## [Unreleased] - 2026-08-24

### Changed

- **5 星评价体系重构（ADR-004 v2：字段不一致驱动）**：V4 反向管线 Step 5 Review Agent 由 v1 的 `evidence_alignment` 多维度方案重构为「字段类型驱动的单维度映射」。review LLM 改为扫描 3 provider 的 analysis 文本输出结构化字段级**裁判间分歧**列表 `field_disagreements: list[{field, category, providers, values, detail}]`（注意：仅追踪 provider 之间的判断分歧，EoICD-HLR 事实性差异由各 provider 自己的 analysis 承载），后端 `_map_star_rating(agreement, field_disagreements)` 按 agreement_level 分档 + 是否有 key 字段分歧映射到 5 档星评。映射规则：full+无字段不一致→5★、full+任意字段不一致→4★、majority+无 key 字段分歧→3★、majority+有 key 字段分歧→2★（触发复查）、split/single_source/no_consensus→1★。key 字段白名单（12 个）：Direction / DataFormatType / BitOffset / ParameterSize / OneState / ZeroState / Label / FuncRngMin / FuncRngMax / Units / Period / SDIExpected。vague 表达（无具体字段名）不影响降级。Prompt 增加 Step 0 明确区分「EoICD-HLR 事实性不一致」与「裁判间意见分歧」两类语义。e2e 用例5/6 同步重构注入策略。详见 ADR-004。

### Breaking Change

- **`ConsensusResult.evidence_alignment`**：**完全移除**（无软删除），保留 1 个版本。
- **`ConsensusResult.inconsistent_attributes`**：删除（合并到 `field_disagreements`，后者带 `category` 分类）。
- **`ConsensusResult.field_disagreements: list[FieldDisagreement]`**：新增字段（Pydantic Literal 类型 `category ∈ {key, non_key, vague}`）。
- **`ConsensusResult.cited_fields: list[str]`**：新增字段，列出所有被 provider 引用的字段名（vague 案例为空数组）。
- **e2e_baseline 需重新生成**：旧 v1 baseline（含 `evidence_alignment`）无法被 pydantic 解析；直接跑基线管线产出 v2 JSON 即可，无迁移脚本。

## [Unreleased] - 2026-08-23

### Added

- **5 星评价体系（ADR-004 v1，已被 v2 替代）**：V4 反向管线 Step 5 Review Agent 升级为 5 档星评（5★/4★/3★/2★/1★），新增 `evidence_alignment` 字段（strong/moderate/weak）由 review LLM 自评 evidence 强度，后端按 `(agreement_level, evidence_alignment)` 二维映射到 5 档星评，避免 LLM 直接选星的批次漂移。映射规则：full+strong→5★、full+moderate/weak→4★、majority+strong/moderate→3★、majority+weak→2★、split/single_source/no_consensus→1★。新增 e2e 用例5（`backend/tests/e2e/test_use_case_5_five_star_rating.py`）。**该版本在真实 LLM 跑 `故障注入1.0.docx` 暴露根本性问题（5 档分布失衡：仅 5★/3★ 触发），2026-08-24 由 v2 字段不一致方案替代**。

### Changed

- **Step 5.5 一星复查触发条件扩展**：`_resolve_low_confidence_case_ids` 触发条件从 `star_rating == 1` 扩展到 `star_rating ∈ {1, 2}`，peer-aware 复查给 2★（多数一致但 evidence 弱）一个升到 3★ 的机会。
- **共识报告星级分布表**：从「3 主行 + 3 子行」扩展为「4 主行（5★/4★/3★/2★）+ 3 子行（1★ 三降级子类型）」，`_star_str` 渲染 0-5 共 6 档（含无匹配 0 颗）。
- **`final_coverage_status` 阈值**：5★/4★/3★（star ≥ 3）取多数一致的 coverage_status；2★/1★ 强制「待确认」，防止 majority+weak 的低 evidence 共识被当成业务结论采纳。v2 阈值未变。

### Breaking Change

- **`ConsensusResult.star_rating`**：类型注解从 `1-3` 扩展到 `1-5`（数据契约变化）。
- **`star_distribution` summary 字段**：键从 `{1, 2, 3}` 扩展为 `{1, 2, 3, 4, 5}`，前端读取要兼容。
- **`ConsensusResult.evidence_alignment`**：新增字段（默认 ""），老 mock 数据缺失可视为 ""；review LLM 不再输出 `star_rating`，仅输出 `agreement_level` + `evidence_alignment`，由后端按映射规则算星。**该字段在 v2 中已完全移除**。
- 老 `consensus_results.json` 不存在跨版本兼容，老数据需重新跑管线。v2 提供迁移脚本回填关键字段。

## [Unreleased] - 2026-08-20

### Removed

- 移除 V3 旧版代码与依赖：后端顶层 `crew/` / `merge/` / `scoring/` / `docx/` / `parsers/` / `llm/` / `prompts/` / `skills/` / `pipeline.py` / `models.py` 及 `api/v3/` 路由全部删除；`requirements.txt` 移除 `crewai` / `litellm`。详见 ADR-002。
- 移除 `Job.kind` 字段与 V3/V4 跨版本分派逻辑（ADR-002 D3）；`JobStatus` 枚举迁入 `job_manager.py`。
- 移除 V4 冗余代码：早期正向原型（`run_forward_pipeline`、`comparison/case_builder.py`、`matching/{candidate_matcher,text_matcher,unified_matcher}.py`、`prompts/forward_judge.md` 及配套正向模型/config 常量与 CLI 命令 `match`/`judge`/`report`/`analyze`）与旧单模型反向 CLI（`reverse-judge`/`reverse-report` 及 `judge_reverse_cases`/`generate_reverse_report`）。详见 ADR-003。

### Changed

- FastAPI 入口仅保留 `/api/v4` 命名空间；旧 `/api/health`、`/api/eoicd/analyze`、`/api/jobs/*` 全部移除（现返 404）。
- 前端移除 V3 上传 / 状态 / 结果组件与 `api/index.ts`、`types.ts` 中的 V3 符号；仅保留 V4 界面。
- 文档同步：`README.md`、`docs/architecture/*`、`docs/project/*`、`backend/.env.example` 移除 V3 表述；新增 ADR-002；ADR-001 标记 Partially Superseded。
- 新增 ADR-003（移除 V4 早期正向原型与旧单模型反向 CLI）；ADR-002 D4 标记由 ADR-003 取代；`docs/architecture/current-architecture.md` 同步更新 prompts 资产清单与 ADR 引用。

## [Unreleased] - 2026-08-21

### Added

- **drain 任务数上限**：新增 `DEGRADATION_DRAIN_MAX_TASKS`（默认 60）配置，超过上限的超时任务被 cancel（未执行的取消，已执行的结果丢弃），防止极端场景下 drain 任务无限堆积。
- **任务提交限流**：新增 `DEGRADATION_MAX_INFLIGHT`（默认 6）配置，信号量控制同时提交到线程池的任务数，超限任务在 submit 前阻塞等待，从源头限制并发。新增 e2e 用例3b（drain_max_tasks 上限验证）。

## [Unreleased] - 2026-08-19

### Added

- **case 级超时后台收尾（drain）**：Step 4 多智能体裁判改为线程池执行（concurrent.futures），超时的裁判任务不再取消丢弃，而是转入后台线程池继续执行；Step 4.5 在总预算（`DEGRADATION_DRAIN_BUDGET`，默认 300s）内统一收尾，迟到的有效结果替换 TIMEOUT 占位后进入共识，慢但有效的输出不再被舍掉。新增 `degradation.drained_late_count` 统计与 e2e 用例3（慢 provider 收尾验证）。

## [Unreleased] - 2026-08-25

### Added

- **RPDU 多控制器适配合并（Issue #74）**：新增 `rpdu` controller profile，支持远程功率分配单元的 Excel 格式 HLR 输入、header 自适应追溯解析、4 项反向匹配增强（中文后缀剥离、方向软约束带 conflict 标记、信号编号加分、`top_k=50`）。
- **profile 扩展维度**：V4 profile schema 新增三个 profile 维度的扩展点（HLR 解析驱动 `hlr_parser_driver.driver` / 追溯策略 `trace_strategy` / 匹配增强 `matcher`），所有新增字段全部默认关闭 → AMS/FGMC/HSCU 行为字节不变。
- **HLR 解析工厂**：新增 `create_hlr_parser(source_path, profile=)`，按扩展名分发到 `HLRWordParser`（.docx，默认）或 `HLRExcelParser`（.xlsx，RPDU）。
- **API HLR 扩展名校验**：`POST /api/v4/coverage-analysis` 的 HLR 文件扩展名校验改为基于 `parsers.registered_extensions()` 工厂白名单，支持 .docx 和 .xlsx；新增解析器只需在工厂注册，API 自动同步。

### Changed

- `backend/app/api/v4/coverage.py` 白名单加入 `"rpdu"`；错误消息改为动态列出支持列表。
- `backend/app/v4/pipeline.py`：`_parse_hlr` 改用 `create_hlr_parser` 工厂；Step 3 两条路径全部透传 `profile=` 给 `build_trace_index` 和 `match_reverse`。

## [Unreleased] - 2026-08-24

### Changed

- **HSCU HLR 预处理 hook 适配新文档结构**：`profiles/hscu/hooks.py` 表格解析从固定位置改为自动识别。`_identify_label_tables()` 按启发式（≥ 3 列 + ≥ 2 行 + 至少一行同时含 LBL cell 和 ≥ 2 位数字 octal cell）扫描所有 table，识别 HSCU 新文档中同时存在的 Table[0]（RDCU1 入站 11 行 × 8 列，含 `_R1` 后缀）和 Table[8]（HSCU 出站 12 行 × 4 列，无 `_R1`）两张 LBL 总览表，全部合并入 mapping。`_extract_row_mapping()` 改为行内扫描，定位 LBL cell 和 octal cell 时不再假设固定列偏移。
- **Hook 扩展支持 RDCU1 catalog col 5 多行信号名称**：8 列 RDCU1 catalog 的 col 5（信号名称列）每个 cell 多行，每行是一个独立 signal name 由同 octal 承载（如 `LBL_ABV1_RPDU_R1 → 51` 承载 `ABV1_CB_CLOSED_RPDU_R1`、`ABV1_LOAD_VOLT_AVAIL_RPDU_R1` 等 12 个信号）。新增 `_extract_signal_names()` 把这些裸 signal 名作为额外 mapping key，让 HSCU HLR 中以裸名形式引用 RDCU1 signal 的需求（023194、022645）也能命中 EoICD 块。
- **Hook 输出 octal 左填充 3 位**：ARINC-429 八进制是 3 位（000-377），EoICD block key 始终为 3 位形式。HSCU catalog 常省略前导 0（如 `74`、`51`），hook 生成 alias 时统一 `zfill(3)` 避免 Stage1 prefix filter (`L<label>/` vs `L<3位>`) 失配。同时 `_looks_like_octal_cell()` 把长度下限从 3 位降到 2 位以接受省略前导 0 的 octal，但仍拒绝单数字以避免与 SDI（`0/1/2/3`）混淆。
- **`pipeline._parse_hlr()` 临时暴露完整 source_file 给 hook**：`HLRWordParser.parse()` 把 `result.source_file` 存为 basename，hook 用 `Path(source_file).exists()` 在 backend cwd 下找不到文件导致 auto_parse 静默失败。`_parse_hlr()` 在调用 hook 前临时把 `result.source_file` 切到完整 `input_path`，hook 调用结束后恢复 basename — JSON 输出和 AMS/FGMC 行为一致仍保留 basename。

### Verified

- HSCU E2E（job `a54aab93`，真实 LLM）：`hlr_已匹配=4, hlr_待确定=2, hlr_无匹配=4`，6/10 HLR 拿到 EoICD block key。新文档中 023194（ABV1_LOAD_VOLT_AVAIL_RPDU_R1）从「无匹配」升级到「待确定」。
- AMS（job `082b4a48`）+ FGMC（job `ed36e75c`）回归：`hlr_requirements.json` 中 0 个 alias annotation（auto_parse 默认 False 不被触发），匹配数不变。

## [Unreleased] - 2026-08-21

### Added

- **Profile HLR 预处理 Hook 机制**：`ControllerProfile` 新增 `HLRPreprocessConfig` 配置段（`enabled` + `extra_mappings` + `auto_parse_hlr_table_0` + `apply_to_fields`）。`profiles/__init__.py` 新增 `apply_hlr_preprocess_hook()` 通用调用入口，通过 `importlib` 动态加载各 profile 的 `preprocess_hlr_requirements()` 函数。`pipeline._parse_hlr()` 在 HLR Word 解析后、写 JSON 前调用 hook，让改写后的内容对下游 AI 标注、分类、匹配可见。
- **HSCU LBL→L<octal> 别名追加 hook（`profiles/hscu/hooks.py`）**：HSCU HLR 文本使用符号化标签名（`LBL_DIS_00_SYS1`），而 EoICD PubSub 块以八进制编码（`L145_DIS_00_SYS1_T1A`）。Hook 通过 YAML `extra_mappings` 配置映射，按 per-token 范围追加 `（亦称：L<octal>_<NAME>）` 别名，让 AI 标注器在两种形式间识别，从而让反向匹配 Stage1 的 prefix filter 能命中 EoICD 块。规则：仅对实际出现且已映射的 LBL token 追加、自动剥离 `_SSM` 后缀、占位值跳过、幂等。
- **HSCU 当前映射 1 个（`LBL_DIS_00_SYS1` → `145`）**：job `39f938f5`（真实 LLM）HSCU E2E：matched_count `0 → 1`，unmatched `10 → 9`。补全 4 个映射后预期 ~6/10 matched。

### Changed

- 不修改 `reverse_matcher.py` / `hlr_classifier.py` / `hlr_labeler.py` / `trace_parser.py`。
- AMS / FGMC profile 不受影响：`hlr_preprocess.enabled` 默认为 `False`。

## [Unreleased] - 2026-08-12

### Changed

- **LLM Client 截断自适应重试下沉**：`finish_reason=length` 截断重试从业务层 (`_chat_with_truncation_retry`) 下沉到三个 LLM client（DeepSeek / MiniMax / Qwen）的 `chat()` 方法内部，截断时自动翻倍 `max_tokens` 重试（4096→8192→16384，上限 16384），覆盖所有 LLM 调用方（judge / review / labeler）。`ChatResponse.truncated` 字段随之移除。

## Unreleased

### Added

- 初始化工程目录和工程文档体系。
- 建立最小可运行前后端工程（React + TypeScript 前端，FastAPI 后端）。
- Docker Compose 本地启动方式。
- 前端文件上传页面和任务状态查询。
- 后端接收 EoICD Word、多个 Excel 附件和软件高层需求文件。
- 后端 job_id 创建与任务状态管理（内存）。
- 两个占位下载接口。
- pipeline.py 端到端流程骨架（mock 实现）。
- 预留 parsers、crew、prompts、skills、scoring、docx 等模块边界。

## [Unreleased] - 2026-06-12

### Added

- 端到端原型数据流：parsers/ → crew/生成候选 → crew/打分 → scoring/融合评分 → crew/差异比对 → docx/ 生成结构化文档。
- parsers/ 模块：结构化 EoICD 解析结果（UnifiedInputPackage），支持接口级条目。
- crew/ 模块三类智能体 stub：候选生成（固定两份）、候选打分（固定评分）、差异比对（固定5条差异项）。
- prompts/ 文本资产：generation_prompt.md、scoring_prompt.md、comparison_prompt.md。
- skills/ 文本资产：generation_skill.md、scoring_skill.md、comparison_skill.md。
- scoring/ 模块：融合 crew 评分（×0.6）和 Python 规则评分（×0.4），决策最佳候选。
- docx/ 模块：生成含结构化表格的 Word 文档（接口名称、信号名、数据类型等 ICD 场景字段）。
- pipeline.py 串联完整数据流，各 stub 模块数据在各阶段流转。

## [Unreleased] - 2026-06-16

### Added

- 引入 CrewAI 框架（crewai>=1.0），实现基于 chunk 的多智能体条目化生成、评分择优与对比流程。
- 新增 `backend/app/llm/` 模块：`factory.py`（env 驱动 + mock fallback）、`prompt_loader.py`（Python 端上下文拼接，不修改 prompts/skills 文本）、`mock_llm.py`（继承 `crewai.BaseLLM` 的结构化 mock LLM）。
- 新增 `backend/app/crew/{agents,tasks,crews}.py`：5 个 Agent 工厂、5 个 Task 工厂、3 个 Crew 工厂。
- 新增 `backend/app/merge/` 模块：跨 chunk 合并 + 按模型维度合并。
- `backend/app/models.py` 扩增 `EoICDChunk / ChunkCandidate / ChunkAgentScoreResult / ChunkPythonScoreResult / BestChunkResult / ModelRequirementResult / MergedRequirementResult / ComparisonReportResult / GenerationOutput / ScoringOutput / ComparisonOutput`。
- `parsers/` 升级为 `List[EoICDChunk]`（默认 1 个 chunk-001，但 pipeline 已按 `for chunk in eoicd_chunks` 编写）。
- `scoring/` 升级为 chunk 内 Python 硬规则评分（4 维 25×4=100）+ agent 评分 × 0.6 + python × 0.4 融合。
- `docx/` 输出 4 份 Word：MiniMax条目化需求 / DeepSeek条目化需求 / 最优条目化需求（额外落 EoICD条目化需求.docx） / EoICD与软件高层需求差异报告。
- 新增 2 个下载接口：`/api/jobs/{job_id}/outputs/minimax-requirements`、`/api/jobs/{job_id}/outputs/deepseek-requirements`。
- `requirements.txt` 新增 `crewai>=1.0`、`pydantic>=2.11,<3`。
- 新增 `backend/.env.example`（仅占位，不含真实 Key；`.env` 已被 `.gitignore` 忽略）。
- `docker-compose.yml` 新增 22 个模型相关环境变量占位 + `env_file` 引用 `.env`。
- 新增环境变量：`USE_MOCK_LLM`、`CREWAI_VERBOSE`、`MINIMAX_*`（11 项）、`DEEPSEEK_*`（11 项），全部走 env 读取，**不在代码中写死**任何 API Key、Base URL、Model Name 或运行参数。
- 前端 `api/index.ts` 扩展 `JobResultResponse.outputs` 字段；`getDownloadUrl` 支持 `minimax-requirements` / `deepseek-requirements`。
- 前端 `JobStatus.tsx` 增 2 个下载链接（MiniMax / DeepSeek），保留原 2 个链接，分组展示。

### Changed

- `crew/{candidate_generator,candidate_reviewer,difference_analyzer}.py` 改为调用真实 CrewAI Crew。
- `UnifiedInputPackage.eoicd` 替换为 `eoicd_chunks: List[EoICDChunk]`。
- `JobOutputs.outputs` 扩展 `minimax_docx` / `deepseek_docx` 字段。
- 旧 `/api/jobs/{job_id}/outputs/requirements` 接口语义重映射为"最优条目化需求"（物理文件 `EoICD条目化需求.docx` 保留）。
- `prompts/__init__.py` 和 `skills/__init__.py` 的加载器加 `lru_cache` 缓存。

### Fixed

- 由于 crewai 拉入的 starlette 1.3.1 与 fastapi 0.109.2 冲突，requirements 中显式指定 `starlette<0.37,>=0.36.3` 兼容范围（实际安装验证后记录为 0.36.3）。
- `docker-compose.yml` `env_file: ./backend/.env` 改为 `env_file: { path: ./backend/.env, required: false }`，让 `.env` 可选（详见 `debug-log.md` BUG-20260617-001）。
- `requirements.txt` `uvicorn[standard]==0.27.1` 改为 `uvicorn[standard]>=0.31.1,<0.37`，解决 crewai 间接依赖 mcp>=1.16 要求 uvicorn>=0.31.1 导致的 `ResolutionImpossible`（详见 `debug-log.md` BUG-20260617-002）。
- `docker-compose.yml` volume 路径从 `./backend/app/output:/app/output` 改为 `./backend/app/output:/app/app/output`，对齐 `main.py` 实际写入路径（详见 `debug-log.md` BUG-20260617-003）。

## [Unreleased] - 2026-06-22

### Added

- 引入真实 LLM 后端（Issue #16）：MiniMax M2.7 和 DeepSeek 通过统一的 `provider=openai` 路径接入 CrewAI，`USE_MOCK_LLM=0` 时调用真实 API。
- `backend/app/llm/factory.py` 新增两个 monkey-patch，解决 CrewAI 与 MiniMax/DeepSeek 的结构化输出兼容：
  - `_patch_crewai_completion_for_unsupported_models()`：MiniMax `<think>` 清洗 + `response_format=json_object` 替代不兼容的 `json_schema`。
  - `_patch_crewai_instructor_for_unsupported_models()`：TOOLS mode 下对 MiniMax 间歇性 tool_calls 缺失做 content → tool_call fallback。
- `_provider_creds` 字典 + `_litellm_with_fallback` 按模型名动态注入 API Key/Base URL，避免多模型共用 `OPENAI_API_KEY` 环境变量冲突。
- `get_minimax_llm()` / `get_deepseek_llm()` 新增 `overrides` 参数，Agent 工厂可按角色注入 timeout / max_tokens。
- 5 个 Agent 按职责设定 timeout/max_tokens：generation 300s/16384、scoring 120s/4096、comparison 180s/8192。

### Changed

- `DEEPSEEK_PROVIDER` 统一为 `openai`，与 MiniMax 走相同的 `LLM` → `InternalInstructor` → TOOLS mode 结构化输出路径。
- `docker-compose.yml` 移除 22 个模型相关环境变量内联声明，全部通过 `env_file: ./backend/.env` 注入，简化维护。
- `backend/Dockerfile` 新增 litellm 安装步骤。

### Fixed

- `generation_prompt.md` 修正："生成 2 份候选结果" → "生成 1 份"，wrapper `candidates` 数组 → 单个 `ChunkCandidate` 对象，与 Pydantic schema 对齐，避免 MiniMax 输出多 JSON 拼接导致解析失败。
- `_litellm_with_fallback` 和 `_handle_completion` 的 JSON 解析改用 `JSONDecoder.raw_decode()` 防御多 JSON 拼接场景。

## [Unreleased] - 2026-06-27

### Added

- **EoICD 真实文件输入支持**：新增 `eoicd_word_parser.py`（Word 文档解析）和 `eoicd_excel_parser.py`（PubSub Excel 表格解析），替代旧版 stub 解析器。
- **PubSub 嵌套数据预处理**：`parsers/__init__.py` 新增 `build_nested_sheets()`，将 PubSub Excel 的 Publisher/Subscriber 行数据转换为三层嵌套结构（Sheet → rows → hierarchy），供 LLM 端直接消费。
- **generation_skill.md 大幅扩展**：从 20 行 stub 扩展为 220+ 行完整生成规则，包含 8 条规则（层级信号名拼接、排除清单、属性中文名映射含英文原名、描述模板、单位自动追加、去重、空值跳过、叶节点属性参考），适配 PubSub 树状层级数据。
- **generation_prompt.md 重写**：明确 PubSub2IRD 处理路径（excel_data 优先），定义 IRD 格式 entry_id 和双模式字段规范（PubSub / 接口模式）。
- **scoring_skill.md 重写**：扩展 4 维评分细则（完整性/一致性/可追溯性/可读性），强制评分区分度和 `recommended_is_best` 唯一推荐。
- **scoring_prompt.md 重写**：移除 stub 描述，明确 chunk 级候选互评要求和横向对比规则，新增 scoring 输出关键提醒。
- **DeepSeek V4 Mode.MD_JSON 支持**：DeepSeek 路径切换为 `Mode.MD_JSON` + thinking 保留，`extract_json_from_codeblock()` 自动跳过 `<think>` 标签提取 JSON，恢复 scoring 等复杂推理任务质量。
- **generation_skill.md 规则 8·叶节点属性参考**：明确 DP/RP 叶节点常见属性列表，中间层级元数据（IDAL、XsdVersion、CANMessageProtocolType 等）不应生成需求条目，抑制模型输出非需求性属性。

### Changed

- DeepSeek 路径从 `Mode.TOOLS` + `thinking=disabled` 切换到 `Mode.MD_JSON` + thinking 保留。
- MiniMax TOOLS mode fallback：content → tool_call fallback 在 tool_calls 缺失时自动提取 JSON 包装。
- `_excel_to_chunk()` 聚合策略：所有 Excel Sheet → 单个 EoICDChunk（`excel-chunk-001`），`excel_data` 字段使用 `build_nested_sheets()` 的嵌套结构。
- `_build_real_llm()` 默认 `provider=openai`，MiniMax/DeepSeek 统一走 LLM → InternalInstructor 路径。
- LLM max_tokens 注入 instructor client，避免 instructor 使用内置默认 4096 截断长输出。

### Fixed

- **CrewAI 上下文污染修复**：所有 generation 和 scoring Task builder 设置 `context=None`，阻止 Process.sequential 自动将前序 Task 的 raw output 注入后续 Task 上下文，消除 MiniMax/DeepSeek scoring 输出完全一致的 bug。
- **多模型凭证冲突修复**：`_litellm_with_fallback` 按模型名动态匹配 `_provider_creds` 注入 api_key/api_base，避免多模型共用 `OPENAI_API_KEY`/`OPENAI_BASE_URL` 环境变量冲突。

### 2026-06-29 代码清理与数据流修复

#### Removed

- 删除 `_flatten_schema_defs()` 函数及调用点（MiniMax $defs 展平逻辑）。该函数基于错误的归因添加：空 tool_call arguments 实际是 DeepSeek TOOLS mode 的问题，已通过切换 MD_JSON 解决，与 MiniMax $defs 无关。
- 删除 MiniMax 分支中的死代码：`if "deepseek-v4" in mdl: thinking=disabled` — 该条件在 `is_minimax=True` 分支下永不为真，从未实际执行。

#### Fixed

- **Excel 数据流修复**：`EoICDChunk.excel_data` 类型从 `Optional[ParsedEoICDExcel]` 改为 `list[dict]`，直接存储 `build_nested_sheets()` 的嵌套结构；`tables` 字段回归只存 Word 内嵌表格。修复了 tasks.py 中 `excel_data=chunk.tables`（将 Word 表格误传为 Excel 数据）的 bug。
- **文档清理**：CHANGELOG、development-log、debug-log 中移除所有基于错误归因的 MiniMax $defs 展平/空 tool_call 相关描述。

## [Unreleased] - 2026-06-29

### Changed

- **前端 UI 完整重设计**：参照 icd_demo v1.0 视觉风格，统一蓝色主题（#0066cc），实现 3 步工作流步骤条（上传文件 → 智能处理 → 查看结果），卡片式布局，Header/Footer 完整框架。
- 文件上传页改为三区布局（EoICD Word / Excel 附件 / 软件高层需求），支持文件列表管理与移除。
- 新增 Word（mammoth）和 Excel（xlsx）客户端实时文件预览。
- 处理中页面新增 3 阶段进度同步：后端 pipeline 实时推送进度文字（解析输入 → 生成评分择优 → 检查需求一致性），前端轮询显示。
- 结果页新增最优条目化需求和差异分析报告双卡 DOCX 预览，保留全部 4 个输出文档下载入口。
- 新增全局 CSS 设计令牌系统（CSS Variables），响应式适配（900px 断点）。

### Added

- 前端新增依赖：`xlsx`（Excel 预览）、`mammoth`（Word 预览）。
- 前端新增组件：`FilePreview.tsx`、`ProcessingView.tsx`、`ResultView.tsx`。
- 前端新增 `index.css` 全局样式表、`types.ts` UI 类型定义。
- 后端 `pipeline.py` 新增 3 阶段 `job.update` 进度消息。

## [Unreleased] - 2026-07-01

### Added

- **真实 SRS Parser 实现**：`backend/app/parsers/software_req_parser.py` 从硬编码 stub 改为基于 python-docx 的真实解析。从"软件需求"章节下的 8 行 × 2 列需求表格中提取字段，按文档约定映射（对象类型 `需求 → requirement`、`注释 → comment`；实现方法 `手工编码 → manual_coding`、`基于模型 → model_based`），并跳过 Table[0] 缩略语表（3×3）。处理空单元格和 "NA"/"N/A" 标记，`requirement_id` 或 `requirement_text` 为空时跳过该条 + warn log。
- **`ParsedSoftwareRequirement` 数据模型扩展**：从 3 字段扩展为 8 字段，新增 `object_type`（"requirement" / "comment"）、`is_derived`（bool）、`rationale`、`verification_method`、`implementation_method`（"manual_coding" / "model_based"）、`source_file`。
- **差异比对输出结构升级**：`DifferenceEntry` 和 `DifferenceItem` 拆 `difference_id` 为两个关联 ID（`difference_requirement_id` 关联 SRS 端 `requirement_id`、`difference_eoicd_entry_id` 关联 EoICD 端 `entry_id`），并把 `requirement_text` 改名为 `eoicd_requirement_text` 以消除歧义。
- **结构化 description 格式**：每条差异的 `description` 字段约定为多属性对比的结构化文本，每行一条 `属性 <名>: SWHLR=<值> IRD=<值> <判定> - <分析>`，末尾追加 `整体判定 / 整体分析 / 整体建议` 三行。判定值 5 种：`一致` / `不一致` / `仅IRD定义` / `仅SWHLR描述` / `待确认`，由 `difference_type` 取值映射。
- **差异报告 docx 渲染升级**：汇总表从 4 列扩展为 5 列（差异编号 / 关联定位 / 差异类型 / 差异描述 / 建议处理方式），详情区新增"关联定位"block（分行列出 SRS ID 和 EoICD 条目 ID）。新增 `_render_description()` 函数按 `\n` 拆行渲染 description，并对"属性 XX:" / "整体XX:" 前缀加粗。
- **comparison_prompt.md 与 comparison_skill.md 同步更新**：清除原"stub"提示，明确两边均为真实解析后的结构化数据；新增 description 结构化格式章节与判定值映射表。

### Changed

- `crew/difference_analyzer.py` 字段搬运更新，对齐新 schema。
- `crew/tasks.py` `expected_output` 字段名同步（`requirement_text` → `eoicd_requirement_text`，新增两个关联 ID 字段名）。
- `llm/mock_llm.py` `_comparison_mock_data()` 5 条 mock diff 改写为新 schema 字段 + 结构化 description 文本（供 mock 模式演示）。

### Known Issues

- 真实 LLM 模式下 `deepseek_comparison` agent 在 description 结构化后输出变长，存在偶发 `max_tokens` 上限触发 `IncompleteOutputException` 的情况，导致任务失败。当前未修改 `agents.py` 的 `max_tokens=8192` 配置，建议后续根据实际输出长度评估调整，或在 prompt 中限制 description 行数上限。

## [Unreleased] - 2026-07-28：V4.0 后端工程化集成（Issue A 落地）

### Added

- **V4.0 后端工程化集成**：把 `_v4_backend_raw/backend/app/` 整体迁入 `backend/app/v4/`，新增 `/api/v4` FastAPI 路由命名空间，V3 与 V4 双版本共存。
- 新增 5 个 V4 FastAPI 端点（`/api/v4/*` 前缀）：
  - `POST /api/v4/coverage-analysis`：multipart 接收 `hlr_word_file` + `eoicd_publisher_file` / `eoicd_subscriber_file` + 可选 `traceability_files` + `use_mock_llm` / `judge_providers` / `enable_traceability_prefilter`，同步返回 V4JobId。
  - `GET /api/v4/jobs/{job_id}`：返回 `V4JobStatusResponse`（含 `stage` / `stage_index` / `case_index` / `mock_models`）。
  - `GET /api/v4/jobs/{job_id}/result`：返回 `V4JobResultResponse`（含 `summary` / `outputs` / `mock_models` / `errors`）。
  - `GET /api/v4/jobs/{job_id}/outputs/{eoicd-xlsx|consensus-docx|consistency/{model}}`：3 类对外下载。
  - `GET /api/v4/health`：V4 健康检查。
- 新增 V4 Pydantic schemas：`V4AnalyzeResponse` / `V4JobStatusResponse` / `V4JobOutputs` / `V4JobResultSummary` / `V4JobResultResponse`（位于 `backend/app/api/v4/schemas.py`），与 V3 响应 schema 互不污染。
- 新增 V4 runner 工具（`backend/app/api/v4/runner.py`）：`run_v4_pipeline_thread()` 后台线程包装，env 保存/恢复（修正 #2：进入线程前 `os.environ.get` 备份，退出时 `try/finally` 恢复），落盘后反读 `multi_judge_results.json` 派生 `mock_models`（D5 规则：`mock_models = [p for p in actual_providers if p in {"minimax","qwen"}]`）。
- 新增 ADR-001：`docs/decisions/ADR-001-V4后端接入策略.md`（D1-V4 作为后续主线；D2-V3 旧 API 暂留；D3-`/api/v4` 独立命名空间；D4-V4 业务逻辑保护；D5-mock_models 显式标识；D6-`consistency/{model}` 扩展点；D7-JSON 不暴露）。
- 新增追溯表预筛选能力（V4 `enable_traceability_prefilter=true`）：`backend/app/v4/traceability/trace_parser.py` 在主名匹配失败时 `glob(*.xlsx)` 排序兜底（解决 MSYS bash 中文文件名编码降级场景）。

### Changed

- **V4 路径布局（按 Issue A 决定）**：
  - V3 任务目录：`backend/output/v3/{job_id}/`（平铺，input + output 不分）。
  - V4 任务目录：`backend/output/v4/{job_id}/input/` + `backend/output/v4/{job_id}/output/`（分两层）。
  - 此前 V4 临时目录 `backend/app/output/` 已删除，所有输出迁到 `backend/output/` 根下。
- **V3 与 V4 共享 JobManager**：`backend/app/job_manager.py` 给 `Job` 加 `kind: Literal["v3","v4"]` 字段（默认 "v3"），V4 路由显式传 `kind="v4"`；V3/V4 路由跨版本查询返回 404 + 友好提示。
- **V3 旧 V3 router（机械拆分）**：原 `backend/app/main.py` 拆分到 `backend/app/api/v3/router.py`（173 行），其中 `/api/jobs/{job_id}` 与 `/api/jobs/{job_id}/result` 加 `job.kind != "v3"` 跨版本 404 检查；其他路由 URL、字段、后台线程逻辑、文件保存、下载 helper 全部保持原状。`backend/app/main.py` 改为 33 行的 thin shell（CORS + V3 router 装载，预留 V4 router 装载位）。
- **V4 router 路径表达式统一 4 层**：`coverage.py:job_dir` / `outputs.py:root` / `jobs.py:base_outputs_dir` 都从 `parent.parent.parent` 升级为 `parent.parent.parent.parent`，与 V3 共享 `backend/output/` 根目录对齐。
- **V4 路由层 V4 路径计算都用 4 层**（不再走 3 层）；3 处都用 `Path(__file__).resolve().parent.parent.parent.parent / 'output' / 'v4' / {job_id} [/output]` 模式。
- **`backend/.env.example` 收尾**：删去之前为 V4 加的 `_V4` 后缀占位段（`DEEPSEEK_API_KEY_V4` / `DEEPSEEK_BASE_URL_V4` 等），恢复 39 行 V3-only 模板。V4 直接读 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `JUDGE_PROVIDERS` / `USE_MOCK_LLM`。
- **`backend/requirements.txt` 增 3 项 V4 依赖**：`python-dotenv>=1.0.0`、`requests>=2.31.0`、`pyyaml>=6.0`。
- **`docker-compose.yml` volume 路径调整**：`. / backend/app/output:/app/app/output` → `./backend/output:/app/output`，与 V3/V4 共享根目录。
- **`backend/app/v4/config.py` 环境加载路径**：`_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"`（原 `_v4_backend_raw/backend/.env`）改为 `parent.parent.parent / ".env"`（`backend/.env`）。

### Fixed

- **V4 DeepSeek URL 双 `/v1` 拼写 bug**：`backend/app/v4/llm/deepseek_client.py:34` 和 `backend/app/v4/matching/hlr_labeler.py:51` 各自拼 `f"{base_url.rstrip('/')}/v1/chat/completions"`，与 `.env` 里 `DEEPSEEK_BASE_URL=https://api.deepseek.com/v1`（带 `/v1` 后缀）叠加成 `/v1/v1/chat/completions`，DeepSeek 报 404。修复：两处都加 `if base.endswith('/v1'): base = base[:-3]` 幂等保护。
- **V4 trace_parser 硬编码中文文件名 brittleness**：`backend/app/v4/traceability/trace_parser.py:115,170` 直接 `trace_dir / "单模块需求矩阵分析（设备2软件高层）-裁剪.xlsx"` 等中文字符串，与 `enable_traceability_prefilter=true` 路径上 MSYS bash 编码降级冲突。修复：抽 `_discover_trace_files(trace_dir)` 工具函数，**先按主名精确匹配 → 失败则 `trace_dir.glob("*.xlsx")` 排序兜底**，并保证 Table 1 / Table 2 不会命中同一文件。
- **V4 outputs.py / coverage.py path 4 层 Bug（Issue A 期间发现）**：`parent.parent.parent.parent`（4 层）导致容器内落点 `/app/output/v4/...` 落到了 volume mount 之外。修到 3 层（`parent.parent.parent`），与 V3 router 一致。
- **V4 `parent.parent.parent.parent` / `parent.parent.parent` 4 vs 3 层 Bug（Issue A 期间发现）**：`jobs.py:70` 和 `outputs.py:32` 仍在 3 层，与 coverage.py 写盘 4 层错位，导致 `/result.outputs` 全部 false / 5 类下载 404。修到 4 层，对齐 coverage.py。
- **V4 `hlr_labeler` 直接 `requests.post`**：原 `_call_label_api` 独立拼 URL / Authorization 头 / retry 循环，不走 `get_llm()` factory，与 `comparison/*.py` 的 3 处调用方式分裂。修复：`_call_label_api` 改用 `get_llm("deepseek").chat(messages=..., max_retries=0)`，外层 retry 仅兜 JSON 解析错误；`label_hlrs` 去掉 `api_key/base_url/model` 参数，全部由 factory 从 env 读。V4 内部 `import requests` 从 2 处（deepseek_client.py + hlr_labeler.py）收敛为 1 处（deepseek_client.py 抽象层）。

### Notes

- 本次 Issue A 是 V4 后端工程化集成的"包装层"工作，V4 业务逻辑零修改（除 bug 1 / bug 2 两处修复外）。Issue A 期间两次对 V4 内部模块（deepseek_client.py、hlr_labeler.py、trace_parser.py）的修改按用户授权"特权"实施，未建立 ADR-002。
- 本期 V4 仅暴露 3 类对外下载（`eoicd-xlsx` / `consensus-docx` / `consistency/{model}`）；4 类内部 JSON（`multi_judge_results.json` / `consensus_results.json` / `reverse_matches.json` / `reverse_report.json` 等）按 D7 不暴露给 API。
- `/api/v4/jobs/{id}/result.outputs.eoicd_xlsx` 等 5 个布尔字段在 V4 落盘成功后均为 true，由 `runner.derive_outputs()` 反读盘与 SSoT 一致。
- V4 业务内部 import `requests` 仅 1 处（`backend/app/v4/llm/deepseek_client.py`）；其余 4 处 LLM 调用（`comparison/{semantic_judge,multi_judge,review_agent}.py` + `matching/hlr_labeler.py`）均走 `get_llm("deepseek").chat()` 工厂。

## [Unreleased] - 2026-07-31：V4 追溯表兜底机制与共识报告增强

### Added

- **追溯索引协议开销字段过滤**：`trace_parser.py` 新增 `_PROTOCOL_BLOCKKEY_SUFFIXES` 常量，在构建 HLR→BlockKey 追溯索引时自动跳过 `/SDI`、`/LABEL`、`/PARITY`、`/SSM`、`/OCTLBL` 等 A429 协议开销后缀的 block_key，避免虚增 traced-block 统计。
- **追溯表预筛选兜底机制**：`pipeline.py` 中 `_match_reverse_with_trace()` 新增 per-HLR fallback 逻辑——预筛选匹配结果为"无匹配"的 HLR 自动回退到全量 EoICD 匹配，防止因追溯表数据覆盖不全或 label 不匹配导致的漏判。
- **共识报告不一致属性栏输出**：`consensus_word_generator.py` 明细表新增"不一致属性"列（位于 ICD Block 和分析摘要之间），Review Agent 识别出的不一致属性（总线类型、信号方向等）以 " | " 分隔显式列出，并按判定状态（已覆盖/不一致/待确认/无匹配）分组展示。
- **前端 V4 专用组件**：新增 `V4FileUpload.tsx`（HLR Word + Pub/Sub Excel + 追溯表上传）、`V4ResultView.tsx`（状态分布卡片 + 星级柱状图 + 预览 + 下载），替换 V3 旧组件。
- **前端依赖**：新增 `lucide-react`（图标库）。
- **环境变量模板**：`.env.example` 新增 Qwen (DashScope) 配置段（`QWEN_API_KEY` 等 11 项）。

### Changed

- **管线步骤编号统一**：`pipeline.py` 中所有 Step 编号从 1/3、1.5/3、2/3、3/5、4/5、5/5 统一为 1/6、2/6、3/6、4/6、5/6、6/6。stage 映射同步调整：1→parse, 2→label, 3→match, 4→multi_judge, 5→review, 6→report。`runner.py` 中 `_parse_progress()` 同步更新。
- **V4 result.summary 字段调整**：`status_distribution` 增加"无匹配"键（值来自 `match_stats["unmatched_count"]`），使前端可直接展示四种状态分布。
- **前端轮询间隔**：V4 任务轮询从 2 秒/600 次调整为 10 秒/120 次（总超时约 20 分钟不变）。
- **前端 `App.tsx`**：完全切换为 V4 管线（`V4FileUpload` / `V4ResultView` / V4 API client），增加 V4 独立健康检查，保留 V3 旧组件文件不动。
- **HLR Labeler prompt 修正**：bus_types 标准名称明确化（CAN→A825、AFDX→A664、ARINC429→A429）。
- **DeepSeekClient**：默认 `max_tokens` 从 1024 调整为 4096，新增 `finish_reason=length` 截断告警。

### Fixed

- **共识报告"判定分布"表无匹配缺失**：`consensus_word_generator.py` 中"判定分布"表新增"无匹配"行（紫色标注，来自 `match_stats["hlr_无匹配"]`），合计 = 裁判数 + 无匹配数。
- **V4ResultView STATUS_META 键名不匹配**：前端 `STATUS_META` 键名从英文（`covered`/`inconsistent`/`needs_review`）改为中文（`已覆盖`/`不一致`/`待确认`），与后端 `status_distribution` 实际键名对齐。

## [Unreleased] - 2026-08-11：V4 Multi-Agent 降级处理机制

### Added

- **降级模块**：新增 `backend/app/v4/degradation/` 独立包（`config.py` / `context.py` / `fallback.py`），对 Step 4 Multi-Judge 和 Step 5 Review Agent 提供系统性异常兜底。
- **Case 级超时控制**：3 个 provider 并行裁判时，前 2 个完成后第三个给予固定额外等待时间（默认 120s），超时后生成 error judgment 而不中断 case。不足 2 个完成时使用兜底上限（默认 300s）。
- **Provider 熔断器**：连续失败达阈值（默认 3 次）后自动跳过该 provider，TTL 到期自动恢复。401/403 认证错误立即熔断。
- **Review 评审降级**：1 个 provider 存活 → 星 ≤ 1★，agreement = "single_source"；2 个存活 → 星 ≤ 2★。对 review_judgments() 输出做后处理。
- **降级可观测性**：`consensus_results.json` 和 API response 新增 `degradation` 字段，包含 provider 健康状态、超时次数、星级截断次数。
- **HTTP 超时提升**：DeepSeek / MiniMax / Qwen 三个 client 的 HTTP 请求超时从 60s → 120s，与 case 级超时配合。
- **新增 4 个环境变量**：`DEGRADATION_CASE_TIMEOUT`（300） / `DEGRADATION_EXTRA_WAIT`（120） / `DEGRADATION_CONSECUTIVE_FAILURES`（3） / `DEGRADATION_UNHEALTHY_TTL`（300），全部通过 `.env.example` 暴露，不配时用默认值。

### Changed

- **Step 4 调用切换**：pipeline 中 Step 4 从 `judge_with_panel()` 切换为 `_judge_with_degradation()`。
- **Step 5 增加后处理**：Review Agent 执行后增加 `_apply_degradation_review()` 对星级和 agreement 做硬上限约束。
- **LLM Client 默认参数**：`review_agent.py` 和 `semantic_judge.py` 的 `max_tokens` 从 8192 → 4096。

## [Unreleased] - 2026-08-11：V4 一星复查机制（Issue #53）

### Added

- **Step 5.5 一星复查（peer-aware 反思）**：新增 `comparison/re_review.py`，`re_review_judgments()` 对 `star_rating == 1` 的 case 由三个 provider 以 peer-aware 方式各自重新评判。每个 provider 看到自己之前的判断（Judgment A）和 peer 的判断（Judgment B/C），携带完整 analysis 文本触发反思纠正。返回类型 `tuple[MultiJudgeOutput, set[str]]`（更新后的 multi_out + 被复查 case_id 集合）。
- **Step 5.6 部分共识重跑**：仅对 `re_reviewed_ids` 中的 case 重跑 `review_judgments()`，其余 case 保持 Step 5 原结果不变。
- **新增 `prompts/re_review.md`**：一星复查 LLM prompt，包含 peer-aware 复查规则、反思引导和证据驱动逐项核对模板。
- **`hlr_labeler.py` max_tokens 调整**：DeepSeek HLR 标注 `max_tokens` 从 1024 调整为 2048，避免频繁截断告警。
- **workflow.md Step 5.5/5.6 更新**：V4 总体流程图、单步输入输出表、异常处理表均已同步新增两个步骤。

### Fixed

- **pipeline.py `review_judgments` 局部引用错误**：原 `re_review_judgments()` 内部存在局部 `from review_agent import review_judgments` 导入，导致 Step 5.6 的外层调用因变量遮蔽产生 `UnboundLocalError`。修复：移除内部局部 import，统一从模块级导入。
- **`re_review_judgments` 的 `multi_out=None` 崩溃**：集成测试中发现当 `multi_out` 传入 `None` 时，函数访问 `.results` 报 `AttributeError`。修复：从 `output_dir / "multi_judge_results.json"` 加载 MultiJudgeOutput 后再继续处理。
- **error provider 被重新查询的问题**：re-review 对所有一星 case 的所有 provider 都重新调用 LLM，即使该 provider 在原始 judgment 中已经是 `coverage_status="error"`。error judgment 被覆盖丢失，导致 degradation 统计错误。修复：跳过 `coverage_status == "error"` 的 provider，不重新查询。
- **`build_cases` case_id 格式错误**：测试脚本生成的 case_id 格式为 `REV-0199`（来自 HLR 编号），与 pipeline 实际生成的 `REV-0001`（顺序编号）不一致，导致 `case_map` 和 `mjr_map` 的 key 无法匹配，所有一星 case 被静默跳过。修复：`build_cases` 改用顺序编号。

### Notes

- 一星复查的测试方式为手动注入"错误但看似合理"的 analysis 文本，而非仅修改 coverage_status 标签。peer-aware 机制要求 provider 看到自己之前的错误分析才能触发真正反思和判断纠正。
- `re_review_results.json` 写入审计记录，`multi_judge_results.json` 更新供 Step 5.6 继续使用，两者落盘时机由 `re_review_judgments()` 内部管理。
- 集成测试三场景验证通过：3 providers 存活 → 3★；2 providers 存活 → cap 2★；1 provider 存活 → cap 1★。

## [Unreleased] - 2026-08-14：V4 降级机制修复与共识报告星级表调整（Issue #59）

### Fixed

- **裁判失败状态归一为 error**：`semantic_judge.py` 三种失败路径（JSON 解析失败 / API 错误 / 重试耗尽）的 `coverage_status` 从 `needs_review` / `unmatched` 统一改为 `error`，避免失败被误判为业务结论，保证 degradation 的 surviving provider 统计正确。
- **0 存活降级分支缺失**：`_apply_degradation_review()` 新增 0 个 provider 存活场景——强制星级 ≤ 1★、`agreement = "no_consensus"`、`final_coverage_status = "待确认"`，防止共识 LLM 在纯 error 输入上幻觉出高星级。
- **复查后降级封顶失效**：Step 5.6 部分共识重跑后重新应用 `_apply_degradation_review()` 并重建 summary，避免复查升星绕过降级封顶。

### Changed

- **共识报告星级分布表**：删除 1★ 主行（需人工复核），仅保留 3 个子类型行（分歧/仅单一来源/无有效裁判）；★☆☆ 显示在首个子行星列并纵向合并 3 行；子类型标签加粗、与主行格式统一。
- **共识明细表共识列标签映射**：新增 `no_consensus → 无有效裁判`、`single_source → 仅单一来源`。
- **降级配置**：`DegradationConfig` 新增 `zero_provider_star_cap=1`、`zero_provider_agreement="no_consensus"` 默认值。

## [Unreleased] - 2026-08-19：V4 反向管线多控制器 Profile 化（Issue #63）

### Added

- **Controller Profile 子包**：新增 `backend/app/v4/profiles/`，`base.py` 定义 `ControllerProfile` + 4 个 Config dataclass（`HLRParserConfig` / `TraceabilityConfig` / `ClassifierKeywords` / `AILabelingConfig`），`__init__.py` 提供 `ProfileRegistry` 单例。profile 配置以 `profiles/{id}/config.yaml` 声明，新控制器可通过新增目录接入，无需改动业务代码。
- **AMS profile（默认）**：从现状代码 1:1 抽取，行为与 Issue A 完全一致，向后兼容。
- **FGMC profile（燃油测量管理计算机）**：术语表位于 `tables[1]`、需求表 ≥12 行、支持"是否为需求"= "否" 行过滤、追溯表用 glob 模式（`*追溯*.xlsx` / `*矩阵分析*.xlsx`）、燃油域分类关键词与 AI 标注示例。
- **API 新增 `controller_profile` 字段**：`POST /api/v4/coverage-analysis` 新增 form 字段，默认 `ams`，白名单 `{ams, fgmc}`，非法值在创建任务前返回 422。
- **CLI 新增 `--controller-profile`**：`label` / `reverse` / `reverse-analyze` 三个子命令支持，`choices=["ams","fgmc"]`，默认 `ams`。
- **Profile 单元测试**：新增 `backend/app/v4/tests/profiles/`，覆盖 registry / models / HLR parser / classifier / labeler / 追溯表 / pipeline 共 24 个用例。

### Changed

- `HLRWordParser` / `trace_parser` / `hlr_classifier` / `hlr_labeler` 改为 profile-driven，profile 由 pipeline 统一注入，不再依赖模块级硬编码常量；未传 profile 时退化为 AMS 默认行为。
- `HLRRequirement` 模型扩展 6 个 optional 字段（`code` / `source` / `covered_ids` / `notes` / `input_data` / `output_data`），供 FGMC 需求表使用；AMS 侧保持为空不影响既有输出。

### Fixed

- V4 不再硬编码 AMS 专属的追溯表中文文件名、sheet 名、HLR 表行数阈值和字段名；接入新控制器不再需要修改 parser / matcher 源码。

## [Unreleased] - 2026-08-20：V4 HSCU 控制器 Profile 接入（Issue #63 续）

### Added

- **HSCU profile（液压系统控制单元）**：新增 `backend/app/v4/profiles/hscu/` 子包，含 `__init__.py` / `config.yaml` / `hooks.py` / `README.md`。基于现有 AMS / FGMC profile 模式，无需改动业务代码即可接入。
- **HSCU 适配要点**（与 AMS / FGMC 差异）：
  - HLR 字段映射：HSCU 需求表行标签用 `需求正文` 而非 `需求中文`（其他控制器均为 `需求中文`）；术语表位于 `tables[0]`，需求表 ≥ 8 行。
  - 无 `is_requirement` 列：HSCU 需求表中没有"是否需求"列，`filter_non_requirement` 关闭；其他控制器中 AMS 默认开启，FGMC 通过 `filter_non_requirement: true` + 匹配 "否" 过滤。
  - 追溯表 T1：glob 模式 `附件1*需求*ICD*.xlsx`（AMS 用精确中文文件名，FGMC 用 `*追溯*.xlsx`）；sheet 名匹配关键词 `待填_需求接口追溯表`。
  - 追溯表 T2：glob 模式 `*液压*单模块需求矩阵*.xlsx`，`data_start_row: 2`（跳过当前需求文档 / 下层需求文档合并行 + 列标题行）。
- **API `controller_profile` 白名单扩展**：`POST /api/v4/coverage-analysis` form 字段 `controller_profile` 白名单由 `{ams, fgmc}` 扩展为 `{ams, fgmc, hscu}`，非法值仍返回 422。
- **HSCU 测试文档**：在 `backend/app/v4/profiles/hscu/README.md` 中记录 HSCU 与 AMS / FGMC 的差异、T1 sheet 名纠正（`待填_需求接口追溯表` 而非 `需求_设备接口追溯表`）和 `data_start_row` 含义。

### Changed

- `backend/app/api/v4/coverage.py` `ALLOWED_CONTROLLER_PROFILES` 集合加入 `"hscu"`，错误信息更新为 `allowed: ams, fgmc, hscu`。
- `docs/architecture/api.md` §13.2 `controller_profile` 字段白名单由 `{ams, fgmc}` 改为 `{ams, fgmc, hscu}`，§13.6 错误响应同步更新。
- `docs/project/scope.md` §8.2.1 profile 表新增 `hscu` 行，描述 HSCU 适用控制器 / 术语表位置 / HLR 需求表结构 / 追溯表文件名。
- `docs/architecture/current-architecture.md` profiles 目录树新增 `hscu/` 节点。

### Fixed

- **HSCU T1 sheet 名配置纠正**：最初错误将 T1 sheet 名配置为 `需求_设备接口追溯表`，通过对 `wb.sheetnames` 做 codepoint inspection 后改为 `待填_需求接口追溯表`（HSCU 实际 sheet 名），与 trace_parser `by_name_keywords` 匹配逻辑对齐。
- **HSCU 早期误诊的 GBK mojibake 修复已删除**：首次接入时误判 HSCU T1 xlsx 存在 GBK-as-UTF-8 mojibake，引入 `_xlsx_mojibake.py` 工具 + 在 `TraceabilityTableConfig` 加 `repair_gbk_mojibake` 字段 + 在 `trace_parser._read_table1/2` 加 `_maybe_heal()` 调用。经 codepoint 校验 HSCU 文件实际为干净 UTF-8，"mojibake" 现象是 Windows console 无法渲染某些 CJK 字符所致。按 debug-rules.md "最小修改原则" 全部回退删除，无 mojibake 相关代码残留。

### Notes

- HSCU E2E 已通过 mock LLM 验证：job `dd0790ed` 跑通完整 6 步管线，4 类 DOCX 输出齐全，HLR 解析 10 条 / 追溯匹配 15 条。
- HSCU 0/10 反向匹配：当前 HSCU HLR 信号关键词（如 `HYD_xxx` / `LBL_xxx`）在提供的 EoICD 样例文件中未出现（样例仅含 AHMU `AIRCRAFT_STATUS` 信号），属测试数据问题，非 profile 问题。
- AMS / FGMC 回归验证通过：AMS job `a335bce9`（~60s）+ FGMC job `67e745b9`（<5s）均产出 4 DOCX，无回归。

