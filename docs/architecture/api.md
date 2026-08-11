# API 设计说明

本文档用于说明 **ICD工具原型Ver2.0** 的目标 API 设计。
当前版本处于原型初始化阶段，本文档中的接口为初始设计草案，后续可根据前后端实现和任务模型调整。

## 1. API 设计原则

API 设计应遵守以下原则：

1. 前端通过 API 与后端交互，不直接访问后端文件系统；
2. 文件上传、任务状态查询、结果查询和文件下载应分离；
3. 后端应通过任务标识维护一次分析过程；
4. API 返回结果应便于前端展示任务状态和下载结果；
5. 当前版本优先满足本地演示原型，不追求完整生产级接口设计；
6. API 字段可在最小可运行工程实现过程中继续细化。

## 2. 目标接口概览

当前规划的核心接口如下：

| 接口                                             | 方法     | 说明                              |
| ---------------------------------------------- | ------ | ------------------------------- |
| `/api/health`                                  | `GET`  | 后端健康检查                          |
| `/api/eoicd/analyze`                           | `POST` | 上传输入文件并创建分析任务                   |
| `/api/jobs/{job_id}`                           | `GET`  | 查询任务状态                          |
| `/api/jobs/{job_id}/result`                    | `GET`  | 查询任务处理结果摘要                      |
| `/api/jobs/{job_id}/outputs/requirements`      | `GET`  | 下载"最优 EoICD 条目化需求"（语义见 §7）      |
| `/api/jobs/{job_id}/outputs/minimax-requirements` | `GET`  | 下载 MiniMax 条目化需求文档            |
| `/api/jobs/{job_id}/outputs/deepseek-requirements` | `GET`  | 下载 DeepSeek 条目化需求文档           |
| `/api/jobs/{job_id}/outputs/difference-report` | `GET`  | 下载差异报告文档                        |

## 3. 健康检查接口

### 3.1 接口定义

```text
GET /api/health
```

### 3.2 接口用途

用于检查后端服务是否正常运行。

### 3.3 预期返回

```json
{
  "status": "ok"
}
```

## 4. 创建分析任务接口

### 4.1 接口定义

```text
POST /api/eoicd/analyze
```

### 4.2 接口用途

用于上传 EoICD 源文件和软件高层需求文件，并创建一次分析任务。

### 4.3 输入文件

该接口应支持上传以下文件：

1. EoICD Word 主文件；
2. 一个或多个 EoICD Excel 附件；
3. 软件高层需求文件。

### 4.4 初始请求字段草案

字段命名可在实现阶段进一步细化。

```text
eoicd_word_file
eoicd_excel_files
software_requirement_file
```

### 4.5 预期返回

接口成功后应返回任务标识和初始任务状态。

```json
{
  "job_id": "example-job-id",
  "status": "pending",
  "message": "分析任务已创建"
}
```

### 4.6 说明

该接口只负责创建任务，不要求同步返回完整分析结果。
实际分析过程可由后端 pipeline 执行，并通过任务状态接口查询进度。

## 5. 查询任务状态接口

### 5.1 接口定义

```text
GET /api/jobs/{job_id}
```

### 5.2 接口用途

用于查询指定分析任务的当前状态。

### 5.3 任务状态

建议状态包括：

| 状态          | 含义           |
| ----------- | ------------ |
| `pending`   | 任务已创建，尚未开始处理 |
| `running`   | 任务正在处理       |
| `completed` | 任务处理完成       |
| `failed`    | 任务处理失败       |

### 5.4 预期返回

```json
{
  "job_id": "example-job-id",
  "status": "running",
  "message": "任务正在处理",
  "created_at": "2026-01-01T10:00:00",
  "updated_at": "2026-01-01T10:01:00"
}
```

字段可根据实际实现进行调整。

## 6. 查询任务结果摘要接口

### 6.1 接口定义

```text
GET /api/jobs/{job_id}/result
```

### 6.2 接口用途

用于查询任务完成后的结果摘要。

该接口不直接返回完整 Word 文档内容，只返回前端展示所需的摘要信息和输出文件状态。

### 6.3 预期返回

