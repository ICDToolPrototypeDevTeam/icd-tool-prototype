# CHANGELOG

本文件记录 EoICD 一致性分析工具各版本的功能级变更。以用户可见的大功能为单位，不记录单文件的小修正。

---

## 变更记录规则

### 变更范围

以下变更**必须**记录：
- 新增模块（如新的 parser、新的匹配器）
- 新增 CLI 命令或重大参数变更
- 数据模型字段变更（增/删/改名）
- 新增解析规则或规则行为变更
- 输出格式变化（JSON 结构、字段名、排序）
- 重大 bug 修复（影响输出正确性的修复）
- 架构级调整（模块拆分/合并、流水线重构）

以下变更**不记录**：
- 单文件内的拼写/格式修正
- 注释、docstring、类型标注的修改
- 依赖版本小升级
- 纯重构（不改变行为的代码整理）
- 调试用临时修改

### 变更颗粒度

- 以**用户可感知的功能变化**为单位，不以 commit 为单位
- 一个版本号对应一个或多个关联功能的交付
- 版本号格式：`v{major}.{minor}.{patch}`
  - **major**：架构变更、不兼容的模型变更
  - **minor**：新功能模块、新规则、新 CLI 命令
  - **patch**：bug 修复、配置更新、输出格式微调

### 更新时机

- 每完成一个大功能模块后更新
- 不要在每个小修改后更新，避免日志碎片化
- 如一个版本包含多项变更，合并为一条记录而非多条

### 记录格式

```
## vX.Y.Z (YYYY-MM-DD)

### 新增
- 功能摘要（涉及的主要文件或模块）

### 变更
- 变更摘要（旧行为 → 新行为）

### 修复
- 问题摘要（影响范围）

### 移除
- 移除的功能或字段
```

无对应条目的类别可省略。

---

## v4.1.2 (2026-07-24)

### 变更
- **一致性对比提示词重写**（`reverse_judge.md`）：从通用覆盖性检查改为四步审查法（提取 ICD 约束 → 提取 HLR 声明 → 逐项比对 → 判定），只比对 HLR 明确写出的技术声明，未提及属性不构成不一致
- **一致性结果四分类**：裁判输出从五类（covered/partial/missing/inconsistent/needs_review）简化为四类（已覆盖/不一致/需确认/无匹配），`mock_llm.py` 同步更新
- **DeepSeek 单模型一致性报告**（`word_generator.py`）：`generate-consistency-report` 输出改为仅展示 DeepSeek 裁判结果，新增判定分类说明、判定结果概览表（含占比）、分析明细表（HLR ID / 判定结果 / ICD Block / 分析摘要 / 置信度）
- **JSON 解析容错增强**（`semantic_judge.py`）：截断 JSON 自动修复（补闭合引号+括号），新增 sub_signals 明细格式化输出
- **反向匹配摘要不再截断**（`reverse_matcher.py`）：summary 中的 ICD Block 列表从 Top-5 改为全部输出
- **管线自动产出文档**（`pipeline.py`）：`reverse-analyze` 一步生成全部产出 — Step 1 自动输出 `EoICD条目化清单.xlsx`，Step 5 自动输出 3 份单模型 Word 报告 + 1 份多模型共识报告
- **`generate-word --model` 支持多值**（`main.py`）：`--model deepseek minimax qwen` 一条命令生成三份单模型报告

### 修复
- **同事分支一致性改进合并**（`fix/match-accuracy-improve` → `feat/merge-comparision-improved-version`）：融合 `icd-tool-refactor-v4.0.1.1` 的 2 个提交，保留我方多模型裁判+Review Agent 架构，融入提示词/报告/JSON修复/匹配增强等改进

---

## v4.1.1 (2026-07-23)

### 修复
- **bit_field 维度评分大面积返回 0**：HLR 文本中 `bitX=value`（赋值）和 `bitX的`（所有格）模式未被正则覆盖，且多 bit 标签的 profile "先到先得"丢失了 sub-signal bit 偏移量
  - `hlr_classifier.py`：新增 `_BIT_ASSIGN_RE` / `_BIT_POSSESSIVE_RE` 两个正则，补全 bit 字段提取覆盖面
  - `reverse_matcher.py`：`_score_block()` 新增 `block.sub_signals` 回退（Pass 2），修复 profile 级别的 bit offset 信息丢失
  - 修复效果：bit_field=0 的 HLR 从 9/15 降至 3/15（3 个正确为 0），6 个修复的 HLR 中 3 个获满分 20

---

