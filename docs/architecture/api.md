# API 设计说明

本文档用于说明 **ICD工具原型Ver4.0** 的 API 设计。当前版本仅保留 V4 `/api/v4` 命名空间；V3 `/api` 接口已随 V3 代码一并移除（见 ADR-002）。

## 1. API 设计原则

API 设计应遵守以下原则：

1. 前端通过 API 与后端交互，不直接访问后端文件系统；
2. 文件上传、任务状态查询、结果查询和文件下载应分离；
3. 后端应通过任务标识维护一次分析过程；
4. API 返回结果应便于前端展示任务状态和下载结果；
5. 当前版本优先满足本地演示原型，不追求完整生产级接口设计。

## 2. 目标接口概览

当前核心接口如下：

| 接口                                             | 方法     | 说明                              |
| ---------------------------------------------- | ------ | ------------------------------- |
| `/api/v4/health`                               | `GET`  | 后端健康检查                          |
| `/api/v4/coverage-analysis`                    | `POST` | 上传输入文件并创建 V4 反向管线任务               |
| `/api/v4/completeness-analysis`                | `POST` | 上传输入文件并创建 V4 正向完整性分析任务            |
| `/api/v4/jobs`                                 | `GET`  | 查询未完成/中断任务列表（可选 `status`、`task_type` 过滤） |
| `/api/v4/jobs/{job_id}`                        | `GET`  | 查询任务状态                          |
| `/api/v4/jobs/{job_id}/resume`                 | `POST` | 继续被中断的任务（按 manifest 参数快照重跑，已完成的 LLM 判定复用缓存） |
| `/api/v4/jobs/{job_id}/abandon`                | `POST` | 放弃被中断的任务（仅标记，不删除文件）            |
| `/api/v4/jobs/{job_id}/result`                 | `GET`  | 查询任务处理结果摘要（按 `task_type` 分发正确性/完整性两种 schema） |
| `/api/v4/jobs/{job_id}/outputs/eoicd-xlsx`     | `GET`  | 下载 EoICD 条目化清单（xlsx）      |
| `/api/v4/jobs/{job_id}/outputs/consensus-docx` | `GET`  | 下载多模型共识差异分析报告（docx）            |
| `/api/v4/jobs/{job_id}/outputs/consistency/{model}` | `GET`  | 下载单模型差异分析报告（docx）           |
| `/api/v4/jobs/{job_id}/outputs/forward-xlsx`   | `GET`  | 下载正向完整性分析明细表（xlsx）            |
| `/api/v4/jobs/{job_id}/outputs/forward-docx`   | `GET`  | 下载正向完整性分析报告（docx）            |

`{model}` ∈ `{deepseek, minimax, qwen}`。

## 3. 健康检查接口

```text
GET /api/v4/health
```

预期返回：

```json
{ "status": "ok", "api_version": "v4" }
```

## 4. 创建分析任务接口

```text
POST /api/v4/coverage-analysis
Content-Type: multipart/form-data
```

请求字段（multipart）：

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `hlr_word_file` | UploadFile (.docx) | 是 | HLR Word 文档 |
| `eoicd_publisher_file` | UploadFile (.xlsx) | 二选一 | EoICD Publisher PubSub Excel |
| `eoicd_subscriber_file` | UploadFile (.xlsx) | 二选一 | EoICD Subscriber PubSub Excel |
| `traceability_files` | list[UploadFile] (.xlsx) | 否 | 0-N 追溯 Excel；启用预筛选时必传 |
| `use_mock_llm` | bool (form) | 否（默认不覆盖） | 显式覆盖 mock 开关；未提供时以 `.env` 的 `USE_MOCK_LLM` 为准（Mock 仅由 env 控制） |
| `judge_providers` | list[str] (form) | 否（默认 `["deepseek"]`） | 多模型 panel provider 白名单 ∈ `{deepseek, minimax, qwen}` |
| `enable_traceability_prefilter` | bool (form) | 否（默认 false） | 是否启用追溯预筛选 |
| `controller_profile` | str (form) | 否（默认 `ams`） | 控制器 profile id ∈ `{ams, fgmc, hscu, rpdu, fsecu}`，决定 HLR 解析规则、分类关键词、追溯表配置与 AI 标注示例 |
| `no_refine` | bool (form) | 否（默认 false） | 仅 RPDU profile 生效；`true` 时关闭 Step 3.5 refine 后处理（matched ICD Block 无关过滤 + 精确/同义词补采），与最初版 RPDU 行为对齐做 A-B 对照 |