```json
{
  "job_id": "example-job-id",
  "status": "completed",
  "summary": {
    "requirement_count": 0,
    "difference_count": 0
  },
  "outputs": {
    "requirements_docx": true,
    "difference_report_docx": true
  }
}
```

字段可根据实际实现进行调整。

## 7. 下载 EoICD 条目化需求文档接口

### 7.1 接口定义

```text
GET /api/jobs/{job_id}/outputs/requirements
```

### 7.2 接口用途

用于下载任务生成的条目化需求文档。

### 7.3 输出文件

预期下载文件名：

```text
EoICD条目化需求.docx
```

### 7.4 说明

- 物理文件 `EoICD条目化需求.docx` 的内容**与"最优条目化需求"相同**（同一份 docx 落两份文件名）。
- 本接口保留向后兼容，**不**强制前端切换文案。
- 如果任务尚未完成或文件不存在，接口应返回明确错误信息。

### 7.5 下载 MiniMax 条目化需求文档接口

```text
GET /api/jobs/{job_id}/outputs/minimax-requirements
```

预期下载文件名：`MiniMax条目化需求.docx`。该文件是 MiniMax generation agent 在所有 chunk 上的全量候选合并。

### 7.6 下载 DeepSeek 条目化需求文档接口

```text
GET /api/jobs/{job_id}/outputs/deepseek-requirements
```

预期下载文件名：`DeepSeek条目化需求.docx`。该文件是 DeepSeek generation agent 在所有 chunk 上的全量候选合并。

## 8. 下载差异报告接口

### 8.1 接口定义

```text
GET /api/jobs/{job_id}/outputs/difference-report
```

### 8.2 接口用途

用于下载任务生成的 EoICD 与软件高层需求差异报告。

### 8.3 输出文件

预期下载文件名：

```text
EoICD与软件高层需求差异报告.docx
```

### 8.4 说明

如果任务尚未完成或文件不存在，接口应返回明确错误信息。

## 9. 错误响应草案

当前阶段可采用统一错误响应结构。

```json
{
  "detail": "错误说明"
}
```

后续可根据需要扩展为：

```json
{
  "error_code": "FILE_PARSE_FAILED",
  "message": "文件解析失败",
  "details": {}
}
```

当前版本暂不强制完整错误码体系。

## 10. 待实现阶段细化内容

以下内容可在最小可运行工程或端到端原型实现过程中进一步细化：

1. 文件上传字段名称；
2. 任务状态模型；
3. 任务结果摘要字段；
4. 错误码体系；
5. 文件下载响应头；
6. 前端轮询任务状态的时间间隔；
7. 是否采用同步任务、后台任务或队列；
8. 是否保留中间结果预览接口。

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

本文档是前后端接口协作的初始事实源，但在原型阶段允许随实现过程进行合理调整。

## 13. V4 路由（Issue A 落地，2026-07-28）

本节追加于原 12 节之后。V4 与 V3 双版本共存；V3 旧 API 行为不变（§1-§12），V4 新增 `/api/v4` 命名空间。

### 13.1 V4 健康检查

```text
GET /api/v4/health
```

预期返回：

```json
{ "status": "ok", "api_version": "v4" }
```

### 13.2 V4 创建分析任务

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
| `use_mock_llm` | bool (form) | 否（默认 false） | 是否走 mock |
| `judge_providers` | list[str] (form) | 否（默认 `["deepseek"]`） | 多模型 panel provider 白名单 ∈ `{deepseek, minimax, qwen}` |
| `enable_traceability_prefilter` | bool (form) | 否（默认 false） | 是否启用追溯预筛选 |

文件名校验：`[^A-Za-z0-9._\-一-龥]` 之外字符会被替换为 `_`；`safe_filename()`。

`judge_providers` 任一不在白名单 → 422。

预期返回（V4AnalyzeResponse）：

```json
{
  "job_id": "<uuid>",
  "status": "pending",
  "message": "V4 反向管线任务已创建"
}
```

### 13.3 V4 任务状态

```text
GET /api/v4/jobs/{job_id}
```

预期返回（V4JobStatusResponse）：

