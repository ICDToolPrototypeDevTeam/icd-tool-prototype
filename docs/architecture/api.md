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
| `/api/v4/health`                               | `GET`  | 后端健康检查（含 MongoDB 可用性；未登录可访问） |
| `/api/v4/auth/login`                           | `POST` | 用户名 + 密码登录，下发 HttpOnly Cookie |
| `/api/v4/auth/logout`                          | `POST` | 退出登录并清除 Cookie |
| `/api/v4/auth/me`                              | `GET`  | 当前登录用户（`id` / `username` / `is_admin`） |
| `/api/v4/admin/users`                          | `GET`  | 管理员查询全部账号 |
| `/api/v4/admin/users`                          | `POST`  | 管理员创建账号（请求字段 `is_admin`） |
| `/api/v4/admin/users/{user_id}`                | `PATCH` | 管理员重置密码 / 禁用启用 / 设置取消管理员（请求字段 `is_superuser`） |
| `/api/v4/coverage-analysis`                    | `POST` | 上传输入文件并创建 V4 反向管线任务               |
| `/api/v4/completeness-analysis`                | `POST` | 上传输入文件并创建 V4 正向完整性分析任务（Celery 模式下 503，见第 18 节） |
| `/api/v4/jobs`                                 | `GET`  | 查询未完成/中断任务列表（可选 `status`、`task_type` 过滤） |
| `/api/v4/jobs/{job_id}`                        | `GET`  | 查询任务状态                          |
| `/api/v4/jobs/{job_id}/logs`                   | `GET`  | 任务日志增量拉取（`offset` 取上次返回的 `next_offset`，首次 0；`limit` 默认 500、上限 2000；进程重启后可从 `job.log` 尾部恢复；**只认内存里的任务**） |
| `/api/v4/jobs/{job_id}/logs/download`          | `GET`  | 下载运行日志 `job.log`（**磁盘口径**：任务失败 / 中断 / 被终止 / 进程重启后仍可下载，供排查出错原因与执行过程） |
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
| `/api/v4/settings`                             | `GET`  | 查询当前生效的模型名与思考开关（运行时设置） |
| `/api/v4/settings`                             | `PUT`  | 保存模型名与思考开关并立即生效（落盘 `output/settings.json`，无需重启） |
| `/api/v4/hlr-schemas`                          | `GET`  | 查询内置模板与自定义 HLR 表格样式列表（第 16 节） |
| `/api/v4/hlr-schemas`                          | `POST` | 新建自定义 HLR 表格样式（201） |
| `/api/v4/hlr-schemas/{schema_id}`              | `PUT`  | 更新自定义样式（内置模板只读） |
| `/api/v4/hlr-schemas/{schema_id}`              | `DELETE` | 删除自定义样式（内置模板只读） |
| `/api/v4/hlr-schemas/scan`                     | `POST` | 扫描上传 .docx 的表格结构（不调用 LLM） |
| `/api/v4/hlr-schemas/preview`                  | `POST` | 用指定样式试解析 .docx（不调用 LLM） |

`{model}` ∈ `{deepseek, minimax, qwen}`。

除 `GET /api/v4/health` 与 `POST /api/v4/auth/login` 外，本版所有接口都要求登录；任务级接口再按归属校验。完整权限矩阵、Cookie 参数与 401/403/404/503 语义见第 17 节。

任务执行模式由后端环境变量 `JOB_EXECUTOR` 决定（`celery` 为生产部署，`inline` 仅测试/回滚）。Celery 模式下同一套接口路径与请求/响应字段不变，但任务由独立 Worker 进程执行，新增的 503（控制面/队列不可用、正向未启用）与 409（Worker 仍在执行）语义见第 18 节。

## 3. 健康检查接口

```text
GET /api/v4/health
```

预期返回（MongoDB 可用时）：

```json
{ "status": "ok", "api_version": "v4", "database": "ok" }
```

MongoDB 不可用时返回 **503**：

```json
{ "detail": "database unavailable" }
```

该接口无需登录，供部署自检与前端连接状态展示使用。

## 4. 创建分析任务接口

```text
POST /api/v4/coverage-analysis
Content-Type: multipart/form-data
```

需登录（未登录 → 401）。创建成功后后端把当前登录用户写入任务归属（`owner_id` / `owner_username`），后续任务级接口按该归属校验。

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
| `controller_profile` | str (form) | 否（空 = 自动识别） | 系统类型 ∈ `{ams, fgmc, hscu, rpdu, generic}`，决定 HLR 解析规则、分类关键词、追溯表配置与 AI 标注示例；`generic`（其他系统/通用）必须同时提供 `hlr_schema`，且不会被自动识别命中 |
| `no_refine` | bool (form) | 否（默认 false） | 仅 RPDU profile 生效；`true` 时关闭 Step 3.5 refine 后处理（matched ICD Block 无关过滤 + 精确/同义词补采），与最初版 RPDU 行为对齐做 A-B 对照 |
| `hlr_schema` | str (form, JSON) | 否（默认不启用） | 自定义 HLR 表格样式引用（见下）；仅 `.docx` 且必须同时提供 `controller_profile` |

文件名校验：`[^A-Za-z0-9._\-一-龥]` 之外字符会被替换为 `_`；`safe_filename()`。

上传并发加固：文件按 1MB 分块流式写入 `.part` 临时文件，完成后原子改名；单文件上限 50MB、单请求全部文件合计上限 200MB（含 `/completeness-analysis`、`/hlr-schemas/scan`、`/hlr-schemas/preview`），超限返回 413。HLR 表格自动识别等 CPU 型探测在线程池执行，不阻塞事件循环。

`judge_providers` 任一不在白名单 → 422。

`controller_profile` 不在白名单 → 422（在创建任务前 fail fast）。不传该字段时按文档内容自动识别（只覆盖 `ams` / `fgmc` / `hscu` / `rpdu`；无法识别 → 422，提示手动选择）。

`no_refine` 仅 RPDU profile 生效；非 RPDU profile 传入该字段被忽略。CLI 等价形参为 `reverse-analyze --no-refine`。

`hlr_schema` 为可选的 HLR 表格样式引用（JSON 字符串），仅影响 Step 1 的 HLR 解析（行标签映射与需求表识别），不改变后续步骤的输入契约。接受三种形式：

1. **完整样式配置**：`{"version": 1, "mode": "docx_vertical", "fields": {...}, "identify": {"id_pattern": "..."}}`（结构见第 16 节），可直接使用、无需先保存；
2. **内置模板引用**：`{"template_id": "builtin_ams"}`（可选 `builtin_ams` / `builtin_fgmc` / `builtin_hscu` / `builtin_rpdu`）；模板所属控制器必须与 `controller_profile` 一致，否则 422；`builtin_rpdu` 不可用于自定义样式（422）；
3. **已保存样式引用**：`{"schema_id": "sch_xxxxxxxx"}`；样式不存在或配置非法 → 422（创建任务接口统一按样式校验失败处理）。

约束：

- `hlr_schema` 仅支持 `hlr_word_file` 为 `.docx`；`.xlsx` → 422；
- 使用 `hlr_schema` 时必须在同一请求中显式提供 `controller_profile`（不会走自动识别），否则 422；
- 样式在创建任务目录之前完成解析与校验，非法引用不会留下残留任务目录；
- 解析后的样式快照写入任务目录 `input/hlr_schema.json` 与任务 manifest 的 `params.hlr_schema`；续跑（从 manifest 恢复）按快照重建解析配置，不依赖该样式之后是否被修改或删除；
- 未提供 `hlr_schema` 时行为与改动前完全一致；
- `controller_profile = "generic"`（其他系统/通用）必须提供 `hlr_schema`，否则 422；通用系统不支持追溯预筛（同时传 `enable_traceability_prefilter=true` 与追溯文件 → 422）；
- `generic` 未声明 `auto_detect`，不会参与自动识别，必须显式选择。

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