文件名校验：`[^A-Za-z0-9._\-一-龥]` 之外字符会被替换为 `_`；`safe_filename()`。

`judge_providers` 任一不在白名单 → 422。

`controller_profile` 不在白名单 → 422（在创建任务前 fail fast）。不传该字段时行为与 Issue A 完全一致（AMS 默认）。

`no_refine` 仅 RPDU profile 生效；非 RPDU profile 传入该字段被忽略。CLI 等价形参为 `reverse-analyze --no-refine`。

预期返回（V4AnalyzeResponse）：

```json
{
  "job_id": "<uuid>",
  "status": "pending",
  "message": "V4 反向管线任务已创建"
}
```

## 5. 查询任务状态接口

```text
GET /api/v4/jobs/{job_id}
```

预期返回（V4JobStatusResponse）：

```json
{
  "job_id": "<uuid>",
  "status": "pending | running | completed | failed | interrupted | abandoned",
  "stage": "parse | label | match | multi_judge | review | report | done",
  "stage_index": 3,
  "stage_total": 5,
  "case_index": 12,
  "case_total": 12,
  "message": "Step 3/5: Multi-agent judging",
  "resumed": false,
  "reuse": { "reused": 33, "rerun": 0 },
  "mock_models": ["minimax", "qwen"],
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

`mock_models` 按 ADR-001 D5 规则取值：`multi_judge_results.json.providers ∩ {"minimax", "qwen"}`；`USE_MOCK_LLM=1` 时所有 provider 都进 `mock_models`。

`resumed` 表示本次运行是否为中断后的恢复运行（`resume` 启动后置 `true`，首跑为 `false`）。`reuse` 仅在恢复运行时非空，以**模型调用次数**为单位反映本次运行的两个去向：`reused` = 直接复用中断前已完成结果、未发起请求的调用次数；`rerun` = 本次接续发起的调用次数（含中断前未执行到的步骤，以及中断时正在执行或已失败、缓存中没有可用结果的调用）。计数按 Step 2（HLR 标注）/4/5/5.5/5.6 逐条模型调用累计、不去重（同一结果在后续步骤再次命中会再计一次），因此与 `llm_cache.jsonl` 的行数不是同一口径；也与需求条数不同 —— 每个需求对应「每个模型一次判定 + 一次共识」等多次调用。其中 Step 2 整批完成、命中 `hlr_labels.json` 直接加载时，加载的 N 条按 `reused` 计入。正向（完整性）管线无判定缓存，`reuse` 恒为 `{"reused": 0, "rerun": 0}`。

`interrupted` / `abandoned` 为任务中断恢复相关状态，见第 13 节。

## 6. 查询任务结果摘要接口

```text
GET /api/v4/jobs/{job_id}/result
```

仅当 `status == completed` 时返 200 + 完整结果，否则 409。

预期返回（V4JobResultResponse）：

```json
{
  "job_id": "<uuid>",
  "status": "completed",
  "summary": {
    "eoicd_count": 122674,
    "eoicd_blocks_total": 1568,
    "eoicd_blocks_matched": 11,
    "hlr_count": 16,
    "matched_count": 5,
    "pending_count": 7,
    "unmatched_count": 4,
    "judged_count": 12,
    "agreement_distribution": {"majority": 7, "full": 4, "split": 1},
    "star_distribution": {"1": 1, "2": 7, "3": 4},
    "status_distribution": {"已覆盖": 7, "待确认": 4, "不一致": 1, "无匹配": 4},
    "average_star_rating": 2.25
  },
  "outputs": {
    "eoicd_xlsx": true,
    "consistency_deepseek_docx": true,
    "consistency_minimax_docx": true,
    "consistency_qwen_docx": true,
    "consensus_docx": true
  },
  "mock_models": ["minimax", "qwen"],
  "degradation": {
    "provider_status": {
      "deepseek": "healthy",
      "minimax": "healthy",
      "qwen": "healthy"
    },
    "total_case_timeouts": 0,
    "review_star_capped_count": 0
  },
  "errors": []
}
```

`outputs.*` 5 个布尔为 false 时表示对应 docx/xlsx 未生成（pipeline 中途失败、文件被 GC 等）。

降级场景下 `agreement_distribution` 可能出现 `single_source` / `no_consensus` 键（仅 1 个 / 0 个 provider 存活）；0 个存活时对应 case 强制 1★、`no_consensus`，`status_distribution` 计入 待确认。

## 7. 下载输出接口

```text
GET /api/v4/jobs/{job_id}/outputs/eoicd-xlsx
GET /api/v4/jobs/{job_id}/outputs/consensus-docx
GET /api/v4/jobs/{job_id}/outputs/consistency/{model}
```

`{model}` ∈ `{deepseek, minimax, qwen}`（白名单校验，非法 → 400）。

5 类文件命名（与 `backend/app/api/v4/runner.V4_OUTPUT_FILES` SSoT 一致）：

| URL 段 | 物理文件名 |
| --- | --- |
| `eoicd-xlsx` | `EoICD条目化清单.xlsx` |
| `consensus-docx` | `EoICD与SWHLR多模型差异分析报告.docx` |
| `consistency/deepseek` | `EoICD与SWHLR单模型差异分析报告_DeepSeek.docx` |
| `consistency/minimax` | `EoICD与SWHLR单模型差异分析报告_MiniMax.docx` |
| `consistency/qwen` | `EoICD与SWHLR单模型差异分析报告_Qwen.docx` |

Content-Type：
- `.xlsx` → `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- `.docx` → `application/vnd.openxmlformats-officedocument.wordprocessingml.document`

