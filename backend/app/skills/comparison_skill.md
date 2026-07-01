# EoICD 条目化需求与软件高层需求对比 Skill

## Overview

对比 EoICD 需求条目化结果（IRD — 从 EoICD Publisher/Subscriber 接口表中自动提取的原子化需求，以自然语言需求语句形式输出）与软件高层需求（SWHLR — 从 SWHLR 文档中解析的结构化需求条目），检测两者之间的不一致。

**V2 变更：** 输入已完全结构化——IRD 为条目化需求数组，SWHLR 为解析后的需求数组。全程无需脚本依赖，由 AI 直接驱动聚类、分类、匹配、对比全流程。

## Prerequisites

输入为两组结构化数据：

| 数据 | 说明 |
|------|------|
| IRD 条目列表 | EoICD 接口表经需求条目化后的原子需求集合，每条为一个 JSON 对象 |
| SWHLR 需求列表 | 从软件高层需求文档中解析的结构化需求条目集合，每条为一个 JSON 对象 |

### IRD JSON 格式

```json
[
  {
    "entry_id": "REQ-001",
    "description": "HF_AMSC2.po429_OA_1000...OFV_TRV_FAILED_CLOSED的数据格式类型（DataFormatType）应为DIS",
    "interface_name": "DP / A429",
    "signal_name": "HF_AMSC2.po429_OA_1000.po429_OA_Msg.L11_VCS_Fans_status.OFV_TRV_FAILED_CLOSED",
    "source": "Publisher Table / A429-RP"
  }
]
```

| 字段 | 说明 | 用途 |
|------|------|------|
| `entry_id` | 条目唯一标识（如 `REQ-001`） | 溯源 |
| `description` | 自然语言需求描述，**属性名和期望值内嵌在中文描述中** | 提取属性值 |
| `interface_name` | 格式 `"层级 / 总线"`，如 `"DP / A429"`、`"RP / A664"` | 确定信号层级和总线类型 |
| `signal_name` | 完整信号路径，以 `.` 分隔的层级路径 | 提取 Label 号、信号语义名、聚类 Key |
| `source` | 格式 `"方向表 / Sheet名"`，如 `"Publisher Table / A429-RP"` | 确定方向 |

**方向约定：**
- `source` 以 `"Publisher Table"` 开头 → 该条目来自 ICD **发布端表**，描述数据发布行为 → **发送**
- `source` 以 `"Subscriber Table"` 开头 → 该条目来自 ICD **订阅端表**，描述数据订阅行为 → **接收**

**`signal_name` 第一段的作用：** `signal_name` 以 `.` 分隔，第一段标识该信号涉及的硬件/软件组件（如 `HF_AMSC2`、`HF_FCM_3`）。对比时若该组件名与 SWHLR 所描述的软件主体不一致，需在报告中注明该信号可能涉及外部系统，方向判定以 `source` 字段为准，同时结合端口名中的 `pi`（输入端口）/`po`（输出端口）辅助校验。

**`interface_name` 解析：**
- 层级：`DP` = Data Parameter（数据字段，核心对比对象），`RP` = Raw Parameter，`A429Channel` 等为通道/端口级配置
- 总线：`A429` / `A664` / `A825` 等

**`description` 中的属性提取：**

每条 `description` 的通用结构为：`{signal_path}的{中文属性名}（{EnglishName}）应为{value}`。属性英文名在中文括号 `（）` 内，期望值在 `应为` 之后。

关键正则提取模式：

