# EoICD 数据处理方案 v5.0

> 实际已实现的流水线：定位 → 裁剪（含身份信息提取） → **追溯（可选）** → 去重归簇 → 生成身份主键
>
> **v5.0 调整**（2026-09-01）：新增「需求追溯过滤」环节。
> 在去重归簇之后，检测输入目录中是否存在追溯表：
> - **没有追溯表** → 按原方案全量保留，行为与 v4.0 完全一致
> - **有追溯表** → 按链路逐级追溯，只保留与输入软件高层需求相关的 EoICD，进一步缩小处理范围
>
> 支持的链路：
> - **3 层链路**：软件高层需求 --(设备↔软件追溯表)--> 设备层级需求 --(EoICD↔设备追溯表)--> EoICD
> - **4 层链路**：软件高层需求 --(设备↔软件追溯表)--> 设备层级需求 --(系统↔设备追溯表)--> 系统层级需求 --(EoICD↔系统追溯表)--> EoICD
>
> **v4.0 调整**：
> 1. **身份信息新增 `direction`（信号方向）**：Publisher 表只取 DP 层 → `TX`（本系统设备发送的信号）；Subscriber 表只取 RP 层 → `RX`（本系统设备接收的信号）。方向同时纳入身份主键。
> 2. **`bit` 改为 `bit_range`（位范围）**：不是单个位偏移，而是由 `BitOffsetWithinDS/Msg` + `ParameterSize` 计算的闭区间位范围，如 `16-31`。
>
> **v3.0 调整**：裁剪阶段新增身份信息提取，提取顺序调整为「先提取身份信息 → 再裁剪属性」；去重后新增身份主键生成，用于正向匹配阶段的快速身份比对。

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         数据处理流水线 (Data Pipeline)                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   原始 Excel        第一步      第二步        第2.5步        第三步        第四步      │
│  ┌─────────────┐  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐  ┌─────────┐ │
│  │ EoICD Pub   │─▶│ 定位    │▶│ 裁剪     │▶│ 追溯     │▶│ 去重    │─▶│生成主键│ │
│  │ EoICD Sub   │  │ locate  │ │ crop     │ │ trace    │ │ dedup   │  │identity│ │
│  └─────────────┘  └────┬────┘ └────┬─────┘ └────┬─────┘ └────┬────┘  └────┬───┘ │
│                        │           │            │            │           │       │
│       追溯表+HLR ────────────────────┘            │            │           │       │
│       (可选，放 data/raw)            追溯结果 ◀────┘            │           │       │
│                              trace_result.json                │           │       │
│                                         过滤后只保留相关EoICD ◀┘           │       │
│                                                                                 │
│                   过滤后Excel   JSON信号列表   追溯链结果   归簇JSON  含主键     │
│                   (行列完整)    (含identity+   (FullName   (按      identity    │
│                                 位范围)        集合)       FullName) key        │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**设计原则**：各步骤工具完全独立，通过文件衔接，各自可单独运行。
- 第二步：在裁剪属性之前，先从原始行数据中提取身份信息
- 第 2.5 步（可选）：检测追溯表并逐级追溯，**无追溯表时自动跳过，不影响原有流程**
- 第三步：按 FullName 归簇；若追溯已启用，则只保留追溯链命中的簇
- 第四步：生成身份主键

---

## 二、四步详解

### 2.1 第一步：定向定位 (`locate_eoicd.py`)

**不变**。功能、输入输出、配置均与 v2.0 一致。

**功能**：按配置条件过滤行，保留与目标方向（HF/软件/硬件）相关的信号。  
**输入**：原始 EoICD Excel（Pub/Sub 各一个文件）  
**输出**：过滤后的 Excel（列完整保留，仅删除无关行）  
**配置**：`config/eoicd_locate.yaml`

```yaml
filters:
  publisher:
    A825-RP:
      Software.Name: ["HF_FWDBFAN1"]
  subscriber:
    A825-RP:
      Software.Name: ["HF_FWDBFAN1"]
```

**耗时参考**：Publisher ~21s，Subscriber ~25s

---

### 2.2 第二步：属性裁剪 + 身份信息提取 (`process_eoicd.py`)

**功能调整**：在裁剪属性之前，先从原始行数据中提取身份信息（bus / label / name / bit）。

**输入**：定位后的 Excel  
**输出**：`eoicd_pub.json` / `eoicd_sub.json`  
**配置**：`config/eoicd_crop.yaml`（不变）

#### 处理逻辑（逐行）

```
对 Excel 的每一行数据：
  1. 读取各层级数据（与现有逻辑相同）
  2. 构建 FullName（层级信号名）和 signal_short_name（叶子层 Name）
  
  3. 提取身份信息 identity（在裁剪属性之前）
     ├─ bus:       见下方「总线提取规则」
     ├─ direction: 见下方「信号方向（direction）提取规则」
     ├─ label:     见下方「Label 提取规则」
     ├─ name:      从 DP/RP 层读取 Name 属性值（即 signal_short_name）
     └─ bit_range: 由 DP/RP 层的位偏移 + ParameterSize 计算闭区间位范围
                   Publisher → BitOffsetWithinDS
                   Subscriber → BitOffsetWithinMsg
  
  4. 裁剪保留标黄属性（按 eoicd_crop.yaml 配置）
  
  5. 输出信号记录，包含 identity + 裁剪后的 attributes
```

#### 身份信息字段说明

| 字段 | 类型 | 提取来源 | 说明 |
|------|------|---------|------|
| `bus` | `str \| None` | Publisher: Sheet 名称<br>Subscriber: LogicalPort.Physical（正则提取） | 总线类型，如 `A664`、`A825`、`A429`、`Analog`、`Discrete`。详见下方提取规则。 |
| `direction` | `str` | 由处理的表类型直接确定 | 信号方向。`TX` = 本系统设备发送（Publisher 表 / DP 层），`RX` = 本系统设备接收（Subscriber 表 / RP 层）。 |
| `label` | `str \| None` | Publisher: A429Word.Name（正则提取 `^L(\d+)`）<br>Subscriber: RP.Label 直接读取 | Label 编号，如 `226`、`215`。详见下方提取规则。 |
| `name` | `str` | DP/RP 层的 `Name` 属性值 | 信号本身的名称（叶子层 Name），如 `WAIS_ON`、`SPEED` |
| `bit_range` | `str \| None` | DP/RP 层的位偏移 + `ParameterSize` | 位范围闭区间字符串，如 `16-31`。详见下方「bit_range（位范围）提取规则」。 |

---

#### 信号方向（direction）提取规则

**核心原则**：EoICD 分为 Publisher_Table 与 Subscriber_Table 两张表，方向由「处理的是哪张表 + 取的是哪一层叶子」唯一确定，不依赖单元格内容。

