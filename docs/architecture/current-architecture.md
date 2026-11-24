# 当前架构说明

本文档用于说明 **ICD工具原型** 的当前软件架构、模块职责和目录边界。

## 1. 架构定位

ICD工具原型采用前后端分离架构。

前端负责用户交互、文件上传、任务状态展示和结果下载。
后端负责文件解析、HLR 标注、反向匹配、多智能体裁判、共识复核和报告文档生成。

当前版本定位为本地演示原型，优先保证端到端流程可运行、模块边界清晰、后续能力便于扩展。

## 2. 总体架构

总体架构如下：

```text
用户浏览器
    ↓
React + TypeScript 前端（登录页 + 角色守卫 + 审计中心）
    ↓
FastAPI 后端（/api/v4）—— 登录鉴权 + 审计 + 任务编排/生产者：上传、落盘、登记、投递
    ├── 账号库 / 审计日志 ──> MongoDB（独立容器；存账号与 audit_logs，不存任务与产物）
    ├── 运行指标 ──> Redis（短期 TTL，容器 / Worker / 趋势采样）
    ↓  任务消息（Celery）
Redis（broker + 跨进程控制面）
    ↓
Celery Worker（prefork，默认 20 个子进程，一子进程一任务）
    ↓
V4 反向管线 pipeline（6 步）
    ↓
解析 / HLR 标注 / 反向匹配 / 多智能体裁判 / 共识复核 / 报告生成

V4 正向管线（完整性分析；Issue #119 起为多 judge 一致性管线，见 §7.1）
    ↓
阶段1 共享 → N judge 并行 → 聚合·仲裁·复查 → Word+Excel 报告
（正向代码树 backend/forward/ 按任务复制到任务目录内运行；
  Celery 模式下正向入口保留但后端返回 503，尚未接入 Worker）
```

当前部署方式为 Docker Compose：本地开发编排为 `frontend` + `backend` + `mongo` + `redis` + `worker` 五个服务；服务器交付为 `backend` + `mongo` + `redis` + `worker` 四个容器（前端静态产物由后端同源托管）。`backend` 与 `worker` 共享 `backend/output` 卷，任务状态以任务目录内的 `job.json` 为唯一权威。MongoDB 是登录与账号管理的硬依赖，任务归属与产物仍按任务目录（`backend/output/v4/{job_id}/`）落盘。执行层设计见 `docs/decisions/ADR-008-Celery-Redis任务执行层.md`。

`JOB_EXECUTOR=inline`（pytest / 回滚路径）时，API 进程内用 `job_scheduler.py` 线程池执行任务，不依赖 Redis 与 Worker；生产部署固定使用 `JOB_EXECUTOR=celery`。

## 3. 前端职责

前端工程位于 `frontend/`。

前端主要职责包括：

1. 提供登录页、登录状态维持与退出登录；登录成功后按角色跳转（审计管理员 → `/audit`，其余 → 业务页）；
2. 提供路由守卫（`RequireAuth` / `RequireAdmin` / `RequireAuditor` / `RequireBusiness`）：未登录跳转登录页；系统管理员不能进入审计中心，审计管理员只能进入审计中心；
3. 提供文件上传页面；
4. 支持上传 HLR Word 文件（必填）；
5. 支持上传 EoICD Publisher / Subscriber PubSub Excel（二选一）；
6. 支持上传一个或多个追溯 Excel（可选）；
7. 调用后端 `/api/v4/coverage-analysis` 创建任务；
8. 展示任务处理状态（六步进度）；
9. 展示处理结果摘要（统计卡片 + 星级分布）；
10. 提供输出文档预览与下载入口；管理员在任务 / 历史列表可看到“所属账号”列；
11. 提供管理员用户管理页（创建账号、重置密码、禁用 / 启用、选择三值角色）；
12. 提供管理员模型设置页面，运行时修改三个模型名称与思考模式开关（无需重启服务）；非系统管理员隐藏该入口；
13. 提供审计中心（`/audit`）：概览、登录日志、操作日志、系统运行状态四个标签页；日志支持按时间、结果、角色、模块、操作类型、账号、IP、关键词筛选与服务端分页，详情抽屉展示业务明细；系统运行页用纯 SVG / CSS 图表展示容器资源、任务进程与告警，5 秒轮询且在页面隐藏时暂停。

前端不负责：

1. 解析 Word 或 Excel 文件；
2. 调用大模型；
3. 执行反向匹配或裁判流程；
4. 执行降级保护规则；
5. 生成报告文档；
6. 保存正式业务数据。

## 4. 后端职责

后端工程位于 `backend/`。

后端主要职责包括：

1. 接收前端上传的输入文件；
2. 提供登录 / 登出 / 当前用户接口，系统管理员创建账号、重置密码、禁用 / 启用账号、修改三值角色（MongoDB 账号库）；
3. 创建并维护本地分析任务，创建时写入当前登录用户的归属；
4. 解析 HLR Word 文件与 EoICD PubSub Excel；
5. 用 DeepSeek 对每条 HLR 做 AI 标注；
6. 执行反向匹配（含追溯表预筛选）；
7. 编排多智能体裁判（DeepSeek / MiniMax / Qwen）与降级保护；
8. 执行 Review Agent 共识复核与星级评分；
9. 生成 1 份 xlsx + 4 份 docx 报告；
10. 提供任务状态查询和结果下载接口，并按任务归属做统一访问校验；
11. 记录登录与操作审计日志（异步批量写入 MongoDB，fail-open），并向审计管理员提供日志查询、概览与系统运行状态接口。

## 5. 后端模块划分

后端核心代码位于 `backend/app/`。

