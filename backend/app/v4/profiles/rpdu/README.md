# RPDU Profile

远程功率分配单元（Remote Power Distribution Unit, RPDU）控制器。

## 来源

Issue #74 多控制器适配：将 RPDU 本地分支适配代码合并到 V4 主线，所有改动集中在本 profile 目录，不污染 AMS / FGMC / HSCU 现有行为。

## HLR 输入格式

Excel 工作簿（`.xlsx`），由 `parsers/hlr_excel_parser.py` 的 `HLRExcelParser` 解析：

| 列 | 字段             | 示例                |
|----|------------------|---------------------|
| A  | `requirement_id` | 需求编号（RPDU-…） |
| B  | `implementation` | 模块名称           |
| C  | `content`        | 需求内容           |

布局假设：
- Sheet 1（按文件名 / 顺序取第一张）
- 行 1 = 标题行（合并，丢弃）
- 行 2 = 列头（丢弃）
- 行 3+ = 数据

Excel 格式无缩略语表，`glossary` 字段保持空列表。

## 追溯表

`trace_strategy: header_adaptive` 切换到 `trace_parser._read_table*_header_adaptive`：

- **Table 1（ERD ↔ ICD FullName）**：`配电系统需求与EoICD追溯表*.xlsx`
  - 列定位靠中文关键字扫描（`erd编号`/`ICD FullName`），不依赖列索引。
- **Table 2（ERD ↔ HLR）**：`单模块需求矩阵分析*.xlsx`
  - 列定位同样靠关键字扫描，支持 `cfg.skip_module` 过滤。

其他 profile 保持原 `profile_columns` 路径（AMS/FGMC/HSCU 字节一致）。

## 反向匹配增强

`matcher` 段启用全部 4 项 RPDU 专属增强：

| 开关                                  | 默认（其他 profile） | RPDU |
|---------------------------------------|---------------------|------|
| `enable_cn_suffix_strip`              | `false`             | `true` |
| `enable_direction_soft_on_exact_signal` | `false`           | `true` |
| `enable_signal_number_bonus`          | `false`             | `true` |
| `top_k`                               | `20`                | `50` |

关闭 `matcher` 段或设为默认 → 行为与 v4 #63 之前完全一致。

## 追溯预过滤：per-HLR 池（Issue #74 RPDU 专属）

RPDU 的 EoICD 是多 HLR 共享的「设备 ICD」结构——同一份 EoICD Excel 同时承载几十个 HLR 的接口信号。如果沿用 v4 #63 的 union-pool 预过滤（把所有 HLR 命中的 ERD→ICD 块并集后再做 reverse match），则 8 个 traceable HLR 共同贡献 ~2471 个 EoICD block，top_k=50 会被无关的 LRM / 状态类信号淹没，导致像 `Heater_Group_X_RPDU_ESW_CMD` 这样的真正目标信号进不了候选集。

修复开关 `prefilter_per_hlr: true`（RPDU profile 专属，默认 `False`）：

```yaml
# Issue #74 (RPDU): run Group A matching per-HLR with each HLR's own
# traced blocks. Prevents other-HLR blocks from polluting top_k=50
# (e.g. LRM status signals drowning out heater ESW_CMD signals).
prefilter_per_hlr: true
```

行为差异：

| 模式 | 触发条件 | 行为 |
|------|---------|------|
| **union-pool**（AMS/FGMC/HSCU 默认） | `prefilter_per_hlr: false` 或字段缺失 | 把所有 Group A HLR 命中的 EoICD block 做并集，一次性送入 reverse match。日志：`Filtered EoICD: X / Y entries` |
| **per-HLR pool**（RPDU 专属） | `prefilter_per_hlr: true` | 每个 HLR 只在自己的 traced block 集合上跑 reverse match。日志：`Per-HLR filtered EoICD total: X / Y entries` |

实现细节：