```
数据格式类型（DataFormatType）应为(\S+)
参数大小（ParameterSize）应为(\d+)Bits
数据集内位偏移（BitOffsetWithinDS）应为(\d+)
Label号（Label）应为(\d+)
最小发送间隔（TransmissionIntervalMinimum）应为(\d+)ms
单位（Units）应为(\S+)
期望SDI（SDIExpected）应为(\d+)
满量程最大值（FullScaleRngMax）应为([\d.-]+)
满量程最小值（FullScaleRngMin）应为([\d.-]+)
最低有效位分辨率（LsbRes）应为([\d.]+)
发布延迟（PublishedLatency）应为([\d.-]+)ms
机载维护属性（OHMSAttribute）应为(\S+)
编码集（CodedSet）应为(.+)
0态（ZeroState）应为(.+)
1态（OneState）应为(.+)
系统延迟最坏情况限制（SysLatencyWCLimit）应为(\d+)ms
乘数（Multiplier）应为(\d+)
功能范围最大值（FuncRngMax）应为([\d.-]+)
功能范围最小值（FuncRngMin）应为([\d.-]+)
数据可用性（DataAvailability）应为([\d.Ee+-]+)
数据完整性（DataIntegrity）应为([\d.Ee+-]+)
```

> **注意：** 并非每条 IRD 条目都包含上述所有属性。属性仅在 ICD 中有定义时才会出现在 `description` 中。

### SWHLR JSON 格式

```json
[
  {
    "requirement_id": "FSF21000101_HLR_225",
    "requirement_text": "软件应按照FPGA接口协议获取5V参考电压并解析为控制通道控制板5V参考电压。",
    "object_type": "requirement",
    "is_derived": false,
    "rationale": "",
    "verification_method": "评审、测试",
    "implementation_method": "manual_coding",
    "source_file": "/tmp/srs.docx"
  }
]
```

| 字段 | 说明 | 用途 |
|------|------|------|
| `requirement_id` | 需求唯一标识（如 `FSF21000101_HLR_225`） | 溯源 |
| `requirement_text` | 完整需求描述文本 | **核心对比源**——提取 Label、bit 范围、方向、语义 |
| `object_type` | 固定为 `"requirement"` | 可忽略 |
| `is_derived` | 是否为衍生需求 | 辅助判断 |
| `rationale` | 基本原理/补充说明（可能含额外技术参数） | 辅助提取属性 |
| `verification_method` | 验证方法 | 参考信息 |
| `implementation_method` | 实现方法 | 参考信息 |
| `source_file` | 来源文件 | 可忽略 |

## Workflow

### Step 0: 确认输入

确认已拿到 IRD 条目列表和 SWHLR 需求列表（字段格式见上方 Prerequisites），直接进入处理流程。

### Step 1: IRD 条目解析与信号聚类

#### 1a) 提取属性

对每条 IRD 条目，用正则从 `description` 中提取属性名（英文）和期望值：

| 正则模式 | 提取属性名 | 示例 |
|---------|-----------|------|
| `数据格式类型（DataFormatType）应为(\S+)` | `DataFormatType` | DIS, BNR, OPAQUE |
| `参数大小（ParameterSize）应为(\d+)Bits` | `ParameterSize` | 19 Bits |
| `数据集内位偏移（BitOffsetWithinDS）应为(\d+)` | `BitOffsetWithinDS` | 10 |
| `Label号（Label）应为(\d+)` | `Label` | 203 |
| `最小发送间隔（TransmissionIntervalMinimum）应为(\d+)ms` | `TransmissionIntervalMinimum` | 50 ms |
| `单位（Units）应为(\S+)` | `Units` | ft, degrees C |
| `期望SDI（SDIExpected）应为(\d+)` | `SDIExpected` | 3 |
| `满量程最大值（FullScaleRngMax）应为([\d.-]+)` | `FullScaleRngMax` | 50000 |
| `满量程最小值（FullScaleRngMin）应为([\d.-]+)` | `FullScaleRngMin` | -2000 |
| `最低有效位分辨率（LsbRes）应为([\d.]+)` | `LsbRes` | 0.5 |

对于未匹配到任何已知属性模式的 `description`，按自然语言理解提取属性信息。

#### 1b) 提取信号元信息

从 `signal_name`、`interface_name`、`source` 中提取：