| 模块             | 职责                            |
| -------------- | ----------------------------- |
| `main.py`      | FastAPI 应用入口：CORS + `/api/v4` 路由装载；lifespan 初始化 MongoDB / Beanie 并播种初始管理员、执行 Celery 自检、启动扫描与失联巡检；SPA 静态兜底限定在静态目录内（`unquote` + `resolve` + `relative_to`，拒绝 `..`）；`ICD_DOCS_ENABLED` 控制 `/docs` / `/redoc` / `/openapi.json` |
| `auth/`        | 认证与账号模块：`config.py`（认证 env 读取与校验）、`db.py`（Motor + Beanie 初始化 / ping，`document_models` 含 User 与 AuditLog）、`models.py`（User 文档，`role` 三值与 `token_version`）、`roles.py`（角色常量、中文标签与 `effective_role` 兼容回退）、`schemas.py`（请求 / 响应模型，`role` 与旧字段兼容）、`manager.py`（用户名小写化、密码哈希、重复创建保护、角色与 `is_superuser` 同步）、`backend.py`（VersionedJWTStrategy + CookieTransport + FastAPIUsers）、`seed.py`（初始管理员播种 + 历史账号角色回填）、`deps.py`（`current_user` / `current_admin` / `current_auditor` / `require_business_access`）、`router.py`（`/auth/*`、`/admin/users*`，登录与账号变更写审计日志；重置密码 / 禁用递增 `token_version`）、`access.py`（任务归属统一访问校验） |
| `audit/`       | 审计模块（ADR-010）：`catalog.py`（模块 / 动作 / 角色 / 结果中文词表、User-Agent 解析、检索文本拼接）、`models.py`（`audit_logs` 集合与索引）、`writer.py`（异步批量写入器，有界队列 + fail-open）、`schemas.py`（审计接口响应模型）、`service.py`（事件构造、查询、概览、词表）、`system.py`（容器级资源、任务进程、队列、依赖与健康判定）、`router.py`（`/audit/*`，仅审计管理员） |
| `job_manager.py` | 任务状态管理（`JobStatus` / `Job` / `JobManager`）；持有任务累计 token（`total_tokens`）与 manifest 持久化；任务归属字段 `owner_id` / `owner_username` 随 Job 与 `job.json` 持久化。Celery 模式下 manifest（`job.json`）是唯一权威状态，`get_job` / `list_jobs` 每次回读并叠加控制面快照；启动扫描按「pending 存活 / 心跳新鲜存活 / 心跳过期按意图收尾 / Redis 不可达不判断」恢复 |
| `token_usage.py` | 反向 LLM token 采集：解析 OpenAI 兼容 `usage` 的三种形状，并从线程绑定的 Job 累加（只 import `job_log`，不 import `job_manager`，避免循环依赖） |
| `job_log.py`   | 任务日志缓冲、stdout/stderr Tee、线程归属与 job.log 落盘；Celery 模式下 API 进程按字节偏移从 `job.log` 增量同步 Worker 日志（只消费完整行，二进制追加避免 CRLF 字节错位）；不依赖任何 `app.*` 业务模块 |
| `job_scheduler.py` | **inline 模式**的进程内任务队列：固定 `MAX_CONCURRENT_JOBS`（默认 2）个常驻工作线程领取任务（新任务 FIFO，续跑任务插队首，不抢占执行中任务）；出队时做票据比对（同一任务的旧条目丢弃）与跳过裁决（排队期间被终止 / 放弃的任务不执行，原因写入该任务日志）。Celery 模式下不使用 |
| `execution/` | 任务执行层（ADR-008）：`config.py`（执行模式与 env 解析）、`celery_app.py`（Celery 应用与长任务参数、worker 启停时启停指标上报）、`dispatcher.py`（投递 / revoke）、`tasks.py`（Worker 侧入口：认领、心跳、执行反向管线，认领后上报任务进程 PID）、`control.py`（Redis 控制面与 Lua 原子操作、活动任务快照）、`reaper.py`（失联 Worker 巡检）、`metrics.py`（cgroup 内存 / CPU 采样、任务子进程资源、Redis 指标快照与 1 小时历史，供审计中心系统运行页读取）；只被 `api/v4/runner.py`、`api/v4/jobs.py`、`main.py` 调用，不反向依赖路由层 |
| `runtime_settings.py` | 运行时模型设置：三个模型名与三个思考开关原子落盘到 `OUTPUT_DIR/settings.json`，保存时写入 `os.environ`，启动时回读；不依赖任何业务管线 |
| `api/v4/`      | V4 路由层：`router.py`（聚合）、`schemas.py`（响应模型）、`runner.py`（经 `job_scheduler` 入队 + 7 个 derive_*）、`coverage.py` / `jobs.py` / `outputs.py` / `settings.py` |
| `api/v4/hlr_schemas.py` | HLR 表格样式管理与辅助接口：样式列表 / 新建 / 更新 / 删除、文档表格扫描与试解析（扫描与试解析均不调用 LLM）；内置模板只读（403），未知样式 404、重名 409、配置非法 422 |
| `v4/pipeline.py` | V4 反向管线编排（`run_reverse_pipeline`，6 步）；正向管线不在 `v4/` 内（见 `forward/` 与 `app/forward/` 两行） |
| `v4/config.py` | V4 env 加载（DEEPSEEK_* / USE_MOCK_LLM / JUDGE_PROVIDERS）+ 业务常量 |
| `v4/concurrency.py` | 通用任务内并行执行器 `run_parallel_ordered`：固定窗口提交、结果按输入顺序归位、线程上下文绑定、取消时停止调度未提交条目；不依赖任何降级模块 |
| `v4/errors.py` | 管线异常 → 面向用户的错误分类与建议；复用 `degradation.fallback.classify_exception` |
| `v4/models.py` | V4 Pydantic 模型（EoICDRequirement / HLRLabel / ReverseCase / ConsensusResult 等） |
| `v4/parsers/`  | EoICD PubSub Excel + HLR Word 解析；`glossary_tables.py` 按表头语义识别缩略语表/术语表（不依赖固定表序号、列数或列顺序） |
| `v4/parsers/hlr_schema.py` | HLR 表格样式模型与辅助：样式配置校验与序列化、内置模板派生（AMS/FGMC/HSCU/RPDU）、样式叠加到 `HLRParserConfig` / `ControllerProfile`、文档表格扫描与试解析辅助 |
| `v4/hlr_schema_store.py` | 自定义 HLR 表格样式的持久化与引用解析：`OUTPUT_DIR/hlr_schemas.json` 原子读写、CRUD、名称唯一校验、`template_id` / `schema_id` / 内联配置三种引用的解析与快照序列化 |
| `v4/matching/` | HLR 标注、条目过滤、信号画像、Block 聚合、HLR 分类、反向匹配、追溯预筛选；`glossary_index.py` 为每条 case 选取命中的文档术语（只供裁判/复查 prompt，不参与匹配评分）；`internal_channel.py` 为 AMS 内部通道需求前置判断（命中按无匹配 + 内部接口标注，不进入 Block 评分与裁判） |
| `v4/comparison/` | 多模型裁判、Review Agent 共识、一星复查、报告生成 |
| `v4/degradation/` | Provider 健康跟踪、Case 超时、熔断、星级降级；`concurrency.py` 仅保留降级专用的进程级 executor / 信号量与 `_submit_with_gate` 背压闸门 |
| `v4/doc_generators/` | xlsx + 单模型 docx + 共识 docx 生成（反向） |
| `v4/llm/`       | LLM 抽象层：`factory.py`（env 驱动 + mock fallback）、deepseek/minimax/qwen client、`mock_llm.py` |
| `v4/prompts/`   | Prompt Markdown 文本资产（reverse_judge / consensus / re_review） |
| `v4/traceability/` | 追溯表预筛选（独立零耦合模块） |
| `v4/refine/` | RPDU 专属：matched ICD Block 无关过滤 + 精确/同义词补采（Issue RPDU 适配续）；Step 3 后可选阶段 |
| `forward/`     | 正向（完整性）多 judge 一致性管线**原样代码树**（唯一事实源；每任务整树复制到 `<job_dir>/forward/` 运行） |
| `app/forward/` | 正向适配层：`root` / `projects` / `config_builder` / `mock_server` / `execution` / `artifacts` / `adapter`；编排与翻译、零业务算法、**不 import `app.v4.*` 业务模块**（唯一例外：零业务算法的纯 I/O 工具 `v4/parsers/zip_entry_normalize`，BUG-20261010-001） |
| `output/`      | 运行时生成的输出文件存放目录                |

**HLR 表格样式与 controller profile 的分工**：自定义样式只覆盖解析器的 `field_map`（行标签映射）与 `requirement_scan`（需求表识别与 `id_pattern` 校验）；controller profile 继续负责匹配关键词、追溯表配置、AI 标注示例与 preprocess hook —— `build_profile_with_schema` 只替换 `hlr_parser` 配置，profile 的其余配置原样保留。内置模板由各 profile 的 `config.yaml` 派生，未修改的模板按该控制器的原生解析路径执行（native），因此默认行为与样式能力引入前一致。下游管线只消费 `requirement_id` + `content`（可选 `rationale`），样式变化不影响 Step 2 及之后的输入与产物结构。

### 5.1 认证与账号隔离（阶段 A，ADR-007；角色三值与审计管理员见 ADR-010）

