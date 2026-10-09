# HLR 软件高层需求聚类工具

把民机机载软件高层需求（HLR）文档（`.docx`）中的需求条目，按"需求文本中明确提及的接口属性"聚类，输出 JSON。是"EoICD 接口要求 → 软件高层需求落实性检查"流水线的上游模块。

## 一、目录结构

```
hlr_cluster_tool/
├── cluster_hlr.py          # 核心脚本：需求聚类（Python 3.8+）
├── ai_client.py            # AI 接口层：OpenAI 兼容的 LLM 客户端（纯标准库）
├── ai_maps.py              # AI 映射生成/增量更新：name/state/signal 三表（可单独运行）
├── ai_config.example.json  # AI 接口配置模板（复制为 ai_config.json 后填写）
├── eoicd_crop.yaml         # 属性清单（类的框架，23 个属性）
├── name_map.json           # AI 辅助识别的信号名映射（Name 属性用，可选）
├── state_map.json          # AI 辅助识别的状态定义映射（OneState/ZeroState 用，可选）
├── signal_map.json         # AI 辅助识别的信号级拆分映射（多信号需求按信号拆，可选）
├── 属性识别规则.md          # 属性识别规则说明（重要，先读）
├── requirements.txt        # 依赖：PyYAML
└── example/
    ├── HLR_完整未故障注入.docx      # 示例输入 A（主机 AMS 写法：中文名 + label/bit/SDI）
    ├── HLR聚类结果.json            # 示例输出 A：属性聚类结果
    ├── HLR需求去重结果.json         # 示例输出 A：需求级去重结果（供身份匹配）
    ├── RPDU_HLR未注入故障v1.docx    # 示例输入 B（RPDU 写法：英文信号名 + <占位符> + 信号列举）
    ├── RPDU聚类结果.json           # 示例输出 B：属性聚类结果
    ├── RPDU需求去重结果.json        # 示例输出 B：需求级去重结果
    ├── FGMC软件高层需求.docx         # 示例输入 C（FGMC 写法：全英文表头 + 条件从句 + Label114）
    ├── FGMC聚类结果.json           # 示例输出 C
    ├── HSCU软件高层需求.docx         # 示例输入 D（HSCU 写法：助记符标签/信号 LBL_* / HYD_*）
    └── HSCU聚类结果.json           # 示例输出 D
```

## 二、环境准备

```bash
# 1. 安装 Python 3.8+
# 2. 安装依赖（只需 PyYAML，其余为标准库）
pip install -r requirements.txt
```

## 三、运行

```bash
# 默认：处理 example/ 下的示例 docx，用同目录 eoicd_crop.yaml
python cluster_hlr.py

# 指定输入输出
python cluster_hlr.py --docx 需求文档.docx --yaml eoicd_crop.yaml --out 结果.json

# Name 属性用 AI 辅助识别的信号名（推荐，name_map.json 由 AI 提取生成）
python cluster_hlr.py --name-map name_map.json

# OneState/ZeroState 用 AI 辅助识别的状态定义（推荐，state_map.json 由 AI 提取生成）
python cluster_hlr.py --state-map state_map.json

# 多信号需求按信号拆分接口项（推荐，signal_map.json 由 AI 提取生成）
python cluster_hlr.py --signal-map signal_map.json

# 关闭"多信号需求自动拆分"（默认开启：需求列举 ≥2 个英文信号名时自动按信号拆项）
python cluster_hlr.py --no-auto-split

# 指定需求级去重结果输出路径（默认与 --out 同目录，文件名"聚类结果"→"需求去重结果"）
python cluster_hlr.py --dedup-out 需求去重结果.json

# 需求变更后自动更新三个映射表（需先配置 AI 接口，见第七节）
python cluster_hlr.py --ai-update

# 只查看变更计划、不调用 AI（离线可用）
python cluster_hlr.py --ai-update --ai-dry-run

# 终端打印每个属性类的成员
python cluster_hlr.py --debug
```

## 四、输入格式要求

**需求文档（.docx）**：每条需求一个表格，字段为两列键值对，其中必须包含：
- `需求ID`（如 `FSF21000101_HLR_280`、`FSF24710101A_HLR_052331`，格式不限）
- `需求中文`（需求正文）

> **只从表格提取，表外正文不参与**：文档里"范围/术语/缩略语/设备概述"等说明性段落、
> 以及需求表格前的小标题，都**不参与任何提取**（FGMC 这类文档正文里也写总线/信号，但那是说明不是需求）。
> 表格本身按下列规则过滤，跳过清单见输出的 `meta.skipped_tables[]`：
> 非需求表（无需求编号字段）、需求编号为 N/A 的说明表、"是否为需求=否"、空表。
> 实测：FGMC 12 张表 → 跳过 5 张 → 7 条需求；HSCU 12 张表 → 跳过 2 张 → 10 条需求。

