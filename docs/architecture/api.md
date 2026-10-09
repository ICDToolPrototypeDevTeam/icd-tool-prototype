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
| `/api/v4/jobs/{job_id}/logs`                   | `GET`  | 任务日志增量拉取（`offset` 取上次返回的 `next_offset`，首次 0；`limit` 默认 500、上限 2000；进程重启后可从 `job.log` 尾部恢复） |
| `/api/v4/jobs/{job_id}/resume`                 | `POST` | 继续被中断 / 被终止的任务（按 manifest 参数快照重跑，已完成的 LLM 判定与已落盘的解析产物复用缓存） |
| `/api/v4/jobs/{job_id}/abandon`                | `POST` | 放弃被中断 / 被终止 / 排队中的任务（门槛 = 没有任何线程在跑；仅标记，不删除文件） |
| `/api/v4/jobs/{job_id}/cancel`                 | `POST` | 终止运行中 / 排队中的任务（`running` / `pending` → 200，其余状态 → 409；排队中（尚未绑定管线线程）的任务**立即**落 `canceled`、不等出队，运行中的任务在检查点停止；不删除任何文件，任务最终以 `canceled` 结束） |
| `/api/v4/jobs/{job_id}/result`                 | `GET`  | 查询任务处理结果摘要（按 `task_type` 分发正确性/完整性两种 schema） |
| `/api/v4/jobs/{job_id}/outputs/eoicd-xlsx`     | `GET`  | 下载 EoICD 条目化清单（xlsx）      |
| `/api/v4/jobs/{job_id}/outputs/consensus-docx` | `GET`  | 下载多模型共识差异分析报告（docx）            |
| `/api/v4/jobs/{job_id}/outputs/consistency/{model}` | `GET`  | 下载单模型差异分析报告（docx）           |
| `/api/v4/jobs/{job_id}/outputs/forward-xlsx`   | `GET`  | 下载正向完整性分析明细表（xlsx）            |
| `/api/v4/jobs/{job_id}/outputs/forward-docx`   | `GET`  | 下载正向完整性分析报告（docx）            |
| `/api/v4/history`                              | `GET`  | 查询服务器上保留的历史结果（磁盘口径，含内存中没有的已完成任务） |
| `/api/v4/history/delete`                       | `POST` | 硬删除历史结果（整目录，含上传的输入文件；需 `confirm=true`） |

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
| `use_mock_llm` | bool (form) | 否（默认不覆盖） | 显式覆盖 mock 开关；未提供时以 `.env` 的 `USE_MOCK_LLM` 为准（当前前端总是显式提交本字段） |
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
  "status": "pending | running | completed | failed | interrupted | abandoned | canceled",
  "stage": "parse | label | match | multi_judge | review | report | done",
  "stage_index": 3,
  "stage_total": 5,
  "case_index": 12,
  "case_total": 12,
  "message": "Step 3/5: Multi-agent judging",
  "resumed": false,
  "reuse": { "reused": 33, "rerun": 0 },
  "mock_models": ["minimax", "qwen"],
  "mock": false,
  "cancel_requested": false,
  "error": null,
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

`mock_models` 按 ADR-001 D5 规则取值：`multi_judge_results.json.providers ∩ {"minimax", "qwen"}`；该交集**总是**执行，故该字段只会出现 `minimax` / `qwen`（`deepseek` 不会出现）。`USE_MOCK_LLM=1` 时三个模型的判定都由 Mock 产生，但该字段的取值规则不变。