`backend/app/auth/` 是认证与账号管理的唯一入口，V4 路由不在 `main.py` 内直接实现鉴权逻辑：

```text
backend/app/auth/
├── config.py    # MONGO_URL / MONGO_DB / AUTH_JWT_SECRET / AUTH_TOKEN_TTL_SECONDS /
│                # AUTH_COOKIE_NAME / AUTH_COOKIE_SECURE / ICD_ADMIN_* / ICD_DOCS_ENABLED
├── db.py        # AsyncIOMotorClient + init_beanie（User + AuditLog）+ ping_db；启动不可达即失败
├── models.py    # User（集合 users；email 字段承载小写用户名；is_active / is_superuser / role / token_version）
├── roles.py     # ROLE_USER / ROLE_ADMIN / ROLE_AUDITOR / ROLE_ANONYMOUS / ROLE_SYSTEM、
│                # ROLE_LABELS、effective_role（role 为空时按 is_superuser 回退）、is_admin / is_auditor
├── schemas.py   # UserCreateRequest（role，兼容 is_admin）/ UserUpdateRequest（role，兼容 is_superuser）/
│                # UserRead（含 role / role_label / is_auditor）/ MeResponse
├── manager.py   # UserManager：用户名小写化、密码哈希、重复创建与唯一索引竞态保护、角色与 is_superuser 同步
├── backend.py   # VersionedJWTStrategy（Token 版本号校验）+ CookieTransport（HttpOnly / SameSite=Lax / Path=/）+ FastAPIUsers
├── seed.py      # 用户集合为空时按 ICD_ADMIN_* 创建初始管理员；ensure_user_roles 回填 / 修正历史账号角色
├── deps.py      # current_user / current_admin / current_auditor / require_business_access
├── router.py    # /auth/login、/auth/logout、/auth/me、/admin/users*（登录 / 登出 / 账号变更写审计日志；重置密码 / 禁用递增 token_version）
└── access.py    # get_job_owner / ensure_owner / require_job_access / is_valid_job_id
```

角色与访问规则：

| 角色 `role` | 中文标签 | 权限范围 |
| --- | --- | --- |
| `user`（默认） | 业务用户 | 业务页面与业务接口；任务按归属可见 |
| `admin` | 系统管理员 | 业务全部权限 + `/admin/users*` + 全局设置 / HLR 样式管理；**不可进入审计中心**（前后端均 403） |
| `auditor` | 审计管理员 | **仅** `/auth/login`、`/auth/logout`、`/auth/me`、`/health` 与 `/audit/*`；业务接口统一 403 |

`is_superuser` 保留为兼容字段，由 `role` 派生（`admin` → True，其余 → False）；`role` 为空的历史账号按 `is_superuser` 回退（`effective_role`），启动时 `ensure_user_roles()` 回填落库。

关键边界：

1. **Mongo 存账号与审计日志**：`mongo` 为 compose 独立服务（命名卷 `mongo_data`），后端容器通过服务名访问；服务器编排不映射宿主端口。任务与产物仍落 `backend/output/v4/{job_id}/`，设置与 HLR 样式仍落 `backend/output/*.json`。
2. **任务归属**：`Job` 与 `job.json` 增加 `owner_id`（`str(User.id)`，权限键）与 `owner_username`（冗余展示）。创建任务时写入当前登录用户；续跑沿用原归属；CLI 与旧 manifest 无归属 → 仅管理员可见。
3. **统一访问校验**：所有按 `job_id` 的任务接口（内存 / 磁盘）统一走 `app/auth/access.py` 的 `require_job_access()` / `ensure_owner()`；内存任务先查 `Job.owner_id`，磁盘任务读 manifest，归属不匹配或任务不存在统一 404，管理员放行全部；任务 / 历史列表按 `owner_id` 过滤。`outputs.py` 先校验归属再判断文件存在性，避免通过 404/200 差异探测他人任务。普通用户访问管理员接口 → 403。
4. **业务路由统一业务准入**：`api/v4/router.py` 用 `_business_only = [Depends(require_business_access)]` 统一挂到业务子路由（coverage、completeness、jobs、outputs、history、settings、hlr_schemas），审计管理员访问业务接口统一 403，不逐个路由重复判断。
5. **历史与全局配置**：`GET /history` 按 manifest 的 `owner_id` 过滤（管理员全部）；`POST /history/delete`、`GET/PUT /settings`、HLR 样式新建 / 更新 / 删除限管理员；HLR 样式列表 / 扫描 / 试解析对登录用户开放。
6. **失败语义**：未登录 401；Mongo 运行中失联 → 受保护接口 503 `{"detail": "database unavailable"}`；启动时 Mongo 不可达 → 启动失败并打印明确日志；登录失败返回 400（账号不存在 / 密码错误为 `LOGIN_BAD_CREDENTIALS`，密码正确但账号被禁用为 `LOGIN_USER_NOT_ACTIVE`；具体原因只写审计日志）。
7. **静态兜底加固**：SPA 兜底只返回解析后位于静态目录内的文件；含 `..`（含编码形式）或越界的请求返回 404，不回落 `index.html`（不做路径纠正）；`/api/*` 未匹配路由返回 JSON 404；`ICD_DOCS_ENABLED=0` 时 `/docs` / `/redoc` / `/openapi.json` 也不被兜底接住。
8. **正向代码树未改**：`backend/forward/` 业务实现与产物生成方式不变，正向适配层只在 API 入口增加登录与归属校验；正反向仍互不 import 业务模块。

### 5.2 审计与运行监控（ADR-010）

`backend/app/audit/` 是审计日志与系统运行状态的唯一入口，业务路由只通过 `record_event()` 记录事件，不直接写 Mongo：

```text
backend/app/audit/
├── catalog.py   # 类别 / 模块 / 动作 / 动作类型 / 角色 / 结果中文词表；User-Agent 解析；关键词检索文本拼接
├── models.py    # AuditLog（集合 audit_logs；ts 倒序、category / module / actor / result / ip 索引；永久保留）
├── writer.py    # AuditWriter：有界队列（20000）+ 后台批量写入（100 条 / 0.5s）；fail-open，不阻塞业务
├── schemas.py   # 审计日志 / 明细 / 概览 / 系统运行 / 历史趋势响应模型
├── service.py   # build_event / record_event / query_logs / get_log / overview / meta
├── system.py    # 容器资源、任务进程、队列、依赖健康与 1 小时趋势聚合；告警阈值判定
└── router.py    # /audit/logs、/audit/logs/{id}、/audit/meta、/audit/overview、/audit/system、/audit/system/history
```

运行监控数据来源（`app/execution/metrics.py`）：

- **API 容器**：API 进程每 5s 采样自身 cgroup 内存（v2 `memory.current` / v1 `memory.usage_in_bytes`，有 `inactive_file` 时扣除）与 CPU（按 quota / 宿主核数归一），写 Redis `icd:metrics:api`（TTL 30s）。
- **Worker 容器**：Celery Worker 启动时起后台线程，每 5s 上报 CPU / 内存到 `icd:metrics:worker:{hostname}`（TTL 20s）；心跳超过 15s 视为失联。
- **任务进程**：Worker 认领任务后上报子进程 PID；审计接口按 `psutil` 读取各任务 RSS / CPU，仅展示不设阈值。
- **趋势**：采样点写入 Redis `icd:metrics:history`（720 点 / 1 小时 TTL），供系统运行页折线图；Redis 不可用时返回 `available=false` 与提示，不报 500。
- **依赖健康**：Mongo / Redis 只做连接与延迟探测，不挂载 `docker.sock`、不做宿主机级监控。