| 表 | 读取的叶子层 | direction | 含义 |
|----|------------|-----------|------|
| `AMS_EoICD_Publisher_Table.xlsx` | `DP`（Data Parameter） | `TX` | 该系统设备**发送**的信号 |
| `AMS_EoICD_Subscriber_Table.xlsx` | `RP`（Received Parameter） | `RX` | 该系统设备**接收**的信号 |

**说明**：
- Publisher 表在处理时**只取 DP 层**信号，Subscriber 表**只取 RP 层**信号，因此方向不会出现歧义
- 方向字段必定有值（不可能为 `None`）
- 方向纳入身份主键后，**TX 与 RX 的簇天然不会互配**；实测 pub/sub 两侧 `identity_key` 重合数为 **0**

**提取代码**：

```python
# leaf_type 在解析表头时已按 side 确定：publisher → "DP"，subscriber → "RP"
identity["direction"] = "TX" if side == "publisher" else "RX"
```

---

---

#### 总线（bus）提取规则

**核心原则**：
- **Publisher 侧**：Sheet 名称即代表输出方视角的总线信息，直接读取 Sheet 名提取
- **Subscriber 侧**：从**第二个** `LogicalPort.Physical` 属性值中提取总线标识（Subscriber 表每个 Sheet 有两个 LogicalPort 层：第一个是 Publisher 侧，第二个是 Subscriber 侧）
- **兜底规则**：任何情况下，若上述途径未提取到总线信息，则 `bus = None`

**重要区分**：
- Publisher 失败时：尝试 Sheet 名提取（因为 Sheet 名就是总线信息）
- Subscriber 失败时：**不回退**，直接 `bus = None`（因为 Sheet 名是 RX 侧的分类视角，不等于信号实际来源的总线）

**重要发现**：Subscriber 各 Sheet 的信号实际总线与 Sheet 名**不一致**：
- A664-RP Sheet → 实际总线：`A429`（`pi429_xxx`）
- A825-RP Sheet → 实际总线：`A429`（`pi429_xxx`）
- A429-RP Sheet → 实际总线：`A429` / `A825` / `Discrete`（混合）
- Analog-RP Sheet → 实际总线：`A429`（`pi429_xxx`）
- Discrete-RP Sheet → 实际总线：`A429`（`pi429_xxx`）

这说明 **Subscriber 侧必须从每个信号的 Physical 值独立提取总线，绝对不能回退 Sheet 名**。

---

##### A. Publisher（TX 信号）总线提取

**提取路径**：直接读取当前 Sheet 的名称

**方法**：
1. 获取当前 Sheet 的名称（如 `A429-RP`）
2. 去除后缀 `-RP`，剩余部分即为总线标识
3. 若 Sheet 名不符合 `<总线>-RP` 格式，则 `bus = None`

**各 Publisher Sheet 总线提取结果**：

| Sheet 名称 | 提取结果 | 说明 |
|-----------|---------|------|
| **A664-RP** | `A664` | AFDX 总线 |
| **A825-RP** | `A825` | CAN 总线 |
| **A429-RP** | `A429` | ARINC 429 总线 |
| **Analog-RP** | `Analog` | 模拟量 |
| **Discrete-RP** | `Discrete` | 离散量 |

**提取代码**：

```python
import re

# Publisher: 从 Sheet 名称提取总线
sheet_name = ws.title  # 如 "A429-RP"
bus_match = re.match(r'^(.+)-RP$', sheet_name)
if bus_match:
    identity["bus"] = bus_match.group(1)  # "A429"
else:
    identity["bus"] = None
```

---

##### B. Subscriber（RX 信号）总线提取

**提取路径**：`Subscriber → LogicalPort（第二个） → Physical`

**重要前提**：Subscriber 表每个 Sheet 有两个 LogicalPort 层：
- **第一个 LogicalPort**（如 col 23）：Publisher 侧，**无 Physical 属性**
- **第二个 LogicalPort**（如 col 108）：Subscriber 侧，**有 Physical 属性**

必须从**第二个 LogicalPort** 读取 Physical，才是接收信号相关的信息。

**方法**：
1. 定位并读取**第二个 LogicalPort 层**的 `Physical` 属性值
2. 对 `Physical` 值按**优先级顺序**执行正则匹配：

| 优先级 | 正则模式 | 匹配示例 | 提取结果 |
|-------|---------|---------|---------|
| 1 | `ANALOG` / `ANLG`（不区分大小写） | `piANALOG_U1` | `Analog` |
| 2 | `DISC`（不区分大小写） | `piDISC_LowSpeedCmd` | `Discrete` |
| 3 | `429`（不区分大小写） | `pi429_A1` | `A429` |
| 4 | `825`（不区分大小写） | `p825_1` | `A825` |
| 5 | `664`（不区分大小写） | `pi664_xxx` | `A664` |

3. 若任一模式命中，返回对应的总线名称
4. 若全部未命中，或 `Physical` 属性不存在/为空 → `bus = None`
5. **不回退**：不使用 Sheet 名作为回退来源

**注意**：模拟量除 `ANALOG` 外，还需考虑缩略形式 `ANLG`，故正则使用 `ANALOG|ANLG`。

**各 Subscriber Sheet 总线提取情况**：

| Sheet | 第二个 LogicalPort.Physical 值示例 | 正则匹配模式 | 提取结果 | 最终 bus |
|-------|----------------------------------|------------|---------|---------|
| **A664-RP** | `AMSC1.pi429_A1` | `429` | 命中 → `A429` | `A429` |
| **A825-RP** | `AMSC1.pi429_A1` | `429` | 命中 → `A429` | `A429` |
| **A429-RP** | `AMSC1.pi429_A1` / `AFTEFAN1.p825_1` / `AFTBFAN1.piDISC_LowSpeedCmd` | `429` / `825` / `DISC` | 分别命中 → `A429` / `A825` / `Discrete` | 混合总线 |
| **Analog-RP** | `AMSC1.pi429_S1` | `429` | 命中 → `A429` | `A429` |
| **Discrete-RP** | `AMSC1.pi429_A1` | `429` | 命中 → `A429` | `A429` |

**关键说明**：
- 所有 Sheet 的 Physical 值**均存在**，且包含总线标识
- **A664-RP / A825-RP / Analog-RP / Discrete-RP** 的实际总线均为 `A429`（`pi429_xxx`），与其 Sheet 名**不一致**
- **A429-RP** 包含混合总线：`A429`（`pi429_xxx`）、`A825`（`p825_1`）、`Discrete`（`piDISC_xxx`）
- **不回退**：即使 Physical 属性不存在或无法匹配，也不回退到 Sheet 名。Subscriber 的 Sheet 名是 RX 侧的分类视角，不等于信号实际来源的总线类型