## v4.1.0 (2026-07-21)

### 新增
- **追溯表预筛选模块**（`app/traceability/`）：通过两张追溯 Excel 表构建 HLR ID → ICD BlockKey 桥接索引，在反向匹配前预筛选 EoICD 搜索空间
  - `trace_parser.py` — 读取追溯表、构建索引，`name_to_block_key()` 独立于 matching/ 零耦合
  - 可追溯 HLR 的 EoICD 搜索空间缩减约 96%（84,114 → 2,854），不可追溯 HLR 回退全量搜索
- **`reverse-match` / `reverse-analyze` 新增 `--traceability-dir` 可选参数**：传入追溯表目录即可启用预筛选，不传则行为完全不变
- **管线新增 `_match_reverse_with_trace()` 函数**：自动分流 HLR 为可追溯组（过滤 EoICD 后匹配）和回退组（全量匹配），合并结果含追溯统计

### 变更
- `pipeline.py`：`run_reverse_pipeline()` 新增可选参数 `trace_dir`，Step 2 支持追溯表预筛选路径
- `CLAUDE.md` / `docs/architecture/current-architecture.md`（新增设计决策 #9）/ `docs/project/file-boundaries.md`：全面更新文档反映新模块

---

## v0.9.1 (2026-07-14)

### 变更
- **全量 Excel 验证**（`AMS_EoICD_Publisher_Table.xlsx` + `AMS_EoICD_Subscriber_Table.xlsx`）：
  - EoICD 条目（去重后）：69,489，ICD Block 数：5,346，涵盖 167 个唯一 Label 号
  - 反向匹配：32 条 HLR → 20 已匹配 / 10 待确定 / 2 无匹配
  - AI 反向裁判（30 cases）：covered 3 / partial 4 / inconsistent 11 / missing 7 / needs_review 5
  - 确认 cropped 数据中的 "无匹配"（L36/L121/L135 等）为数据裁剪导致，全量数据中均可正常匹配

---

## v0.9.0 (2026-07-13)

### 新增
- **ICD Block 架构**（`signal_profiler.py`）：替代 profile 级匹配，以信号族（signal family）为匹配单元
  - `ICDBlock` 数据类：将跨通道/总线的 SignalProfile 合并为一个 Block，AI 看到完整信号定义而非碎片化 profile
  - `_extract_signal_family()`：正则剥离通道前缀（`L{label}_[{port}_]{bus_ch}_`），提取纯信号族名（如 `AFTEFAN1_HW_FAULT`），支持端口号可选的命名格式
  - `build_blocks()`：按 `(label, signal_family)` 分组，合并属性/方向/总线/token，过滤协议开销信号族（LABEL/SDI/SSM/PARITY/OCTLBL）
- **三级分层评分**（`reverse_matcher.py`）：基于总分和活跃维度数将匹配结果分为三级
  - 已匹配（total ≥ 25 且 active_dims ≥ 2）：高置信，送入 AI 做 ICD 一致性差异对比
  - 待确定（total ≥ 12 但不满足上述）：部分维度命中，标记需人工确认
  - 未匹配（total < 12 或无候选）：过滤，不进 AI
- **Block 级 6 维度评分改进**：signal_name 维度改用 `block.signal_family`（而非原始叶节点名），消除 AFTEFAN1 vs AFTEFAN2 的 token 交叉污染
- **全量 sample_entries**（`reverse_case_builder.py`）：从 Top-3 扩展为全部 DP+RP entries，确保 AI 看到完整 ICD 属性信息
- **`BitOffsetWithinMsg` 纳入关键属性**（`config.py`）：`REVERSE_KEY_ATTRS` 从 18 项扩展为 19 项

### 变更
- **匹配单元**：SignalProfile → ICDBlock（反向管线全线：reverse_matcher、reverse_case_builder、semantic_judge 的 prompt 构建）
- **反向管线架构**：新增 ICD Block 聚合层（`build_blocks`），位于信号画像构建与 Block 级匹配之间
- **CLAUDE.md 设计决策 7-8**：全面更新反映 Block 架构和三级分层

---

## v0.8.0 (2026-07-13)