关键边界：

1. **审计写入 fail-open**：队列满或 Mongo 异常时丢弃事件并记日志，业务请求正常返回；审计失败不得影响业务。
2. **敏感信息不落库**：不记录密码、JWT / Cookie、API Key、文件内容；登录失败原因只在审计记录中体现。
3. **日志不可删**：v1 无删除接口、无 CSV 导出；查询本身不记审计，避免自放大。
4. **时间口径**：存储 UTC，展示 Asia/Shanghai；IP 取 `request.client.host`，暂不解析 `X-Forwarded-For`。
5. **角色互斥**：系统管理员不能进入审计中心；审计管理员只能使用审计中心（含登录 / 登出 / 本人信息 / 健康检查）。
9. **会话吊销**：`User.token_version` 随登录写入 JWT claim `ver`；重置密码或账号由启用改为禁用时递增，该账号所有已签发 JWT 下一次请求即 401；重新启用不恢复旧会话。旧 Token 无 `ver` 按 0 处理。

## 6. 前端模块划分

前端核心代码位于 `frontend/src/`。

| 模块            | 职责          |
| ------------- | ----------- |
| `components/` | 页面组件        |
| `api/`        | 后端 API 调用封装 |

前端应通过 `frontend/src/api/` 统一访问后端接口，不建议在页面组件中分散编写原始请求逻辑。

## 7. 主流程数据流

后端主流程由 `v4/pipeline.py` 统一编排，按 6 步组织。

```text
上传文件
    ↓
Step 1 解析输入
   HLR Word + EoICD PubSub Excel → hlr_requirements.json + eoicd_requirements.json
   HLR Word 按表头语义识别缩略语表/术语表 → glossary / terms（terms 为空不写字段）
   可选自定义 HLR 表格样式只替换解析层 field_map / requirement_scan，不改变下游契约
   模块: v4/parsers/{eoicd_excel_parser,hlr_word_parser,glossary_tables,hlr_schema}.py
    ↓
Step 2 HLR AI 标注
   DeepSeek 标注 bus_types / labels / devices / signal_keywords → hlr_labels.json
   模块: v4/matching/hlr_labeler.py
    ↓
Step 3 反向匹配
   条目过滤 → 信号画像 → Block 聚合 → HLR 分类 → 两阶段匹配 → 可选追溯预筛选
   模块: v4/matching/{entry_filter,signal_profiler,reverse_case_builder,hlr_classifier,reverse_matcher,traceability}.py
    ↓
Step 3.5 RPDU refine 后处理（仅 RPDU profile 启用，由 pipeline `refine=True` 触发）
   matched ICD Block 无关过滤 + 精确补采 + 同义词补采 → 重建 cases
   模块: v4/refine/{block_filter,runner}.py
    ↓
Step 3.6 文档术语索引（不新增 LLM 调用）
   每条 case 只保留 SWHLR 正文/基本原理命中的术语与缩略语 → ReverseCase.glossary_hits
   未命中 case 保持空列表；匹配评分不使用该索引
   模块: v4/matching/glossary_index.py
    ↓
Step 4 多智能体裁判（含降级保护）
   DeepSeek / MiniMax / Qwen 并行判定 → multi_judge_results.json
   初判 prompt 追加 case 命中的「文档术语与缩略语」段（非 EoICD 依据）
   模块: v4/comparison/{multi_judge,semantic_judge}.py + v4/degradation/*
    ↓
Step 5 Review Agent 共识
   综合复核 + 星级评分 → consensus_results.json（含一星复查 + 部分共识重跑）
   低星复查 prompt 复用同一段 case 命中术语
   模块: v4/comparison/{review_agent,re_review}.py + v4/degradation/context.py
    ↓
Step 6 报告生成
   1 份 xlsx + 3 份单模型 docx + 1 份共识 docx
   模块: v4/doc_generators/* + v4/comparison/report_generator.py
    ↓
更新任务状态和输出文件路径
```

重要约束：

```text
输入文件只在解析阶段处理一次。
后续标注、匹配、裁判和共识复核流程应复用解析后的结构化数据。
```

反向管线的任务内并发（只影响反向，正向管线不变）：

```text
Step 2 HLR 标注         REVERSE_LABEL_CONCURRENCY   默认 1（串行）；>1 时未命中条目并行标注
Step 4 / 5 / 5.5 / 5.6  REVERSE_CASE_CONCURRENCY    默认 1（串行）；>1 时 case 维度并行
每模型并发峰值          ICD_WORKER_CONCURRENCY × max(case_concurrency, label_concurrency)
                        （同一任务同一时刻只处于一个阶段）；超过 LLM_PER_MODEL_LIMIT 时启动日志打 WARN
结果顺序                并行不改变产物顺序：所有并行入口按输入顺序归位
共享预算                Step 4 / 5.5 的 provider 调用仍走进程级 executor + DEGRADATION_MAX_INFLIGHT 信号量
```

实现入口集中在 `v4/concurrency.run_parallel_ordered()`：固定窗口提交、每个线程经
`bind_thread_context` 继承 job 归属与运行上下文、取消时停止调度未提交条目、异常原样抛出。

### 7.1 正向完整性分析流程（EoICD → HLR，多 judge 一致性管线）

正向管线回答「EoICD 业务对象在 HLR 正文中是否漏写」，与反向分析（正确性比对）互补。Issue #119 起，正向改为**原样集成**的多 judge 一致性管线，当前代码树对应「正向检查集成-发送工具集成版 V5」：业务实现全部在 `backend/forward/` 原样代码树内（`multi_judge_runner.py` 编排），工程侧由适配层 `backend/app/forward/` 驱动，与反向管线（`v4/pipeline.py`）严格分离。

```text
上传文件（HLR 需求文件 .docx/.xlsx + EoICD PubSub Excel[+ 追溯表 0-N 张，单槽多文件，未传=全量 / 传任一张=追溯]）
    ↓
Step 1/4 阶段1 共享（EoICD 数据处理，只跑一次）
   HLR .xlsx 走扁平需求清单解析（V4.1）；中文名称先做翻译预扫并落盘缓存
   共享产物复制给各 judge（「输入只解析一次」在新正向中的落点）
    ↓
Step 2/4 各 judge 并行独立判定（阶段2/3/4；每 judge 一个子进程）
   真实模式 deepseek / minimax / qwen；MOCK 模式三个 mock judge
    ↓
Step 3/4 聚合 + Step5 仲裁 + Step5.5 peer-aware 复查
   仲裁固定 deepseek（配置 arbitrator 段）
   V4.1 起输出 judge 有效性与复核失败计数；单 judge 有硬上限 watchdog
    ↓
Step 4/4 报告生成
   Word 报告 + Excel 明细；按固定交付名复制到 <job_dir>/output/
    ↓
更新任务状态和输出文件路径
```

进度口径（前端 `components/progress/stageMeta.ts` 的正向四段定义，由 `TaskProgressBoard` 渲染）：`parse → multi_judge → review → report`；`case_index / case_total` = 已完成 / 全部 judge 数。

V5 起为降低内存与磁盘占用：属性比对大报告用 `common/jsonstream.py` 流式解析（不再整文件载入）；阶段1 共享产物由 `--eoicd-processed-dir` 直接引用，不再逐 judge 复制；`retained_*` / `attr_not_mentioned_*` / `attr_clustered_*` 等中间态默认不落盘（`config.output` 下 `save_retained_intermediate` / `keep_not_mentioned_intermediate` / `keep_clustered_intermediate` 可显式保留）；judge 子进程不再归档 `report_*.json`（`--no-report-archive`）。上传字段、产物名称与结果口径不变。