## 8. 错误响应

| 场景 | HTTP | 备注 |
| --- | --- | --- |
| `hlr_word_file` 缺失或非 .docx | 422 | |
| pub/sub Excel 都没传 | 422 | 至少传一个 |
| 任意 Excel 非 .xlsx | 422 | |
| 文件 >50 MB | 413 | 整请求 >200 MB |
| 文件名含非法字符 | 422 | |
| `judge_providers` 出现 `claude` 等 | 422 | 错误信息含 `allowed: deepseek, minimax, qwen` |
| `controller_profile` 不在白名单 | 422 | `controller_profile: unsupported '<name>'; allowed: ams, fgmc, hscu, rpdu, fsecu` |
| `{model}` 不在 `{deepseek,minimax,qwen}` | 400 | `invalid model: <name>; allowed: ...` |
| 任务 `running` 时调 `/result` | 409 | `job not finished: status=...` |
| 任务 `failed` 时调 `/result` | 409 | |
| 任务不存在 | 404 | `job not found` |

## 9. 输出路径布局

| 版本 | 根目录 | 内部结构 |
| --- | --- | --- |
| V4 | `backend/output/v4/{job_id}/` | 分层：`input/`（用户上传原始文件）+ `output/`（pipeline 产物） |

`docker-compose.yml` volume 映射 `./backend/output:/app/output`。

V4 `input/traceability/` 子目录用于 `enable_traceability_prefilter=true` 时追溯表落点。

## 10. JSON 中间产物（不暴露）

