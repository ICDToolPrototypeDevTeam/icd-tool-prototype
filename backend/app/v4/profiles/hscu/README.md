# HSCU Profile

液压系统控制单元（Hydraulic Servo Control Unit, HSCU）。

## 来源

V4 反向管线的第三个 controller 测试样例，位于 `test-input/HSCU裁剪需求/`。

## HLR Word 文档结构

新版本 `HSCU软件高层需求-裁剪.docx`（含两个 LBL 总览表）：

- **Table[0]**：RDCU1 入站 LABEL 总览表（11 行 × 8 列）
  - 列布局：`序号 | LABEL名称 | LABEL号 | SDI号 | 通道 | 信号名称 | 刷新率 | SSM`
  - LABEL 名称带 `_R1` 后缀（如 `LBL_AIR_SPEED_FCM1_R1` → 206）
  - col 5「信号名称」是**多行 cell**：每行一个独立 signal name（如 `ABV1_CB_CLOSED_RPDU_R1`、`ABV1_LOAD_VOLT_AVAIL_RPDU_R1`），这些 signal 由同一 octal  承载
- **Table[8]**：HSCU 出站 LABEL 总览表（12 行 × 4 列）
  - 列布局：`LABEL名称 | LABEL号 | SDI号 | 通道一致性`
  - LABEL 名称**无** `_R1` 后缀（如 `LBL_DIS_03_INFO` → 140）
- **Tables [1, 2, 3, 5, 6, 7, 9, 10, 11, 12]**：每个需求 8 行 × 2 列
  - 行标签固定：`需求ID` / `需求正文` / `对象类型` / `是否衍生` / `基本原理` / `安全相关` / `验证方法` / `实现方式`
- 与 AMS 的核心差异：需求正文字段名是 **`需求正文`**，AMS 是 `需求中文`；HSCU 无 `是否为需求` 列（不过滤）
- 不开启 GBK mojibake 修复：HLR docx 是干净 UTF-8

旧版本只有 Table[0] 一张 RDCU1 表 + 需求表，hook 现在已自适应新版本的两表结构。

## EoICD Excel 结构

PubSub 格式，sheet 命名沿用 `A664-RP` / `A825-RP` / `A429-RP` / `Analog-RP` / `Discrete-RP`，ASCII-only，不需要修复。Subscriber `A664-RP` 中包含 RDCU1 → HSCU 的入站 ARINC-429 标签，编码形式为 `L<octal>_<signal_name>_<通道后缀>`（如 `L051_ABV1_LOAD_VOLT_AVAIL_RPDU_R1A`）。

## 追溯表

### Table 1：`附件1：需求与ICD追溯表 - HSCU-EOICDREVA-1.0.xlsx`

- Sheet 名 `待填_需求接口追溯表`
- Col D (3) = ERD编号，Col H (7) = ICD FullName

### Table 2：`液压-单模块需求矩阵分析-设备2软件.xlsx`

- Sheet `Sheet1`，干净 UTF-8
- 50 行 × 7 列
  - Row 1：`当前需求文档` / `下层需求文档`（合并表头）
  - Row 2：`需求编号 | 模块名称 | 需求内容 | 需求编号 | 模块名称 | 需求内容 | 链接类型`
  - Row 3+：实际数据
- Col A (0) = ERD编号（fill-forward），Col D (3) = 下级需求编号（HLR ID），Col E (4) = 下级模块名称
- **不过滤任何 module**：HSCU 的下级模块统一为"液压系统控制单元控制软件高层需求规范"，
  与 AMS 的"EICD 跳过"语义不同

## 验证

- 端到端 API 测试：`POST /api/v4/coverage-analysis` 带 `controller_profile=hscu`，4 DOCX 输出 + traceability 命中
- 回归：AMS + FGMC 全流程回归，确保无破坏

## HLR 预处理 Hook（HSCU 专用）

### 问题

HSCU HLR Word 文本使用符号化标签名（`LBL_DIS_00_SYS1`），而 EoICD PubSub 块以八进制标签号编码（`L145_DIS_00_SYS1_T1A`）。反向匹配 Stage1 的 prefix filter（`LBL_DIS_00_SYS1/`）永远命中 0 EoICD 块，导致 HSCU 0/10 匹配。

新文档里 source-select 需求（023194、022645）还会在正文中以**裸 signal name**（无 `LBL_` 前缀）引用 RDCU1 入站信号（如 `ABV1_LOAD_VOLT_AVAIL_RPDU_R1`、`AIR_SPEED_FCM1_R1`）。这些 signal 在 EoICD 里编码为 `L<octal>_<signal_name>_<通道>`（如 `L051_ABV1_LOAD_VOLT_AVAIL_RPDU_R1A/R1B/R2A/R2B`），hook 也需要给它们追加 alias。

### 解决方式

`pipeline._parse_hlr()` 解析完 HLR 后调用 `apply_hlr_preprocess_hook()`，由 HSCU 的 `hooks.py:preprocess_hlr_requirements()` 实现 LBL→L<octal> 别名追加。

`pipeline._parse_hlr()` 在 hook 调用期间临时把 `result.source_file` 切到完整 `input_path`，hook 调用结束后恢复 basename — 保证 hook 能用绝对路径 re-open Word，但 JSON 输出和 AMS/FGMC 行为一致仍保留 basename。

### 自动 catalog 提取（`auto_parse_hlr_table_0`）

