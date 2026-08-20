# FGMC Profile

燃油测量管理计算机（Fuel Gauging and Management Computer）。

## 来源

Issue #63 引入的第二个测试样例，位于 `test-input/工具-FGMC控制器测试案例/`。

## HLR Word 文档结构

- Table 0：术语定义表（2 列：术语 / 定义）
- Table 1：缩略语表（3 列：缩略语 / 英文全称 / 中文全称）
- Tables 2..N：每个需求 13 行 × 2 列
- 行标签：ID / 需求编号 / 需求描述 / 是否为需求 / 是否安全性相关 / 是否衍生 / 需求原理 / 需求来源 / 覆盖的需求ID / 验证方法 / 注释 / 输入数据 / 输出数据
- **"是否为需求" = "否" 的行必须过滤**

## EoICD Excel 结构

与 AMS 完全一致：PubSub 格式，sheet 命名 `A664-RP` / `A825-RP` / `A429-RP` / `Analog-RP` / `Discrete-RP`。

## 追溯表

- Table 1：文件名 glob `*追溯*.xlsx` 或 `*接口基线*.xlsx`
  - 优先 sheet 名含"追溯"/"接口基线"/"接口基线表_EoICD"/"待填_需求接口追溯表"；fallback 第 0 个 sheet
  - Col D (3) = ERD编号，Col H (7) = ICD FullName
- Table 2：文件名 glob `*矩阵分析*.xlsx` 或 `*需求矩阵*.xlsx`
  - 优先 sheet 名含 "Sheet1" 或 "矩阵"；fallback 第 0 个 sheet
  - Col A (0) = 当前需求编号（父需求），Col D (3) = 下层需求编号（HLR ID），Col E (4) = 下层模块名称
  - 不跳过任何 module（FGMC 不一定含 EICD 概念）