`resumed` 表示本次运行是否为中断后的恢复运行（`resume` 启动后置 `true`，首跑为 `false`）。`reuse` 仅在恢复运行时非空，以**模型调用次数**为单位反映本次运行的两个去向：`reused` = 直接复用中断前已完成结果、未发起请求的调用次数；`rerun` = 本次接续发起的调用次数（含中断前未执行到的步骤，以及中断时正在执行或已失败、缓存中没有可用结果的调用）。计数按**反向管线的 Step 2（HLR 标注）/4/5/5.5/5.6** 逐条模型调用累计、不去重（同一结果在后续步骤再次命中会再计一次），因此与 `llm_cache.jsonl` 的行数不是同一口径；也与需求条数不同 —— 每个需求对应「每个模型一次判定 + 一次共识」等多次调用。其中 HLR 标注整批完成、命中 `hlr_labels.json` 直接加载时，加载的 N 条按 `reused` 计入。**正向管线不产生 `reuse` 计数**（恒为 `{"reused": 0, "rerun": 0}`）：新正向多 judge 一致性管线的续跑复用发生在任务目录内的正向工作区（代码树副本与各 judge 工作区中间结果原样复用，见第 13.2 节），不逐条经过父进程记账。

`interrupted` / `abandoned` 为任务中断恢复相关状态，见第 13 节。

`mock` 表示本次运行是否按 MOCK 模式执行（结果页据此提示「模拟数据不可用于验收」）；`cancel_requested` 表示已请求终止、正等待管线在检查点停止，前端据此把终止按钮显示为「正在终止…」。

`canceled` 表示运行中的任务被用户主动终止（`POST /api/v4/jobs/{job_id}/cancel`，见第 2 节）；它与 `abandoned` 语义不同：`abandoned` 指任务被中断 / 被终止后由用户放弃，是**终态**，`canceled` 指**仍在运行**的任务被终止，**不是终态** —— 被终止的任务可 `resume` 续跑（按参数快照重跑，续跑前复位取消标志）或 `abandon` 收尾。两者都不删除任何文件；终止是协作式的，`status` 会保持 `running` / `pending` 直至管线在检查点停止，接口不返回虚构的中间状态。

**任务队列（多输入批量并行）**：任务不直接各起线程，而是统一进入**进程内队列**（`backend/app/job_scheduler.py`），由固定 `MAX_CONCURRENT_JOBS`（默认 2，见 `backend/.env.example`）个常驻工作线程领取执行；超出部分在队列中等待。新任务按入队先后 FIFO；`resume` 续跑的任务**插到队首**（它是用户「已经在做、被打断」的工作，不排在刚提交的新任务后面重新等队尾），插队只作用于等待队列、不抢占正在执行的任务。排队中的任务 `status` 为 `pending`、`message` 为「任务已排队，等待空闲执行槽位（并发上限 N）」；排队期间被终止 / 放弃的任务**不会被执行**（出队时跳过，跳过原因写入该任务日志，可在 `/logs` 与日志面板读到「排队等待期间…跳过执行」）。对排队任务的 `cancel` 立即生效（直接落 `canceled`，不等前面的任务跑完），随后照常可 `abandon`（排队中也可**直接**放弃，不必先终止）或 `resume`（重新入队后与首次提交一样如实落 `pending` + 排队提示，被工作线程领走才翻为 `running`）。队列本身不跨进程持久化；排队中的任务已落盘 manifest，容器重启后由启动扫描一并标记为 `interrupted`（可 `resume` 重新入队 / `abandon`），与运行中任务同待遇。

`error` 为结构化失败信息，仅在任务 `failed` 或 `canceled` 时非空，运行中与成功时为 `null`：

```json
{
  "category": "INPUT_FILE | INPUT_FORMAT | CONFIG | LLM_AUTH | LLM_NETWORK | LLM_TIMEOUT | LLM_RATE_LIMITED | LLM_OUTPUT | ALL_PROVIDERS_UNHEALTHY | OUTPUT_DISK | CANCELLED | INTERNAL",
  "title": "模型服务认证失败",
  "stage": "multi_judge",
  "stage_index": 4,
  "error_type": "HTTPError",
  "message": "401 Client Error: Unauthorized for url: https://api.deepseek.com/v1/chat/completions",
  "detail": "HTTPError: 401 Client Error: Unauthorized for url: https://api.deepseek.com/v1/chat/completions (HTTP 401)",
  "traceback_tail": "Traceback (most recent call last): ...",
  "hint": "请联系管理员检查模型 API Key 是否有效或已过期。",
  "at": "ISO-8601"
}
```