`hooks.py:_extract_label_mappings()` 不依赖固定 table index，按启发式扫描所有 table，自动识别 LBL 总览表（≥ 3 列 + ≥ 2 行 + 至少一行同时包含 `LBL_*` cell 和 ≥ 2 位数字 octal cell）。识别到的 table 全部合并入 mapping，文档顺序在前的优先（HSCU 的 Table[8] 在 Table[0] 之后，所以 HSCU 的无 `_R1` 后缀形式会覆盖 RDCU1 的 `_R1` 形式）。

每个 LBL 行提取两类映射：
- **catalog 名称**：`(LBL_<NAME>_R1, LBL_<NAME>) → octal`（向后兼容旧 RDCU1 文档的 `_R1` 后缀）
- **信号名称**（仅 8 列 RDCU1 catalog 适用，col 5）：col 5 多行 cell 中每个符合 `^[A-Z][A-Z0-9_]{4,}$` 的 token 都作为 key，value 为该行 octal。这样 `ABV1_LOAD_VOLT_AVAIL_RPDU_R1 → 51` 这类裸 signal name 也能命中。

Octal 容许 2-3 位数字：HSCU 文档常省略前导 0（如 `74`、`51`），但 1 位数字仍被排除（避免与 SDI `0/1/2/3` 混淆）。

生成的 alias 形式为 `L<3位octal>_<NAME>`（如 `L051_ABV1_LOAD_VOLT_AVAIL_RPDU_R1`）。3 位左填充是必要的：reverse_matcher Stage1 prefix filter 直接用 `L<label>/` 匹配 EoICD block key，EoICD 的 block key 始终是 3 位形式，缺前导 0 时前缀不匹配。

### 当前映射

| 来源 | 数量 | 说明 |
|---|---|---|
| `extra_mappings` (YAML) | 1 | `LBL_DIS_00_SYS1 → 145`（兜底/覆盖） |
| auto_parse Table[0] (RDCU1 8-col) | 11 catalog 名称 + 19 信号名称 | 含 2 位 octal (`74/77/75/76/51`) |
| auto_parse Table[8] (HSCU 4-col) | 12 catalog 名称 | 全部 3 位 octal |
| **合计** | **56 entries** | |

### 别名追加规则（按 requirement 范围）

- 两种 token 都会被扫描：`LBL_<NAME>_(SSM)?` 和裸 signal token（仅当裸 token 直接命中 mapping 时）
- 仅对实际出现且已映射的 token 才追加别名
- 自动剥离 `_SSM` 后缀再查 mapping
- 占位值（`???` / `?`）跳过
- 幂等：重复执行不会重复追加
- Alias 中的 octal 统一左填充到 3 位以匹配 EoICD block key

### 实测匹配（job `a54aab93`，新文档 + 真实 LLM）

| HLR | 内容要点 | 匹配状态 | 备注 |
|---|---|---|---|
| 022587 | `LBL_DIS_00_SYS1` | 已匹配 | hook + extra_mappings |
| 023389 | `LBL_DIS_00_SYS1_SSM` | 已匹配 | hook + extra_mappings |
| 023507 | `LBL_DIS_03_INFO` | 已匹配 | hook auto_parse Table[8] |
| 023124 | `LBL_QTY_SYS2` | 已匹配 | hook auto_parse Table[8] |
| 023194 | 裸 `ABV1_LOAD_VOLT_AVAIL_RPDU_R1` | 待确定 | hook auto_parse Table[0] col 5 + 3 位 octal 填充 |
| 022645 | 裸 `AIR_SPEED_FCM1_R1` | 待确定 | hook auto_parse Table[0]（LBL 名称 col 1 同时也是裸名） |
| 025797 / 025798 | 占位 `LBL_XXX` | 无匹配 | HSCU HLR 文档未填写 |
| 022995 / 022996 | `LBL_CMD1_OHMS` | 无匹配 | 该标签不在两张 catalog 中（需用户提供 R1 → CMD1_OHMS 映射或文档补充） |

**汇总：matched + pending = 6/10**（job `a54aab93`：`hlr_已匹配=4, hlr_待确定=2, hlr_无匹配=4`）。

### 不修改范围

- `reverse_matcher.py` / `hlr_classifier.py` / `hlr_labeler.py` / `trace_parser.py` 不改动
- `pipeline._parse_hlr()` 的改动仅在 hook 调用窗口临时切换 `result.source_file`，写出的 JSON 与 AMS/FGMC 行为一致（仍为 basename）
- AMS / FGMC profile 不受影响：`hlr_preprocess.enabled` 默认为 `False`，hook 路径不会被触发
- LLM prompt / 评分规则不变

### 验证

- 内联测试：直接调用 `_extract_label_mappings` 解析新 HSCU Word → 56 个 mapping（含 12 个 `ABV1_*` signal）
- Inline pipeline 测试：`_parse_hlr(input_path, output, profile=hscu)` → 6/10 requirements 被 rewrite
- Docker 重建 + HSCU 真实 LLM E2E (job `a54aab93`)：`hlr_已匹配=4, hlr_待确定=2, hlr_无匹配=4`，6 个 HLR 都拿到了 EoICD block key
- AMS (job `082b4a48`) + FGMC (job `ed36e75c`) 回归：0 个 alias annotation，auto_parse 默认 False 不被触发，行为完全保持