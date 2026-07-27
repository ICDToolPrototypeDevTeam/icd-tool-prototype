# Debug Log

> **Note:** This file was migrated from root `DEBUGLOG.md` on 2026-07-20.

## 2026-07-07 — Minimum Pipeline E2E Run

### 1. Alias Matcher: `_expand` substring corruption (FIXED)

**现象**: `_expand()` 使用 `str.replace(alias, canonical)` 无条件替换，短 ASCII 别名（`s`→`秒`, `can`→`a825`）会破坏包含这些字符的单词。
- `"refreshperiod"` → `"refre秒hperiod"`
- `"scanner"` → `"sa825ner"`

**修复**: `_expand` 改为对长度 ≤3 的 ASCII 别名使用 `re.sub(r'\b' + re.escape(alias) + r'\b', ...)` 词边界替换；中文及长别名保持 `str.replace`（无碰撞风险）。

**相关文件**: `backend/app/matching/alias_matcher.py:73-89`

---

### 2. Alias Matcher: `matched_fields` 重复条目 (FIXED)

**现象**: 同一 canonical key 有多个别名时（如 `fan`, `FAN`, `风扇` → canonical `fan`），`matched` 列表产生重复 `['alias:fan', 'alias:fan', 'alias:fan']`。

**修复**: 构建 `MatchCandidate` 前 `matched = list(set(matched))` 去重。

**相关文件**: `backend/app/matching/alias_matcher.py:54`

---

### 3. CLI: Windows 反斜杠参数解析 (FIXED)

**现象**: `--output-dir output\` 末尾反斜杠在 bash/windows 环境下被解释为转义符，导致 `--top-k 5` 被解析为参数值 `5` 而非选项，报 `unrecognized arguments: 5`。

**修复**: 路径去掉尾部反斜杠，使用 `--output-dir output`。

---

### 4. Alias Matcher: 短别名导致虚假匹配 (KNOWN, NOT FIXED)

**现象**: 评分循环中 `original in eoicd_norm` 的 Python 子串检查，使得单字母别名 `s` 几乎在所有英文文本中都"命中"，导致 alias_count 虚高。

**影响**: 评分偏高但不影响排序（所有 EoICD 都受到同等程度的虚高）。在 32 条 HLR 小数据集上实际影响有限。

**后续建议**: 若扩展别名表或增大 HLR 规模，考虑将评分循环中的 `original in text` 也改为词级别匹配。

---

### 5. Terminal: 中文显示乱码 (HARMLESS)

**现象**: Windows 终端（cp936 编码）下 `print` 输出中文为 `������`。

**确认**: JSON 文件使用 `ensure_ascii=False` + UTF-8 编码，数据正确。仅终端渲染问题，不影响功能。

---

## 2026-07-08 — v0.6.0 Parser All-Layer Extraction & signal_name Externalization

### 6. Rule Matcher: `_extract_signal_name()` from description was fragile (FIXED)

**现象**: `_extract_signal_name()` 从 description 文本中解析信号名，仅以 `的` 和 `接收的` 作为分隔符。非叶节点层级（Software、SubSoftware 等）的 description 模板 `{signal}的{attr}应为{value}` 在 signal 含多段 `.` 路径时仍可正确分割，但该逻辑依赖隐式约定，缺乏鲁棒性。

**修复**: 移除 `_extract_signal_name()`，直接使用 `EoICDRequirement.signal_name` 模型字段。v0.6.0 将 `signal_name` 的 `exclude=True` 移除后，该字段已在解析阶段构建并可供所有下游模块使用。

**相关文件**: `backend/app/matching/rule_matcher.py:9-19`（已删除）

---

### 7. Parser: Leaf-only extraction missed intermediate layer attributes (FIXED)

**现象**: `_extract_leaf_requirements()` 仅从叶节点（DP/RP）提取属性，中间层级（Software、SubSoftware、PubSubGroup 等）的属性被跳过，导致部分 ICD 信息丢失。

**修复**: 重构为 `_extract_layer_requirements()`，遍历所有层级逐层生成条目，每个层级的 signal_name 为该层到 Software 的路径。条目数量预期显著增加。

**相关文件**: `backend/app/parsers/eoicd_excel_parser.py:143-214`

---

## 2026-07-23 — 反向匹配 bit_field 维度评分修复

### 8. bit_field 维度评分为 0（已修复）

**现象**: 反向匹配 6 维度评分中，15 个已匹配/待确定 HLR 中有 9 个 bit_field=0，包括 HLR 文本明确提到 bit 位置的情况（如 `bit15=1`、`bit10=1`、`bit21至bit28`）。

**复现**: 运行 `reverse-analyze`，检查 `reverse_matches.json` 中 `top_scores[].dimensions.bit_field` 值。

**根因**: 三个独立问题共同导致：
1. **正则缺失**: `extract_bit_fields()` 仅匹配 `bitX至bitY`（范围）和 `bitX为description`（单 bit 描述）。HLR 文本中常见的 `bitX=value`（赋值）、`bitX的`（所有格）模式未被覆盖。
2. **Profile 属性信息丢失**: `build_profiles()` 的"先到先得"语义下，多 bit 标签中每个 profile 仅保留第一个 dp_ref 条目的 BitOffsetWithinDS 值。
3. **sub_signals 未被评分使用**: `build_blocks()` 已正确计算 `block.sub_signals`（含完整 bit_offset/size/dtype），但 `_score_block()` 仅查询 `prof.attributes`，未使用 `block.sub_signals`。

**修复**:
1. 在 `hlr_classifier.py` 新增两个正则：
   - `_BIT_ASSIGN_RE = r"bit(\d+)\s*="` — 捕获 `bit15=1`、`bit8=0` 等赋值格式
   - `_BIT_POSSESSIVE_RE = r"bit(\d+)\s*的"` — 捕获 `bit10的` 所有格格式
2. 在 `_score_block()` 添加 Pass 2：profile 属性未匹配时，回退检查 `block.sub_signals` 中的 bit_offset/size。

**涉及文件**:
- `backend/app/matching/hlr_classifier.py:20-22` — 新增正则定义
- `backend/app/matching/hlr_classifier.py:83-95` — 新增两个匹配循环
- `backend/app/matching/reverse_matcher.py:283-316` — bit_field 评分新增 sub_signals Pass 2

**验证**: 重新运行反向匹配后，bit_field=0 的 HLR 从 9 个降至 3 个（3 个确实无 bit 引用，正确）。6 个修复的 HLR 中：3 个获满分 20（HLR_4276、HLR_473、HLR_547），3 个获部分分 8（HLR_267、HLR_278、HLR_544）。完整管线 DeepSeek 真实 LLM 验证通过（test_v8，共识星级 2.1）。