### 新增
- **反向匹配管线**（HLR → EoICD）：检查每条 HLR 是否在 EoICD 中有对应的接口定义
  - `matching/hlr_classifier.py` — HLR 4路分类器：regex 提取信号类别（A429显式/模拟量/离散量/A429隐式/逻辑非通信）、Label 号、位字段 {offset, size}、SDI 值、方向
  - `matching/signal_profiler.py` — EoICD 信号画像聚类：按 Label/叶节点名分组为 SignalProfile，含叶节点子聚类（Lxxx/LeafName）避免协议开销与数据字段属性交叉污染
  - `matching/entry_filter.py` — EoICD 条目过滤：排除协议 DataFormatType（A429OCTLBL/A429PARITY/A429SDI/A429_SSM_BNR）
  - `matching/reverse_matcher.py` — 两阶段精确匹配：Stage 1 Label前缀粗筛（高召回）→ Stage 2 6维度叶节点评分（signal_name 30 + direction 15 + bit_field 20 + sdi 15 + data_type 10 + device_bus 10，满分 100）→ Top-K=5
  - `matching/reverse_case_builder.py` — ReverseCase 构造：18 个 ICD 关键属性过滤（~68% token 缩减），匹配和未匹配 HLR 均生成 case
- **反向裁判与报告**（`comparison/`）
  - `semantic_judge.py` — 新增反向裁判 SYSTEM_PROMPT（HLR→EoICD 覆盖判定），`judge_reverse_cases()` 函数
  - `report_generator.py` — 新增 `generate_reverse_report()`：含判定分布统计、按信号类别分布、关键发现、建议
- **反向 CLI 命令**（`main.py`）：`reverse-match` / `reverse-judge` / `reverse-report` / `reverse-analyze`（一键全流程）
- **反向数据模型**（`models.py`）：`HLRCoverageResult` / `ReverseMatchOutput` / `ReverseCase` / `ReverseJudgmentResult` / `ReverseJudgmentOutput`
- **反向关键属性集**（`config.py`）：`REVERSE_KEY_ATTRS`（18 项 ICD 一致性判定关键属性）、`PROTOCOL_DATAFORMATS`（协议开销 DataFormatType 集合）

### 变更
- **匹配架构扩展**：原正向管线（EoICD→HLR）保持不变，反向管线独立运行，共享 EoICD/HLR 解析结果、HLR AI 标签和别名映射
- **CLI 命令数**：8 → 12 个子命令
- **输出目录结构**：所有输出文件夹统一归入 `output/` 下

---

## v0.7.0 (2026-07-09)

### 新增
- **EoICD 查询富化模块** (`matching/eoicd_enricher.py`)：将 EoICD 结构化字段展开为多语言匹配 token
  - 信号路径分解（按 `.` 分段，取叶节点+前缀段为设备 token）
  - CamelCase/下划线/连字符 token 分解
  - 总线别名扩展（synonyms.yaml + 自定义规则）
  - 方向动词注入（DP→发送动词集、RP→接收动词集）
  - 属性类别自动归类（`ATTR_CATEGORY_MAP`，~40 属性→10 类别）
  - Label 值提取
  - 信号别名扩展（`SIGNAL_LEAF_ALIASES` + synonyms 双向查找 + 子串匹配）
- **HLR AI 预标注模块** (`matching/hlr_labeler.py`)：DeepSeek API 批量提取结构化标签
  - 提取 bus_types / labels / devices / signal_keywords / attr_categories / direction_keywords
  - JSON 文件缓存（`hlr_labels.json`），避免重复 API 调用
  - 异常降级：API 不可用或无 key 时回退到空标签
- **7 维度统一评分匹配器** (`matching/unified_matcher.py`)：替代旧三路独立匹配
  - 总线匹配（10 分）、Label 匹配（20 分）、方向匹配（10 分）、设备匹配（15 分，四档 0/5/10/15）、信号关键词（20 分，三档 0/10/20）、属性类别（5 分）、BM25（20 分）
  - 满分 100 分，各维度分数与明细记录到 `matched_fields`
- **HLR 标注 CLI 命令** `label-hlr`：独立运行 HLR AI 预标注并保存结果
- **`HLRLabel` / `HLRLabelOutput` 数据模型** (`models.py`)

### 变更
- **匹配架构重构**：三路独立匹配器（rule_matcher + alias_matcher + text_matcher）→ 双端富化 + 统一 7 维评分
  - `candidate_matcher.py`：匹配编排入口，集成 enrich → label → unified score → Top K 全流程
  - `case_builder.py`：简化为 `candidate_matcher.match_requirements()` 的薄封装