需登录；普通用户仅能访问 `owner_id` 属于自己的任务，管理员可访问全部。任务不存在、无归属或归属不是当前用户 → **404**（不区分“不存在”与“无权访问”，避免用状态码差异探测他人任务）。

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
  "token_usage": { "total_tokens": 123456 },
  "error": null,
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

`mock_models` 按 ADR-001 D5 规则取值：`multi_judge_results.json.providers ∩ {"minimax", "qwen"}`；该交集**总是**执行，故该字段只会出现 `minimax` / `qwen`（`deepseek` 不会出现）。`USE_MOCK_LLM=1` 时三个模型的判定都由 Mock 产生，但该字段的取值规则不变。

`resumed` 表示本次运行是否为中断后的恢复运行（`resume` 启动后置 `true`，首跑为 `false`）。`reuse` 仅在恢复运行时非空，以**模型调用次数**为单位反映本次运行的两个去向：`reused` = 直接复用中断前已完成结果、未发起请求的调用次数；`rerun` = 本次接续发起的调用次数（含中断前未执行到的步骤，以及中断时正在执行或已失败、缓存中没有可用结果的调用）。计数按**反向管线的 Step 2（HLR 标注）/4/5/5.5/5.6** 逐条模型调用累计、不去重（同一结果在后续步骤再次命中会再计一次），因此与 `llm_cache.jsonl` 的行数不是同一口径；也与需求条数不同 —— 每个需求对应「每个模型一次判定 + 一次共识」等多次调用。其中 HLR 标注整批完成、命中 `hlr_labels.json` 直接加载时，加载的 N 条按 `reused` 计入。**正向管线不产生 `reuse` 计数**（恒为 `{"reused": 0, "rerun": 0}`）：新正向多 judge 一致性管线的续跑复用发生在任务目录内的正向工作区（代码树副本与各 judge 工作区中间结果原样复用，见第 13.2 节），不逐条经过父进程记账。

`interrupted` / `abandoned` 为任务中断恢复相关状态，见第 13 节。

`mock` 表示本次运行是否按 MOCK 模式执行（结果页据此提示「模拟数据不可用于验收」）；`cancel_requested` 表示已请求终止、正等待管线在检查点停止，前端据此把终止按钮显示为「正在终止…」。

`token_usage` 是任务**累计**消耗的模型 token 总量（只暴露总量，不拆输入/输出、不拆模型、不提供调用次数）。口径如下：

- 仅 API 任务统计：新任务从 0 开始；续跑（`resume`）在原值上继续累加，不因重跑步骤而清零。MOCK 任务的 `0` 是**有效值**（未产生真实调用），前端用 MOCK 标签说明。
- **旧任务**（本功能上线前创建、manifest 中没有该字段）返回 `null`：前端不展示、DOCX 不写该行 —— 不把未统计伪装成 0。
- 运行中读内存实时值；落盘按 5 秒节流，进入终态时强制落盘。完成页/结果接口读**实时值**，可能比 DOCX 报告里的数字略大（报告取生成时点的一次性快照，之后迟到的并发调用不计入报告）。
- 反向与正向口径一致；正向跨 judge 子进程的消耗经增量标记与 sidecar 汇总后对账，见 `docs/architecture/current-architecture.md`。
- CLI 路径不绑定 `Job`，不统计、返回体不适用。
- 接口只暴露总量；DOCX 报告口径更细：反向 3 份单模型报告各写该模型自身的累计用量（同一 provider 的标注、裁判、共识、复查调用都计入该模型），多模型共识报告与正向报告写任务总量。分模型数据只在本服务内部累计，不进入 API。

`canceled` 表示运行中的任务被用户主动终止（`POST /api/v4/jobs/{job_id}/cancel`，见第 2 节）；它与 `abandoned` 语义不同：`abandoned` 指任务被中断 / 被终止后由用户放弃，是**终态**，`canceled` 指**仍在运行**的任务被终止，**不是终态** —— 被终止的任务可 `resume` 续跑（按参数快照重跑，续跑前复位取消标志）或 `abandon` 收尾。两者都不删除任何文件；终止是协作式的，`status` 会保持 `running` / `pending` 直至管线在检查点停止，接口不返回虚构的中间状态。

**任务执行模式**：由 `JOB_EXECUTOR` 决定，接口行为随之不同（详见第 18 节）。

- **Celery 模式（生产）**：任务落盘 manifest 后投递到 Redis 队列 `icd.reverse`，由独立 Worker 进程执行；并发数由部署参数 `ICD_WORKER_CONCURRENCY`（默认 20）决定，超出部分在 Redis 队列中等待（FIFO，不区分新任务/续跑）。排队中的任务 `status` 为 `pending`、`message` 为「任务已排队，等待 Worker 执行…」。Redis 队列持久化，**API 重启不影响排队任务**（仍是 `pending`，Worker 会执行）；Worker 崩溃/重启由心跳巡检收尾为 `interrupted`（或按控制意图 `canceled` / `abandoned`），可 `resume` 续跑。每个 API 实例都能读到同一份 manifest，多副本部署时状态一致。
- **inline 模式（pytest / 回滚）**：任务进入**进程内队列**（`backend/app/job_scheduler.py`），由固定 `MAX_CONCURRENT_JOBS`（默认 2，见 `backend/.env.example`）个常驻工作线程领取执行。新任务 FIFO；`resume` 续跑任务**插到队首**，插队只作用于等待队列、不抢占正在执行的任务。排队中的任务 `status` 为 `pending`、`message` 为「任务已排队，等待空闲执行槽位（并发上限 N）」；排队期间被终止 / 放弃的任务**不会被执行**（出队时跳过，原因写入任务日志）。队列本身不跨进程持久化；容器重启后启动扫描把 `pending` / `running` 一并标记为 `interrupted`（可 `resume` / `abandon`）。

