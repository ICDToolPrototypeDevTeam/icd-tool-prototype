# ADR-007 Celery + Redis 任务执行层

| 项目 | 内容 |
| --- | --- |
| 状态 | **已接受**（已实施，待服务器验收） |
| 生效范围 | 后端任务执行层（反向管线）；前端上传与任务页；部署方式 |
| 提议日期 | 2026-11-12 |
| 关联 ADR | ADR-001（V4 后端接入策略）；不改变评分体系、输出文档格式与既有 API 路径 |

---

## 背景

工具即将面向约 20 个账号开放，使用方式为「20 个用户同时轮询任务状态 + 同时上传文件 + 最多 20 个分析任务同时运行」。真实大文件任务可能持续 24 小时以上。

改造前的执行方式为 `job_scheduler.py` 单进程内线程池：

- 常驻工作线程数固定 `MAX_CONCURRENT_JOBS`（默认 2），即一个 API 进程最多同时跑 2 个任务；
- 任务状态只存在 API 进程内存中，进程重启后正在跑的任务直接丢失（只能靠启动扫描标记为 interrupted）；
- 扩容只能多开 API 容器，但各容器内存状态互不可见，前端轮询会打到哪个容器取决于负载均衡，无法保证一致。

这与「20 并发任务、24 小时+ 长任务、可重启、可恢复」的目标不匹配。

## 决策

### D1：引入 Celery Worker + Redis 作为任务执行层，API 只做生产者

- **Redis**：Celery 的 broker（任务消息队列）+ 跨进程控制面（取消/强制终止/放弃/心跳/占用租约）。
- **API 进程（FastAPI）**：只负责接收上传、落盘输入、写 `job.json`、把任务投递到 Redis，然后返回。不在 API 进程内执行管线。
- **Worker 进程（Celery prefork）**：从 Redis 取任务，独立进程内执行反向管线；一个子进程同一时刻只跑一个任务。

### D2：只接入反向管线；正向管线保留入口但 Celery 模式下返回 503

正向管线在本次改造前尚未测试通过，不进入 Celery。`FORWARD_PIPELINE_ENABLED` 未显式配置时：inline 模式默认允许（保持历史行为），celery 模式默认禁止；创建/续跑正向任务在 Celery 模式下由后端直接返回 503（前端入口保留可见）。后续正向测试通过后，以同一 `execution` 模块扩展 `submit_forward` 即可，不需要改本 ADR 的其余结构。

### D3：任务状态以 `job.json` 为唯一权威，Redis 只存控制意图

- 不使用 Celery result backend。Worker 把状态、结果、token 用量写入任务目录的 `job.json`（manifest）；
- API 侧 `JobManager` 在 Celery 模式下每次 `get_job` / `list_jobs` 从 manifest 回读并叠加控制面快照（`cancel` / `force` / `abandon`）；
- API 重启语义：`pending` 保持 `pending`（Redis 里的消息仍在，Worker 会执行）；`running` 且心跳新鲜保持 `running`；心跳过期按控制意图收尾为 `canceled` / `abandoned`，否则 `interrupted`；Redis 不可达时不做任何判断（不能把「暂时连不上控制面」当成「Worker 已死」）。

### D4：跨进程控制面使用 Redis Hash + Lua 原子操作

每个任务一个 hash `icd:job:{job_id}:ctl`，字段包括 `run_token`、`claimed_token`、`heartbeat_at`、`cancel`、`force`、`abandon`，外加活跃任务 zset `icd:active`。占用、心跳、取消请求、强制请求、放弃请求、释放租约全部通过 Lua 脚本原子完成。

- `run_token`：每次新建/续跑生成新的 uuid，Worker 认领时校验；旧队列消息因 token 不匹配被丢弃，避免续跑后重复执行；
- Celery task id 取 `{job_id}:{run_token}`，使同一任务的续跑不会被 Celery revoked 集合误杀；
- Worker 每 30 秒心跳；API 侧巡检线程（reaper，每 60 秒）把心跳超过 300 秒的任务按上述语义收尾；
- Redis `visibility_timeout` 设为 30 天，覆盖 24 小时+ 的长任务，避免消息被重复投递。

### D5：Worker 并发默认 20，一子进程一任务

- 启动参数 `-c ${ICD_WORKER_CONCURRENCY:-20}`，prefork 模式，20 个子进程；
- `worker_prefetch_multiplier=1`、`worker_max_tasks_per_child=1`：防止单个 Worker 预取多个长任务导致排队饥饿，也避免长任务反复复用子进程积累内存；
- `task_acks_late=True`、`task_reject_on_worker_lost=False`：任务在子进程崩溃时由 reaper 判定为 `interrupted` 并允许续跑，而不是无限重投；
- 并发 20 意味着内存需求按「20 × 单任务峰值」规划，部署时必须显式设置 Worker 容器内存上限（见部署说明 §5.4）。