- **Label 号**：从 `signal_name` 中用正则 `L(\d+)` 提取（如 `L203` → Label=203）。Label 号在 JSON 数据中以十进制整数表示。
- **信号语义名**：取 `signal_name` 最后一段（最后一个 `.` 之后），如 `Pressure_Altitude`、`OFV_TRV_FAILED_CLOSED`。
- **方向**：`source = "Publisher Table"` → 发送；`source = "Subscriber Table"` → 接收。同时检查 `signal_name` 中是否含 `pi`（输入端口）或 `po`（输出端口）辅助确认。
- **总线类型**：从 `interface_name` 的 `/` 之后提取（如 `A429`、`A664`），或从 `source` 的 Sheet 名提取（如 `A429-RP` → A429）。
- **层级**：从 `interface_name` 的 `/` 之前提取（`DP`、`RP`、`A429Channel`、`LogicalPort` 等）。

#### 1c) 信号聚类

按 Label 号分组。同一 Label 的所有 IRD 条目聚合为一个**信号画像**：

```
信号画像结构（内部表示）：
- label: Label 号（如 11, 203）
- signal_key: Label 段完整名（如 L11_VCS_Fans_status）
- direction: "发送" | "接收"
- bus_types: 出现的总线类型集合
- interface_levels: 出现的层级集合
- entries: 该信号下所有 IRD 条目列表
- attributes: { 属性名 → {value, entry_id} } 字典
```

> **注意：** 同一 Label 号可能有多条 `signal_name` 不同的信号（不同端口/通道的同一 Label）。按 `signal_name` 的 Label 段（含 `L` + 数字的路径段）做次级分组。例如 `L203_Voted_Pressure_Altitude` 和 `L203_3_B1_PRESSURE_ALTITUDE_FCM3` 是同一 Label 203 下的不同信号实例。

无 Label 的信号（如 RFAN、模拟量）按 `signal_name` 中的信号语义段聚类。

#### 1d) 生成自然语言摘要

每个信号画像生成一句摘要：

> "[方向] Label [Label号]，[关键属性列表]，信号含义为[信号语义名]"

示例：
- "发送 Label 11，bit偏移22长度1bit，数据类型DIS，发送间隔1000ms，信号含义为OFV_TRV_FAILED_CLOSED"
- "接收 Label 203，bit偏移10长度19bit，数据类型BNR，量程-2000~50000ft，信号含义为Pressure_Altitude"

### Step 2: SWHLR 需求解析与分类

#### 2a) 逐条解析 SWHLR 需求

对每条 SWHLR 需求，从 `requirement_text`（必要时参考 `rationale`）中提取结构化信息：

**提取 Label 号：** 正则 `L(\d+)`（如 `L203`、`L30`）

**提取 bit 范围：**
- 模式 `bit(\d+)至bit(\d+)` → bit_offset, bit_end, bit_size = end - start + 1
- 模式 `bit(\d+)至bit(\d+)为(.+?)信号` → bit_offset + semantic
- 模式 `bit(\d+)为(.+?)` → 单个 bit 位置
- 模式 `bit(\d+)=(\d+)时` → 状态条件描述（如 `bit15=1时`），非 bit 范围定义——此时 bit 为该位置的 1bit 离散字段
- 多个 bit 范围的（如 L35: bit15~bit18），逐条列出

**提取方向：**
- 关键词 "发送"/"写入"/"输出"/"发布" → 发送
- 关键词 "接收"/"解析"/"采集"/"输入"/"获取" → 接收
- 同时含双向关键词时根据上下文判断（如"从A429接收数据中解析L203" → 接收；"向A429发送数据中的L214写入" → 发送）

**提取数据类型：** 从描述上下文推断。bitX~bitY 描述数值量（如气压高度、温度）→ 通常 BNR；bitX 描述离散状态（True/False/有效/无效）→ DISCRETE。

**提取 SDI 值：** 正则 `SDI=(\d+)` 或 `SDI(\d+)` 或 `SDI为(\d+)`