**提取代码**：

```python
import re

# 总线正则模式（按优先级排序）
bus_patterns = [
    (re.compile(r'ANALOG|ANLG', re.I), 'Analog'),  # 匹配 ANALOG / ANLG → Analog
    (re.compile(r'DISC', re.I), 'Discrete'),       # 匹配 DISC → Discrete
    (re.compile(r'429', re.I), 'A429'),            # 匹配 429 → A429
    (re.compile(r'825', re.I), 'A825'),            # 匹配 825 → A825
    (re.compile(r'664', re.I), 'A664'),            # 匹配 664 → A664
]

if side == "publisher":
    # Publisher: 从 Sheet 名称提取
    bus_match = re.match(r'^(.+)-RP$', sheet_name)
    identity["bus"] = bus_match.group(1) if bus_match else None
else:
    # Subscriber: 从第二个 LogicalPort.Physical 提取，不回退
    # 注意：Subscriber 表有两个 LogicalPort 层，第二个才是 Subscriber 侧
    physical = layer_values.get("LogicalPort", {}).get("Physical")
    if physical:
        physical_str = str(physical).strip()
        for pattern, bus_name in bus_patterns:
            if pattern.search(physical_str):
                identity["bus"] = bus_name
                break
        else:
            # Physical 有值但无法匹配任何模式
            identity["bus"] = None
    else:
        # Physical 不存在或为空
        identity["bus"] = None
```

---

#### Label 提取规则

**核心原则**：
- **Publisher 侧**：仅当该 Sheet 存在 `A429Word` 层级时，才尝试从 `A429Word.Name` 提取 Label；否则 Label 为 `None`
- **Subscriber 侧**：直接从 `RP.Label` 读取；无值或为空时 Label 为 `None`
- **兜底规则**：任何情况下，若上述途径未提取到 Label 号，则 `label = None`

---

##### A. Publisher（TX 信号）Label 提取

**提取路径**：`Publisher → A429Word → Name`

**方法**：
1. 扫描该 Sheet 的层级结构（Row 2），确认是否存在 `A429Word` 层
2. 若存在 `A429Word` 层，读取该层级的 `Name` 属性值
3. 对 `A429Word.Name` 执行正则匹配：`^L(\d+)`，提取开头的数字部分
4. 若匹配成功，Label = 提取到的数字字符串（如 `"226"`）；否则 Label = `None`
5. 若该 Sheet **不存在** `A429Word` 层，Label = `None`（无需尝试提取）

**各 Publisher Sheet 实际情况**：

| Sheet | 是否存在 A429Word 层 | A429Word.Name 示例 | Label 提取结果 |
|-------|-------------------|-------------------|---------------|
| **A664-RP** | ✅ 存在（col 56） | （无数据行） | 无数据行 → `None` |
| **A825-RP** | ✅ 存在（col 28） | `空` | A429Word.Name 为空 → `None` |
| **A429-RP** | ✅ 存在（col 22） | `L226_AMSC_OB_DATA_LOAD` | 提取到 `"226"` |
| **Analog-RP** | ❌ 不存在 | — | 无 A429Word 层 → `None` |
| **Discrete-RP** | ❌ 不存在 | — | 无 A429Word 层 → `None` |

**关键说明**：
- A429-RP Sheet 的全部 11,900 行 DP 数据，A429Word.Name 100% 匹配 `L<数字>` 格式，Label 提取准确率 100%
- A664-RP 虽存在 A429Word 层，但该 Sheet 无数据行（仅表头），自然无 Label
- A825-RP 虽存在 A429Word 层，但 A429Word.Name 全部为空，无法提取 Label
- Analog-RP、Discrete-RP 不存在 A429Word 层，Label 直接为 `None`

**提取代码**：

```python
import re

label_pattern = re.compile(r'^L(\d+)')

if side == "publisher":
    # Publisher: 从 A429Word.Name 提取 Label（仅当 A429Word 层存在时）
    a429word_layer = layer_values.get("A429Word")
    if a429word_layer:
        a429word_name = a429word_layer.get("Name")
        if a429word_name:
            match = label_pattern.match(str(a429word_name))
            identity["label"] = match.group(1) if match else None
        else:
            identity["label"] = None  # A429Word 层存在但 Name 为空
    else:
        identity["label"] = None  # 该 Sheet 不存在 A429Word 层
```

---

##### B. Subscriber（RX 信号）Label 提取

**提取路径**：`Subscriber → RP → Label`

**方法**：
1. 读取当前信号行 RP 层的 `Label` 属性值
2. 若 `Label` 存在且非空，Label = 该值（转为字符串并去除首尾空格）
3. 若 `Label` 不存在或为空字符串，Label = `None`

**各 Subscriber Sheet 实际情况**：

| Sheet | RP 信号行数 | RP.Label 有值比例 | Label 提取示例 |
|-------|------------|------------------|---------------|
| **A664-RP** | ~9300 | 有 | `134`、`215` 等 |
| **A825-RP** | ~2250 | 有 | `300`、`215` 等 |
| **A429-RP** | 2588 | 99.2% | `215`、`50`、`20` 等 |
| **Analog-RP** | — | 有 | 可直接读取 |
| **Discrete-RP** | — | 有 | 可直接读取 |

**提取代码**：

```python
if side == "subscriber":
    # Subscriber: 直接从 RP.Label 读取
    label_raw = leaf_values.get("Label")
    if label_raw is not None:
        label_str = str(label_raw).strip()
        identity["label"] = label_str if label_str else None
    else:
        identity["label"] = None
```

---

#### bit_range（位范围）提取规则

**核心原则**：信号参数占用的不是单个 bit 位，而是一段连续的 bit 区间。位范围由**起始偏移**与**参数长度**共同确定，表示为**闭区间字符串** `start-end`。

**计算方式**：

```
bit_range = "{start}-{start + ParameterSize - 1}"
```

| 侧 | 起始偏移字段 | 长度字段 |
|----|------------|---------|
| Publisher（DP 层） | `BitOffsetWithinDS` | `ParameterSize` |
| Subscriber（RP 层） | `BitOffsetWithinMsg` | `ParameterSize` |

**取值与兜底规则**（按优先级）：

| 情况 | 处理 | 示例 |
|------|------|------|
| 偏移与长度均有值 | 正常计算闭区间 | 偏移 16、长度 16 → `16-31` |
| 偏移 < 0（ICD 中表示「未定义/不适用」） | 偏移视为缺失，从 0 开始 | 偏移 -1、长度 8 → `0-7` |
| 偏移缺失但长度有值 | 兜底从 0 开始 | 长度 32 → `0-31` |
| 长度缺失或 ≤ 0 | `bit_range = None` | — |

**提取代码**：