> **多系统写法已做兼容**（不同系统提交的文档写法不一致），详见 `属性识别规则.md` 三·补4：
> 表头字段名别名（需求编号/需求描述/需求正文/原理/实现方式…）、需求 ID 格式不限、
> 尖括号占位符 `<xxx>` 处理、下划线式英文信号名（`RAT_HEAT_FAULT`）、助记符信号（`HYD_1_LO_PRESS`）、
> 名称型助记标签（`LBL_DIS_00_SYS1` → 记入 `label_name`，不作为 LABEL 号）、
> 多信号需求自动拆分、条件从句（FGMC "若…为X，软件应将…"）、"X总线"优先识别、周期/bit/label 多种写法、
> bit 的两种笔法（`bitN` 0-based / "第N位" 1-based 减 1，连贯位段自动合并）等。

**检测范围分两类，口径不同**：

| 内容 | 范围 | 说明 |
|------|------|------|
| 属性类判定（23 个属性） | **整条需求表格的相关字段**（需求中文、基本原理、验证方法/实现方法等） | 量程、单位、分辨率等常写在"基本原理"里，同样参与检测；证据标注来源字段（`[需求中文]` / `[基本原理]`）；`对象类型`/`是否衍生`/`安全相关` 等元数据不参与 |
| 身份信息（bus/label/bit/name/direction） | **只认"需求中文"** | 身份是需求对信号本身的描述；若并入其他字段，会被别的字段的总线字样污染（如模拟量需求"从ADCINA3接收"被带成 A429） |

总线（bus）只认需求中文里显式出现的关键词（A429/A825/模拟量等），需求没写就为 null，
不从章节、LABEL 字段名或其他表格字段推断。

**属性清单（.yaml）**：`eoicd_crop.yaml` 定义"有哪些属性类"，结构为
`publisher/subscriber → 总线(sheet) → 层 → 属性名列表`。属性名即聚类的"类"，
同名属性自动合并为一个类。见文件内注释。

## 五、输出 JSON 结构

```json
{
  "meta": {
    "req_count": 16, "item_count": 29, "dedup_count": 16,
    "dropped_req_count": 0,                        // 身份全空、未生成聚类的需求数
    "skipped_table_count": 1,                      // 跳过的非需求表格数
    "skipped_tables": [                            // 跳过审计：哪张表、为什么、表前文字是什么
      { "table_no": 1, "reason": "非需求表（无\"需求编号/需求ID\"字段）",
        "preceding_text": "以下术语和定义适用于本文件。", "desc": null }
    ],
    "parse_scope": "仅从 docx 表格提取需求；表格之外的正文段落不参与任何提取；…",
    "detection_fields": ["需求中文", "基本原理", "验证方法"],
    "attribute_count": 23, "...": "..."
  },
  "attribute_classes": [               // 23 个属性类（含空类）
    {
      "attribute": "Label",
      "sources": ["publisher.A429-RP.DP", "subscriber.A429-RP.RP"],
      "item_count": 12,
      "req_ids": ["FSF21000101_HLR_280", ...],
      "requirements": [
        { "req_id": "...", "label": "L36", "bit": "13-28",
          "name": "AFTEFAN1速度信号", "direction": "RX",
          "attr_value": "36",              // 提取出的该属性值（Label 类=label 号）
          "evidence": ["[需求中文]L36"] }
      ]
    }
  ],
  "requirements": [                    // 每条需求及其归属类
    { "req_id": "...", "bus": "A429", "label": "36", "bit": "13-28",
      "direction": "RX",               // 信号方向：RX=接收 / TX=发送 / null=未提及
      "label_name": null,              // 名称型助记标签（LBL_DIS_00_SYS1），非 LABEL 号；无则 null
      "attribute_classes": ["Name","Label","BitOffsetWithinMsg","SDIExpected"],
      "req_text": "..." }
  ],
  "dropped_requirements": [            // 身份信息全空、未生成聚类的需求（归档，不静默丢弃）
    { "req_id": "...", "req_text": "...", "item_count": 1,
      "reason": "身份信息（bus/label/name/bit）全为空，未生成聚类" }
  ]
}
```

