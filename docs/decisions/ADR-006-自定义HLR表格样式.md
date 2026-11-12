# ADR-006 自定义 HLR 表格样式

| 项目 | 内容 |
| --- | --- |
| 状态 | **已接受**（部分被 ADR-007 取代：2026-11-12 起系统引入登录与账号隔离，样式写入限管理员；本文「无数据库、无鉴权」的限制不再成立） |
| 生效范围 | 后端（解析层 / API / 持久化）+ 前端上传页 + 文档 |
| 提议日期 | 2026-11-06 |
| 关联 ADR | ADR-001（V4 后端接入策略）；本决策不改变评分体系与输出文档格式 |

---

## 背景

不同控制器、不同型号文档的 SWHLR Word 需求表写法不一致：行标签（「需求编号 / 需求正文 / 基本原理」还是「SWHLR ID / 需求中文」）、需求表在文档中的位置、需求 ID 的形态都可能不同。当前这些差异由 controller profile（`backend/app/v4/profiles/{id}/config.yaml` 的 `hlr_parser.field_map` 与 `requirement_scan`）承载。

这带来两个问题：

1. 新文档 / 新型号的需求表写法变化时，只能修改 profile 配置、重新构建镜像并部署，现场用户无法自助适配；
2. profile 是全局配置，为单个文档临时改动会影响该控制器的所有任务，风险高。

同时，下游（标注 / 匹配 / 裁判 / 报告）只消费解析产物中的 `requirement_id` 与 `content`（可选 `rationale`），因此「如何把 Word 表格读成需求条目」可以与「如何匹配与判定」解耦。

## 决策

### D1：解析样式与 controller profile 解耦

新增独立的「HLR 表格样式」概念，只覆盖解析器的两个配置面：

- `field_map`：行标签 → 标准字段的映射；
- `requirement_scan`：需求表识别方式与需求 ID 校验（`id_pattern`）。

controller profile 继续负责匹配关键词、追溯表配置、AI 标注示例与 preprocess hook；样式叠加不修改 profile 的其余配置（`build_profile_with_schema` 只替换 `hlr_parser`）。

### D2：样式模型固定为「纵向行标签 / 值表格」

样式配置为 `{version: 1, mode: "docx_vertical", fields: {标准字段: [行标签别名]}, identify: {id_pattern}}`；`id` 与 `content` 必填，其余为标准可选字段（rationale / verification_method / implementation 等）。样式仅支持 `.docx`。

### D3：内置模板由 profile 派生、只读

从 AMS / FGMC / HSCU / RPDU 四个 profile 的 `config.yaml` 派生 `builtin_ams` / `builtin_fgmc` / `builtin_hscu` / `builtin_rpdu` 四个只读模板，作为用户的编辑起点，避免 profile 与模板两处事实源漂移。未修改的模板按该控制器的原生解析路径执行（native）。RPDU 使用内置解析路径，仅列出、不适用于自定义样式。

### D4：自定义样式服务端持久化、全局共享

自定义样式保存在 `OUTPUT_DIR/hlr_schemas.json`（临时文件 + 原子替换写入），由后端统一管理；遵循现有原型「无数据库、无鉴权」的限制，样式对全部用户 / 会话可见。名称忽略大小写唯一（1–50 字符），内置模板不可修改 / 删除。

### D5：创建任务接口新增可选样式引用

`POST /api/v4/coverage-analysis` 新增可选表单字段 `hlr_schema`，支持完整样式配置、内置模板引用、已保存样式引用三种形式；仅 `.docx`，使用时必须显式选择系统类型，内置模板须与所选控制器一致。校验在创建任务目录之前完成，非法引用不会留下残留任务目录。不传该字段时走原路径，行为不变。

### D6：样式快照随任务落盘

解析后的样式快照写入任务目录 `input/hlr_schema.json` 与任务 manifest 的参数快照；续跑（从 manifest 恢复）按快照重建解析配置，不依赖样式之后是否被修改或删除。

### D7：扫描 / 试解析作为无 LLM 的辅助能力