- 新增函数 `pipeline.match_reverse_per_hlr()`（`backend/app/v4/pipeline.py`），对每个 HLR 单独调用 `match_reverse(profile=profile)`，保留 RPDU 全部 4 项 matcher 增强。
- 字段定义在 `ControllerProfile.prefilter_per_hlr: bool`（`backend/app/v4/profiles/base.py`），`from_dict` 中读 `data.get("prefilter_per_hlr", False)`，因此 AMS/FGMC/HSCU 不声明该字段时自动回落到 `False`，字节一致。
- 触发位置：`pipeline._match_reverse_with_trace()` 根据 `profile.prefilter_per_hlr` 分流。
- 兜底：per-HLR 池跑完仍为「无匹配」的 HLR，会自动 fallback 回全量 EoICD 跑一遍（与 union-pool 共享同一条 fallback 路径）。

回归验证：

- RPDU：`HLR_052331` 修复前 top-50 全是 LRM 状态信号，修复后 14 个 `Heater_Group_*_RPDU_ESW_CMD` 候选；`Group A` 由 0/8 提升到 6/8（+fallback 8/8）。
- AMS / FGMC / HSCU：上传追溯表后日志仍为 `Filtered EoICD: ...`（union-pool），行为与 Issue #74 之前字节一致。

## 与其他 profile 的关系

RPDU 是首个非 `.docx` HLR 的 profile；`create_hlr_parser` 工厂函数按扩展名分发，RPDU 的 `hlr_parser_driver.driver: xlsx` 让其走 `HLRExcelParser` 路径。其他 profile 继续走 `HLRWordParser`，互不影响。

`HLRExcelParser` 与 `HLRWordParser` 都返回标准 `HLROutput`，下游（labeling / matching / judging）完全 parser-agnostic。

## 自动识别（API 入口，Issue RPDU 适配续）

`POST /api/v4/coverage-analysis` 在用户不传 `controller_profile` 时走 `coverage.py::_detect_system_type` 自动识别。原 detector 仅支持 `python-docx` 打开 Word 表，对 RPDU xlsx 直接抛 `PackageNotFoundError` → 接口 500。本节为 RPDU 自动识别补 detector 基础设施 + RPDU 专属规则。

### 加载层扩展（基础设施）

`_detect_system_type` 拆出 `_load_hlr_tables(hlr_path)` helper，按扩展名分发 `.docx`（`python-docx`）与 `.xlsx`（`openpyxl`），统一为 `list[list[list[str]]]`；`_match_auto_detect` 形参改为新数据结构，并新增 `min_rows` 字段（≥N 语义，与原 `required_rows` 的 ==N 精确语义并存）。基础设施下沉后，未来任何 xlsx profile 只需在 `config.yaml` 写 `auto_detect` 段即可接入 detector，`coverage.py` 无需修改。

### RPDU `auto_detect` 规则

```yaml
auto_detect:
  min_rows: 4                 # 故障注入样本 13 行 / 全量样本 581 行，无法用单一精确 required_rows
  required_cols: 7            # 与 3 个 .docx profile 的 2 列天然隔离
  cell_patterns:
    "0":
      row: 2                  # Excel 行 3 = 第一行数据（行 1=标题 / 行 2=表头 / 行 3+=数据）
      starts_with: "FSF24"    # RPDU controller number 前缀
```

选用 FSF24 前缀（`col 0 row 2 starts_with`）而非「需求编号/模块名称」列头文本的原因：列头为通用中文关键字不可靠；FSF24 是 RPDU 项目控制器唯一标识前缀，与其他系统的 controller-distinguishing prefix 模式对齐（AMS 用 FSF21、HSCU 用 FSF29、FGMC 用 FGMC）。

### 兼容性

- AMS / FGMC / HSCU / FSECU：未声明 `auto_detect` 或保持原 `required_rows` 精确语义，行为字节不变。
- RPDU Word 模板（暂未提供）：不会被本规则误匹配（Word 表与 Excel 加载层数据结构不同），按「无法识别 HLR 文件所属系统类型」错误处理，需手动指定 `controller_profile`。

### 验证