2026-11-10 起正向管线接入前端「模型设置」的思考开关：`config_builder` 为每个真实 judge / 仲裁器写入 `provider`，`multi_judge_runner` 以 `AI_PROVIDER` 传入 judge 子进程；HLR 聚类 AI 映射、名称判定、术语翻译、属性比对（含冲突判定）、仲裁共 5 个请求构造点在开关**关闭时**追加 provider 专属字段，开启 / 未知 provider / mock 保持历史请求体。原有业务算法不变。

正向集成层（Issue #119）的模块边界与约束：

```text
backend/forward/            正向代码树（唯一事实源，内容与「发送工具集成版 V5」一致，工程侧不改其业务实现；
                             仅保留工程集成契约：token 增量标记 / sidecar / --base-tokens、--ai-key 日志脱敏）
backend/app/forward/        适配层（编排与翻译，零业务算法）：
                              projects（系统类型识别：显式选择优先，否则按文件名自动识别）
                              config_builder（mock / 真实两套配置；真实密钥仅运行环境解析、子进程 env 传入）
                              execution（子进程执行：进度标记 / 取消 / 超时 / 杀进程树）
                              artifacts（工作区副本 / 产物收集 / 汇总映射）
                              adapter（run_forward_job 编排）
分离原则                    适配层不 import 任何 app.v4.* 业务模块（仅复用 job_manager / job_log 基础设施；
                             另复用零业务算法的纯 I/O 工具 v4/parsers/zip_entry_normalize，仅此一项）；
                             正向不 import 反向、反向不 import 正向，除前端共用外无交叉共用
并发隔离                    正向 runner 的工作区硬编码在代码树内（runs/integration_workspace、output/），
                             两个并发任务共用一棵树会互相覆盖 → 每任务把代码树（约 2MB，仅代码）
                             复制到 <job_dir>/forward/ 运行；续跑复用已有副本，保住 judge 中间结果；
                             复制时跳过运行时缓存（identity_match/src/cache、单judge结果汇总/outputs），
                             任务成功收尾时清理副本内 runs/（失败/中断保留，供续跑与排查）
结果口径                    6 张统计卡取自判定分布（已落实→covered_direct、部分落实→covered_aggregate、
                             未落实→uncovered，其余需人工判断项含「不一致」桶汇总为 possible）；
                             ai_reviewed = 复核覆盖已复核条数；eoicd_count = 信号总数；hlr_count 恒 0；
                             analysis_mode 仅表示是否上传追溯表（full / trace）
```

旧正向实现（`v4/matching/forward_*`、`v4/comparison/coverage_reviewer`、`v4/doc_generators/forward_*`、`v4/prompts/forward_review.md`、`v4/traceability/forward_scope.py`）已随 Issue #119 删除。

## 8. 模块边界约束

开发时应遵守以下模块边界：

1. `main.py` 不应承载复杂业务逻辑；
2. 文件解析逻辑应放在 `v4/parsers/`；
3. 反向匹配逻辑应放在 `v4/matching/`；
4. 多智能体裁判与共识逻辑应放在 `v4/comparison/`；
5. 降级保护逻辑应放在 `v4/degradation/`；与降级无关的通用并行执行工具应放在 `v4/concurrency.py`；
6. 报告文档生成逻辑应放在 `v4/doc_generators/`；
7. 任务状态管理应放在 `job_manager.py`，不应写入 `main.py`；任务执行模式差异（inline / celery）必须收敛在 `execution/` 内，路由层不得写死 Celery 调用；
8. 任务执行层只允许 `runner.py` / `jobs.py` / `main.py` 调用 `execution/`；`execution/` 不得 import `api/v4/*`，避免 Worker 进程拉起整个 Web 层；
9. 前端 API 调用应集中放在 `frontend/src/api/`；
10. 前端组件不应直接处理后端复杂业务逻辑；
11. 正向完整性分析逻辑必须留在 `backend/forward/` 原样代码树内（唯一事实源）；工程侧只允许 `app/forward/` 适配层做编排与翻译，**适配层不得 import 任何 `app.v4.*` 业务模块**（唯一例外：零业务算法的纯 I/O 工具 `v4/parsers/zip_entry_normalize`，按 BUG-20261010-001 复用，不得扩大）；正向不 import 反向、反向不 import 正向，正反向分离、除前端共用外无交叉共用（Issue #119）。

不得将文件解析、匹配、裁判、共识和报告输出混写在单个大文件中。

## 9. 输出文件管理

运行时生成的输出文件存放在后端输出目录中。

```text
backend/output/v4/{job_id}/
├── job.json               # 任务 manifest（状态 / 参数快照 / run_token / 结果摘要 / token）
├── job.log                # 任务日志（API 与 Worker 共享，按字节偏移增量读取）
├── input/                 # 用户上传的原始文件
│   ├── hlr.docx
│   ├── pub.xlsx
│   ├── sub.xlsx
│   └── traceability/      # 仅 enable_traceability_prefilter=true 时存在
└── output/                # pipeline 写出的产物（5 份对外 + 7 份内部 JSON）
```

运行时生成的输出文件不应提交到 Git。既有 `backend/output/v3/` 历史产物保留不删除（ADR-002 D7）。

## 10. 当前架构限制

当前架构服务于本地演示原型，存在以下限制：

1. 任务状态以任务目录内的 `job.json` 为准，不引入独立任务状态服务；MongoDB 用于账号库与审计日志，任务与分析产物不入库（阶段 B 后续设计）。
2. 已实现三种角色（业务用户 / 系统管理员 / 审计管理员）与操作审计，但仍无自助注册、邮箱验证与找回密码；角色不可自定义、不可细粒度到具体操作级别。
3. 暂不实现按账号的资源配额 / 多租户配额管理：Celery 提供的是全局 Worker 并发（默认 20），无按用户配额与公平调度。
4. 暂不实现云端部署和容器编排平台（Kubernetes 等）的运维能力。
5. 任务执行层已由 Celery + Redis 承担（ADR-008）：单队列 `icd.reverse`、固定 Worker 并发（默认 20）、FIFO，无多级优先级、无抢占；`job_scheduler.py` 仅保留为 inline 测试 / 回滚路径。
6. Celery 模式下正向管线未接入 Worker（创建与续跑返回 503），需待正向测试通过后另行接入。
7. Windows 桌面打包版本阶段暂停交付，只保证内网服务器 Docker 部署（ADR-007）。
8. 运行监控只覆盖 API 容器、Worker 容器与任务进程，MongoDB / Redis 仅做健康与延迟探测；不挂载 `docker.sock`、不采集宿主机级进程与整机资源。
9. 告警阈值为容器级固定值（内存 75% / 90%、CPU 85% / 95%、磁盘 80% / 90%、Worker 心跳 15s、依赖不可用），不可在页面配置；任务进程 RSS / CPU 仅展示，不设阈值。
10. 本地开发 compose 未设置容器内存上限时，内存百分比显示为空，只显示已用字节；需在服务器部署中通过 compose 限额获得百分比。
11. 审计日志依赖 MongoDB：MongoDB 不可用时审计写入被丢弃（fail-open），审计查询返回 503；v1 无删除接口、无 CSV 导出、不记录查询自身。

如后续需要扩展上述能力，应通过新的 Issue 或 ADR 明确设计后再实施。

## 11. 架构变更原则

如需调整架构，应遵守以下原则：