**提取周期：** 模式 `每(\d+)(ms|秒|s)` 或 `(\d+)ms为周期`

**提取信号语义：** 需求中描述的信号功能名（中文 + 英文对照），如"气压高度"、"驾驶舱温度COCKPIT_TEMP_SELECTION"。

**提取单位/量程/分辨率：** 从 `rationale` 字段补充提取（如 `单位：degrees C`、`功能范围-70~1100`、`分辨率：8`）。

#### 2b) 四路径决策树分类

对每条 SWHLR 需求，按以下决策树分类（**按优先级依次检查，命中即停止**）：

```
① 含 Label 号（L + 数字，如 L203、L30）？
   → 是 → 路径① "A429显式"
   → 否 → 继续

② 含模拟量/采集关键词（ADC/模拟量/电压/电流/传感器/采样/量程/V/mA）且无 Label 号？
   → 是 → 路径② "模拟量"
   → 否 → 继续

③ 含离散量/开关量关键词（微动/开关/True/False/故障/状态/跳变/触点）且无 Label 号？
   → 是 → 路径③ "离散量"
   → 否 → 继续

④ 含总线/通信关键词（CAN/A825/A664/A429/总线/接收/发送）+ 信号功能描述？
   → 是 → 路径④ "A429隐式"（有总线协议词但需求文本无 Label 号）
   → 否 → 非通信需求，跳过
```

> **Label 优先原则：** 含 Label 号的需求即使同时涉及离散量/模拟量语义（如"L30 bit15=1时开关为True"），也归入路径①——Label 提供了最精确的匹配入口。

#### 2c) 构建 SWHLR 需求画像

```
SWHLR 需求画像结构（内部表示）：
- req_id: requirement_id
- description: requirement_text（原始全文）
- rationale: rationale（补充技术参数）
- signal_category: "A429显式" | "A429隐式" | "模拟量" | "离散量" | "逻辑/非通信"
- direction: "发送" | "接收" | "N/A"
- labels: [提取的Label号列表]
- bit_fields: [{offset, size, semantic}] 列表
- data_type: 显式描述或推断的数据类型
- unit: 显式描述或推断的单位
- period: 发送/采样周期
- sdi_value: SDI 值
- signal_semantic: 信号功能语义
- scale_range: 量程描述（模拟量）
- resolution: 分辨率描述（模拟量）
```

### Step 3: 跨文档匹配

#### 3a) 按信号类别分层匹配

**路径① A429显式 — Label 号精确匹配：**

1. 从 SWHLR 需求画像提取 Label 号
2. 在 IRD 信号画像中搜索相同 Label 号
3. 若同一 Label 下 IRD 有多个信号实例（如 DP/RP 级别 vs 消息级），优先匹配 DP 级别（数据字段级）画像
4. 匹配结果标记为 **"精确匹配"**

**路径② 模拟量 — 通道名/语义匹配：**

1. 从 SWHLR 需求提取：ADC 通道名（如 ADCINA1）、物理量描述
2. 在 IRD 无 Label 的信号画像中按通道名或物理量语义匹配
3. 匹配结果标记为 **"精确匹配"**（通道名一致）或 **"语义匹配"**

**路径③ 离散量 — 信号功能名语义匹配：**

1. 从 SWHLR 需求提取：信号功能名、状态描述
2. 在 IRD 无 Label 的离散信号画像中按功能名语义匹配
3. 匹配结果标记为 **"语义匹配"**

**路径④ A429隐式 — 信号语义匹配：**

1. 从 SWHLR 需求提取：方向、总线类型、信号功能名
2. 在对应总线类型的 IRD 信号画像中按功能名语义搜索
3. 找到候选后提取其 Label 号，后续对比步骤复用路径①逻辑
4. 匹配结果标记为 **"语义匹配"**

#### 3b) 匹配结果分类