下列中间产物是 V4 内部数据，**不**作为下载 API 暴露，**仅**保留在 `backend/output/v4/{job_id}/output/` 内供服务端日志与后续 Issue 调试：

- `multi_judge_results.json`
- `consensus_results.json`
- `reverse_matches.json`
- `reverse_report.json`
- `eoicd_requirements.json`
- `hlr_requirements.json`
- `hlr_labels.json`
- `llm_cache.jsonl`（内容寻址 LLM 缓存，见第 13.2 节）

如前端需要看这些数据，**不**通过 `GET /api/v4/jobs/{id}/outputs/{name}`；应在后续 Issue 加 `Accept: application/json` 内容协商或独立子路由。

## 11. API 变更原则

如 API 发生变化，应同步更新本文档。

以下变化必须更新本文档：

1. 新增或删除接口；
2. 修改接口路径；
3. 修改请求字段；
4. 修改响应字段；
5. 修改任务状态定义；
6. 修改输出文件下载方式；
7. 修改错误响应结构。

如未来本文档内容与 `backend/app/api/v4/*.py` 不一致，**以代码为准**并在本文件回写差异。

## 12. 正向完整性分析接口（EoICD → HLR）

正向完整性分析回答「EoICD 业务对象在 HLR 正文中是否漏写」，与反向分析（HLR → EoICD 正确性比对）互补。正向任务在 `Job` 中以 `task_type == "completeness"` 区分（反向为 `"correctness"`）；`GET /jobs/{id}/result` 按 `task_type` 分发到正确性/完整性两种响应 schema。

### 12.1 创建正向分析任务

```text
POST /api/v4/completeness-analysis
Content-Type: multipart/form-data
```

请求字段（multipart）：

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `hlr_word_file` | UploadFile (.docx) | 是 | HLR Word 文档 |
| `eoicd_publisher_file` | UploadFile (.xlsx) | 二选一 | EoICD Publisher PubSub Excel |
| `eoicd_subscriber_file` | UploadFile (.xlsx) | 二选一 | EoICD Subscriber PubSub Excel |
| `analysis_mode` | str (form) | 否（默认 `full`） | `full`（全量）或 `trace`（追溯范围） |
| `device_icd_trace_file` | UploadFile (.xlsx) | trace 模式必填 | 表1：设备→ICD 追溯表 |
| `system_device_trace_file` | UploadFile (.xlsx) | trace 模式必填 | 表2：设备→高层需求追溯表 |
| `use_mock_llm` | bool (form) | 否（默认不覆盖） | 显式覆盖 mock 开关；未提供时以 `.env` 的 `USE_MOCK_LLM` 为准（Mock 仅由 env 控制） |

`analysis_mode` 不在 `{full, trace}` → 422；trace 模式缺任意一张追溯表 → 422。

预期返回（V4AnalyzeResponse，与反向共用）：

```json
{
  "job_id": "<uuid>",
  "status": "pending",
  "message": "V4 正向完整性分析任务已创建"
}
```

### 12.2 查询正向结果摘要（共用 `/result`，按 `task_type` 分发）

```text
GET /api/v4/jobs/{job_id}/result
```

正向任务（`task_type == "completeness"`）与反向任务（`task_type == "correctness"`）共用同一 `/result`，后端按 `task_type` 返回 `V4ForwardJobResultResponse` 或 `V4JobResultResponse`。仅当 `status == completed` 时返回 200，否则 409。

预期返回（V4ForwardJobResultResponse）：

```json
{
  "job_id": "<uuid>",
  "status": "completed",
  "summary": {
    "analysis_mode": "full",
    "total_blocks": 1568,
    "covered_direct": 450,
    "covered_aggregate": 0,
    "parent_referenced": 192,
    "possible": 509,
    "uncovered": 417,
    "unsupported": 0,
    "input_error": 0,
    "ai_reviewed": 701,
    "eoicd_count": 122674,
    "hlr_count": 32
  },
  "outputs": {
    "forward_xlsx": true,
    "forward_docx": true
  },
  "errors": []
}
```