- **身份信息全空的需求不保留聚类**：`bus`/`label`/`name`/`bit` 全为 `null` 时，该接口项从
  `attribute_classes`、`requirements`、去重结果中移除，但会归档到顶层的 `dropped_requirements[]`，
  并在 `meta.dropped_req_count` / `meta.dropped_item_count` 给出计数，便于人工复核。
  典型场景：需求只写"以 EOICD 规定的刷新率处理 `LBL_XXX`"，`LBL_XXX` 是模板占位符、无具体标签/信号。

- `label`（LABEL 号）：**只认数字编号**（`L36` / `LABEL126` / `Label114` / `标号126`）。
  需求里没有具体编号时为 `null` —— **不用助记名顶替**（部分系统的 ICD 里标签只有编号，
  文档内的 `LBL_DIS_00_SYS1` 只是助记名，不是 LABEL 号）。
- `label_name`（助记标签名）：`LBL_*` 形式的名称型标签（子字段 `LBL_X_SSM`/`LBL_X_Status` 不算独立标签，
  模板占位符 `LBL_XXX` 剔除）。**仅作附加信息**，不参与 `label` 判定，也不算身份是否为空。
- `direction`（信号方向）：从**需求中文**提取，`RX`=接收信号、`TX`=发送信号、`null`=文本未提及方向。
  TX 特征优先（发送数据/发送通道/发送/写入/置入），RX 特征其次（接收/解析/采集/读取）——
  "接收到ATP指令后发送XX"判 TX；"发送风扇状态消息""采集离散量故障状态"等也正确识别。

- `name`（信号名）：从**需求中文**提取，采用「显式标注 → A429 固定格式字段 → 英文标识符 →
  分级锚点 → 全局兜底」五级算法（见 `属性识别规则.md` 三·补）；提取不到为 `null`（不臆造）。
  可选由 AI 辅助映射（name_map / signal_map）覆盖。
- `attr_value`：从需求表格文本提取的该属性取值（Label=label 号、BitOffset=bit 范围、FuncRng=量程上下限、
  RefreshPeriod=周期值、Units=单位、MessageSize=消息长度、ActivityTimeout=活动超时值等），用于横向对比同类需求。
- `evidence`：触发归类的原文片段（来源字段 `[需求中文]`/`[基本原理]`；AI 辅助识别的来源为
  `[AI辅助识别]`；多信号自动拆分出的接口项来源为 `[信号自动拆分]`）。
- 空类（需求未提及的属性）也保留在 `attribute_classes` 中，`item_count=0`。**23 个属性均有识别规则**；
  若某属性在需求中确实未被提及，该类为空属正常结果。
- 涉及 `bitX=1`/`bitX=0` 编码定义的需求，会同时归入 **OneState、ZeroState、CodedSet** 三个类。

### 需求级去重结果（`HLR需求去重结果_*.json`）

独立于属性聚类结果的第二份输出，供**后续身份匹配阶段**使用：每条需求唯一一条记录，
合并其全部接口身份与属性类并集。

```json
{
  "dedup_requirements": [
    {
      "req_id": "FSF21000101_HLR_547",
      "bus": "A429",
      "interfaces": [
        { "label": "L11", "name": "压调OFVTRV失效在关位故障有效标志", "bit": "22", "direction": "TX" },
        { "label": "L11", "name": "RFAN可用标志", "bit": "24", "direction": "TX" }
      ],
      "interface_count": 7,
      "attribute_classes": ["Name", "BitOffsetWithinDS", "CodedSet", "Label", "OneState", "ZeroState"],
      "req_text": "..."
    }
  ]
}
```

- 多信号需求（signal_map 拆分）在此合并为一条需求的多个 `interfaces`；
- `attribute_classes` 为该需求所有接口项的属性类并集（按属性清单顺序）。

## 六、AI 辅助识别与映射更新

Name（信号名）、OneState/ZeroState（状态定义）、信号级拆分（多信号需求）语义性强，
纯正则提取质量不稳，均支持两级：

1. **正则兜底**（默认，脚本内置）。
2. **AI 辅助识别**（推荐）：AI 读需求提取，人工确认后产出映射文件，脚本优先用它覆盖正则结果：
   - `name_map.json`（`--name-map`，Name 属性用，全量覆盖）
   - `state_map.json`（`--state-map`，OneState/ZeroState 用，部分覆盖：只含涉及状态定义的需求）
   - `signal_map.json`（`--signal-map`，信号级拆分用，部分覆盖：只含多信号需求，如 HLR_267/547/4510）

> **信号名语义约定**：无具体信号名、但需求操作 A429 固定格式字段（SDI/SSM/奇偶校验 Parity）时，
> name 识别为该字段名（如"在LABEL的SDI位写入固定数据" → name=SDI，HLR_544）。
> 此类需求在身份匹配阶段按"通用需求"单独汇总（match_engine 识别 name 为字段名的需求），不逐簇匹配。