```python
def _to_int(value):
    """单元格值安全转 int；空值/非法值返回 None（兼容 '16'、16.0、' 16 '）"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


bit_attr = "BitOffsetWithinDS" if side == "publisher" else "BitOffsetWithinMsg"
bit_offset = _to_int(leaf_values.get(bit_attr))
bit_size = _to_int(leaf_values.get("ParameterSize"))

# 偏移为负（如 -1）在 ICD 中表示「未定义」，等同缺失，
# 否则会生成 "-1--1" 这类有歧义的范围字符串
if bit_offset is not None and bit_offset < 0:
    bit_offset = None

if bit_size is not None and bit_size > 0:
    start = bit_offset if bit_offset is not None else 0
    identity["bit_range"] = f"{start}-{start + bit_size - 1}"
```

**实测情况**（2026-09-01，Windows 环境）：

| 侧 | 信号数 | bit_range 缺失 | 起始位为 0 | 说明 |
|----|--------|---------------|-----------|------|
| Publisher | 12,200 | 0（0.0%） | 1,147 | 偏移 100% 有值，其中 1,147 条偏移本来就是 0 |
| Subscriber | 12,274 | 0（0.0%） | 9,394 | 其中 8,736 条源数据偏移为空（占 71.2%，集中在 A664-RP 的 88.2%、A429-RP 的 18.9%），652 条偏移为 -1；按兜底规则全部归入起始位 0 |

**注意**：Subscriber 侧大量信号是「整字参数」，`BitOffsetWithinMsg` 在源数据中本就为空，这是数据本身的特性，不是提取缺陷。但兜底后这些信号的 `bit_range` 会集中在 `0-31`（8,756 条），会削弱身份主键的区分度，正向匹配阶段需要配合 label / name 一起比对。

---

#### 输出 JSON 结构（单条信号）

```json
{
  "_meta": {
    "sheet": "A429-RP",
    "side": "publisher",
    "source_file": "AMS_EoICD_Publisher_Table.xlsx"
  },
  "signal_name": "HF_AMSC1.po429_OA_500.L304.WAIS_ON",
  "signal_short_name": "WAIS_ON",
  "identity": {
    "bus": "A429",
    "direction": "TX",
    "label": "304",
    "name": "WAIS_ON",
    "bit_range": "15-30"
  },
  "attributes": {
    "DP": {
      "Name": "WAIS_ON",
      "BitOffsetWithinDS": 15,
      "DataFormatType": "BNR",
      "Label": 304,
      "ParameterSize": 16
    },
    "LogicalPort": {
      "RefreshPeriod": 100
    }
  }
}
```

> **设计要点**：身份信息从原始行数据中直接提取，不受裁剪配置影响。即使某个身份相关字段不在裁剪清单中，也已在 identity 中保留。裁剪后的 `attributes` 仅保留业务比对所需的属性。

#### 耗时参考

Publisher ~13s，Subscriber ~16s（与 v2.0 相当，身份信息提取开销极小）

---

### 2.3 第三步：去重归簇 (`dedup_eoicd.py`)

**功能不变**。按 `FullName`（`signal_name`）相同归为一簇。

**调整点**：去重后的簇代表信号需包含完整的 `identity` 信息（因为 identity 已内嵌在单条信号记录中，去重逻辑无需改动，直接透传即可）。

**输入**：`eoicd_pub.json` / `eoicd_sub.json`  
**输出**：`eoicd_pub_clustered.json` / `eoicd_sub_clustered.json`

#### 输出 JSON 结构（单条簇）

```json
{
  "_meta": {
    "sheet": "A429-RP",
    "side": "publisher",
    "source_file": "AMS_EoICD_Publisher_Table.xlsx"
  },
  "signal_name": "HF_AMSC1.po429_OA_500.L304.WAIS_ON",
  "signal_short_name": "WAIS_ON",
  "identity": {
    "bus": "A429",
    "direction": "TX",
    "label": "304",
    "name": "WAIS_ON",
    "bit_range": "15-30"
  },
  "identity_key": "A429--TX--304--WAIS_ON--15-30",
  "attributes": {
    "DP": {
      "Name": "WAIS_ON",
      "BitOffsetWithinDS": 15,
      "DataFormatType": "BNR",
      "Label": 304,
      "ParameterSize": 16
    }
  },
  "_cluster": {
    "cluster_id": "A429-RP-pub-0001",
    "duplicate_count": 3,
    "members": [
      "HF_AMSC1.po429_OA_500.L304.WAIS_ON",
      "HF_AMSC1.po429_OA_500.L304.WAIS_ON",
      "HF_AMSC1.po429_OA_500.L304.WAIS_ON"
    ]
  }
}
```

**效果参考**（实际运行结果）：
- Publisher：12,400 条 → **2,646 簇**（压缩比 4.7x）
- Subscriber：14,506 条 → **2,160 簇**（压缩比 6.7x）

---

### 2.4 第 2.5 步：需求追溯过滤（`src/trace_eoicd.py`，新增·可选）

**功能**：在去重归簇之后，若输入目录中存在追溯表，则按需求追溯链路逐级追溯，
求出与输入软件高层需求（HLR）相关的 EoICD FullName 集合，只保留这些簇。

**输入**：
- 追溯表与软件高层需求文件（放在 `data/raw` 下，自动识别）
- 配置 `config/traceability.yaml`

**输出**：`data/processed/trace_result.json`（追溯链结果）

#### 检测逻辑

扫描 `data/raw` 下所有 `.xlsx/.docx`，对每张工作表只用**表头行**做关键字匹配：

| 表类型 | 识别条件（表头行必须同时含） | 本测试集中的实例 |
|--------|---------------------------|----------------|
| `eoicd_erd`（EoICD ↔ 需求） | `ERD编号` + `ICD FullName`（或 `系统需求编号` + `ICD FullName`） | 设备需求与系统ICD追溯表 / `待填_需求接口追溯表` |
| `sys_erd`（系统 ↔ 设备需求） | `系统需求` 系列列 | （4 层链路时出现） |
| `erd_hlr_matrix`（设备 ↔ 软件高层需求矩阵） | 出现 **2 个** `需求编号` 列（左右两栏） | 单模块需求矩阵分析（设备2软件高层） |
| `hlr_source`（软件高层需求输入） | 文件名含 `软件高层需求` / `HLR` | 空气管理系统控制器…软件高层需求规范.docx |

**两个关键坑（已规避）**：
1. **只用表头行，不用前几行拼接文本**。追溯表的「说明」Sheet 正文里常常出现 `ERD编号`、`ICD FullName` 等字样，若用拼接文本会把说明页误判成追溯表。
2. **矩阵表必须校验列重复数**。接口基线表（如 `接口基线表_EICD`）表头里有 `VBS处理列-关联的ERD需求编号`，只含 1 个「需求编号」列，靠「出现 2 个需求编号列」可准确排除。