1. 小范围模块调整可通过普通 Issue 实施；
2. 涉及主流程、目录结构、API 契约或多智能体方案变化时，应先形成设计说明；
3. 重大架构变化应新增 ADR；
4. 架构变化后应同步更新本文档；
5. 不应在普通 Bug 修复任务中顺手进行架构重构。

## 12. V4 后端集成细节

### 12.1 V4 顶层入口与子包

```text
backend/app/
├── main.py                 # 顶层 FastAPI 入口（CORS + V4 router 装载 + lifespan 初始化 Mongo/管理员 + 静态兜底加固）
├── auth/                   # 认证与账号管理（config / db / models / roles / schemas / manager / backend / seed / deps / router / access）
├── audit/                  # 审计与运行监控（ADR-010）：词表 / AuditLog 模型 / 异步写入器 / 查询与概览 / 系统运行聚合 / 审计接口
├── job_manager.py          # 共享 Job / JobManager（JobStatus 唯一来源；owner_id / owner_username 随 manifest 持久化）
├── job_log.py              # 进程级日志缓冲 / stdout-stderr Tee / job.log 落盘与恢复（不 import app.*）
├── job_scheduler.py        # inline 模式进程内队列（常驻工作线程 + 票据 + 出队跳过裁决；并发上限 MAX_CONCURRENT_JOBS）
├── execution/              # 任务执行层（ADR-008）：执行模式配置 / Celery 应用 / 投递与 revoke /
│                           #   Worker 任务入口 / Redis 控制面 / 失联巡检 / 资源指标采集（不 import api/v4）
├── token_usage.py          # 反向 token 采集（usage 解析 + 绑定 Job 累加；不 import job_manager）
├── runtime_settings.py     # 运行时模型设置：output/settings.json 持久化 + os.environ 热应用
├── forward/                # 正向适配层（Issue #119）：编排与翻译，零业务算法，不 import app.v4.*
│                           #   （唯一例外：zip_entry_normalize 纯 I/O 工具）
│   ├── root.py             # 正向树路径 / 交付文件名 / 汇总快照名等常量
│   ├── projects.py         # 系统类型识别（显式选择优先 / 文件名自动识别）
│   ├── config_builder.py   # 多 judge 配置生成（mock / 真实；真实密钥仅运行环境解析、子进程 env 传入）
│   ├── mock_server.py      # MOCK judge 服务（OpenAI 兼容端点，本地）
│   ├── execution.py        # 子进程执行：进度标记 / 取消 / 超时 / 杀进程树
│   ├── artifacts.py        # 工作区代码树副本 / 产物收集 / 汇总映射
│   └── adapter.py          # 编排与翻译（run_forward_job）
├── api/
│   └── v4/
│       ├── router.py       # V4 路由聚合（health + audit + coverage + completeness + jobs + outputs + history + settings + hlr-schemas）
│       ├── schemas.py      # V4Job* / V4ModelSettings* Pydantic
│       ├── runner.py       # V4 任务启动（celery → execution.dispatcher；inline → job_scheduler）+ 执行函数（runtime 上下文绑定 + derive_*）
│       ├── coverage.py     # POST /api/v4/coverage-analysis
│       ├── completeness.py # POST /api/v4/completeness-analysis（含 controller_profile）
│       ├── jobs.py         # GET /api/v4/jobs/{id}[/result]
│       ├── outputs.py      # GET /api/v4/jobs/{id}/outputs/{kind}
│       ├── settings.py     # GET/PUT /api/v4/settings（运行时模型名）
│       └── hlr_schemas.py  # /api/v4/hlr-schemas（样式 CRUD / 扫描 / 试解析）
└── v4/                     # V4 业务子包
    ├── cli.py              # V4 CLI 入口
    ├── config.py           # V4 env 加载 + 业务常量
    ├── concurrency.py      # 通用任务内并行执行器（固定窗口 + 保序 + 取消）
    ├── errors.py           # 管线异常 → 用户可读的错误分类与建议
    ├── hlr_schema_store.py # 自定义 HLR 表格样式：output/hlr_schemas.json 持久化 + 引用解析
    ├── models.py           # V4 Pydantic 模型
    ├── pipeline.py         # V4 反向管线编排（run_reverse_pipeline）
    ├── parsers/            # EoICD Excel + HLR Word 解析（含 glossary_tables 表头语义识别、hlr_schema 自定义表格样式）
    ├── profiles/           # Controller profile registry（Issue #63 引入）
    │   ├── __init__.py     # ProfileRegistry 单例 + init_registry / get_registry
    │   ├── base.py         # ControllerProfile + 4 个 Config dataclass
    │   ├── ams/            # AMS profile（默认；从现状代码 1:1 抽取，向后兼容）
    │   │   ├── __init__.py
    │   │   ├── config.yaml # HLR 字段映射 / 分类关键词 / 追溯表配置 / AI 标注示例
    │   │   ├── hooks.py    # profile 专属可选钩子
    │   │   └── README.md
    │   └── fgmc/           # FGMC profile（燃油测量管理计算机）
    │       ├── __init__.py
    │       ├── config.yaml
    │       ├── hooks.py
    │       └── README.md
    │   └── hscu/           # HSCU profile（液压系统控制单元）
    │       ├── __init__.py
    │       ├── config.yaml
    │       ├── hooks.py
    │       └── README.md
    │   └── rpdu/           # RPDU profile（远程功率分配单元，Issue #74 多控制器适配）
    │       ├── __init__.py
    │       ├── config.yaml
    │       ├── hooks.py
    │       └── README.md
    │   └── generic/        # 其他系统（通用）：解析完全由自定义表格样式驱动，下游中性默认配置
    │       └── config.yaml
    │   └── fsecu/          # FSECU profile（前舱座椅电子控制单元，占位）
    │       ├── __init__.py
    │       ├── hooks.py
    │       └── config.yaml
    ├── matching/           # 反向匹配、信号画像、HLR 分类、entry filter、case 术语索引
    ├── comparison/         # multi_judge + review_agent + 报告生成
    ├── doc_generators/     # xlsx + 3 类 docx 生成
    ├── prompts/            # reverse_judge / consensus / re_review .md
    ├── llm/                # factory / deepseek_client / minimax_client / qwen_client / mock_llm
    ├── traceability/       # 追溯表预筛选（独立零耦合模块）
    ├── refine/             # RPDU 专属：无关 block 过滤 + 精确/同义词补采
    ├── degradation/        # Provider 健康跟踪 / Case 超时 / 熔断 / Review 降级
    └── synonyms.yaml       # 别名映射
```

```text
backend/forward/            # 正向管线原样代码树（唯一事实源；每任务整树复制到 <job_dir>/forward/ 运行）
```

### 12.2 V4 业务流程（6 步反向管线）