```json
{
  "job_id": "<uuid>",
  "status": "pending | running | completed | failed",
  "stage": "parse | label | match | multi_judge | review | report | done",
  "stage_index": 3,
  "stage_total": 5,
  "case_index": 12,
  "case_total": 12,
  "message": "Step 3/5: Multi-agent judging",
  "mock_models": ["minimax", "qwen"],
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

`mock_models` 按 ADR-001 D5 规则取值：`multi_judge_results.json.providers ∩ {"minimax", "qwen"}`；`USE_MOCK_LLM=1` 时所有 provider 都进 `mock_models`。

### 13.4 V4 任务结果

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
    "status_distribution": {"covered": 7, "needs_review": 4, "inconsistent": 1, "无匹配": 4},
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

### 13.5 V4 3 类对外下载

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

### 13.6 V4 错误响应

| 场景 | HTTP | 备注 |
| --- | --- | --- |
| `hlr_word_file` 缺失或非 .docx | 422 | |
| pub/sub Excel 都没传 | 422 | 至少传一个 |
| 任意 Excel 非 .xlsx | 422 | |
| 文件 >50 MB | 413 | 整请求 >200 MB |
| 文件名含非法字符 | 422 | |
| `judge_providers` 出现 `claude` 等 | 422 | 错误信息含 `allowed: deepseek, minimax, qwen` |
| `{model}` 不在 `{deepseek,minimax,qwen}` | 400 | `invalid model: <name>; allowed: ...` |
| 任务 `running` 时调 `/result` | 409 | `job not finished: status=...` |
| 任务 `failed` 时调 `/result` | 409 | |
| 跨版本查询（如 V3 路由查 V4 job_id） | 404 | `use /api/v4/jobs/... instead` |

### 13.7 V4 路径布局（V3 与 V4 输出文件落到不同根目录）

| 版本 | 根目录 | 内部结构 |
| --- | --- | --- |
| V3 | `backend/output/v3/{job_id}/` | 平铺：input / output 不分 |
| V4 | `backend/output/v4/{job_id}/` | 分层：`input/`（用户上传原始文件）+ `output/`（pipeline 产物） |

`docker-compose.yml` volume 映射 `./backend/output:/app/output`。

V4 `input/traceability/` 子目录用于 `enable_traceability_prefilter=true` 时追溯表落点。

### 13.8 V4 JSON 中间产物（D7 不暴露）

下列 7 个 JSON 是 V4 内部中间产物，**不**作为下载 API 暴露，**仅**保留在 `backend/output/v4/{job_id}/output/` 内供服务端日志与后续 Issue 调试：

- `multi_judge_results.json`
- `consensus_results.json`
- `reverse_matches.json`
- `reverse_report.json`
- `eoicd_requirements.json`
- `hlr_requirements.json`
- `hlr_labels.json`

如前端需要看这些数据，**不**通过 `GET /api/v4/jobs/{id}/outputs/{name}`；应在后续 Issue 加 `Accept: application/json` 内容协商或独立子路由。

### 13.9 V4 路由与 V3 路由的对应关系

| 关注点 | V3 路由 | V4 路由 |
| --- | --- | --- |
| 任务创建 | `POST /api/eoicd/analyze` | `POST /api/v4/coverage-analysis` |
| 任务状态 | `GET /api/jobs/{id}` | `GET /api/v4/jobs/{id}` |
| 任务结果 | `GET /api/jobs/{id}/result` | `GET /api/v4/jobs/{id}/result` |
| 下载输出 | `GET /api/jobs/{id}/outputs/{requirements,minimax-requirements,deepseek-requirements,difference-report}` | `GET /api/v4/jobs/{id}/outputs/{eoicd-xlsx,consensus-docx,consistency/{model}}` |
| 健康检查 | `GET /api/health` | `GET /api/v4/health` |

V3 与 V4 的 `outputs` schema、文件命名、`requirements` 字典字段完全不同；前端若需切换版本，须按 §1-§12 vs §13 各自实现客户端。

### 13.10 V4 API 变更原则

按本文件 §11：V4 任何 API 变更（路径、字段、状态定义、文件命名、错误响应）需同步更新本节。如未来 §13 内容与 `backend/app/api/v4/*.py` 不一致，**§13 以代码为准**并在本文件回写差异。