对排队 / `pending` 任务的 `cancel` 在两种模式下都立即生效（直接落 `canceled`）；已开始执行的任务在检查点协作式停止。`abandon` 在任务已开始执行但尚未收尾时返回 409。

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
  "token_usage": { "total_tokens": 123456 },
  "errors": []
}
```

`token_usage` 口径同状态接口（累计总量；旧任务为 `null`；MOCK 的 0 为有效值）。

`outputs.*` 5 个布尔为 false 时表示对应 docx/xlsx 未生成（pipeline 中途失败、文件被 GC 等）。

降级场景下 `agreement_distribution` 可能出现 `single_source` / `no_consensus` 键（仅 1 个 / 0 个 provider 存活）；0 个存活时对应 case 强制 1★、`no_consensus`，`status_distribution` 计入 待确认。

`matched_count` / `pending_count` / `unmatched_count` 分别取自 `reverse_matches.json` 的 `hlr_已匹配` / `hlr_待确定` / `hlr_无匹配`。AMS 内部通道命中项（14.4）本身计入 `无匹配`，因此也计入 `unmatched_count` 与 `status_distribution["无匹配"]`；`stats.hlr_内部接口` 只是其中的子计数，不影响上述三个计数之和。

## 7. 下载输出接口

```text
GET /api/v4/jobs/{job_id}/outputs/eoicd-xlsx
GET /api/v4/jobs/{job_id}/outputs/consensus-docx
GET /api/v4/jobs/{job_id}/outputs/consistency/{model}
```

需登录；下载前先按任务归属校验（普通用户仅本人，管理员全部），越权或任务不存在统一 404，不得先返回文件存在性差异。`{model}` ∈ `{deepseek, minimax, qwen}`（白名单校验，非法 → 400）。

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
| `controller_profile` 不在白名单 | 422 | `controller_profile: unsupported '<name>'; allowed: ams, fgmc, hscu, rpdu, generic` |
| 使用 `hlr_schema` 但 `hlr_word_file` 非 .docx | 422 | `自定义表格样式仅支持 Word 文档` |
| 使用 `hlr_schema` 但未提供 `controller_profile` | 422 | `使用自定义样式时请选择系统类型` |
| `hlr_schema` 引用非法（JSON 损坏 / 未知 `template_id` 或 `schema_id` / 内置模板与系统类型不一致 / 配置校验失败） | 422 | 创建任务前 fail fast |
| 以 `builtin_rpdu` 作为自定义样式引用 | 422 | RPDU 使用内置解析，不支持自定义表格样式 |
| 使用 `generic` 系统但未提供 `hlr_schema` | 422 | `使用通用系统时请配置自定义表格样式` |
| `generic` 系统 + 追溯预筛文件 | 422 | `通用系统暂不支持追溯预筛，请关闭后重试` |
| `{model}` 不在 `{deepseek,minimax,qwen}` | 400 | `invalid model: <name>; allowed: ...` |
| 任务 `running` 时调 `/result` | 409 | `job not finished: status=...` |
| 任务 `failed` 时调 `/result` | 409 | |
| 任务不存在 | 404 | `job not found` |
| 未登录访问受保护接口 | 401 | 登录状态与账号启用状态由 fastapi-users 判定 |
| 重置密码 / 禁用后的旧会话 | 401 | Token 版本号不匹配，等同于未登录 |
| 普通用户访问管理员接口 | 403 | `/settings`、`/admin/users*`、历史删除、HLR 样式写入 |
| 已登录访问他人任务 / 无归属任务 | 404 | 与任务不存在同一响应，不暴露任务是否存在 |
| MongoDB 不可用 | 503 | `{"detail": "database unavailable"}` |

## 9. 输出路径布局

| 版本 | 根目录 | 内部结构 |
| --- | --- | --- |
| V4 | `backend/output/v4/{job_id}/` | 分层：`input/`（用户上传原始文件）+ `output/`（pipeline 产物） |
| 运行时设置 | `backend/output/settings.json` | 三个模型名与思考开关（见第 15 节）；容器重建后保留 |
| 自定义 HLR 表格样式 | `backend/output/hlr_schemas.json` | 全局共享的自定义样式（见第 16 节）；容器重建后保留 |

`docker-compose.yml` volume 映射 `./backend/output:/app/output`。

V4 `input/traceability/` 子目录用于 `enable_traceability_prefilter=true` 时追溯表落点。

## 10. JSON 中间产物（默认不暴露）

下列中间产物是 V4 内部数据，**默认不**作为下载 API 暴露，**仅**保留在 `backend/output/v4/{job_id}/output/` 内供服务端日志与后续 Issue 调试：

- `multi_judge_results.json`
- `consensus_results.json`
- `reverse_matches.json` —— **唯一例外**：按用户要求，以「新开子路由」方式开放了**只读查看**（`GET /api/v4/jobs/{job_id}/intermediates/reverse-matches`，浏览器内联渲染、非下载，见 14.4）
- `reverse_report.json`
- `eoicd_requirements.json`
- `hlr_requirements.json`
- `hlr_labels.json`
- `llm_cache.jsonl`（内容寻址 LLM 缓存，见第 13.2 节）

如前端需要看这些数据，**不**通过 `GET /api/v4/jobs/{id}/outputs/{name}`；应走 ADR-001 D7 预留的两条路径之一：`Accept: application/json` 内容协商，或新开独立子路由（`reverse_matches.json` 即按后者开放，见 14.4）。

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

需登录（未登录 → 401）。创建成功后任务归属写入当前用户，与反向任务同一套归属 / 访问规则。

请求字段（multipart）：

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `hlr_word_file` | UploadFile (.docx / .xlsx) | 是 | HLR 需求文件（V4.1 起支持 .xlsx 扁平需求清单；字段名沿用 `hlr_word_file`） |
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
  "token_usage": { "total_tokens": 123456 },
  "errors": []
}
```

`token_usage` 口径同状态接口（累计总量；旧任务为 `null`；MOCK 的 0 为有效值）。正向值由各 judge 的 sidecar 与父进程实时增量对账得出（取本轮较大者），再叠加续跑前的历史总量。

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
| `hlr_word_file` 缺失或非 .docx / .xlsx | 422 |
| pub/sub Excel 都没传 | 422 |
| 任意 Excel / 追溯表非 .xlsx | 422 |
| `controller_profile` 非空且不在项目表 | 422 |
| 任务 `running`/`failed` 时调 `/result` | 409 |
| 正向 xlsx/docx 未生成时下载 | 404 |

## 13. 任务中断恢复接口

任务元数据随每步进度持久化到 `backend/output/v4/{job_id}/job.json`（manifest）。输入文件与各阶段中间产物均已落盘，续跑无需重新上传。

进程启动时的扫描（`load_interrupted`）按执行模式区分：

- **inline 模式**：仍为 `pending` / `running` 的 manifest 必然来自上一个已退出的进程 → 标记为 `interrupted`（保留原 `updated_at` 作为「中断前的最后进度时间」）并载入内存。
- **Celery 模式**：`pending` 保持 `pending`（Redis 中的任务消息仍在，Worker 会执行）；`running` 且心跳新鲜（< 300 秒）保持 `running`；心跳过期则按 Redis 控制意图收尾为 `canceled` / `abandoned`，否则 `interrupted`；**Redis 不可达时不做任何判断**（不能把「暂时连不上控制面」当成「Worker 已死」）。