**signal_map.json 格式**（`req_id -> 接口项列表`）：

```json
{
  "FSF21000101_HLR_547": {
    "value": [
      { "label": "11", "name": "压调OFVTRV失效在关位故障有效标志", "bit": "22", "direction": "TX" },
      { "label": "11", "name": "RFAN可用标志", "bit": "24", "direction": "TX" }
    ],
    "text_hash": "0b6026b2"
  }
}
```

有 signal_map 记录的需求按接口项逐条输出（每项独立的 label/name/bit/direction，且 BitOffset
属性值按该项 bit 覆盖）；无记录的需求回退按 label 级拆分。

**映射默认不会自动更新**（AI 静态产物，脚本只读不写），但脚本运行时做**两级一致性校验**，
需求更新后自动提示，结果写入 `meta.name_map_status` / `meta.state_map_status` / `meta.signal_map_status`：

| 检测 | 触发 | 说明 |
|------|------|------|
| missing | 新增需求（映射无此 ID） | name_map 提示（full 覆盖）；state_map/signal_map 不提示（partial 覆盖属正常） |
| stale | 删除需求（映射有冗余 ID） | 提示，映射可清理 |
| text_changed | **需求中文已变但 ID 未变** | 提示"映射值可能过时，需重新生成" |

**text_changed 依赖映射文件带 `text_hash`**：映射格式为
`{"req_id": {"value": <值>, "text_hash": "<需求中文md5前8位>"}}`（也兼容旧的
`{"req_id": <值>}` 简单格式，但无内容级检测）。AI 生成映射时写入当前需求原文的 hash，
脚本运行时比对，发现原文变化即提示重新生成。

> 想让它**自动更新**而不必每次人工重生成？配置 AI 接口后加 `--ai-update` 即可，见下一节。

## 七、AI 接口：需求变更后自动更新映射

工具内置 AI 接口层（`ai_client.py` + `ai_maps.py`），配置好大模型 API 后，脚本可在检测到
需求变更（新增/删除/原文改动）时**自动增量重建**三个映射表。

### 7.1 配置

三种方式，优先级 **命令行 > 环境变量 > 配置文件 > 内置默认**：

```bash
# 方式一：配置文件（推荐）——复制模板并填写
cp ai_config.example.json ai_config.json
#   编辑 ai_config.json，填 base_url / api_key / model

# 方式二：环境变量
export HLR_AI_BASE_URL=https://api.deepseek.com/v1
export HLR_AI_API_KEY=sk-xxxxxxxx
export HLR_AI_MODEL=deepseek-chat

# 方式三：命令行（临时覆盖）
python cluster_hlr.py --ai-update --ai-base http://10.0.0.100:8000/v1 --ai-key xxx --ai-model Qwen2.5-32B-Instruct
```

只要服务是 **OpenAI 兼容协议**（`/chat/completions`）即可，包括 DeepSeek、通义千问、
智谱 GLM、Kimi，以及**内网部署的 vLLM / Ollama / one-api 网关**——`ai_config.example.json`
里给了各类示例。实现只用标准库 `urllib`，**不引入任何新依赖**。

先做一次连通性自检：

```bash
python ai_client.py --ai-config ai_config.json
# 配置: deepseek-chat @ https://api.deepseek.com/v1 (key=sk-x***xxxx)
# 启用: True
# 自检: 通过 - 连通正常，返回 {'ok': 1}
```

### 7.2 使用

```bash
# 正常运行 + 检测到变更时自动调用 AI 更新映射
python cluster_hlr.py --docx 需求.docx --ai-update

# 只看变更计划，不实际调用 AI、不写文件（离线也能用，适合变更前确认）
python cluster_hlr.py --docx 需求.docx --ai-update --ai-dry-run

# 忽略增量判断，全部重新生成映射
python cluster_hlr.py --docx 需求.docx --ai-update --ai-force

# 单独更新某一个映射表（便于分批处理 / 排查）
python ai_maps.py --docx 需求.docx --map name   --out name_map.json
python ai_maps.py --docx 需求.docx --map state  --out state_map.json
python ai_maps.py --docx 需求.docx --map signal --out signal_map.json
```

### 7.3 增量策略（只对变化的部分调 AI，成本可控）

| 情况 | 处理 |
|------|------|
| `stale`：需求已删除 | 直接从映射中删除，**不调 AI** |
| `missing`：新增需求 | 交给 AI 生成 |
| `text_changed`：原文改了 | 交给 AI 重新生成（原值可能过时） |
| 其余未变化条目 | 原样保留（含 hash），**不调 AI** |