`category` 与 `title` 的取值：`INPUT_FILE` 输入文件缺失 / `INPUT_FORMAT` 文件无法解析 / `CONFIG` 服务配置缺失 / `LLM_AUTH` 模型服务认证失败 / `LLM_NETWORK` 无法连接模型服务 / `LLM_TIMEOUT` 模型服务超时 / `LLM_RATE_LIMITED` 模型服务限流 / `LLM_OUTPUT` 模型返回格式异常 / `ALL_PROVIDERS_UNHEALTHY` 所有模型服务均不可用 / `OUTPUT_DISK` 输出写入失败 / `CANCELLED` 任务已取消 / `INTERNAL` 内部错误。`hint` 为面向用户的可执行建议，`traceback_tail` 为堆栈尾部（最多 20 行），`at` 为分类发生时间。 其中 `LLM_AUTH` 为**预留分类**，当前版本不会产生该取值：单个模型的 401/403 会被逐条兜底（判官降级为 error judgment、HLR 标注退化为兜底标签），只有全部模型都不可用时才以 `ALL_PROVIDERS_UNHEALTHY` 报出。

## 6. 查询任务结果摘要接口

```text
GET /api/v4/jobs/{job_id}/result
```

仅当 `status == completed` 时返 200 + 完整结果，否则 409。

任务失败时的失败原因请改从 `GET /api/v4/jobs/{job_id}` 的 `error` 字段读取（见第 5 节）。

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

下载保存名在上述物理文件名基础上追加产物生成时刻后缀 `_YYYYMMDD_HHMM`（东八区，取自产物文件落盘时刻，非下载时刻——重复下载文件名稳定不变）。例：`EoICD与SWHLR单模型差异分析报告_DeepSeek_20261009_1715.docx`。磁盘产物文件名不变。

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
| `controller_profile` | str (form) | 否（空 = 自动识别） | 系统类型：`ams` / `eps` / `fgmc` / `hscu`；为空时按上传文件名自动识别；取值不在项目表 → 422 |
| `trace_files` | UploadFile (.xlsx) × N | 追溯模式至少 1 张 | 追溯表多文件单字段（同名字段重复提交，照反向 `traceability_files` 模式）；几张、各是什么表由正向树按表头关键字自动识别（AMS 2 张 / EPS 4 层链 3 张），接口不做张数与配对约束 |
| `use_mock_llm` | bool (form) | 否（默认不覆盖） | 显式覆盖 mock 开关；未提供时以 `.env` 的 `USE_MOCK_LLM` 为准（当前前端总是显式提交本字段） |