已为 `interrupted` 的 manifest 同样载入，因此反复重启不会丢失中断任务。

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
    "input_files": ["HLR.docx", "Publisher.xlsx"],
    "owner_username": "<username> | null"
  }
]
```

- `owner_username` 为任务归属账号，仅用于展示（管理员视角）；权限判断使用 manifest / Job 的 `owner_id`，该字段不参与鉴权。
- 需登录。普通用户只返回 `owner_id` 等于自己的任务；管理员返回全部任务（含无归属的 CLI / 旧任务）。
- 两个 query 参数均可选：`status` 按状态精确过滤，`task_type` 按任务类型过滤。
- 内存中只保留本次进程创建的任务与启动时载入的 `interrupted` 任务，因此该接口不会返回历史 `completed` / `failed` 任务。`canceled` 同样**不在**启动载入范围内：被终止的任务只在**本次进程存活期间**可续跑 / 可放弃，容器重启后即从列表中消失（与 `completed` / `failed` 同待遇）。**看历史任务请用 `GET /api/v4/history`（第 14 节），那是磁盘口径的接口。**
- `input_files` 取自 manifest 参数快照中的输入文件（仅文件名，不含路径）；CLI 等无 manifest 的任务为空数组。

### 13.2 继续任务

```text
POST /api/v4/jobs/{job_id}/resume
```

可继续的任务状态为 `interrupted`（进程中断）与 `canceled`（用户终止）；其余状态一律 409。续跑前会先**复位取消标志**（`cancel_requested` 与内部取消事件）—— 被终止的任务仍带着上次置位的取消标志，不复位则第一个检查点会立即再次取消。

Celery 模式下续跑额外校验 Worker 租约：控制面仍显示该任务被 Worker 占用且心跳新鲜（< 300 秒）→ **409**（任务可能还在跑，不允许重复启动）；租约已过期/不存在 → 清理旧租约后以新 `run_token` 重新投递。控制面不可达 → **503**。旧队列消息因 `run_token` 不匹配会被 Worker 丢弃，续跑不会被执行两次。正向任务续跑在 Celery 模式下 → **503**（第 18 节）。

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

只把状态标记为 `abandoned`，**不删除任何输入或输出文件**（磁盘清理不在本期范围）。可放弃的任务为 `interrupted` 与 `canceled`（与 `resume` 同一门槛），**外加尚未被 Worker 领取的 `pending`**。放弃是这套状态机里的终态：被终止的任务需要一个收尾方式；排队中的任务被 Worker 取到时会校验 `abandon` 标志并跳过执行（见第 18 节），一步放弃即可，不必先 `cancel` 再 `abandon` 两步走。门槛统一为「**没有任何执行单元在跑**」——正在运行的 `running` 任务只能先 `cancel`。

Celery 模式下：`pending` 且未被领取 → 标记控制面 `abandon` 并直接落 `abandoned`；已领取且心跳新鲜 → **409**；控制面不可达 → **503**。

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
| `resume` 时 Worker 租约仍新鲜（任务可能还在执行） | 409 |
| Celery 模式下控制面 / 队列不可达（`resume` / `abandon` / `cancel` / 强制终止） | 503 |
| Celery 模式下创建或续跑正向任务（功能未接入 Worker） | 503 |

### 13.5 manifest 字段说明

`output/v4/{job_id}/job.json` 由任务自身在每次 `update()` 时原子写入（`.tmp` + `os.replace`），字段：

```json
{
  "schema_version": 1,
  "job_id": "<uuid>",
  "task_type": "correctness | completeness",
  "owner_id": "<user _id> | null",
  "owner_username": "<username> | null",
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

`owner_id` / `owner_username` 为任务归属字段：`owner_id` 是权限判断键，`owner_username` 仅用于展示。创建任务时写入当前登录用户；续跑沿用原值，不允许修改。旧 manifest（升级前创建）与 CLI 任务没有这两键，按 `null`（未归属）加载——只有管理员可见、可下载、可删除，不做批量迁移。

`params` 中的路径一律为相对 `job_dir` 的相对路径，保证输出目录整体搬迁后仍可恢复。不持久化任务结果（重跑时重新生成）。无 `job_dir` 的任务（如 CLI 直跑 `app/v4/cli.py`）不写 manifest，行为与本次改动前一致。

**部署前提（Docker）**：本能力依赖 `output/` 跨进程存活。`docker-compose.yml` 已把 `./backend/output` 以 bind mount 挂到容器内 `/app/output`（且未设置 `OUTPUT_DIR`），因此 `docker compose restart` / `down` + `up` 后 manifest 仍在、中断任务不丢。若把该卷去掉、改为匿名 volume 或设置 `OUTPUT_DIR` 指向容器内非挂载路径，本能力会静默失效（重启后任务列表为空）。

## 14. 历史结果接口（列表 / 删除 / 日志下载 / 匹配中间产物查看）

第 13 节的 `/jobs` 是**内存口径**（本进程新建 + 启动载入的中断任务），跑完的任务重启后即从内存消失；历史结果走**磁盘口径**：直接扫 `backend/output/v4/*/`，因此重启后仍能看到并下载以前的结果。前端入口是顶栏「历史结果」页。

同一口径也覆盖**运行日志**（14.3）：任务失败 / 中断 / 被终止后，用户最需要的是出错原因与执行过程，而那时任务往往已经不在内存里 —— 日志下载因此不查内存登记表，直接读任务目录下的 `job.log`。

同一口径还提供**反向匹配中间产物查看**（14.4）：`reverse_matches.json` 是 Step 3 的匹配明细，排查「某条 SWHLR 为什么没匹配上」时最直接的一手数据 —— 它只落盘、进程重启后照样在，因此查看接口同样直接读任务目录。

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
    "owner_username": "<username> | null",
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
    "has_log": true,
    "has_reverse_matches": true,
    "mock": false,
    "token_usage": { "total_tokens": 123456 }
  }
]
```

- **每个子目录都会列出**，不只有跑完的：`status` 取 manifest 原值；**没有可读 manifest** 的残留目录（上传失败留下、或 manifest 损坏）以 `status="unknown"`、`task_type=""`、时间取目录 mtime 出现 —— 这类目录不出现在任何其它列表里，却照样占磁盘，只能在这里看到并清理。
- `outputs` 是七类对外产物的存在性布尔（反向 5 类 + 正向 2 类），与第 7 节的下载路径一一对应；全为 `false` 即「无可下载产物」（未跑完或生成失败）。
- `has_log` 表示目录里有没有运行日志（`job.log`），与 `outputs` **并列而非其中一项**：日志不是对外产物，而是任务跑过就有的过程记录。失败 / 中断的任务常常 `outputs` 全为 `false` 却 `has_log=true` —— 这时日志是它唯一能给出的东西，前端据此单独渲染「运行日志」下载链接。
- `has_reverse_matches` 表示目录里有没有反向匹配中间产物（`output/reverse_matches.json`，Step 3 输出），与 `has_log` 同理**不属于** `outputs` 七类词汇表。只有反向任务会写这个文件，正向任务恒为 `false`；前端据此在该条目上渲染「匹配中间产物」查看链接（14.4）。
- `size_bytes` 为该任务目录占用的字节数（**含 `input/` 里用户上传的原始文件**与全部中间产物），供删除前告知释放空间。
- `mock` 含义同状态接口：模拟数据不可用于验收。
- `token_usage` 读 manifest 的 `token_usage` 字段，口径同状态接口：累计总量；**旧任务无该键 → `null`**（前端不显示），新任务的 `0`（如 MOCK）照常显示。
- `owner_username` 为任务归属账号（仅管理员视角展示）；权限判断使用 manifest 的 `owner_id`，该字段不参与鉴权。
- 需登录。普通用户只返回 `owner_id` 等于自己的目录；管理员返回全部目录。未归属目录（旧版本遗留 / manifest 损坏 / 无 manifest 的残留目录）只对管理员可见。该接口只读磁盘，不改变任何状态。

### 14.2 删除历史结果（硬删除）

```text
POST /api/v4/history/delete
Content-Type: application/json