#### 链路判定

| 检测到的表 | 链路 | 追溯顺序 |
|-----------|------|---------|
| 矩阵表 + EoICD↔需求表（左列为 ERD） | **3 层** | HLR → ERD → EoICD |
| 矩阵表 + 系统↔设备表 + EoICD↔需求表（左列为 SRD） | **4 层** | HLR → ERD → SRD → EoICD |

EoICD↔需求表左侧 ID 列的语义（ERD 还是 SRD）**自动判断**：先找 `系统需求编号`/`SRD编号` 列；
没有则按 ID 内容匹配 `*_ERD_*` / `*_SRD_*` 的数量占比决定。

#### 解析要点

- **合并单元格向下填充**：追溯表与矩阵表中，需求编号只在每组首行出现一次（Excel 合并单元格），
  解析时对 ID 列做 forward fill，否则会丢失大量行
- **矩阵表过滤**：下层「模块名称」需含 `软件高层需求`（排除混入的 `EICD`、`Draft` 条目）；
  上层「模块名称」需含 `需求规范` / `ERD`
- **HLR 编号提取（三种来源，按优先级）**：

  | 优先级 | 来源 | 提取方式 |
  |-------|------|---------|
  | 1（优先） | 结构化 HLR 结果 JSON（如 `hlr_clustered.json`） | 读 `requirements[].req_id` |
  | 2 | Word 需求文档 `.docx` | 直接解析 `word/document.xml`，正则提 `FSF*_HLR_*` |
  | 3 | Excel 需求清单 `.xlsx` | 优先读「需求编号」列，找不到再全表正则 |

  同一目录中同时存在时，**只用优先级最高的那种**（日志会打印 `HLR 来源: 结构化 JSON` 或 `需求文档`）。
  已实测两条路径结果完全一致（HLR 16 → ERD 11 → EoICD 372）。
  把 `hlr_clustered.json` 放进 `data/raw` 即自动切换到 JSON 来源。

#### 实测结果（AMS 控制器测试案例）

```
链路判定: 3-layer
软件高层需求(HLR): 16 个编号，来源 1 个文件（docx）
第 1 跳 HLR → 设备层级需求: 11 个（矩阵表有效关系 277 条）
最后 1 跳 需求 → EoICD: 372 个 FullName
```

过滤效果：

| 文件 | 过滤前 | 保留 | 剔除 | 追溯表中未命中 |
|------|-------|------|------|--------------|
| `eoicd_pub_clustered.json` | 2,621 簇 | **264** | 2,357 | 108 |
| `eoicd_sub_clustered.json` | 1,752 簇 | **68** | 1,684 | 304 |

输出文件：`eoicd_pub_clustered_traced.json`（264 簇）、`eoicd_sub_clustered_traced.json`（68 簇）。
完整版 `*_clustered.json` 仍会保留，便于对比。

**注意**：本测试案例中 docx 只含 16 个 HLR 编号，且分散覆盖了矩阵表中全部 11 个 ERD，
因此 ERD 层没有起到收敛作用；但 FullName 层仍从 4,373 簇收敛到 332 簇（7.6%）。
收敛效果取决于输入 HLR 文件的完整度。

#### 实测结果（配电装置 / ATA24EPS 控制器测试案例，4 层链路）

输入目录 `data/raw/配电装置追溯表-裁剪`，含 `ATA24EPS_EoICD_Publisher_Table.xlsx` / `ATA24EPS_EoICD_Subscriber_Table.xlsx`，以及三张追溯表：
`单模块需求矩阵分析(系统2设备).xlsx`（系统↔设备）、`单模块需求矩阵分析(设备2软件).xlsx`（设备↔软件）、`配电系统需求与EoICD追溯表_20260629_统计结果_RevB.xlsx`（EoICD↔系统需求），
HLR 输入为 `RPDU_HLR未注入故障v1.docx`。

```
链路判定: 4-layer
软件高层需求(HLR): 11 个编号
第 1 跳 HLR → 设备层级需求(ERD): 19 个
第 2 跳 ERD → 系统层级需求(SRD): 15 个
最后 1 跳 系统需求 → EoICD: 20,458 个 FullName
```

过滤效果：

| 文件 | 过滤前 | 保留 | 保留率 | 剔除 |
|------|-------|------|--------|------|
| `eoicd_pub_clustered.json` | 8,445 簇 | **6,780** | 80.3% | 1,665 |
| `eoicd_sub_clustered.json` | 1,358 簇 | **850** | 62.6% | 508 |

输出文件：`eoicd_pub_clustered_traced.json`（6,780 簇）、`eoicd_sub_clustered_traced.json`（850 簇），位于 `data/processed_eps/`。

**要点说明**：
- 4 层链路自动识别成功：追溯工具在 `EoICD↔需求` 表左列检测到 `*_SRD_*` / `*_SRS_*` 编号（如 `EPDSSRS_020942`），结合 `系统↔设备` 表的存在，判定为 4 层链路由 `build_trace()` 走 `HLR→ERD→SRD→EoICD`。
- 与 AMS 3 层相比，EPS 的保留率显著更高（>60% vs AMS 的 3.9%~10.1%），原因是 EPS 的 11 个 HLR 通过 19 个 ERD、15 个 SRD 覆盖了绝大多数 EoICD 信号；收敛效果同样取决于输入 HLR 的完整度。
- EPS 项目无定位过滤条件，流水线中 `skip_locate=True`，直接对原始表做裁剪 + 追溯 + 去重。

---

### 2.5 第四步：生成身份主键（`dedup_eoicd.py` 内嵌）

**功能**：去重后的每个簇，基于其 `identity` 五元组生成一个**身份主键字符串**，用于后续正向匹配阶段的快速身份比对。

**生成规则**：

```
identity_key = "{bus}--{direction}--{label}--{name}--{bit_range}"
```

- 分隔符：固定为 `--`（双连字符）
- 字段顺序：总线 → 方向 → label → name → 位范围
- **None 值处理**：若 identity 中某个字段为 `None`，在 key 中转为字符串 `"None"`，保持 key 格式统一
- **方向的作用**：`direction` 作为第二级过滤，保证 TX 与 RX 的簇不会互配

**示例**：

| 侧 | identity | identity_key |
|---|---------|-------------|
| Publisher | `{"bus": "A825", "direction": "TX", "label": null, "name": "SPEED", "bit_range": "16-31"}` | `A825--TX--None--SPEED--16-31` |
| Publisher | `{"bus": "A429", "direction": "TX", "label": "304", "name": "WAIS_ON", "bit_range": "15-30"}` | `A429--TX--304--WAIS_ON--15-30` |
| Subscriber | `{"bus": "A429", "direction": "RX", "label": "134", "name": "L134_AIRCRAFT_REG_NUMBER_CHAR_1_OHMS_R2B", "bit_range": "0-31"}` | `A429--RX--134--L134_AIRCRAFT_REG_NUMBER_CHAR_1_OHMS_R2B--0-31` |