### D6：保留 inline 模式作为测试与回滚路径

`JOB_EXECUTOR` 未配置或为 `inline` 时，维持原有进程内线程池行为（API 内执行）。该模式只用于：

1. pytest（不依赖 Redis / Celery）；
2. 紧急回滚（部署环境去掉 Redis 与 Worker，改回单容器）。

它不是生产推荐路径。`backend/app/execution/` 是两种模式的统一入口，任务路由代码不感知执行器差异。

### D7：上传并发加固纳入本次范围

- 上传改为 1MB 分块流式写 `.part` 临时文件，完成后 `os.replace` 原子改名；
- 单文件 50MB、单请求 200MB 预算，超限返回 413；
- HLR 表格自动识别等 CPU 型探测放入线程池，不阻塞事件循环。

### D8：日志跨进程走 `job.log` 增量同步

Worker 进程写 `job.log`；API 进程的日志接口按字节偏移增量读取（只消费完整行），避免 24 小时+ 长任务把整份日志反复读入内存。写入使用二进制追加，规避 Windows 下文本模式换行转换导致的字节偏移错位。

## 原因

1. **容量**：进程内 2 线程无法承载 20 个并行任务；Celery prefork 直接给出 20 个独立执行单元，任务之间进程级隔离，单个任务崩溃不影响其他任务。
2. **长任务**：24 小时+ 任务需要「API 可重启、Worker 可滚动重启、任务可续跑」。`job.json` 权威 + 心跳/巡检 + `run_token` 的组合让 API 与 Worker 都能独立重启而不误杀任务。
3. **一致性**：状态不再依赖「轮询打到哪个 API 实例」，任何实例读同一份 manifest，多副本部署可行。
4. **可控性**：取消、强制终止、放弃三种操作在跨进程下需要明确的语义与原子性；Redis Lua 控制面把并发竞争收敛到单点。
5. **迁移成本**：Celery 是用户明确选定的技术栈；反向管线本身是同步函数，Worker 侧只需一层 `tasks.py` 包装，不改管线内部。

## 影响

### 部署

- 由「单后端容器」变为三个服务：`redis` + `backend`（API）+ `worker`；三者共享 `backend/output` 卷；
- 新增环境变量：`JOB_EXECUTOR`、`CELERY_BROKER_URL`、`CELERY_QUEUE`（可选）、`FORWARD_PIPELINE_ENABLED`（可选）、`ICD_WORKER_CONCURRENCY`、`ICD_WORKER_MEM_LIMIT`、`ICD_WORKER_MEMSWAP_LIMIT`、`ICD_REDIS_MEM_LIMIT`；
- 离线服务器需额外导入 `redis:7-alpine` 镜像；
- 内存预算必须显式分配（API / Redis / Worker），否则 20 并发长任务可能触发 OOM（见部署说明 §5.4、§5.5）。

### 行为

- Celery 模式下正向任务的创建与续跑返回 503「当前部署未启用正向完整性分析」；
- Celery 模式下取消运行中任务：已占用 → 请求取消、由 Worker 在检查点收尾；未占用 → 直接置 `canceled`；强制终止 → 请求 + revoke（Worker 侧 SIGKILL）；
- 任务状态新增/明确 `interrupted`、`canceled`、`abandoned` 三种终态语义（见 `docs/project/workflow.md` §10）；
- 新增第三方依赖 `celery[redis]` 与 `redis>=6.1,<7`（kombu 5.7 的 Redis transport 要求 redis-py ≥ 6.1，低版本 Worker 直接起不来）。

### 不变

- 所有既有 API 路径、请求/响应字段不变（仅新增 503/409 错误语义）；
- 反向管线内部 6 步流程、评分规则、输出文档格式不变；
- inline 模式行为不变。

## 替代方案

1. **扩大进程内线程池到 20**：改动最小，但状态仍在进程内存，API 重启即丢任务；GIL 下 20 个 CPU/IO 混合任务相互拖慢；无法多副本。否决。
2. **RQ / Huey / Dramatiq**：能力接近，但用户明确选择 Celery，且 Celery 的 prefork 模型对「一子进程一长任务」更直接。否决。
3. **Celery + 数据库结果后端**：引入数据库违背项目「暂不引入数据库」的边界（ADR-001 时代约束），且 result backend 不解决状态一致性问题——`job.json` 已足够。否决。
4. **Kubernetes 一任务一 Pod**：运维复杂度远超原型阶段需要。否决。

## 状态

已接受，已实施并完成本地 Docker Compose 验证（单任务、排队重启存活、取消、续跑、强制终止、4 路并行、20 并发轮询、巡检收尾、上传 413）。20 路重任务并发压测因本地 Docker 虚拟机内存限制（11.6GB）未执行，需在目标服务器上验证。
