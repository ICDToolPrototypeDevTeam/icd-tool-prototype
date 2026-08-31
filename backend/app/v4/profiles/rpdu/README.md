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