**输出 JSON 结构（单条簇，含 identity_key）**：

```json
{
  "_meta": {...},
  "signal_name": "HF_AMSC1.po429_OA_500.L304.WAIS_ON",
  "signal_short_name": "WAIS_ON",
  "identity": {
    "bus": "A429",
    "direction": "TX",
    "label": "304",
    "name": "WAIS_ON",
    "bit_range": "15-30"
  },
  "identity_key": "A429--TX--304--WAIS_ON--15-30",
  "attributes": {...},
  "_cluster": {...}
}
```

**注意**：身份主键在去重阶段生成，因为此时每个信号的身份信息已通过第二步提取并随记录透传。去重后每个簇的 representative 直接拼接其 `identity` 五元组即可。

**实测区分度**（2026-09-01）：

| 侧 | 簇数 | 不同 key 数 | 区分度 |
|----|------|-----------|--------|
| Publisher | 2,621 | 701 | 26.7% |
| Subscriber | 1,752 | 838 | 47.8% |
| **pub/sub 跨侧重合 key** | — | — | **0 个** |

方向入 key 后 pub/sub 无任何重合 key，验证了 TX/RX 天���隔离。区分度偏低的原因是 Subscriber 侧大量整字参数兜底为 `0-31`、Discrete/Analog 侧 label 全为 `None`，正向匹配阶段需配合 signal_name 等字段进一步区分。

---

## 三、代码调整清单

### 3.1 `src/process_eoicd.py`（裁剪工具）

**调整位置**：在逐行解析的「构建信号名」之后、「裁剪属性」之前，插入身份信息提取逻辑。

**新增代码逻辑**：

```python
import re

label_pattern = re.compile(r'^L(\d+)')

# 总线正则模式（按优先级排序）
bus_patterns = [
    (re.compile(r'ANALOG|ANLG', re.I), 'Analog'),  # 匹配 ANALOG / ANLG → Analog
    (re.compile(r'DISC', re.I), 'Discrete'),       # 匹配 DISC → Discrete
    (re.compile(r'429', re.I), 'A429'),            # 匹配 429 → A429
    (re.compile(r'825', re.I), 'A825'),            # 匹配 825 → A825
    (re.compile(r'664', re.I), 'A664'),            # 匹配 664 → A664
]

# 在现有代码中，leaf_values 已包含 DP/RP 层的所有属性值
leaf_values = layer_values[leaf_idx]

# 提取身份信息（在裁剪之前）
identity = {
    "bus": None,      # 见总线提取规则
    # direction: Publisher 只取 DP 层 → TX（发送）
    #            Subscriber 只取 RP 层 → RX（接收）
    "direction": "TX" if side == "publisher" else "RX",
    "label": None,    # 见 Label 提取规则
    "name": signal_short_name,  # DP/RP 层的 Name 属性值
    "bit_range": None,  # 见 bit_range（位范围）提取规则
}

# ====== bus 提取 ======
if side == "publisher":
    # Publisher: 从 Sheet 名称提取
    bus_match = re.match(r'^(.+)-RP$', sheet_name)
    identity["bus"] = bus_match.group(1) if bus_match else None
else:
    # Subscriber: 从第二个 LogicalPort.Physical 提取，不回退
    # 注意：Subscriber 表有两个 LogicalPort 层，第二个才是 Subscriber 侧
    physical = layer_values.get("LogicalPort", {}).get("Physical")
    if physical:
        physical_str = str(physical).strip()
        for pattern, bus_name in bus_patterns:
            if pattern.search(physical_str):
                identity["bus"] = bus_name
                break
        else:
            # Physical 有值但无法匹配任何模式
            identity["bus"] = None
    else:
        # Physical 不存在或为空
        identity["bus"] = None

# ====== label 提取 ======
if side == "publisher":
    # Publisher: 从 A429Word.Name 提取 Label（仅当 A429Word 层存在时）
    a429word_layer = layer_values.get("A429Word")
    if a429word_layer:
        a429word_name = a429word_layer.get("Name")
        if a429word_name:
            match = label_pattern.match(str(a429word_name))
            identity["label"] = match.group(1) if match else None
        else:
            identity["label"] = None  # A429Word 层存在但 Name 为空
    else:
        identity["label"] = None  # 该 Sheet 不存在 A429Word 层
else:
    # Subscriber: 直接从 RP.Label 读取
    label_raw = leaf_values.get("Label")
    if label_raw is not None:
        label_str = str(label_raw).strip()
        identity["label"] = label_str if label_str else None
    else:
        identity["label"] = None

# ====== bit_range 提取（位范围闭区间）======
def _to_int(value):
    """单元格值安全转 int；空值/非法值返回 None（兼容 '16'、16.0、' 16 '）"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


bit_attr = "BitOffsetWithinDS" if side == "publisher" else "BitOffsetWithinMsg"
bit_offset = _to_int(leaf_values.get(bit_attr))
bit_size = _to_int(leaf_values.get("ParameterSize"))

# 偏移为负（如 -1）在 ICD 中表示「未定义」，等同缺失，
# 否则会生成 "-1--1" 这类有歧义的范围字符串
if bit_offset is not None and bit_offset < 0:
    bit_offset = None

if bit_size is not None and bit_size > 0:
    start = bit_offset if bit_offset is not None else 0
    identity["bit_range"] = f"{start}-{start + bit_size - 1}"

# 输出时加入 identity 字段
signals.append({
    "_meta": {...},
    "signal_name": signal_name,
    "signal_short_name": signal_short_name,
    "identity": identity,        # 【新增】
    "attributes": attrs,         # 裁剪后的属性（现有逻辑）
})
```

### 3.2 `src/dedup_eoicd.py`（去重工具）

**v4.0 调整**：身份主键由四元组改为**五元组**，新增 `direction`，`bit` 改为 `bit_range`。

```python
# 格式: bus--direction--label--name--bit_range
identity = representative.get("identity", {})
bus = identity.get("bus")
direction = identity.get("direction")
label = identity.get("label")
name = identity.get("name")
bit_range = identity.get("bit_range")
representative["identity_key"] = (
    f"{bus if bus is not None else 'None'}--"
    f"{direction if direction is not None else 'None'}--"
    f"{label if label is not None else 'None'}--"
    f"{name if name is not None else 'None'}--"
    f"{bit_range if bit_range is not None else 'None'}"
)
```

去重组簇逻辑本身无需改动：`identity` 已作为信号记录的字段存在，按 `signal_name` 分组后直接透传。