### 12.3 下载正向输出

```text
GET /api/v4/jobs/{job_id}/outputs/forward-xlsx
GET /api/v4/jobs/{job_id}/outputs/forward-docx
```

| URL 段 | 物理文件名 |
| --- | --- |
| `forward-xlsx` | `EoICD至HLR正向完整性分析明细.xlsx` |
| `forward-docx` | `EoICD至HLR正向完整性分析报告.docx` |

正向下载接口校验 `task_type == "completeness"`；反向下载接口校验 `task_type == "correctness"`。用错任务下载 → 404。

### 12.4 正向 JSON 中间产物（不暴露）

正向管线分阶段落盘，以下 JSON 仅保留在 `backend/output/v4/{job_id}/output/` 内，不对外下载：

- `forward_scope.json`（C2 追溯范围）
- `forward_blocks.json`（C3 业务对象块）
- `hlr_identity_index.json`（C4 HLR 身份索引：确定性 token + `label_hlrs()` 召回增强的 llm_label token）
- `forward_candidates.json`（C5 候选召回）
- `forward_deterministic.json`（C6 确定性判定）
- `forward_ai_review.json`（C7 AI 三态复核）
- `forward_coverage.json`（C8 最终覆盖结果）

### 12.5 正向错误响应

| 场景 | HTTP |
| --- | --- |
| `hlr_word_file` 缺失或非 .docx | 422 |
| pub/sub Excel 都没传 | 422 |
| 任意 Excel / 追溯表非 .xlsx | 422 |
| `analysis_mode` 不在 `{full, trace}` | 422 |
| trace 模式缺追溯表 | 422 |
| 任务 `running`/`failed` 时调 `/result` | 409 |
| 正向 xlsx/docx 未生成时下载 | 404 |

## 13. 任务中断恢复接口

任务元数据随每步进度持久化到 `backend/output/v4/{job_id}/job.json`（manifest）。进程启动时扫描一次：仍为 `pending` / `running` 的 manifest 必然来自上一个已退出的进程 → 标记为 `interrupted`（保留原 `updated_at` 作为「中断前的最后进度时间」）并载入内存。已为 `interrupted` 的 manifest 同样载入，因此反复重启不会丢失中断任务。输入文件与各阶段中间产物均已落盘，无需重新上传。

### 13.1 查询任务列表

```text
GET /api/v4/jobs?status=interrupted&task_type=correctness
```

（V4JobListItem，返回数组）

```json
[
  {
    "job_id": "<uuid>",
    "task_type": "correctness | completeness",
    "status": "pending | running | completed | failed | interrupted | abandoned",
    "message": "Step 4/6: Multi-agent judging",
    "created_at": "ISO-8601",
    "updated_at": "ISO-8601",
    "input_files": ["HLR.docx", "Publisher.xlsx"]
  }
]
```

- 两个 query 参数均可选：`status` 按状态精确过滤，`task_type` 按任务类型过滤。
- 内存中只保留本次进程创建的任务与启动时载入的 `interrupted` 任务，因此该接口不会返回历史 `completed` / `failed` 任务。
- `input_files` 取自 manifest 参数快照中的输入文件（仅文件名，不含路径）；CLI 等无 manifest 的任务为空数组。

### 13.2 继续任务

```text
POST /api/v4/jobs/{job_id}/resume
```

按 manifest 中的参数快照（`judge_providers` / `use_mock_llm` / `controller_profile` / `no_refine` / `analysis_mode` / 追溯表等）重跑，走与新建任务完全相同的启动路径。前端不接受也不传递任何覆盖参数。

返回：

```json
{ "job_id": "<uuid>", "status": "running", "message": "任务已继续执行（将重新运行分析流程）" }
```