inline `_detect_system_type` 校准：3 个 RPDU 真实样本（`RPDU软高需求.xlsx` 581 行 / `RPDU软高需求_未注入故障v1.xlsx` 13 行 / `RPDU软高需求_注入故障v1.0.xlsx` 13 行）全部识别为 `rpdu`；27 个 .docx 测试样本（AMS / FGMC / HSCU）回归无破坏；合成 xlsx（FSF21 7 列 / FSF24 5 列）均不误匹配。

## refine 后处理（Step 3.5，RPDU 专属）

RPDU profile 在 `pipeline.run_reverse_pipeline(refine=True)` 时，会在 Step 3 反向匹配后、Step 4 多智能体裁判前，额外跑一遍 `backend/app/v4/refine/` 子包的「无关 block 过滤 + 精确补采 + 同义词补采」三步精化。

**触发条件**：

- 仅 RPDU profile 启用：`profile.profile_id == "rpdu"` 且 `no_refine=False`
- API 入口：`POST /api/v4/coverage-analysis` form 字段 `no_refine=true` 可关闭
- CLI 入口：`reverse-analyze --no-refine` 等价形参

**模块职责**：

| 模块 | 职责 |
|------|------|
| `refine/block_filter.py::filter_matched_blocks` | 接收 `match_result` + `hlr_labels` + `block_index`，输出与 `reverse_matches.json` 同构的过滤+补采后 `ReverseMatchOutput` |
| `refine/runner.py::run_pipeline_refined_stage` | 重建完整 ICD Block 索引 → 过滤+补采 → 覆盖写盘 `reverse_matches.json` → 用过滤后匹配重建 cases → 返回 `(new_match_result, new_cases)` 供 Step 4-6 复用 |

**三步精化设计**：

1. **无关 block 过滤**：对 matched ICD Block 做 leaf 信号名精确比对——剥离 `RX_/TX_/DS_*` 方向/数据源前缀后与 HLR 中出现的信号名（含 HLR label 的 signal_keywords）做精确相等/子串判定，仅保留与该 HLR 强相关的 block。
2. **精确补采**：当传入完整 ICD Block 索引时，额外补回完整 ICD 中同名但未被 top-N 候选覆盖的 block，消除「匹配层 top_k=50 漏采导致误判需确认」的问题。
3. **同义词补采**：通过 `synonyms.yaml` 的 `Airspeed` 等 canonical_term 别名组覆盖 HLR 中以同义词形式引用的信号（如 `空速 / 空速信号 / AIRSPEED / Airspeed / airspeed / Air_Speed / air_speed`），让原本被遗漏的同名 ICD block 也能进入候选集。

**与现有 pipeline 的衔接**：

- 精化分支不调 LLM、不生成共识/报告；返回 `(new_match_result, new_cases)` 后由 pipeline 主干继续走原 Step 4-6（多模型并发 + drain + degradation + 5 星共识 + re_review）。
- 5 星体系、ADR-004 多模型共识、re_review 机制、降级保护均保持不变。
- `refine=False`（默认或被 `no_refine=true` 关闭）时，`pipeline.run_reverse_pipeline` 走原 Step 3 → Step 4 链路，行为与 RPDU refine 引入前字节一致。
- 其他 profile（AMS / FGMC / HSCU / FSECU）的 `pipeline.run_reverse_pipeline(refine=...)` 调用永远传入 `refine=False`，行为字节不变。

**回归验证**（真实 LLM E2E，job `8e6498ab`）：

- 11 条 HLR 的反向匹配数与同事代码 reference case04 完全一致（8/11/1/5/7/9/4/4/4/6/7）。
- 5 星分布平均 4.45：9 个 5★ + 1 个 3★ + 1 个 1★，无 4★/2★。
- `re_review_results.json` 复查触发 REV-0008，split → 待确认。
- AMS / FGMC / HSCU 回归：未走 refine 分支，行为字节不变。

**开关 / 关闭方式**：

- 关闭（与最初版 RPDU 行为对齐做 A-B 对照）：
  - CLI：`--no-refine`
  - API：multipart form 字段 `no_refine: true`
- 开启（默认）：不传 `no_refine` 即可。
- 非 RPDU profile 传入 `no_refine` 字段被忽略。