映射文件里有一个 `__meta__` 元数据键，记录上次生成时需求文档包含的全部需求 ID：

```json
{
  "__meta__": {
    "coverage": "full",
    "source_doc": "HLR_完整未故障注入.docx",
    "generated_at": "2026-09-11T17:39:39",
    "covered_reqs": ["FSF21000101_HLR_199", "..."]
  },
  "FSF21000101_HLR_280": { "value": "AFTEFAN1速度信号", "text_hash": "cd81c149" }
}
```

它的作用是区分"真正新增的需求"与"本来就不需要该属性的需求"——否则 `state_map` /
`signal_map` 这类**部分覆盖**的映射每次运行都会把几十条无关需求重复送 AI。有了它，
第二次运行同样的文档会直接输出「无变化，跳过」。

### 7.4 安全与降级

- **失败不中断**：AI 不可用（未配 key / 网络不通 / 服务报错 / 返回非法 JSON）时只打印警告，
  聚类照常产出结果，退回到正则 + 现有映射。
- **写前备份**：每次写回映射前自动备份为 `<文件>.bak`。
- **密钥安全**：`ai_config.json` 含密钥，**不要提交到代码库、不要随交付包分发**；
  建议用环境变量，或在 `.gitignore` 中排除。
- **批次控制**：`batch_size`（默认 20）控制每次请求携带的需求条数，需求多时自动分批。

## 八、核心原则（务必先理解）

1. **只认需求文档**：不读、不匹配 ICD / EoICD 接口文件，不从接口文件填充任何属性值。
2. **只从表格提取**：docx 里表格之外的正文段落（章节说明/设备概述/需求表前的小标题）一律不参与提取；
   非需求表、"是否为需求=否"的说明性表格跳过，跳过清单写入 `meta.skipped_tables[]` 可审计。
3. **提到才归类**：需求文本里提到某属性才归入该类；没提到的属性类为空。
4. **按属性名聚类，不按属性值**：只要都提到"Label"就在 Label 类，label 号不同不影响。
5. **一条需求可属多类**：提到几个属性就进几个类。
6. **检测范围分两类**：属性类判定用整条需求表格的相关字段（需求中文优先，其余非元数据字段一并扫描，
   含"注释"/"输入数据"）；身份信息（bus/label/bit/name/direction）只认"需求中文"，
   避免被其他字段的总线字样污染。元数据字段（含 `ID`、以 `是否` 开头、含 `安全`/`衍生`/`对象类型`/`来源`）
   不参与检测。**否定语境不算属性**——"…已按0.25分辨率扩大，此处不再做处理"不归入 LsbRes。
   `meta.detection_fields[]` 列出该文档实际参与检测的全部字段，排查属性来源先看它。
7. **按信号拆分**：单个需求涉及多个信号时，按信号 Name 拆为独立接口项。
   优先级：AI 的 signal_map > **自动拆分**（识别到 ≥2 个去重后的英文信号名，可用 `--no-auto-split` 关闭）
   > label 级拆分。每个接口项有独立的 label/name/bit/direction 与属性类。
8. **0/1 编码归三类**：涉及 `bitX=1`/`bitX=0` 编码定义的需求，归入 OneState、ZeroState、CodedSet。
9. **提取不到就留空**：信号名等身份信息提取不到时为 `null`，绝不用领域知识臆造或推断。
   典型：`label` **只认数字 LABEL 号**，需求里没写编号就是 `null`（不用助记名 `LBL_*` 顶替，
   助记名另存 `label_name`）。
10. **多系统写法兼容**：表头字段名别名、需求 ID 格式不限、尖括号占位符、下划线式/助记符信号名、
    多信号列举、条件从句、"X总线"优先、周期/bit/label 多种写法——各系统写法不一致也能处理
    （详见 `属性识别规则.md` 三·补4）。
11. **bit 统一 0-based**：`bitN` 本身即 0-based 直接取；中文序数"第N位"是 1-based，**减 1**
    （FGMC"第14到29位"→ `13-28`）。同一需求内**连贯位段自动合并**——"第18到28位，第29位（符号位）"
    描述的是同一个字段，合并为 `17-28`；不连贯的各自保留（`17-27,29`）。
12. **身份全空不保留**：`bus`/`label`/`name`/`bit` 全为 `null` 的接口项不生成聚类，
    归档到 `dropped_requirements[]`（不静默丢弃），供人工复核。

规则细节见 `属性识别规则.md`。