- **`TextMatcher` 适配双端富化**：BM25 索引和查询均使用 `enriched_text` 字段
- **`analyze` 管线新增 Step 1.5**：解析后自动运行 HLR AI 预标注
- **`config.py` 新增**：`MATCH_WEIGHTS`（7 维度权重）、`ATTR_CATEGORY_MAP`、`SEND_VERBS`/`RECEIVE_VERBS`、`SIGNAL_LEAF_ALIASES`
- **`MatchCandidate.match_source` 默认值** `"rule"|"alias"|"bm25"|"merged"` → `"unified"`

### 移除
- `matching/rule_matcher.py` — 结构化字段规则匹配器（功能并入 unified_matcher）
- `matching/alias_matcher.py` — 关键词/别名匹配器（功能并入 eoicd_enricher + unified_matcher）
- `matching/candidate_matcher.py` 中的旧三路合并去重逻辑（`MATCHER_ORDER`）
- `config.py` 中的旧 `MATCH_SCORE_WEIGHTS`（被 `MATCH_WEIGHTS` 替代）

### 变更
- **解析器：从仅提取叶节点扩展为提取所有层级属性** (`parsers/eoicd_excel_parser.py`)
  - `_extract_leaf_requirements` → `_extract_layer_requirements`，现遍历所有层（Software → SubSoftware → ... → DP/RP）生成条目，而非仅叶节点
  - 每个层级的 signal_name 为该层到 Software 的完整路径，描述模板不变
- **`signal_name` 字段现输出到 JSON** (`models.py`)
  - 移除 `Field(exclude=True)`，`signal_name` 现在序列化到 JSON 输出中
  - 该字段记录从 Software 到当前层级的分层信号路径
- **Rule Matcher 简化** (`matching/rule_matcher.py`)
  - 移除 `_extract_signal_name()` 辅助函数（原从 description 文本中解析信号名）
  - 直接使用 `eoicd_req.signal_name`，消除重复解析逻辑
- **ComparisonCase 增加 signal_name 字段** (`matching/candidate_matcher.py`)
  - `_build_case()` 的 `eoicd_requirement` 字典中新增 `signal_name` 字段

### 修复
- Rule Matcher 原 `_extract_signal_name()` 仅处理 `的` 和 `接收的` 分隔符，非叶节点层级的 description 可能不含这些分隔符导致提取失败，现统一使用模型字段避免此问题

---
## v0.5.0 (2026-07-07)

### 新增
- **候选召回模块** (`matching/`)：三路召回 + 统一排序，为每条 EoICD 条目匹配 Top K HLR
  - `rule_matcher.py` — 结构化字段匹配（信号名 token 重合、总线匹配、属性关键词、设备名，最高 80 分）
  - `alias_matcher.py` — 关键词/别名匹配（`synonyms.yaml` 驱动，中文/长别名 str.replace，短 ASCII 别名词边界 \b 替换，+15/别名，上限 60 分）
  - `text_matcher.py` — BM25 文本匹配（中英混合分词，Okapi BM25 k1=1.5 b=0.75，得分归一化 0-60）
  - `candidate_matcher.py` — 多路召回编排，按 HLR ID 去重取 max score，统一排序取 Top K
- **对比裁判模块** (`comparison/`)：
  - `case_builder.py` — ComparisonCase 构造（EoICD + Top K HLR + match_evidence）
  - `semantic_judge.py` — DeepSeek API 语义裁判（中文 ICD 审查专家 SYSTEM_PROMPT，5 种覆盖状态，60s 超时，最多 2 次重试，异常 fallback needs_review）
  - `report_generator.py` — 差异报告生成（总体统计 + 差异明细 JSON，covered 不进明细表）
- **别名映射表** `synonyms.yaml`：4 大类 ~30 条 canonical 条目（总线类型、设备/组件、信号/字段、通用术语）
- **API 配置模板** `.env.example`：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
- **调试日志** `DEBUGLOG.md`：记录管线调试过程中发现的 5 个问题及处理状态
- **CLI 新增 4 个子命令**：`match` / `judge` / `report` / `analyze`（一键全流程）
- **`analyze` 命令**：parse → match → judge → report 全流程自动化，所有中间 JSON 持久化，支持 `--limit` 和 `--top-k` 参数

### 变更
- `config.py` 追加：python-dotenv 环境加载、`MATCH_SCORE_WEIGHTS` 评分权重、`LOW_SCORE_THRESHOLD`、`DEFAULT_TOP_K`、`DEFAULT_LIMIT`、BM25 参数、`load_synonyms()` 函数
- `models.py` 追加 6 个 Pydantic 模型：`MatchCandidate` / `ComparisonCase` / `JudgmentResult` / `DifferenceReport` / `MatchOutput` / `JudgmentOutput`
- `requirements.txt` 新增依赖：python-dotenv、requests、pyyaml