**v5.0 调整**：新增 `--trace-file`（-t）可选参数。提供追溯结果时，在归簇后按 FullName 过滤，
并额外输出 `*_clustered_traced.json`（完整版仍然保留，便于对比）。

```python
def filter_by_trace(deduped, trace_file):
    trace = json.load(open(trace_file))
    if not trace.get("enabled"):
        return deduped, {"enabled": False, ...}      # 无追溯表 → 原样返回

    allowed = set(trace.get("eoicd_fullnames", []))
    kept = [c for c in deduped if c.get("signal_name", "") in allowed]
    ...
```

不传 `-t` 时行为与 v4.0 完全一致。

### 3.3 `src/trace_eoicd.py`（追溯工具，v5.0 新增）

独立脚本，可单独运行：

```bash
python3 src/trace_eoicd.py -c config/traceability.yaml -o data/processed/trace_result.json
```

主要函数：

| 函数 | 职责 |
|------|------|
| `detect_tables()` | 扫描输入目录，按表头关键字识别追溯表与 HLR 输入文件 |
| `load_hlr_ids()` | 从 docx / xlsx 提取软件高层需求编号 |
| `parse_matrix_table()` | 解析「设备 ↔ 软件高层需求」矩阵表（左右两栏 + 向下填充） |
| `parse_sys_erd_table()` | 解析「系统 ↔ 设备需求」表（4 层链路） |
| `parse_icd_table()` | 解析「EoICD ↔ 需求」表，自动判断左列是 ERD 还是 SRD |
| `build_trace()` | 判定链路层数并逐级追溯 |

`trace_result.json` 结构：

```json
{
  "enabled": true,
  "chain": "3-layer",
  "sources": { "eoicd_erd": [...], "erd_hlr_matrix": [...], "hlr_source": [...] },
  "counts": { "hlr": 16, "erd": 11, "sys": 0, "eoicd_fullname": 372 },
  "hlr_ids": ["FSF21000101_HLR_1237", "..."],
  "erd_ids": ["AMSC_ERD_5020", "..."],
  "sys_ids": [],
  "eoicd_fullnames": ["HF_AMSC2.pi429_B2_275_2.L275_2_B2_OVHD_C", "..."],
  "details": { "matrix": {...}, "icd_trace": {...} }
}
```

`enabled: false` 时流水线按原方案全量保留。

### 3.4 `config/traceability.yaml`（追溯配置，v5.0 新增）

配置化文件识别、列定位、ID 正则与过滤策略，**换项目时改配置即可，不用改代码**。关键项：

- `scan_dirs` / `scan_extensions`：扫描范围
- `table_types.*.required_header_keywords`：表类型识别关键字
- `table_types.erd_hlr_matrix.required_repeat`：矩阵表需出现 2 个「需求编号」列
- `columns.*`：各表的列名（支持同名列按出现次序定位）
- `id_patterns`：HLR / ERD / SRD 编号正则
- `filter.lower_doc_keywords` / `upper_doc_keywords`：矩阵表行过滤

### 3.5 `config/eoicd_crop.yaml`（裁剪配置）

**无需改动**。当前配置已包含 `Name`、`Label`、`BitOffsetWithinDS`/`BitOffsetWithinMsg`、`ParameterSize` 等字段，满足身份提取需求。

> 注意：身份信息是从**原始行数据**中提取的，不依赖裁剪清单；即使某个身份相关字段不在裁剪清单中，也已在 `identity` 中保留。

### 3.6 一键流水线多项目支持（`run_data_processing.py` 的 `--project` 参数）

不同项目的 EoICD 表名、输入目录、链路层数都不同，统一在 `run_data_processing.py` 顶部的 `PROJECTS` 字典登记，命令行用 `--project` 切换，**代码逻辑无需改动**：

```python
PROJECTS = {
    # 空气管理系统控制器（AMS）：3 层链路 HLR → ERD → EoICD
    "ams": {
        "raw_dir": "data/raw",
        "output_dir": "data/processed",
        "pub": "AMS_EoICD_Publisher_Table.xlsx",
        "sub": "AMS_EoICD_Subscriber_Table.xlsx",
        "located": "AMS_EoICD_{side}_Located.xlsx",
        "locate_config": "eoicd_locate.yaml",
        "skip_locate": False,
    },
    # 配电装置（RPDU / ATA24EPS）：4 层链路 HLR → ERD → SRD → EoICD
    "eps": {
        "raw_dir": "data/raw/配电装置追溯表-裁剪",
        "output_dir": "data/processed_eps",
        "pub": "ATA24EPS_EoICD_Publisher_Table.xlsx",
        "sub": "ATA24EPS_EoICD_Subscriber_Table.xlsx",
        "located": "ATA24EPS_EoICD_{side}_Located.xlsx",
        "locate_config": "eoicd_locate_eps.yaml",
        "skip_locate": True,   # 无定位过滤条件，直接处理原始表
    },
}
```

命令行用法：

```bash
# 空气管理系统（3 层链路），输出到 data/processed
python3 run_data_processing.py --project ams

# 配电装置（4 层链路），输出到 data/processed_eps
python3 run_data_processing.py --project eps

# 只跑某一步
python3 run_data_processing.py --project eps --step trace

# 覆盖输入输出目录（不改 PROJECTS 也可临时指定）
python3 run_data_processing.py --project ams --raw-dir data/raw --output-dir data/out
```