`analysis_mode` 不是请求字段（历史字段被忽略）：追溯表不上传（0 张）→ `full`；上传任意张（≥1 张，含 1 张）→ `trace`。接口不做成对校验，张数与类型由正向树按表头自动识别（AMS 2 张 / EPS 4 层链 3 张）。

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
    "total_blocks": 4738,
    "covered_direct": 76,
    "covered_aggregate": 32,
    "parent_referenced": 0,
    "possible": 253,
    "uncovered": 4377,
    "unsupported": 0,
    "input_error": 0,
    "ai_reviewed": 76,
    "eoicd_count": 4738,
    "hlr_count": 0
  },
  "outputs": {
    "forward_xlsx": true,
    "forward_docx": true
  },
  "errors": []
}
```

字段口径（Issue #119 新正向管线）：

- `analysis_mode` 仅 `full` / `trace` 两值，由是否上传追溯表决定（未上传 → `full`；上传任意张 → `trace`，见 12.1），非请求字段；
- 三态判定：`covered_direct` = 已落实、`covered_aggregate` = 部分落实、`uncovered` = 未落实（多 judge 一致性的判定分布）；
- `possible` = 其余需人工判断项（`total_blocks − 三态之和`，含「不一致」桶），`total_blocks` / `eoicd_count` = 参与判定的 EoICD 业务对象总数；
- `ai_reviewed` = 多 judge 复核覆盖条数（聚合产物 `review_coverage.reviewed`）；
- `parent_referenced` / `unsupported` / `input_error` / `hlr_count` 保持字段兼容：新管线不产出对应语义，恒为 0。

### 12.3 下载正向输出

```text
GET /api/v4/jobs/{job_id}/outputs/forward-xlsx
GET /api/v4/jobs/{job_id}/outputs/forward-docx
```

| URL 段 | 物理文件名 |
| --- | --- |
| `forward-xlsx` | `EoICD至HLR正向完整性分析明细.xlsx` |
| `forward-docx` | `EoICD至HLR正向完整性分析报告.docx` |

下载保存名同样追加 `_YYYYMMDD_HHMM` 生成时刻后缀（规则同第 7 节）。

正向下载接口校验 `task_type == "completeness"`；反向下载接口校验 `task_type == "correctness"`。用错任务下载 → 404。

### 12.4 正向中间产物（不暴露）

正向管线分阶段落盘，以下产物不对外下载：

- `<job_dir>/output/forward_summary.json`：结果快照（结果摘要的落盘副本，供容器重启后反读；见第 13 节）；
- `<job_dir>/forward/runs/integration_workspace/`：多 judge 一致性管线的中途产物（阶段1 共享产物、各 judge 工作区及其 AI map 缓存、聚合与复核 JSON）。任务**成功**收尾时该目录整体清理（全量任务实测约 574MB；失败/中断任务保留，供续跑与排查）；
- `<job_dir>/forward/output/`：runner 落盘的 `run_manifest_*.json`、Word/Excel 报告原件（整理后复制为两个固定交付名，见 12.3）。

### 12.5 正向错误响应

| 场景 | HTTP |
| --- | --- |
| `hlr_word_file` 缺失或非 .docx | 422 |
| pub/sub Excel 都没传 | 422 |
| 任意 Excel / 追溯表非 .xlsx | 422 |
| `controller_profile` 非空且不在项目表 | 422 |
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
    "status": "pending | running | completed | failed | interrupted | abandoned | canceled",
    "message": "Step 4/6: Multi-agent judging",
    "created_at": "ISO-8601",
    "updated_at": "ISO-8601",
    "input_files": ["HLR.docx", "Publisher.xlsx"]
  }
]
```

- 两个 query 参数均可选：`status` 按状态精确过滤，`task_type` 按任务类型过滤。
- 内存中只保留本次进程创建的任务与启动时载入的 `interrupted` 任务，因此该接口不会返回历史 `completed` / `failed` 任务。`canceled` 同样**不在**启动载入范围内：被终止的任务只在**本次进程存活期间**可续跑 / 可放弃，容器重启后即从列表中消失（与 `completed` / `failed` 同待遇）。**看历史任务请用 `GET /api/v4/history`（第 14 节），那是磁盘口径的接口。**
- `input_files` 取自 manifest 参数快照中的输入文件（仅文件名，不含路径）；CLI 等无 manifest 的任务为空数组。

### 13.2 继续任务

```text
POST /api/v4/jobs/{job_id}/resume
```

可继续的任务状态为 `interrupted`（进程中断）与 `canceled`（用户终止）；其余状态一律 409。续跑前会先**复位取消标志**（`cancel_requested` 与内部取消事件）—— 同一进程内被终止的任务仍带着上次置位的取消标志，不复位则第一个检查点会立即再次取消。

按 manifest 中的参数快照（`judge_providers` / `use_mock_llm` / `controller_profile` / `no_refine` / `analysis_mode` / 追溯表等）重跑，走与新建任务完全相同的启动路径。前端不接受也不传递任何覆盖参数。

返回：

```json
{ "job_id": "<uuid>", "status": "pending", "message": "任务已继续执行（将重新运行分析流程）" }
```

`:status` 是入队后的**真实状态**：`pending` = 已重新入队、等待空闲执行槽位（与首次提交一致，`GET /jobs/{id}` 的 `message` 为排队提示），任务被工作线程领走时才翻为 `running`，见第 5 节「任务队列」。队列为空时这一瞬间即完成。