---
## v0.4.0 (2026-07-07)

### 新增
- **CLAUDE.md 项目治理文档**：包含开发要求、职责边界、文件权限、跨对话框接续规则、编码约定、变更范围控制
- **CHANGELOG.md**：本文件，明确变更记录的范围、颗粒度和规则

### 变更
- **EoICDRequirement 模型字段调整**：
  - `entry_id` → `ird_id`，格式 `IRD-{bus}-{layer_abbr}-{seq:04d}`，layer_abbr 不足 6 字符时不填充
  - `signal_name` 保留于模型内但 `exclude=True`（不序列化到 JSON，仅供内部去重）
  - `bus_type` 与 `sheet_name` 顺序互换
  - 移除 `interface_name` 字段（无下游用途）
  - `source` 简化为 `"Publisher Table"` / `"Subscriber Table"`
- **去重键增加 `is_dp_ref` 维度**：防止 Publisher DP 条目误吃 Subscriber dp_ref 条目
- **帧信号关键词改为全小写匹配**：`FRAME_SIGNAL_KEYWORDS` 全部 lowercase，`is_frame_signal()` 中 token 也 lowercase 后再比对

### 修复
- **去重率显示异常**：新增 `total_generated` 计数器，展示"生成总量 → per-sheet 去重 → 全局去重"三段统计
- **dp_ref 条目被全局去重误删**：Publisher DP 条目和 Subscriber dp_ref 条目的去重键相同导致后者被丢弃，通过 `is_dp_ref` 加入去重键解决
- **大写 Label/SDI/SSM 未被过滤**：帧信号关键词集大小写不一致导致大写变体漏过 Rule 9 过滤

---

## v0.3.0 (2026-07-06)

### 新增
- **HLR Word 解析器** (`parsers/hlr_word_parser.py`)：
  - 解析 .docx 中表格：表 0 提取为术语表（Glossary），表 1+ 提取为需求表
  - 需求表按固定 8 行 × 2 列结构：需求ID、需求中文、对象类型、是否衍生、基本原理、安全相关、验证方法、实现方法
  - 输出模型 `HLRRequirement` / `HLRGlossaryEntry` / `HLROutput`
- **CLI `parse-hlr` 子命令** 和 **`all` 批量命令**
- **`requirements.txt`** 依赖声明（openpyxl, python-docx, pydantic）

---

## v0.2.0 (2026-07-05)

### 新增
- **EoICD Excel 解析器** (`parsers/eoicd_excel_parser.py`)：
  - 支持 Publisher + Subscriber 双文件输入，合并输出单一 JSON
  - Row1 侧边界检测（Publisher/Subscriber 标记列）
  - Row2 层级块检测、Row3 属性名读取
  - 逐数据行解析 Publisher 块和 Subscriber 块
  - **Rule 1**：信号名层级拼接（相邻同名去重，`.` 连接，含叶节点名）
  - **Rule 2**：排除属性过滤（Name, Guid, FullName, ATA 等 9 项）
  - **Rule 3**：属性中文映射（~40 条），无映射 fallback 英文原名
  - **Rule 4**：描述模板 — RP 自身 `{signal}的{attr}应为{value}{unit}`，dp_ref `{signal}接收的{attr}应为{value}{unit}`
  - **Rule 5**：单位自动追加（时序→ms，大小→Bytes，位宽→Bits）
  - **Rule 6**：两层去重（per-sheet 行间 + 全局跨 sheet）
  - **Rule 7**：空值跳过（None/空串/空白）
  - **Rule 8**：Subscriber dp_ref 提取（从同行 Publisher DP 提取 ParameterSize、BitOffsetWithinDS）
  - **Rule 9**：帧结构信号过滤（DP Name 含 Label/SDI/SSM/PARITY 等关键词时跳过整行）
  - 仅提取叶节点（DP/RP）层属性
- **Pydantic v2 数据模型** (`models.py`)：`EoICDRequirement` / `EoICDOutput`
- **常量配置** (`config.py`)：属性中文映射表、单位规则表、排除属性集、帧信号关键词
- **CLI `parse-eoicd` 子命令**

---

## v0.1.0 (2026-07-04)

### 新增
- 项目目录结构初始化：`backend/app/parsers/`、`backend/app/config.py`、`backend/app/models.py`、`backend/app/main.py`
- argparse CLI 框架（main.py）
- `ref_file/` 和 `doc_input_file/` 目录就位
