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
| `/api/v4/jobs/{job_id}`                        | `GET`  | 查询任务状态                          |
| `/api/v4/jobs/{job_id}/result`                 | `GET`  | 查询任务处理结果摘要                      |
| `/api/v4/jobs/{job_id}/outputs/eoicd-xlsx`     | `GET`  | 下载 EoICD 条目化清单（xlsx）      |
| `/api/v4/jobs/{job_id}/outputs/consensus-docx` | `GET`  | 下载多模型共识差异分析报告（docx）            |
| `/api/v4/jobs/{job_id}/outputs/consistency/{model}` | `GET`  | 下载单模型差异分析报告（docx）           |

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

## 5. 查询任务状态接口

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

下列 7 个 JSON 是 V4 内部中间产物，**不**作为下载 API 暴露，**仅**保留在 `backend/output/v4/{job_id}/output/` 内供服务端日志与后续 Issue 调试：

- `multi_judge_results.json`
- `consensus_results.json`
- `reverse_matches.json`
- `reverse_report.json`
- `eoicd_requirements.json`
- `hlr_requirements.json`
- `hlr_labels.json`

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