| 结果 | 含义 | 后续处理 |
|------|------|---------|
| 精确匹配 | Label 号一致或通道名一致 | → Step 4 逐属性对比 |
| 语义匹配 | AI 判断信号功能语义高度相关 | → Step 4 逐属性对比 |
| IRD 独有 | IRD 有信号但 SWHLR 无对应需求 | 附录输出 |
| SWHLR 独有 | SWHLR 有需求但 IRD 无对应条目 | 附录输出（可能表示 IRD 条目缺失） |

> **多对多注意：** 一条 SWHLR 需求可能引用多个 Label（如 HLR_4510 引用 L126/L134/L135/L136/L137/L150/L260），此时需求分别与每个 Label 的 IRD 信号画像匹配。一个 Label 也可能被多条 SWHLR 需求引用（如 L126 同时被 HLR_4276 和 HLR_473 引用），分别对比。

### Step 4: 逐属性对比

对每对匹配上的信号，逐属性对比 IRD 信号画像 vs SWHLR 需求画像。

#### 4a) 通用对比维度

| 属性 | IRD 来源 | SWHLR 来源 | 一致性判断 |
|------|---------|-----------|-----------|
| bit 范围 | `BitOffsetWithinDS` + `ParameterSize` | bitX至bitY 或 bitX~bitY | 偏移量一致 + bit 数一致 |
| 数据类型 | `DataFormatType` | 显式描述或推断 | BNR↔BNR, DIS↔DISCRETE 视为一致 |
| 单位 | `Units` | 显式描述或推断 | `ft`↔`feet`, `degrees C`↔`℃` 视为等价 |
| 发送间隔/周期 | `TransmissionIntervalMinimum` | 周期/间隔描述 | ±20% 容差内视为一致 |
| SDI 值 | `SDIExpected` | SDI=xx | 数值一致 |
| 信号方向 | `source` 字段 | 发送/接收关键词 | **矛盾时标记为严重不一致** |
| 信号语义 | description 文本 | requirement_text 功能描述 | AI 语义判断 |

#### 4b) 模拟量专属维度

| 属性 | IRD 来源 | SWHLR 来源 |
|------|---------|-----------|
| 量程 | `FullScaleRngMax` / `FullScaleRngMin` | 量程/范围描述（可能来自 rationale） |
| 分辨率 | `LsbRes` | 分辨率描述（可能来自 rationale） |

#### 4c) 判断规则

| 判断 | 条件 | 优先级 |
|------|------|--------|
| ❌ **不一致** | 两边值明确且矛盾 | 最高 |
| ✅ **一致** | 两边值一致或等价 | — |
| ⚠️ **仅 IRD 定义** | SWHLR 该属性未描述 | 低（建议 SWHLR 补充） |
| ⚠️ **仅 SWHLR 描述** | IRD 未找到该属性 | 低（建议 IRD 补充） |
| ❓ **待确认** | 两边信息不足以判断 | — |

### Step 5: 输出结果（JSON 格式）

输出严格的 JSON 对象，**不是** markdown 报告。结构如下：

```json
{
  "differences": [
    {
      "difference_id": "1",
      "difference_requirement_id": "FSF21000101_HLR_510",
      "difference_eoicd_entry_id": "REQ-007",
      "difference_type": "缺失|不一致|冗余|需确认",
      "eoicd_requirement_text": "IRD 原文（与该 diff 有关时填，否则空字符串）",
      "software_requirement_text": "SWHLR 原文（与该 diff 有关时填，否则空字符串）",
      "description": "多属性对比的结构化文本（见下方格式）",
      "suggested_action": "一句话整体建议"
    }
  ]
}
```

#### 字段填写规则