参数说明：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--project` | `ams` | 项目标识，决定输入文件、输出目录与链路层数 |
| `--step` | `all` | 单步执行 `locate` / `crop` / `trace` / `dedup` 或 `all` |
| `--raw-dir` | 取项目配置 | 覆盖输入目录 |
| `--output-dir` | 取项目配置 | 覆盖输出目录 |
| `--config-dir` | `config` | 配置目录（含 `eoicd_crop.yaml` / `traceability.yaml`） |

新增项目只需在 `PROJECTS` 增加一项并保证 `raw_dir` 下存在对应 EoICD 表与追溯表即可；表结构差异（列名、ID 正则）仍通过 `config/traceability.yaml` 配置化调整。

---

## 四、目录结构

```
EoICD数据处理/
├── config/
│   ├── eoicd_locate.yaml          # 定位配置
│   ├── eoicd_crop.yaml            # 裁剪配置
│   └── traceability.yaml          # 追溯配置（v5.0 新增）
├── data/
│   ├── raw/                       # 输入文件（EoICD 表 + 追溯表 + HLR 文档）
│   │   ├── AMS_EoICD_Publisher_Table.xlsx
│   │   ├── AMS_EoICD_Subscriber_Table.xlsx
│   │   ├── 单模块需求矩阵分析（设备2软件高层）-裁剪.xlsx   # 追溯表（可选）
│   │   ├── 设备需求与系统ICD追溯表.xlsx                    # 追溯表（可选）
│   │   └── 空气管理系统控制器…软件高层需求规范.docx         # HLR 输入（可选）
│   └── processed/                 # 处理后文件
│       ├── AMS_EoICD_Publisher_Located.xlsx
│       ├── AMS_EoICD_Subscriber_Located.xlsx
│       ├── eoicd_pub.json         # 含 identity
│       ├── eoicd_sub.json         # 含 identity
│       ├── trace_result.json      # 追溯链结果（v5.0 新增）
│       ├── eoicd_pub_clustered.json        # 含 identity（完整版）
│       ├── eoicd_sub_clustered.json        # 含 identity（完整版）
│       ├── eoicd_pub_clustered_traced.json # 追溯过滤后（v5.0 新增）
│       └── eoicd_sub_clustered_traced.json # 追溯过滤后（v5.0 新增）
├── src/
│   ├── locate_eoicd.py            # 第一步：定向定位（不变）
│   ├── process_eoicd.py           # 第二步：裁剪 + 身份提取
│   ├── trace_eoicd.py             # 第 2.5 步：追溯链解析（v5.0 新增，可选）
│   └── dedup_eoicd.py             # 第三步+第四步：去重归簇 + 追溯过滤 + 生成身份主键
├── run_data_processing.py         # 一键流水线（--step locate/crop/trace/dedup/all）
├── DATA_PROCESSING_PLAN.md        # 本文件
└── MATCHING_PLAN.md               # 正向匹配方案（待补充）
```

---

## 五、待补充事项

| 事项 | 优先级 | 状态 | 说明 |
|------|--------|------|------|
| **总线（bus）提取方式** | 高 | ✅ **已完成** | 已按主人意见详细定义并验证。Publisher 从 Sheet 名提取，Subscriber 从 LogicalPort.Physical 提取（**不回退**）。 |
| **Label 提取方式** | 高 | ✅ **已完成** | 已按主人意见详细定义。Publisher 从 A429Word.Name 提取（A429Word 层存在时），Subscriber 从 RP.Label 读取。提取不到时 Label = None。 |
| **身份主键生成** | 高 | ✅ **已完成** | 去重后每个簇生成 `identity_key`，v4.0 格式为 `bus--direction--label--name--bit_range`，用于正向匹配快速身份比对。 |
| **信号方向（direction）** | 高 | ✅ **已完成** | Publisher 表取 DP 层 → `TX`（发送），Subscriber 表取 RP 层 → `RX`（接收）；已纳入身份主键，pub/sub 跨侧重合 key 为 0。 |
| **bit → bit_range（位范围）** | 高 | ✅ **已完成** | 由 `BitOffsetWithinDS/Msg` + `ParameterSize` 计算闭区间。偏移缺失或为负数（ICD 中 -1 表示未定义）时从 0 兜底；长度缺失时 `bit_range = None`。 |
| bit 提取验证 | 中 | ✅ 已验证 | Publisher 偏移 100% 有值；Subscriber 侧 71.2% 偏移在源数据中为空（整字参数），按兜底规则处理。 |
| **Windows 环境验证** | 高 | ✅ **已完成** | 2026-09-01 在 Windows 上完整跑通四步，pub 12,200 条→2,621 簇，sub 12,274 条→1,752 簇，与原记录一致。 |
| **身份主键区分度** | 中 | ⚠️ **待观察** | pub 区分度 26.7%、sub 47.8%。Subscriber 侧 8,756 条整字参数兜底为 `0-31`，正向匹配阶段可能需要引入 signal_name 或上层端口名做复合比对。 |
| **需求追溯过滤** | 高 | ✅ **已完成** | 3 层链路已实测（AMS 测试案例）：HLR 16 → ERD 11 → EoICD 372；pub 2,621→264 簇，sub 1,752→68 簇。 |
| 无追溯表场景 | 高 | ✅ **已验证** | 输入目录中无追溯表时，`trace_result.json` 输出 `enabled=false`，dedup 打印「保留全部簇（按原方案继续）」，行为与 v4.0 完全一致。 |
| 4 层链路实测 | 中 | ✅ **已完成** | 配电装置 / ATA24EPS（4 层链路）已实测：HLR 11 → ERD 19 → SRD 15 → EoICD 20,458；pub 8,445→6,780 簇（80.3%），sub 1,358→850 簇（62.6%）。自动识别 SRD 列成功。详见 2.4 节「实测结果（配电装置 / ATA24EPS …）」与 3.6 节多项目支持。 |
| 追溯表与 EoICD 版本一致性 | 中 | ⚠️ **待确认** | 当前 `data/raw` 的 EoICD 表是 8/20 版本，追溯表基于 7/17 版本。追溯表中 372 个 FullName，pub 只命中 264、sub 只命中 68，合计 332，有 40 个在两侧都找不到。建议确认是否需换成同版本 EoICD 表。 |

---

## 六、下一步

1. ✅ ~~修改 `process_eoicd.py` 实现身份信息提取~~（v4.0 已完成：bus / direction / label / name / bit_range）
2. ✅ ~~运行验证~~（2026-09-01 Windows 环境已跑通并校验）
3. ✅ ~~需求追溯过滤~~（v5.0 已完成，3 层链路 AMS 已实测、4 层链路 EPS 已实测，均通过自动链路判定）
4. **进入正向匹配阶段开发**：需确认身份主键区分度是否满足要求，必要时引入 signal_name 复合比对
5. **换项目时**：只需改 `config/traceability.yaml`（列关键字、ID 正则），代码不用动

---

> **本方案 v5.0 核心改动**：新增「需求追溯过滤」环节（`src/trace_eoicd.py`）。
> - 自动检测 `data/raw` 中是否存在追溯表：**没有则完全按原方案执行**，有则按链路逐级追溯
> - **3 层链路**：HLR →(设备↔软件追溯表)→ ERD →(EoICD↔设备追溯表)→ EoICD
> - **4 层链路**：HLR →(设备↔软件追溯表)→ ERD →(系统↔设备追溯表)→ SRD →(EoICD↔系统追溯表)→ EoICD
> - 归簇后只保留追溯链命中的 FullName，输出 `*_clustered_traced.json`，完整版同时保留
>
> **v4.0 改动**：
> 1. 身份信息新增 `direction`（TX = Publisher 表 DP 层发送，RX = Subscriber 表 RP 层接收），并纳入身份主键作为第二级过滤。
> 2. `bit` 改为 `bit_range`，由位偏移 + `ParameterSize` 计算闭区间位范围（如 `16-31`），偏移缺失或为负时从 0 兜底。
>
> 身份主键格式：`bus--direction--label--name--bit_range`。