重跑成本说明（反向/正确性管线）：

- Step 1（文件解析）复用上一轮已落盘的 `output/eoicd_requirements.json` 与 `output/hlr_requirements.json`（走管线自带的 `[skip] Using cached EoICD JSON / HLR JSON` 分支），不再重新解析 Excel/Word，实测约 38s → 约 11s（省下的 11s 是 `EoICD条目化清单.xlsx` 的重新生成，那是交付物）。**两份产物缺一不可**，且只在 `resumed=true` 时启用：首跑时同名文件可能是上一轮残留，当成解析结果用会让新上传的输入文件完全不生效；
- Step 2（HLR AI 标注）的每条标注按内容寻址复用（`output/llm_cache.jsonl`，`kind=hlr_label`），只有未完成的才真正调用模型；整批完成后 `output/hlr_labels.json` 落盘，此后直接整体加载跳过；
- Step 4（多模型判定）、Step 5（共识）、Step 5.5（1★/2★ 复查）的**已完成的单条 LLM 判定结果**按内容寻址复用，只有未完成的才真正调用模型，结果随完成进度增量写入 `output/llm_cache.jsonl`。因此中断越晚、继续时省下的调用越多；
- Step 3/6 等确定性步骤（匹配、报告生成）本就很快，仍然全量重跑。
- Step 1 的解析复用不计入 `reuse` 计数：那是文件解析、不是模型调用，两个计数仍严格等于模型调用次数。

重跑成本说明（正向/完整性管线）：

- 续跑**不重新复制代码树**：每任务一份的正向代码副本 `<job_dir>/forward/` 原样复用（含上一轮的 `runs/integration_workspace/` 中间产物与各 judge 工作区的 AI map 缓存；可续跑的本就是失败/中断任务，成功任务收尾时 `runs/` 已清理，见 12.4）；**跨运行真正命中缓存的是 HLR 聚类 AI 映射**（`HLR需求聚类/hlr_cluster_tool/ai_maps.py` 按 map 文件的 `covered_reqs` 元数据增量，只对新增需求调用模型）。阶段1 与各 judge 的阶段2/3/4 仍按原样流程重新执行，**身份匹配 / 属性匹配的 AI 判定不复用**（其结果文件只写不读，`name_pair_deduper.py` 明确运行间独立），续跑不会省下这部分模型调用；
- 新正向管线不使用反向的 `output/llm_cache.jsonl`（旧正向的 `kind=forward_hlr_label` / `kind=forward_review` 已随旧实现删除），也没有旧 Step 8 等步骤；
- `reuse` 计数对正向任务不适用：恒为 `{"reused": 0, "rerun": 0}`（见第 5 节），续跑复用情况见任务日志中正向 runner 的工作区输出。

复用只发生在**同一任务目录内**，不跨任务：反向管线的 `llm_cache.jsonl` 按 `kind` 区分步骤，正向管线整体复用任务目录内的正向工作区，两者即使 prompt 完全相同也互不命中；换模型、改提示词、上游输入变化等会改变调用内容的情况都会自动失效并重新调用。

恢复启动后任务标记 `resumed=true`，反向任务从 0 重新累计 `reuse` 计数（多次恢复不累加上一轮）；`GET /jobs/{id}` 可实时看到 `reuse.reused` / `reuse.rerun` 递增，用于前端展示「已复用中断前结果 N 次 · 接续调用模型 M 次」（次数即模型调用次数，口径见第 5 节）。

### 13.3 放弃任务

```text
POST /api/v4/jobs/{job_id}/abandon
```