- **difference_id**：从 `"1"` 开始递增（`"1"`、`"2"`、`"3"`...）
- **difference_requirement_id**：关联的 SWHLR `requirement_id`；"缺失"类型下填空字符串
- **difference_eoicd_entry_id**：关联的 IRD `entry_id`（如 "REQ-007"）；"冗余"类型下填空字符串
- **eoicd_requirement_text**：IRD 的 `description` 原文；"冗余"类型下填空字符串
- **software_requirement_text**：SWHLR 的 `requirement_text` 原文；"缺失"类型下填空字符串
- **difference_type**：仅 4 种值（缺失 / 不一致 / 冗余 / 需确认），必须与 description 中"整体判定"一致
- **description**：使用下方"结构化格式"
- **suggested_action**：一句话整体行动建议

#### description 结构化格式（必读）

`description` 必须使用多属性对比的结构化文本，**禁止丢属性或写一句笼统话**。

**每行一个属性判定，格式：**
```
属性 <属性名>: SWHLR=<值> IRD=<值> <判定> - <一句话分析>
```

**可选判定值：** `一致` / `不一致` / `仅IRD定义` / `仅SWHLR描述` / `待确认`

**末尾追加 3 行整体总结：**
```
整体判定: <缺失|不一致|冗余|需确认>
整体分析: <一句话根因>
整体建议: <一句话行动>
```

#### 完整示例

```json
{
  "differences": [
    {
      "difference_id": "1",
      "difference_requirement_id": "FSF21000101_HLR_510",
      "difference_eoicd_entry_id": "REQ-007",
      "difference_type": "不一致",
      "eoicd_requirement_text": "L11_VCS_Fans_status.OFV_TRV_FAILED_CLOSED的数据格式类型（DataFormatType）应为DIS",
      "software_requirement_text": "软件应处理 VCS 风扇状态信号 L11，按 A429 协议解析 bit15 状态。",
      "description": "属性 Label: SWHLR=11 IRD=11 一致 - 双方都使用 A429 Label 11\n属性 DataFormatType: SWHLR=BNR(推断) IRD=DIS 不一致 - 数据格式定义冲突\n属性 BitRange: SWHLR=bit15 IRD=bit22(1bit) 不一致 - bit 位置和长度都不同\n属性 Direction: SWHLR=发送 IRD=发送(从Publisher Table推断) 一致\n属性 Units: SWHLR=(无) IRD=(无) 仅IRD定义\n整体判定: 不一致\n整体分析: L11 信号的 DataFormatType 和 bit 范围两边定义不一致\n整体建议: 与需求方确认 L11 的数据格式类型和 bit 布局",
      "suggested_action": "与需求方确认 L11 的数据格式类型和 bit 布局"
    }
  ]
}
```

#### 判定值映射

| description 中的"整体判定" | `difference_type` |
|---|---|
| 不一致 | 不一致 |
| 仅IRD定义 | 缺失 |
| 仅SWHLR描述 | 冗余 |
| 待确认 | 需确认 |
| 一致 | **不输出 diff 条目**（一致项不出现在 differences 数组中） |

#### 注意事项

1. **每对匹配的多属性判定都必须写入 description**，不允许只写一行总结
2. **SWHLR 缺属性**：写 `(无)` 或 `(未明确)`
3. **IRD 缺属性**：写 `(无)`
4. **仅IRD定义 / 仅SWHLR描述**：表示一方有另一方无，**不算"不一致"**
5. **`difference_type` 必须与 description 中"整体判定"一致**
6. **一致项不输出 diff 条目**，只作为单行"属性 X: 一致"出现在 description 中（仅当需要展示时）
7. **优先级**：先按路径①匹配 Label → 路径②→③→④，路径命中后即按对应规则对比

## 信号分类参考

### 属性名映射（description 解析）

