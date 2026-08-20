# HSCU Profile

液压系统控制单元（Hydraulic Servo Control Unit, HSCU）。

## 来源

V4 反向管线的第三个 controller 测试样例，位于 `test-input/HSCU裁剪需求/`。

## HLR Word 文档结构

- Table 0：信号标签表（11 行 × 8 列：缩写 / 英文名 / 中文名 / 数据类型 / 单位 / 范围 / 备注 等）
- Tables 1..N：每个需求 8 行 × 2 列
- 行标签固定：`需求ID` / `需求正文` / `对象类型` / `是否衍生` / `基本原理` / `安全相关` / `验证方法` / `实现方式`
- 与 AMS 的核心差异：需求正文字段名是 **`需求正文`**，AMS 是 `需求中文`；HSCU 无 `是否为需求` 列（不过滤）
- 不开启 GBK mojibake 修复：HLR docx 是干净 UTF-8

## EoICD Excel 结构

PubSub 格式，sheet 命名沿用 `A664-RP` / `A825-RP` / `A429-RP` / `Analog-RP` / `Discrete-RP`，ASCII-only，不需要修复。

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