只把状态标记为 `abandoned`，**不删除任何输入或输出文件**（磁盘清理不在本期范围）。可放弃的任务为 `interrupted` 与 `canceled`（与 `resume` 同一门槛），**外加排队中 / 从未启动的 `pending`**（`thread_ident is None`，即没有任何线程在跑 —— 排队中的任务已入队但工作线程尚未领取；从未启动的幽灵任务连 `job_dir` 都没有）。放弃是这套状态机里的终态：被终止的任务需要一个收尾方式；排队中的任务出队时会被跳过执行（见第 5 节），一步放弃即可，不必先 `cancel` 再 `abandon` 两步走。门槛统一为「**没有任何线程在跑**」——`running` 的任务总有线程，只能先 `cancel`。放弃与工作线程出队之间没有锁，存在毫秒级竞态窗口（出队裁决刚通过即被放弃），此时任务会照常跑完并落 `completed`，无数据损坏。

配套的不变量是「**登记即承诺运行**」：创建任务的接口用 `JobManager.new_job()` 只构造、不登记，由 `launch_*_pipeline` 在入队（起线程）前 `register()`。上传接口还有保存文件与识别系统类型两步，任一步失败请求就结束了、线程不会启动，若在创建时就登记，失败的上传会在列表里留下永远停在 `pending` 的空记录（既无进度也无日志，且当时无法放弃）。

返回：

```json
{ "job_id": "<uuid>", "status": "abandoned", "message": "任务已放弃（文件保留在输出目录，未删除）" }
```

### 13.4 中断恢复错误响应

| 场景 | HTTP |
| --- | --- |
| `resume` / `abandon` 的任务不存在（如后端重启后该任务未留下可恢复记录） | 404 |
| `resume` 的任务状态不是 `interrupted` / `canceled`（如 `completed` / `running` / `abandoned`） | 409 |
| `abandon` 的任务既不是 `interrupted` / `canceled`，也不是「没有任何线程在跑」的 `pending`（如 `running`、`completed`、`abandoned`） | 409 |
| `resume` 时 manifest 记录的输入文件或追溯目录已不存在 | 409 |

### 13.5 manifest 字段说明

`output/v4/{job_id}/job.json` 由任务自身在每次 `update()` 时原子写入（`.tmp` + `os.replace`），字段：

```json
{
  "schema_version": 1,
  "job_id": "<uuid>",
  "task_type": "correctness | completeness",
  "status": "pending | running | completed | failed | interrupted | abandoned | canceled",
  "message": "Step 1/6: Parsing input files",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "params": { "hlr_path": "input/xxx.docx", "...": "..." },
  "resumed": true,
  "reuse": { "reused": 96, "rerun": 0 }
}
```

`resumed` / `reuse` 为中断恢复可视化字段（见第 5 节）：恢复运行开始与每完成一条判定（反向按 case）时随 `_persist` 落盘，进程被杀后计数不丢；正向任务的 `reuse` 恒为 `{"reused": 0, "rerun": 0}`（新正向管线不产生该计数，见第 13.2 节）。旧 manifest 无这两键时按默认值（`false` / `null`）加载，无需迁移。

`params` 中的路径一律为相对 `job_dir` 的相对路径，保证输出目录整体搬迁后仍可恢复。不持久化任务结果（重跑时重新生成）。无 `job_dir` 的任务（如 CLI 直跑 `app/v4/cli.py`）不写 manifest，行为与本次改动前一致。

**部署前提（Docker）**：本能力依赖 `output/` 跨进程存活。`docker-compose.yml` 已把 `./backend/output` 以 bind mount 挂到容器内 `/app/output`（且未设置 `OUTPUT_DIR`），因此 `docker compose restart` / `down` + `up` 后 manifest 仍在、中断任务不丢。若把该卷去掉、改为匿名 volume 或设置 `OUTPUT_DIR` 指向容器内非挂载路径，本能力会静默失效（重启后任务列表为空）。

## 14. 历史结果接口（列表 / 删除）

第 13 节的 `/jobs` 是**内存口径**（本进程新建 + 启动载入的中断任务），跑完的任务重启后即从内存消失；历史结果走**磁盘口径**：直接扫 `backend/output/v4/*/`，因此重启后仍能看到并下载以前的结果。前端入口是顶栏「历史结果」页。