| description 中文模式 | 提取属性名 | 适用类别 |
|---------------------|-----------|---------|
| 数据格式类型（DataFormatType）应为... | `DataFormatType` | A429 显式/隐式 |
| 参数大小（ParameterSize）应为...Bits | `ParameterSize` | A429 显式/隐式 |
| 数据集内位偏移（BitOffsetWithinDS）应为... | `BitOffsetWithinDS` | A429 显式/隐式 |
| Label号（Label）应为... | `Label` | A429 显式/隐式 |
| 最小发送间隔（TransmissionIntervalMinimum）应为... | `TransmissionIntervalMinimum` | 所有 |
| 单位（Units）应为... | `Units` | 所有 |
| 期望SDI（SDIExpected）应为... | `SDIExpected` | A429 显式/隐式 |
| 满量程最大值（FullScaleRngMax）应为... | `FullScaleRngMax` | 模拟量/A429 BNR |
| 满量程最小值（FullScaleRngMin）应为... | `FullScaleRngMin` | 模拟量/A429 BNR |
| 最低有效位分辨率（LsbRes）应为... | `LsbRes` | 模拟量/A429 BNR |
| 发布延迟（PublishedLatency）应为... | `PublishedLatency` | 所有 |
| 系统延迟最坏情况限制（SysLatencyWCLimit）应为... | `SysLatencyWCLimit` | 所有 |
| 编码集（CodedSet）应为... | `CodedSet` | 离散量 |
| 0态（ZeroState）应为... | `ZeroState` | 离散量 |
| 1态（OneState）应为... | `OneState` | 离散量 |
| 机载维护属性（OHMSAttribute）应为... | `OHMSAttribute` | A429 DP 级 |
| 乘数（Multiplier）应为... | `Multiplier` | A429 |
| 功能范围最大值（FuncRngMax）应为... | `FuncRngMax` | 模拟量/A429 BNR |
| 功能范围最小值（FuncRngMin）应为... | `FuncRngMin` | 模拟量/A429 BNR |
| 数据可用性（DataAvailability）应为... | `DataAvailability` | 安全相关 |
| 数据完整性（DataIntegrity）应为... | `DataIntegrity` | 安全相关 |

### A429 数据类型等价表

| IRD DataFormatType | 等价 SWHLR 描述 |
|-------------------|----------------|
| BNR | BNR, Binary, 二进制数值, SINT |
| DIS | DISCRETE, Discrete, 离散, BOOL, Boolean |
| OPAQUE | OPAQUE, 不透明, 透传 |
| A429OCTLBL | (Label 字段，非数据) |
| A429PARITY | (校验位，非数据) |
| A429SDI | (SDI 字段，非数据) |
| A429_SSM_BNR | (SSM 字段，非数据) |

> **注意：** `A429OCTLBL`、`A429PARITY`、`A429SDI`、`A429_SSM_BNR` 是 A429 协议栈开销字段，不是应用层数据。对比时，这些字段不参与应用层数据类型的冲突判定。例如 IRD 中某信号同时有 `DataFormatType=A429OCTLBL` (bit0-7) 和 `DataFormatType=BNR` (bit10-28)，仅以 `BNR` 与 SWHLR 对比。

## 工作原则

1. **聚焦不一致：** 一致项精简带过，详细展开不一致项。不追求逐条罗列所有对比。
2. **Label 优先：** 有 Label 号的需求一律走 A429 显式路径，Label 是最可靠的匹配锚点。
3. **未指定 ≠ 不一致：** SWHLR 未描述的属性标"仅 IRD 定义"，不判为不一致。反之亦然。
4. **协议栈字段过滤：** A429 OCTLBL/PARITY/SDI/SSM 类属性不参与应用层数据对比。
5. **方向必查：** 信号方向（发送/接收）是基础属性，矛盾时必须报告为严重不一致。
6. **语义匹配需合理：** 语义匹配时综合判断，不确定时标"待确认"而非强行判定。
7. **保留溯源链：** 每条不一致标记其 IRD entry_id 和 SWHLR requirement_id，便于追溯。
8. **多重引用处理：** 一条 SWHLR 需求引用多个 Label 时，分别匹配和对比。一个 Label 被多条需求引用时，分别对比。
9. **rationale 字段利用：** SWHLR 的 `rationale` 字段常含单位、量程、分辨率等补充技术参数，对比时务必查阅。