{ "job_ids": ["<uuid>", "..."], "confirm": true }
```

仅管理员可调用（普通用户 → **403**）；管理员可删除任意账号的历史任务。删除粒度是**整个任务目录** `output/v4/{job_id}/`（`shutil.rmtree`），**包括 `input/` 里用户上传的原始文件**与全部中间产物。不进回收站、没有撤销，因此设了三道关卡：

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

### 14.3 下载运行日志

```text
GET /api/v4/jobs/{job_id}/logs/download
```

返回任务输出目录下的 `job.log`（`output/v4/{job_id}/job.log`）：该任务的 stdout/stderr 全集，由 Tee **逐行同步落盘**（封顶 5 MB），包含失败堆栈与执行过程；任务中途被 OOM 杀掉也已写到最后一行为止。需登录；下载前按任务归属校验（普通用户仅本人，管理员全部），越权或任务不存在统一 404。

- `Content-Type: text/plain`；下载保存名 `job_{job_id 前 8 位}_{YYYYMMDD_HHMM}.log`（时刻取日志最后写入时刻、东八区，与第 7 节产物同规则：重复下载文件名稳定，任务号前缀让同批任务的日志区分得开）。
- 与第 2 节概览里的 `/jobs/{job_id}/logs`（JSON 增量拉取，**只认内存里的任务**，进程重启后 404）互补：本接口走磁盘口径，不查内存登记表，因此任务失败 / 中断 / 被终止 / 进程重启之后照样下得动 —— 这些正是最需要日志的场景。
- 前端入口：历史结果页里每个 `has_log=true` 的条目都带「运行日志」下载链接（无产物的失败任务同样有）。

| 场景 | HTTP |
| --- | --- |
| 日志文件不存在（任务从未真正开始、目录已被删） | 404 |
| `job_id` 非法（非 uuid / 越出 `output/v4/`） | 400 |

### 14.4 查看反向匹配中间产物

```text
GET /api/v4/jobs/{job_id}/intermediates/reverse-matches
```

返回任务输出目录下的 `reverse_matches.json`（`output/v4/{job_id}/output/reverse_matches.json`）：Step 3 匹配阶段的原始明细（每条 SWHLR 的 `match_type`、命中的 `matched_profile_keys`、匹配证据与全局统计），排查「某条 SWHLR 为什么没匹配上 / 匹配到了什么」时最直接的一手数据。需登录；查看前按任务归属校验（普通用户仅本人，管理员全部），越权或任务不存在统一 404。

- **浏览器内查看**：`Content-Type: application/json` 且**不发** `Content-Disposition`（未带 `filename`），点击后在新标签页按 JSON 渲染，不落盘；需要留档时在页内另存即可。响应体即磁盘文件原文，不重新序列化（与同事参考文件 diff 时逐字节可比）。
- 与第 7 节 `/outputs/*` 的分工：那是七类对外产物的**下载**；本接口是 JSON 中间产物的**只读查看**。按 ADR-001 D7 的口径，JSON 中间产物默认不暴露，`reverse_matches.json` 是按 D7 预留的「新开子路由」方式开放的**唯一例外**（第 10 节），其余 6 类仍不暴露。
- RPDU refine（Step 3.5，仅 RPDU profile）会**覆盖写盘**该文件：查看的是过滤 + 补采后的最终态。
- AMS 内部通道命中（profile 开启 `matcher.enable_internal_channel_detection`）：该条目 `match_type=无匹配`、`overall=unmatched`、`match_evidence.internal_channel=true`，summary 以 `[内部接口]` 开头；计入 `stats.hlr_无匹配`（同一条目也计入子计数 `stats.hlr_内部接口`），不进入 AI 裁判，也不参与追溯兜底重试。
- 前端入口：历史结果页里每个 `has_reverse_matches=true` 的条目渲染「匹配中间产物」查看链接（新标签页打开，`target="_blank"`、无 `download` 属性）。

| 场景 | HTTP |
| --- | --- |
| 文件不存在（正向任务、Step 3 未跑完、目录已被删） | 404 |
| `job_id` 非法（非 uuid / 越出 `output/v4/`） | 400 |

### 14.5 历史结果错误响应（列表 / 删除）

| 场景 | HTTP |
| --- | --- |
| 未登录 | 401 |
| 普通用户调用删除（仅管理员） | 403 |
| `job_ids` 为空，或缺 `confirm=true` | 400 |
| `job_id` 非法（非 uuid / 越出 `output/v4/`） | 400 |
| 批次内有任务正在运行 | 409 |

## 15. 运行时模型设置接口

用于在服务器部署完成后，直接从前端修改三个模型名与思考模式开关，避免为改配置重新部署/重建容器。**仅管理员可读写**（普通用户 → 403）；模型设置是全局配置，不区分账号。

### 15.1 查询设置

```text
GET /api/v4/settings
```

预期返回：

```json
{
  "deepseek_model": "deepseek-v4-flash",
  "minimax_model": "MiniMax-M2.5",
  "qwen_model": "qwen3.6-35b-a3b",
  "deepseek_thinking": true,
  "minimax_thinking": true,
  "qwen_thinking": true,
  "customized": false,
  "updated_at": null
}
```

- `customized=false`：模型名来自部署配置（`backend/.env`）或代码默认值；思考开关来自 `DEEPSEEK_THINKING` / `MINIMAX_THINKING` / `QWEN_THINKING` 或默认开启；`updated_at=null`。
- `customized=true`：来自服务器输出目录下的 `settings.json`；`updated_at` 为上次保存时间（ISO 8601，东八区）。
- 思考开关同时作用于**反向管线（正确性分析）**与**正向管线（完整性分析）**的模型调用。正向侧按 judge 的 provider 生效：`forward/config_builder` 为每个 judge 写入 `provider`，`multi_judge_runner` 再以 `AI_PROVIDER` 传给 judge 子进程，5 个 LLM 请求构造点据此追加字段（见 15.2 思考模式协议）。

### 15.2 更新设置

```text
PUT /api/v4/settings
Content-Type: application/json
```

请求体（三个模型名全量提交；三个思考开关可省略）：

```json
{
  "deepseek_model": "deepseek-v4-flash",
  "minimax_model": "MiniMax-M2.5",
  "qwen_model": "qwen3.6-35b-a3b",
  "deepseek_thinking": true,
  "minimax_thinking": true,
  "qwen_thinking": true
}
```

- 先去首尾空格再校验；空、超过 200 字符或含换行/控制字符 → 422。
- 思考开关省略或传 `null` → **保留当前生效值**，兼容旧前端 / 旧脚本只提交三个模型名的调用方式。
- 原子写入 `OUTPUT_DIR/settings.json`（Docker 中即挂载的 `./backend/output/settings.json`），并立即更新进程环境变量。
- 反向管线（`v4/llm/factory.get_llm()`）每次调用、正向管线（`forward/config_builder`）每个任务开始时都读取模型名与开关，因此**无需重启**；修改只影响之后的模型调用。反向的 client 每次调用都读环境变量；正向 judge 子进程在任务启动时继承环境快照，因此**只对之后新建的正向任务生效**，正在运行的正向任务不受影响。
- 持久化值**优先于** `backend/.env`；若要让 `.env` 重新生效，删除 `output/settings.json` 后重启服务。
- 只管理模型名与思考开关。API key / base_url / `JUDGE_PROVIDERS` 仍由部署配置管理；`USE_MOCK_LLM=1` 时模型名与开关不参与实际调用。

#### 思考模式协议

| provider | 开启（默认） | 关闭 |
| --- | --- | --- |
| DeepSeek | 发送 `thinking: {"type": "enabled"}` + `reasoning_effort: "low"` | 发送 `thinking: {"type": "disabled"}`，不发送 `reasoning_effort` |
| Qwen | 保持历史请求体，不发送 `enable_thinking`（服务端默认开启） | 发送 `enable_thinking: false` |
| MiniMax | 保持历史请求体，不发送 `enable_thinking` | 按 Qwen 兼容协议发送 `enable_thinking: false` |

- 不向前端暴露 effort 档位；Qwen / MiniMax 不发送 `reasoning_effort`（保持历史行为）。
- 正向管线使用同一套 provider 字段，但只在**关闭**时追加；开启 / 未知 provider / mock 保持历史请求体不变（因此正向「开启」不等于反向 DeepSeek 的显式 `thinking: enabled` + `reasoning_effort: low`，服务端默认行为仍为开启）。
- MiniMax 槽位仍保留 `minimax` 的模型名、token 记账与报告标签。关闭时按 Qwen 兼容协议发送 `enable_thinking: false`，是否生效取决于 `MINIMAX_BASE_URL` 指向的接口：指向 Qwen 兼容接口时生效，旧版 MiniMax OpenAI 兼容接口可能忽略该参数。
- 缓存 key：开启路径沿用历史 key（升级后默认配置仍命中旧缓存）；关闭路径追加 provider 级 `_thinking=disabled` 变体，不复用开启状态下的判定结果。

### 15.3 兼容与错误响应

- 旧 `settings.json` 缺少三个思考开关字段时，按环境变量 / 默认开启处理；下次保存后写入完整字段。
- 设置文件中的单个开关值非法时，该字段回落环境变量 / 默认开启并打印告警，不影响三个模型名的加载。

| 场景 | HTTP |
| --- | --- |
| 未登录 | 401 |
| 普通用户访问（仅管理员） | 403 |
| 模型名为空 / 超长 / 含控制字符 / 类型不对 | 422 |
| 思考开关不是布尔值 | 422 |
| 设置文件无法写入（权限、磁盘等） | 500 |

## 16. HLR 表格样式管理接口

自定义 HLR 表格样式用于适配不同控制器 / 不同文档中 SWHLR Word 需求表的行标签写法与需求表位置。样式只影响 Step 1 的 HLR 解析（行标签映射与需求表识别），不改变后续步骤的输入契约（下游只消费 `requirement_id` / `content` / 可选 `rationale`）。

- **内置模板**：`builtin_ams` / `builtin_fgmc` / `builtin_hscu` / `builtin_rpdu`，由现有控制器 profile 派生，**只读**（`source=builtin`、`deletable=false`，修改 / 删除 → 403）；`usable_for_custom_style=false` 仅出现在 `builtin_rpdu`（RPDU 使用内置解析路径，不支持自定义表格样式）。
- **自定义样式**：服务端持久化在 `OUTPUT_DIR/hlr_schemas.json`（Docker 中即挂载的 `./backend/output/hlr_schemas.json`），**全局共享**（所有登录用户可见，不按账号隔离）。权限：登录用户可查看、扫描、试解析；新建 / 更新 / 删除仅管理员（普通用户 → 403）。
- **样式配置结构**（新建 / 更新请求中的 `config`，以及列表响应中的 `config`）：

```json
{
  "version": 1,
  "mode": "docx_vertical",
  "fields": {
    "id": ["需求编号", "SWHLR ID"],
    "content": ["需求正文", "需求中文"],
    "rationale": ["基本原理"]
  },
  "identify": { "id_pattern": "^FSF" }
}
```

配置约束（校验失败 → 422）：

- `version` 必须为 `1`；`mode` 必须为 `docx_vertical`（当前唯一支持的文档形态：纵向「行标签 / 值」表格）；
- `fields` 必须为非空对象，键只能取以下 15 个标准字段名：`id`、`content`、`rationale`、`object_type`、`is_derived`、`is_safety_related`、`verification_method`、`implementation`、`code`、`source`、`covered_ids`、`notes`、`input_data`、`output_data`、`is_requirement`；值为行标签别名列表；
- `id` 与 `content` 必须存在且至少包含一个非空标签；其余字段可为空列表；
- 单个标签去首尾空格后 1–100 字符；每个字段最多 20 个别名（同字段内忽略大小写去重）；同一标签（忽略大小写）只能归属一个字段；
- `is_requirement`（是否为需求）为可选过滤字段：映射后启用非需求过滤，该行值为「否」的表格不进入需求列表；未映射时即使所选系统原生带过滤（如 FGMC）也不过滤；
- `identify.id_pattern` 可选（默认空串），须为合法正则、≤200 字符；作用于解析出的需求 ID。

### 16.1 查询样式列表

```text
GET /api/v4/hlr-schemas
```

需登录（任意登录用户可查看）。预期返回（`V4HLRSchemaListResponse`）：内置模板在前（`builtin_ams` → `builtin_fgmc` → `builtin_hscu` → `builtin_rpdu`），自定义样式按创建顺序在后。

```json
{
  "schemas": [
    {
      "id": "builtin_ams",
      "name": "空气管理系统控制器 (AMS)",
      "source": "builtin",
      "deletable": false,
      "usable_for_custom_style": true,
      "config": { "version": 1, "mode": "docx_vertical", "fields": { "...": ["..."] }, "identify": { "id_pattern": "..." } },
      "created_at": null,
      "updated_at": null
    },
    {
      "id": "sch_3f2a9c1d",
      "name": "某型号需求表",
      "source": "custom",
      "deletable": true,
      "usable_for_custom_style": true,
      "config": { "...": "..." },
      "created_at": "2026-11-06T10:00:00+08:00",
      "updated_at": "2026-11-06T10:00:00+08:00"
    }
  ]
}
```

内置模板的 `created_at` / `updated_at` 恒为 `null`。

### 16.2 新建自定义样式

```text
POST /api/v4/hlr-schemas
Content-Type: application/json

{ "name": "某型号需求表", "config": { "version": 1, "mode": "docx_vertical", "fields": {...}, "identify": {...} } }
```

仅管理员可调用（普通用户 → 403）。

- 成功 → **201** + 样式项（结构同 16.1 的列表项，`source=custom`、`deletable=true`）。
- 名称去首尾空格后 1–50 字符、不含控制字符；与已有自定义样式或内置模板显示名重名（忽略大小写）→ **409**。
- `config` 校验失败 → **422**。

### 16.3 更新自定义样式

```text
PUT /api/v4/hlr-schemas/{schema_id}
Content-Type: application/json

{ "name": "...", "config": {...} }
```

仅管理员可调用（普通用户 → 403）。

- 全量替换名称与配置；成功 → **200** + 样式项。
- 内置模板（`builtin_*`）→ **403**；未知 `schema_id` → **404**；名称冲突 → **409**；`config` 校验失败 → **422**。

### 16.4 删除自定义样式

```text
DELETE /api/v4/hlr-schemas/{schema_id}
```

仅管理员可调用（普通用户 → 403）。成功 → `{"deleted": true}`。内置模板 → **403**；未知 `schema_id` → **404**。

### 16.5 扫描文档表格

```text
POST /api/v4/hlr-schemas/scan
Content-Type: multipart/form-data
```

需登录（任意登录用户可调用）。请求字段：`file`（UploadFile，仅 `.docx`，非 `.docx` → 422）。本地解析，**不调用 LLM**。

预期返回（`V4HLRSchemaScanResponse`）：

```json
{
  "tables": [
    {
      "table_index": 0,
      "row_count": 8,
      "column_count": 2,
      "looks_like_requirement": true,
      "rows": [
        { "label": "需求编号", "value_preview": "FSF21000101_HLR_001" },
        { "label": "需求正文", "value_preview": "当……时，应……" }
      ]
    }
  ]
}
```

- 最多返回前 30 张表、每表前 30 行；`label` 与 `value_preview` 均截断到 200 字符。
- `looks_like_requirement` 判定：至少 2 列 2 行，且第 0 列任一行标签命中内置模板的 `id` 别名（忽略大小写）或包含「需求」。
- 上传文件只写临时目录，请求结束自动清理，不落 `OUTPUT_DIR`。

### 16.6 试解析

```text
POST /api/v4/hlr-schemas/preview
Content-Type: multipart/form-data
```

请求字段：

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `file` | UploadFile (.docx) | 是 | 待试解析的 SWHLR Word 文档 |
| `schema` | str (form, JSON) | 是 | 样式引用，三种形式同第 4 节 `hlr_schema`（完整配置 / `template_id` / `schema_id`） |
| `controller_profile` | str (form) | 是 | 系统类型 ∈ `{ams, fgmc, hscu, rpdu, generic}` |

需登录（任意登录用户可调用）。本地解析，**不调用 LLM**。预期返回（`V4HLRSchemaPreviewResponse`）：

```json
{
  "total_count": 12,
  "requirements": [
    {
      "requirement_id": "FSF21000101_HLR_001",
      "content_preview": "当……时，应……",
      "rationale_preview": "……",
      "extra_fields": { "verification_method": "测试" }
    }
  ],
  "warnings": [{ "kind": "duplicate_id", "message": "存在重复的需求ID：..." }],
  "parse_warnings": [],
  "hook_applied": true
}
```

- `requirements` 最多 5 条；`content_preview` / `rationale_preview` 截断到 200 字符；`extra_fields` 为 `requirement_id` / `content` / `rationale` / `source_file` 之外的非空字段（`implementation_method` 以 `implementation` 键返回）。
- `warnings` 汇总重复 ID、空正文与解析器登记的解析告警；`parse_warnings` 保留解析器原始登记项。
- `hook_applied` 表示试解析过程中该控制器 profile 的 HLR 预处理 hook 是否实际改写了需求内容。

### 16.7 错误响应

| 场景 | HTTP |
| --- | --- |
| 未登录 | 401 |
| 普通用户新建 / 更新 / 删除样式（仅管理员） | 403 |
| 新建 / 更新时名称非法（空 / 超 50 字符 / 含控制字符、不可见格式字符或孤立代理项）或 `config` 校验失败 | 422 |
| `scan` / `preview` 上传文件非 `.docx` | 422 |
| `scan` / `preview` 上传文件超过 50 MB（分块读取，超限即拒绝） | 422（`文件过大`） |
| `scan` / `preview` 上传的 Word 无法解析（损坏 zip / python-docx / lxml 错误） | 422（`无法解析该 Word 文档`） |
| `preview` 的 `controller_profile` 不在白名单，或内置模板与所选控制器不一致 | 422 |
| 名称与已有自定义样式或内置模板显示名重复（忽略大小写） | 409 |
| 修改 / 删除内置模板 | 403 |
| `{schema_id}` 不存在（PUT / DELETE / `preview` 的 `schema_id` 引用） | 404 |
| 内置控制器配置加载失败（部署包缺失或损坏 profile 目录）：GET 列表与 `preview` 映射为 422；`POST` / `PUT` 在名称唯一性检查中也会间接加载内置模板，部署包损坏时为 500 | 422 / 500 |

## 17. 认证、账号管理与权限（阶段 A）

登录与账号隔离的设计背景与决策见 ADR-007；本节是接口权限矩阵、认证接口与错误语义的唯一事实源。默认情况下，除 `GET /api/v4/health` 与 `POST /api/v4/auth/login` 外，所有接口都要求登录。

### 17.1 权限矩阵

| 资源 / 接口 | 未登录 | 普通用户 | 管理员 |
| --- | --- | --- | --- |
| `GET /api/v4/health` | 允许 | 允许 | 允许 |
| `POST /api/v4/auth/login` | 允许 | 允许 | 允许 |
| `POST /api/v4/auth/logout`、`GET /api/v4/auth/me` | 拒绝（401） | 本人 | 本人 |
| `POST /api/v4/coverage-analysis`、`POST /api/v4/completeness-analysis` | 拒绝（401） | 允许 | 允许 |
| `GET /api/v4/jobs` | 拒绝（401） | 仅本人 | 全部 |
| `GET /api/v4/jobs/{job_id}`、`/result`、`/logs`、`/logs/download` | 拒绝（401） | 仅本人 | 全部 |
| `POST /api/v4/jobs/{job_id}/resume`、`/abandon`、`/cancel`、`/force-cancel` | 拒绝（401） | 仅本人 | 全部 |
| `/api/v4/jobs/{job_id}/outputs/*`、`/intermediates/reverse-matches` | 拒绝（401） | 仅本人 | 全部 |
| `GET /api/v4/history` | 拒绝（401） | 仅本人 | 全部 |
| `POST /api/v4/history/delete` | 拒绝（401） | 拒绝（403） | 允许 |
| `GET /api/v4/settings`、`PUT /api/v4/settings` | 拒绝（401） | 拒绝（403） | 允许 |
| `GET /api/v4/hlr-schemas`、`POST /api/v4/hlr-schemas/scan`、`POST /api/v4/hlr-schemas/preview` | 拒绝（401） | 允许 | 允许 |
| `POST /api/v4/hlr-schemas`、`PUT/DELETE /api/v4/hlr-schemas/{schema_id}` | 拒绝（401） | 拒绝（403） | 允许 |
| `/api/v4/admin/users*` | 拒绝（401） | 拒绝（403） | 允许 |

约定：

- **任务归属**：`owner_id`（`str(User._id)`）是权限判断键；`owner_username` 仅用于展示。任务不存在、无归属或归属不是当前用户时统一返回 **404**，不区分“不存在”与“无权访问”。
- **未归属任务**：升级前已存在的任务目录、CLI 任务与 manifest 损坏的目录只对管理员可见、可下载、可删除。
- **管理员**：可访问全部账号的任务与历史，包括无归属任务。
- **MongoDB 不可用**：受保护接口返回 **503** `{"detail": "database unavailable"}`；`GET /health` 同样返回 503。

### 17.2 登录 / 登出 / 当前用户

```text
POST /api/v4/auth/login
Content-Type: application/x-www-form-urlencoded

username=<用户名>&password=<密码>
```

- 成功 → **204**，响应 `Set-Cookie` 下发登录 Cookie；
- 用户名或密码错误、账号被禁用 → **400**（fastapi-users 的登录失败响应，不区分具体原因）；
- MongoDB 不可用 → **503** `{"detail": "database unavailable"}`；
- 本接口不要求登录。

Cookie 参数：

| 参数 | 值 |
| --- | --- |
| 名称 | `AUTH_COOKIE_NAME`，默认 `icd_auth` |
| `HttpOnly` | 是 |
| `SameSite` | `Lax` |
| `Path` | `/` |
| 有效期 | `AUTH_TOKEN_TTL_SECONDS`，默认 28800 秒（8 小时） |
| `Secure` | `AUTH_COOKIE_SECURE`，内网 HTTP 默认关闭；启用 TLS 后设为 `1` |

`SameSite=Lax` + 同源部署下，跨站 POST 不携带 Cookie；本阶段不额外实现 CSRF Token。

```text
POST /api/v4/auth/logout
```

需登录；未登录 → **401**。清除登录 Cookie，成功 → **204**。

```text
GET /api/v4/auth/me
```

需登录；未登录 → **401**。预期返回：

```json
{ "id": "<user _id>", "username": "zhangsan", "is_admin": false }
```

- `username` 对应存储中的小写用户名；接口不暴露 `email`、密码哈希等内部字段；
- 登录标识只支持用户名（大小写不敏感），不支持邮箱登录；
- 账号被禁用后，下一次请求即被拒绝（401），不依赖 JWT 过期；
- 本阶段不提供刷新 Token；JWT 过期后需重新登录；
- 管理员重置密码或禁用账号后，该账号所有已签发的 Cookie 在下一次请求时立即 401（Token 版本号校验），无需等待 8 小时过期；
- 重新启用账号不会恢复旧 Cookie，用户必须重新登录。

### 17.3 管理员账号管理

以下接口仅管理员可用；普通用户 → **403**，未登录 → **401**。

```text
GET /api/v4/admin/users
```

按 `created_at` 升序返回全部账号（数组）。列表项字段：

```json
{
  "id": "<user _id>",
  "username": "zhangsan",
  "is_active": true,
  "is_superuser": false,
  "created_at": "ISO-8601"
}
```

```text
POST /api/v4/admin/users
Content-Type: application/json

{ "username": "zhangsan", "password": "<至少 8 位>", "is_admin": false }
```

- 成功 → **201** + 账号项（结构同上）；
- **创建请求使用 `is_admin`**（不是 `is_superuser`）；`username` 1–64 字符且不含空白，密码 8–128 字符；
- 用户名重复（忽略大小写）→ **409**；
- 用户名统一转小写存储；创建后不可修改。

```text
PATCH /api/v4/admin/users/{user_id}
Content-Type: application/json

{ "password": "<新密码，可选>", "is_active": false, "is_superuser": true }
```

- 三个字段均可选，只提交需要变更的字段；成功 → **200** + 账号项；
- **更新请求使用 `is_superuser`**（与创建请求的 `is_admin` 不同，注意区分）；
- 重置密码不需要旧密码；
- 重置密码、或 `is_active` 由 true 改为 false 时，该账号所有已登录会话立即失效（下一次请求 401）；重新启用后旧会话仍无效，必须重新登录；
- 用户不存在或 `user_id` 非法 → **404**；
- 禁用 / 降级最后一个活跃管理员 → **409**（系统必须始终保留至少一个活跃管理员）；
- 本阶段**不提供删除账号接口**，只支持禁用；禁用后保留其历史任务归属。

初始管理员：

- 首次启动且用户集合为空时，从 `ICD_ADMIN_USERNAME` / `ICD_ADMIN_PASSWORD` 创建初始管理员；
- 未配置这两个变量且用户集合为空 → 后端启动失败并打印明确日志；不存在默认密码；
- 用户集合非空时忽略初始化变量，不会重置已有账号；
- 唯一管理员忘记密码时，按部署说明中的 `mongosh` 命令手工恢复。

### 17.4 文档端点与静态兜底

- `ICD_DOCS_ENABLED` 控制 `/docs`、`/redoc`、`/openapi.json`：开发默认开启；取值 `0` / `false` / `no` 时关闭。服务器编排设置 `ICD_DOCS_ENABLED=0`。
- `/api/*` 未匹配路由返回 JSON 404，不回落为前端 `index.html`。
- SPA 静态兜底只返回解析后位于静态目录内的文件：请求路径先 URL 解码，含 `..` 段或解析后越界时返回 404，不回落 `index.html`（不做路径纠正）；未登录者不能通过 `/../output/v4/<job_id>/...`（含 `%2e%2e` 编码形式）读取任务产物、`job.json` 或 `.env`。
- 静态目录本身可公开（登录页需要），但不得暴露静态目录以外的任何文件。

## 18. 任务执行层语义（Celery 模式）

本节只描述 `JOB_EXECUTOR=celery`（生产部署）相对 inline 模式的差异；接口路径、请求字段、响应结构均不变。设计背景见 `docs/decisions/ADR-008-Celery-Redis任务执行层.md`。

### 18.1 执行模型

- API 进程（FastAPI）只做生产者：保存上传文件 → 写 manifest（`pending`）→ 投递 Redis 队列 `icd.reverse` → 返回。管线在独立 Celery Worker 进程中执行。
- Worker 默认并发 20（`ICD_WORKER_CONCURRENCY`），prefork 子进程，一子进程同一时刻只执行一个任务；`worker_max_tasks_per_child=1`，任务结束后子进程退出重建。
- `job.json` 是唯一权威状态；API 每次查询回读 manifest 并叠加 Redis 控制面快照。因此任务状态与「请求打到哪个 API 实例」无关。

### 18.2 状态与跨进程同步

- `GET /jobs/{id}` / `GET /jobs`：Celery 模式下从 manifest 刷新后再返回；`pending`、`running`、`completed` 等状态语义与 inline 模式一致。
- 日志：Worker 写 `job.log`；`GET /jobs/{id}/logs` 在返回前按字节偏移把新行同步进 API 进程 buffer，只消费完整行。`/logs/download` 直接读磁盘，行为不变。
- token 用量、结果摘要（`/result`）、输出下载均从 manifest 与任务目录读取，语义不变。

### 18.3 控制操作

| 操作 | Celery 模式行为 | 错误 |
| --- | --- | --- |
| `cancel`（任务未被执行） | 写控制面 `cancel` 并把 manifest 置 `canceled`；Worker 取到后跳过 | 控制面不可达 → 503 |
| `cancel`（任务已执行） | 只写控制面 `cancel`；Worker 在检查点停止并落 `canceled`；API 立即返回 200 且 `status` 保持 `running`、`cancel_requested=true` | 控制面不可达 → 503 |
| 强制终止（`force=true`） | 写控制面 `force` + 向 Worker 发 revoke（SIGKILL）；manifest 置 `canceled`；Worker 收到强制标志后最多 10 秒宽限即退出 | 控制面不可达 → 503 |
| `abandon` | 未被领取的 `pending`：写 `abandon` 并置 `abandoned`；已领取且心跳新鲜 → 409 | 控制面不可达 → 503 |
| `resume` | 租约新鲜 → 409；否则清理旧租约、生成新 `run_token` 重新投递 | 队列/控制面不可达 → 503 |
| 创建 / 续跑正向任务 | 不投递，直接拒绝 | 503（`当前部署未启用正向完整性分析`） |

取消 / 强制终止 / 放弃是**协作式**的：接口不等待 Worker 实际停止；最终状态以 Worker 或巡检线程写入的 manifest 为准。

### 18.4 故障恢复

- **API 重启**：排队任务保持 `pending`；运行中任务按心跳判断（见第 13 节）。
- **Worker 崩溃 / 被杀**：心跳停止；最长 300 秒后由 API 侧巡检线程（每 60 秒一次）按控制意图收尾为 `canceled` / `abandoned`，否则 `interrupted`；之后可 `resume`。
- **Redis 不可达**：控制操作返回 503；启动扫描与巡检不做任何状态判断，避免把暂时故障误判成任务失败。任务本身在 Worker 中继续执行（Worker 与 Redis 断连时 Celery 会重连）。
- **超长任务**：Redis `visibility_timeout` 设为 30 天，覆盖 24 小时+ 任务，避免消息被重复投递。

### 18.5 正向管线

Celery 模式下正向完整性分析未接入 Worker：`POST /completeness-analysis` 与正向任务的 `resume` 返回 503，前端入口保留可见。

`FORWARD_PIPELINE_ENABLED=1` 可显式放开该拦截，但**当前部署不要设置**：放开后正向任务仍走 API 进程内的 `job_scheduler` 线程池（不经 Redis / Worker），会占用 API 进程资源，与「20 并发长任务在 Worker 执行」的部署假设冲突。inline 模式下未配置该变量时正向默认可用，与历史行为一致。