提供文档表格扫描与样式试解析两个本地接口（不调用 LLM），前端在上传页提供样式编辑器；用户可以先查看表格结构与试解析结果，再保存样式或提交任务。

### D8：「是否为需求」为可选映射，映射后才过滤

`is_requirement` 加入样式的标准字段。只有样式显式把某行标签映射到它时，才启用非需求过滤（该行值为「否」的表格不进入需求列表）；未映射时不再从基础 profile 自动继承过滤（包括 FGMC）。内置 FGMC 模板的配置包含该字段，因此「选内置模板 → 改动 → 保存为自定义样式」仍保持原生过滤行为；原生（未修改）内置模板路径不受影响。

### D9：新增「其他系统（通用）」系统类型

新增中性 profile `generic`（`backend/app/v4/profiles/generic/config.yaml`）：不声明 `auto_detect`、不提供内置解析规则或控制器增强配置。选择它时必须同时提供自定义样式，解析完全由样式驱动，下游（标注 / 匹配 / 裁判 / 报告）使用中性默认配置；暂不支持追溯预筛。这样任意系统的 HLR 表格都可以通过样式接入，而不必为每个系统新增控制器 profile。自动识别仍只覆盖四个内置控制器，通用系统必须显式选择。

## 原因

1. 下游只依赖 `requirement_id` / `content`（可选 `rationale`），样式只影响 Step 1，改动面可控；
2. 复用既有 `HLRWordParser` 与 `HLRParserConfig`，样式只是配置叠加，无需新增解析器或分支，既有 profile 行为零回归；
3. 内置模板从 profile 派生，profile 的 `config.yaml` 仍是唯一事实源；
4. 快照随任务落盘，样式的修改 / 删除不会使旧任务无法续跑或无法查看；
5. 不传 `hlr_schema` 时走原路径，风险与回归面最小。

## 影响

- **后端**：新增 `v4/parsers/hlr_schema.py`、`v4/hlr_schema_store.py`、`api/v4/hlr_schemas.py` 与中性 profile `v4/profiles/generic/config.yaml`；`RequirementScanConfig` 增加 `required_fields` / `require_content_value` 并以默认值保持原行为；创建任务接口、runner 与响应模型增加可选样式通路。
- **前端**：上传页新增样式编辑器（扫描、标签映射、试解析、保存 / 修改 / 删除）；系统类型下拉新增「其他系统（通用，需自定义样式）」；不选样式时请求内容不变。
- **持久化**：新增 `OUTPUT_DIR/hlr_schemas.json`（全局共享）；带样式的任务目录新增 `input/hlr_schema.json` 快照。
- **兼容性**：不传样式时行为与改动前一致；历史任务 / 历史结果不受影响。
- **限制**：样式仅支持 `.docx`；无鉴权与用户隔离；RPDU 不支持自定义样式；通用系统无内置增强配置、不支持追溯预筛，匹配质量需按实际文档验证。
- **文档**：`docs/architecture/api.md`、`docs/architecture/current-architecture.md`、`docs/project/workflow.md`、`CHANGELOG.md`、`docs/development/development-log.md` 与本 ADR。

## 替代方案

| 方案 | 不选择理由 |
| --- | --- |
| 为每种表格写法新增一个解析器 | 重复解析逻辑，下游契约与解析器工厂注册需同步扩展，维护成本高 |
| 复制 / 修改 controller profile 后重新部署 | 现场用户无法操作；每次适配都要改代码与镜像，且影响该控制器的全部任务 |
| 前端硬编码常见行标签 | 只能覆盖固定写法，新文档仍需改代码；标签冲突与校验散落前端 |
| 让样式覆盖整份 `HLRParserConfig`（含术语表定位 / hook 等） | 与 profile 职责重叠，可能破坏匹配、追溯与 hook 语义，风险不可控 |
| 为每个新系统新增 controller profile | 需要补齐解析 / 分类 / 追溯 / 标注配置并重新构建部署；通用系统 + 自定义样式可先覆盖解析差异，下游使用中性默认配置 |

## 状态

**已接受** —— 已按本决策实现；不传样式时行为与原版本一致。