```text
1. 解析输入
   EoICD PubSub Excel + HLR Word → 结构化需求列表
   说明: EoICD 解析生成多层级属性（HL 高层 / DP 数据点 / RP 接收参数，带 layer_type / side）
   说明: HLR Word 按表头语义识别缩略语表/术语表 → glossary / terms（不依赖表序号/列数/列顺序）
   模块: parsers/{eoicd_excel_parser,hlr_word_parser,glossary_tables}.py

2. HLR AI 标注
   DeepSeek 对每条 HLR 标注 bus_types / labels / devices / signal_keywords
   模块: matching/hlr_labeler.py

3. 反向匹配（含条目过滤→信号画像→Block 聚合→HLR 分类→匹配→可选追溯预筛选+兜底）
   3a. 条目过滤: 排除协议 DataFormatType 条目 — matching/entry_filter.py
   3b. 信号画像聚类: 按 (Label, LeafName) 聚类 → SignalProfile，仅取 leaf 层（layer_type ∈ {DP, RP}）条目 — matching/signal_profiler.py
   3c. ICD Block 聚合: 按 (label, signal_family) 分组 → ICDBlock
   3d. HLR 分类: 4 路正则分类 + 提取 Label/位字段/SDI/方向 — matching/hlr_classifier.py
   3e. 两阶段 Block 级匹配（Label 前缀粗筛 → 6 维评分 → 三层过滤 → 三级分层）— matching/reverse_matcher.py
       说明: A429 显式路径在 Label 命中但硬门控剔除全部候选时输出 `match_type=无匹配`，不生成 0 Block 的 `待确定` case
       说明: AMS profile 开启 `matcher.enable_internal_channel_detection` 时，评分前先做内部通道判断（同句 ≥ `internal_channel_min_terms` 个不同通道词，AMS 当前 2 个；数据路径动词含“收到”），命中直接输出 `match_type=无匹配` + `match_evidence.internal_channel=true`，summary 带 `[内部接口]` 标注；`stats` 增加 `hlr_内部接口` 子计数 — matching/internal_channel.py
   3f. 可选追溯表预筛选 + 兜底机制 — matching/traceability.py

3.5 RPDU refine 后处理（仅 RPDU profile，由 pipeline `refine=True` 触发）
   重建完整 ICD Block 索引 → matched 无关过滤 + 精确补采 + 同义词补采 → 覆盖写盘 reverse_matches.json → 重建 cases
   模块: refine/runner.py（run_pipeline_refined_stage）+ refine/block_filter.py（filter_matched_blocks）
   关闭 refine（refine=False / no_refine=True）→ 字节走原 3f 之后链路，行为与原 RPDU 完全相同

3.6 文档术语索引（不新增 LLM 调用）
   每条 case 只保留 SWHLR 正文/基本原理命中的术语与缩略语 → ReverseCase.glossary_hits
   未命中 case 保持空列表；匹配评分不使用该索引
   模块: matching/glossary_index.py

4. 多智能体裁判
   3 Agent 平行裁判（DeepSeek/MiniMax/Qwen）
   初判 prompt 追加 case 命中的「文档术语与缩略语」段（非 EoICD 依据）
   模块: comparison/{multi_judge,semantic_judge}.py
   LLM 抽象: llm/factory.py → get_llm(provider)

5. Review Agent 共识
   综合复核 + 星级评价（1-3★）；低星复查 prompt 复用同一段 case 命中术语
   模块: comparison/{review_agent,re_review}.py

6. 报告生成
   1 份 xlsx + 3 份单模型 docx + 1 份共识 docx
   模块: doc_generators/{excel_generator,word_generator,consensus_word_generator}.py
   + comparison/report_generator.py
```

### 12.3 V4 输出文档结构

```text
{job_dir}/                              # = backend/output/v4/{job_id}/
├── input/                              # 用户上传的原始文件（HTTP 接收时保存）
│   ├── hlr.docx
│   ├── pub.xlsx
│   ├── sub.xlsx
│   └── traceability/                   # 仅 enable_traceability_prefilter=true 时存在
│       ├── <file1>.xlsx
│       └── <file2>.xlsx
└── output/                             # V4 pipeline 写出的产物
    ├── EoICD条目化清单.xlsx           # 对外下载 (URL: .../outputs/eoicd-xlsx)
    ├── EoICD与SWHLR单模型差异分析报告_DeepSeek.docx   # 对外 (URL: .../consistency/deepseek)
    ├── EoICD与SWHLR单模型差异分析报告_MiniMax.docx    # 对外 (URL: .../consistency/minimax)
    ├── EoICD与SWHLR单模型差异分析报告_Qwen.docx       # 对外 (URL: .../consistency/qwen)
    ├── EoICD与SWHLR多模型差异分析报告.docx         # 对外 (URL: .../consensus-docx)
    ├── eoicd_requirements.json          # 内部
    ├── hlr_requirements.json            # 内部
    ├── hlr_labels.json                  # 内部（Step 2 输出）
    ├── reverse_matches.json             # 内部（Step 3 输出；历史结果页可只读查看，见 api.md 14.4）
    ├── multi_judge_results.json         # 内部（Step 4 输出；mock_models 也从此文件提取）
    ├── consensus_results.json           # 内部（Step 5 输出）
    └── reverse_report.json              # 内部（Step 6 输出）
```

5 类对外下载对应 `V4_OUTPUT_FILES` 常量（`backend/app/api/v4/runner.py`），是 SSoT。7 个 JSON 中间产物按 ADR-001 D7 不作为下载 API 暴露；唯一例外是 `reverse_matches.json` 的**只读查看**子路由（`GET /jobs/{job_id}/intermediates/reverse-matches`，浏览器内联渲染、非下载，见 `docs/architecture/api.md` 第 10 / 14.4 节），其余 6 类仍不暴露。

### 12.4 V4 LLM 抽象层

```text
llm/factory.py
  get_llm(provider: str) -> LLMClient
    if USE_MOCK_LLM=1: return MockLLMClient
    if provider == "deepseek": return DeepSeekClient(api_key, base_url, model, thinking=...)
    if provider == "qwen": return QwenClient(api_key, base_url, model, thinking=...)
    if provider == "minimax": return MiniMaxClient(api_key, base_url, model, thinking=...)

llm/thinking.py
  thinking_enabled(provider) / cache_variant(provider)
    读 *_THINKING（默认开启）；关闭值供缓存 key 追加 _thinking=disabled

llm/deepseek_client.py
  DeepSeekClient.chat(messages, temperature, max_tokens=1024, timeout=120, max_retries=2, ...)
    幂等 URL:  base = base_url.rstrip('/'); if base.endswith('/v1'): base = base[:-3]
    url = f"{base}/v1/chat/completions"

llm/qwen_client.py
  QwenClient.chat(messages, temperature, max_tokens=1024, timeout=120, max_retries=2, ...)

llm/minimax_client.py
  MiniMaxClient.chat(messages, temperature, max_tokens=1024, timeout=120, max_retries=2, ...)

llm/mock_llm.py
  MockLLMClient.chat(messages, ...) -> ChatResponse
    返写死 JSON，按 MOCK_JUDGE_RESULT 切 covered / inconsistent / needs_review
```

三个模型名（`DEEPSEEK_MODEL` / `MINIMAX_MODEL` / `QWEN_MODEL`）与三个思考开关
（`DEEPSEEK_THINKING` / `MINIMAX_THINKING` / `QWEN_THINKING`）支持运行时修改：
`app/runtime_settings.py` 把前端保存的值写入 `OUTPUT_DIR/settings.json` 并立即更新
`os.environ`；`get_llm()` 每次调用、正向 `config_builder` 每个任务开始时读取，因此
无需重启。启动时 `main.py` lifespan 回读落盘值（优先于 `.env`）。思考开关由反向
`get_llm()` 与正向各 LLM 请求点共同消费：正向 `config_builder` 为每个真实 judge /
仲裁器写入 `provider`，`multi_judge_runner` 以 `AI_PROVIDER` 传入 judge 子进程，
5 个请求构造点只在**关闭**时追加 provider 专属字段（DeepSeek `thinking: disabled`；
Qwen / MiniMax 同时发 `enable_thinking: false` 与 `chat_template_kwargs: {"enable_thinking": false}`，
前者供 DashScope 云接口、后者供自建 vLLM / SGLang），开启 / 未知 provider / mock 保持历史请求体。
反向关闭时 `llm_cache` 的 key 追加 `_thinking=disabled` 变体，开启路径沿用历史 key。