重跑成本说明（反向/正确性管线）：

- Step 2（HLR AI 标注）的每条标注按内容寻址复用（`output/llm_cache.jsonl`，`kind=hlr_label`），只有未完成的才真正调用模型；整批完成后 `output/hlr_labels.json` 落盘，此后直接整体加载跳过；
- Step 4（多模型判定）、Step 5（共识）、Step 5.5（1★/2★ 复查）的**已完成的单条 LLM 判定结果**按内容寻址复用，只有未完成的才真正调用模型，结果随完成进度增量写入 `output/llm_cache.jsonl`。因此中断越晚、继续时省下的调用越多；
- 复用只发生在**同一任务目录内**，不跨任务；换模型、改提示词、上游输入变化等会改变判定内容的情况都会自动失效并重新判定；
- Step 1/3/6 等确定性步骤（解析、匹配、报告生成）本就很快，仍然全量重跑；
- 正向（完整性）管线暂未按 case 复用，`resume` 仍为整段重跑。

恢复启动后任务标记 `resumed=true`，并从 0 重新累计 `reuse` 计数（多次恢复不累加上一轮）；`GET /jobs/{id}` 可实时看到 `reuse.reused` / `reuse.rerun` 递增，用于前端展示「已复用中断前结果 N 次 · 接续调用模型 M 次」（次数即模型调用次数，口径见第 5 节）。

### 13.3 放弃任务

```text
POST /api/v4/jobs/{job_id}/abandon
```

只把状态标记为 `abandoned`，**不删除任何输入或输出文件**（磁盘清理不在本期范围）。

返回：

```json
{ "job_id": "<uuid>", "status": "abandoned", "message": "任务已放弃（文件保留在输出目录，未删除）" }
```

### 13.4 中断恢复错误响应

| 场景 | HTTP |
| --- | --- |
| `resume` / `abandon` 的任务不存在（如后端重启后该任务未留下可恢复记录） | 404 |
| `resume` 的任务状态不是 `interrupted`（如 `completed` / `running` / `abandoned`） | 409 |
| `abandon` 的任务状态不是 `interrupted` | 409 |
| `resume` 时 manifest 记录的输入文件或追溯目录已不存在 | 409 |

### 13.5 manifest 字段说明

`output/v4/{job_id}/job.json` 由任务自身在每次 `update()` 时原子写入（`.tmp` + `os.replace`），字段：

```json
{
  "schema_version": 1,
  "job_id": "<uuid>",
  "task_type": "correctness | completeness",
  "status": "pending | running | completed | failed | interrupted | abandoned",
  "message": "Step 1/6: Parsing input files",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "params": { "hlr_path": "input/xxx.docx", "...": "..." },
  "resumed": true,
  "reuse": { "reused": 96, "rerun": 0 }
}
```

`resumed` / `reuse` 为中断恢复可视化字段（见第 5 节）：恢复运行开始与每完成一个 case 时随 `_persist` 落盘，进程被杀后计数不丢；旧 manifest 无这两键时按默认值（`false` / `null`）加载，无需迁移。

`params` 中的路径一律为相对 `job_dir` 的相对路径，保证输出目录整体搬迁后仍可恢复。不持久化任务结果（重跑时重新生成）。无 `job_dir` 的任务（如 CLI 直跑 `app/v4/cli.py`）不写 manifest，行为与本次改动前一致。

**部署前提（Docker）**：本能力依赖 `output/` 跨进程存活。`docker-compose.yml` 已把 `./backend/output` 以 bind mount 挂到容器内 `/app/output`（且未设置 `OUTPUT_DIR`），因此 `docker compose restart` / `down` + `up` 后 manifest 仍在、中断任务不丢。若把该卷去掉、改为匿名 volume 或设置 `OUTPUT_DIR` 指向容器内非挂载路径，本能力会静默失效（重启后任务列表为空）。
