# AMS Profile

空气管理系统控制器（Air Management System Controller, AMSC）。

## 来源

V4 反向管线 Issue A 期间唯一测试样例；本配置从 `parsers/hlr_word_parser.py` 与 `traceability/trace_parser.py` 中硬编码的字面值 1:1 抽取。

## HLR Word 文档结构

- Table 0：缩略语表（3 列：缩写 / 英文全名 / 中文名称）
- Tables 1..N：每个需求 8 行 × 2 列
- 行标签固定：需求ID / 需求中文 / 对象类型 / 是否衍生 / 基本原理 / 安全相关 / 验证方法 / 实现方法

## EoICD Excel 结构

PubSub 格式，sheet 命名固定 `A664-RP` / `A825-RP` / `A429-RP` / `Analog-RP` / `Discrete-RP`。

## 追溯表

- Table 1：`设备需求与系统ICD追溯表.xlsx`，sheet[1] = "设备_设备接口追溯表"
  - Col D (3) = ERD编号，Col H (7) = ICD FullName
- Table 2：`单模块需求矩阵分析（设备2软件高层）-裁剪.xlsx`，sheet[0] = "Sheet1"
  - Col A (0) = ERD编号（fill-forward），Col D (3) = 下级需求编号（HLR ID），Col E (4) = 下级模块名称
  - 跳过 module_name = "EICD" 的行