V4 内 LLM 调用入口：
- `comparison/semantic_judge.py` × 2 处
- `comparison/multi_judge.py`（按 provider 列表循环调）
- `comparison/review_agent.py`
- `matching/hlr_labeler.py`（_call_label_api）

三个 client（DeepSeek / MiniMax / Qwen）的 `chat()` 均内置 `finish_reason=length` 截断自适应重试：截断时自动翻倍 `max_tokens` 重试（上限 16384，最多 2 次倍增），所有调用方自动受益，无需在业务层自行处理。

V4 内 `import requests` 仅 3 处（`deepseek_client.py` / `minimax_client.py` / `qwen_client.py`），其余全部走 `get_llm(provider).chat()`。

### 12.5 V4 工程约束（ADR-001 / ADR-002 引用）

V4 工程化集成的关键决策由 `docs/decisions/ADR-001-V4后端接入策略.md` 与 `docs/decisions/ADR-002-移除V3.md` 维护。

- ADR-001 中 D3（`/api/v4` 命名空间）、D4（import 适配但不改业务逻辑）、D5（mock_models 显式标识）、D6（`consistency/{model}` 扩展点）、D7（JSON 不暴露）仍有效。
- ADR-001 中 D2 / D8 及 D1 的「暂不删 V3」部分已由 ADR-002 取代（V3 已移除、`kind` 字段已删除）。
- ADR-002 记录了 V3 移除范围、决策点 A1（去 kind）与 B（保留 app/v4 正向原型）；其中 D4（保留正向原型）已由 ADR-003 取代（正向原型已删除）。
- ADR-003 记录了 V4 早期正向原型与旧单模型反向 CLI 的移除范围，并约定未来正向采用 ICDBlock 级 Case、候选检索算法待定。其中**与正向相关的表述已被 Issue #119 取代**：正向已改为原样集成「发送工具集成版」多 judge 一致性管线（当前为 V4.1，见 §7.1），不再采用 ICDBlock 级 Case 的旧设计，`v4/` 内的旧正向模块已删除。

如未来 V4 路由 / Schema / JobManager / LLM 抽象层有变化，需同时更新本节与相应 ADR。

### 12.6 任务 token 用量统计（累计总量）

任务的状态、结果、历史接口展示一个累计 token 总量；DOCX 报告中，反向 3 份单模型报告展示对应模型自身的累计用量，多模型共识报告与正向报告展示任务总量。接口层不暴露分模型数据，也不拆输入/输出或调用次数。

```text
Job.total_tokens: int                    # 内存中的唯一对外事实值
Job.tokens_by_provider: dict[str, int]   # 分 provider 累计（反向单模型报告用）
Job.token_usage_tracked: bool            # 仅内存：该任务是否启用统计（CLI 为 False）
job.json -> token_usage.total_tokens     # 5 秒节流 + 终态强制落盘；from_manifest 容错恢复
job.json -> token_usage.by_provider      # 与总量同时落盘/恢复；旧 manifest 为空
```

统计口径与边界：

- **仅 API 任务**：`runner._begin_job_run()` 在管线启动时置 `token_usage_tracked=True`；CLI 不绑定 `Job`，报告不写该行。
- **续跑累计**：`relaunch_from_manifest` 刻意不清零 `total_tokens`，任务续跑后从原值继续；新任务从 0 开始。
- **旧任务**：manifest 无 `token_usage` 键 → 接口返回 `null`、前端隐藏、DOCX 不写行；不把未统计伪装成 0。
- **MOCK**：未产生真实调用，0 是有效值，前端以 MOCK 标签说明。
- **分模型口径**：provider 的所有调用都计入该 provider（DeepSeek 的标注、裁判、共识、复查调用均计入 DeepSeek；MiniMax/Qwen 为各自裁判调用），因此三份单模型报告之和等于总量中这三个 provider 的部分。正向只有一份报告，set_token_usage 只写总量。
- **报告时点**：反向 Step 6 在生成 4 份 DOCX 前取一次「总量 + 分 provider」快照，4 份共用（单模型取各自分项，共识取总量）；生成报告后迟到的并发调用只反映在完成页/API 的实时值中，报告允许略小。

反向（正确性）采集链：

```text
deepseek / qwen / minimax client
  resp.json() 之后立即 record_token_usage(body["usage"], provider=<client.PROVIDER>)
    # 截断重试的每一次 HTTP 响应都计数；业务调用点零改动
  → app/token_usage.extract_total_tokens
  → current_job().add_token_usage(delta, provider=...)
    # Job 归属来自 job_log 的线程局部上下文；Step4/5.5 线程池已由 bind_thread_context 保留
  → Job.total_tokens + Job.tokens_by_provider（线程安全 + 节流落盘）
  → pipeline Step6 取「总量 + 分 provider」快照
      → 3 份单模型 DOCX（各写该模型分项）+ 1 份共识 DOCX（写总量）
```

正向（完整性）采集链（跨 3 层进程）：

```text
judge 子进程（identity / attribute matcher）
  → shared/utils.record_token_usage：模块内线程安全累加，并 print("[tokens] delta=N")
  → run_identity_match / run_attribute_match 在 atexit 写 sidecar：
       <judge_ws>/data/output/{identity_match,attribute_match}/token_usage.json
judge 子进程（AI 映射生成，V3 起 --ai-update 默认开启）
  → hlr_cluster_tool/ai_client.py 每次响应累加并 print("[tokens] delta=N")
  → cluster_hlr 在映射文件同目录写 sidecar：<judge_ws>/ai_update_token_usage.json
仲裁调用
  → multi_judge_arbitrator.record_token_usage（同一契约：累加 + 增量标记）
  ↓ stdout 逐层冒泡（run_integration.run_cmd → runner → execution._pump）
父进程 Job：只解析 [tokens] delta=N 并做加法（累计值行忽略，避免重复计数；
            V3 runner 回显 judge 输出时会加 "[judge_N name] " 前缀，故按 search 解析）
  ↓
multi_judge_runner：启动每 judge 前清理三类旧 sidecar（续跑防重复计数）
  → 汇总各 judge sidecar（identity + attribute + AI 映射）+ 本轮仲裁值
    + --base-tokens（续跑前总量）
  → summary.token_usage → 正向 DOCX
adapter 对账（必须在 cleanup_run_workspace 删除 runs/ 之前）：
  run_delta = max(实时增量, 汇总增量)
  final = base_tokens + run_delta
  → job.set_token_usage(final, force_flush=True)（绝对赋值，不累加）
  → job.result.token_usage 与 forward_summary.json
```

约束：父进程只认增量标记（`[tokens] delta=N`）；三类 sidecar 路径（identity / attribute / AI 映射）是 runner 与入口脚本之间的契约，未来若与「发送工具集成版」源树同步，必须连同增量标记、sidecar 文件名/路径、`--base-tokens` 一并保留，否则逐字节一致性测试会失败。V3 起 runner 会把 judge 子进程的关键行加 `[judge_N name] ` 前缀回显，父进程解析必须用 search 而非 match。**2026-10-29 同步 V4.1 时上游删除了全部 token 采集代码，工程侧已按本契约重新落回源树（见 `backend/forward/使用说明.md` §6.1），后续同步必须继续保留；2026-11-10 同步 V5 时上游同样未包含 token 采集代码与 §6.1，工程侧已再次按契约落回。**