与之配套，下载接口（第 7 节）定位任务时按「**内存 → 磁盘 manifest**」取任务类型：内存里查不到时读该目录的 `job.json`；两者都取不到（上传失败留下的残留目录没有 manifest）就不再校验类型，交由「该产物文件是否存在」决定 404 与否。修复前只认内存，进程重启后已完成任务的下载一律 404。

### 14.1 查询历史结果

```text
GET /api/v4/history
```

（V4HistoryItem，返回数组；按 `created_at` 倒序）

```json
[
  {
    "job_id": "<uuid>",
    "task_type": "correctness | completeness | \"\"",
    "status": "pending | running | completed | failed | interrupted | abandoned | canceled | unknown",
    "message": "Step 6/6: Generating report",
    "created_at": "ISO-8601",
    "updated_at": "ISO-8601",
    "finished_at": "ISO-8601 | null",
    "input_files": ["HLR.docx", "Publisher.xlsx"],
    "outputs": {
      "eoicd_xlsx": true,
      "consistency_deepseek_docx": false,
      "consistency_minimax_docx": false,
      "consistency_qwen_docx": false,
      "consensus_docx": true,
      "forward_xlsx": false,
      "forward_docx": false
    },
    "size_bytes": 12345678,
    "mock": false
  }
]
```

- **每个子目录都会列出**，不只有跑完的：`status` 取 manifest 原值；**没有可读 manifest** 的残留目录（上传失败留下、或 manifest 损坏）以 `status="unknown"`、`task_type=""`、时间取目录 mtime 出现 —— 这类目录不出现在任何其它列表里，却照样占磁盘，只能在这里看到并清理。
- `outputs` 是七类对外产物的存在性布尔（反向 5 类 + 正向 2 类），与第 7 节的下载路径一一对应；全为 `false` 即「无可下载产物」（未跑完或生成失败）。
- `size_bytes` 为该任务目录占用的字节数（**含 `input/` 里用户上传的原始文件**与全部中间产物），供删除前告知释放空间。
- `mock` 含义同状态接口：模拟数据不可用于验收。
- 该接口不做鉴权（与其它接口一致），只读磁盘，不改变任何状态。

### 14.2 删除历史结果（硬删除）

```text
POST /api/v4/history/delete
Content-Type: application/json

{ "job_ids": ["<uuid>", "..."], "confirm": true }
```

删除粒度是**整个任务目录** `output/v4/{job_id}/`（`shutil.rmtree`），**包括 `input/` 里用户上传的原始文件**与全部中间产物。不进回收站、没有撤销，因此设了三道关卡：

| 关卡 | 行为 |
| --- | --- |
| `confirm` 不是显式 `true` | 400，什么都不删 |
| `job_ids` 为空 | 400 |
| `job_id` 不是 uuid 形式，或解析后的路径不在 `output/v4/` 之下 | 400（防目录穿越 / 软链指向别处） |
| 批次里任一任务还在跑（有线程在跑，或 `pending` 而已 `set_dir`） | **409，整批不删**（部分成功的批次会让人分不清哪些删掉了） |
| 单个目录删除失败（权限、文件被占用） | 计入 `failed`，不影响同批其它任务 |
| 目录已不存在（重复删除） | 计入 `failed`，`error` 为「目录不存在（可能已被删除）」 |

返回：

```json
{
  "deleted": [{ "job_id": "<uuid>", "freed_bytes": 12345678 }],
  "failed": [{ "job_id": "<uuid>", "error": "目录不存在（可能已被删除）" }],
  "total_freed_bytes": 12345678
}
```

删除成功后同时从内存登记表与日志缓冲里摘掉该任务：否则任务列表里会留下一条指向已删目录的幽灵记录（点「继续」报文件缺失、点日志报目录不存在）。服务端另打一行 `[history] deleted job <id> dir=<path> freed=<N>B` 日志。

### 14.3 历史结果错误响应

| 场景 | HTTP |
| --- | --- |
| `job_ids` 为空，或缺 `confirm=true` | 400 |
| `job_id` 非法（非 uuid / 越出 `output/v4/`） | 400 |
| 批次内有任务正在运行 | 409 |
